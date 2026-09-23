"""Connect requested metric routes to exact private-analysis player episodes."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import zipfile

try:
    from .skill_scope_player_identity import load_bound_player_episodes
    from .skill_scope_episode_projection import project_player_episodes
    from .recalculate_skill_scope import recalculate
    from .requested_skill_scope import active_manifest
    from .projectile_hit_catalog import build_projectile_skill_catalog
except ImportError:
    from skill_scope_player_identity import load_bound_player_episodes
    from skill_scope_episode_projection import project_player_episodes
    from recalculate_skill_scope import recalculate
    from requested_skill_scope import active_manifest
    from projectile_hit_catalog import build_projectile_skill_catalog

ROOT=Path(__file__).resolve().parent.parent
# No raw object IDs, source paths, effect targets, or private diagnostic graphs
# cross into skillOperation, which is copied into the public analysis.
DISPLAY_FIELDS={'metricId','characterCode','skillGroup','label','mode','unit','numerator',
    'status','reason','attemptCount','hitCount','hitRate','multiTargetAttemptCount',
    'multiTargetAttemptRate','distinctEnemyTargetsSummedAcrossAttempts',
    'meanDistinctEnemyTargetsPerAttempt','deduplicatedEnemyContactEventCount',
    'targetCohort','distinctTargetsSummedAcrossAttempts','meanDistinctTargetsPerAttempt','deduplicatedTargetContactEventCount',
    'denominatorMeaning','outcomeScope','fallbackUsed',
    'allCastCount','combatCastCount','castCountMeaning',
    'observedCombatCastCount','verifiedCombatCastCount','unresolvedCombatCastCount',
    'perUseCompletenessTracked','incompleteUsesCountedAsMisses',
    'variantBreakdown','reinforcementMappingStatus','phaseScope',
    'calculationConfidence','developmentLabel','verifiedCompletionCredit',
    'cancelledNoRecordedContactCastCount','cancelledNegativeMeaning',
    'regionEstimateMayMisclassify','estimatedRegionCastCount','regionEstimateMeaning',
    'binarySuccessRetainedWithUnresolvedTargets','castsWithUnresolvedTargetRegions',
    'hitRateScope','fullRequestedMetricComplete','confirmedTipCastCount',
    'tipRegionUnresolvedCastCount','tipCountMeaning','phase',
    'estimatedContactCastCount','estimatedOnlyHitCastCount','confirmedHitCastCount',
    'attributionEstimateMayOvercount','phaseExcludedAmbiguousCastCount',
    'attributionEstimateMayMisclassify','estimatedAttributionCastCount','estimatedMissCastCount','attributionEstimateMeaning',
    'provisionallyExcludedWinnerInterruptedCastCount',
    'provisionallyExcludedCancelledCastCount','denominatorStatus',
    'targetCountsAreLowerBounds'}
EPISODE_DISPLAY_FIELDS=DISPLAY_FIELDS|{'outcomes','phaseBreakdown','multiTargetStatus',
    'multiTargetTimelineStatus','distinctEnemyTargetsPerAttempt','distinctEnemyTargetFirstHitTicksPerAttempt',
    'distinctTargetsPerAttempt','distinctTargetFirstHitTicksPerAttempt','policyClassifiedCastCount',
    'binaryCastSuccessComplete','completeTargetCountsAvailable','outcomeTimingScope'}


def public_metric(row,*,episode=False):
    fields=EPISODE_DISPLAY_FIELDS if episode else DISPLAY_FIELDS
    public={key:value for key,value in row.items() if key in fields}
    try:
        from .skill_hit_rate_bounds import unresolved_hit_rate_bounds
    except ImportError:
        from skill_hit_rate_bounds import unresolved_hit_rate_bounds
    bounds=unresolved_hit_rate_bounds(row)
    if bounds is not None:public['unresolvedHitRateBounds']=bounds
    if 'phaseMetrics' in row:
        phases=row['phaseMetrics']
        if not isinstance(phases,dict) or any(not isinstance(k,str) or not isinstance(v,dict) for k,v in phases.items()):
            raise ValueError('phase metrics must be a named mapping')
        public['phaseMetrics']={name:public_metric(phase,episode=episode) for name,phase in phases.items()}
    return public


def public_episode_metrics(projected):
    # The shared projector also serves private diagnostics. Its exact target
    # object IDs and wire graphs are not part of the UI data contract.
    return [{key:episode[key] for key in ('episodeId','startTick','endTick')} |
            {'metrics':[public_metric(row,episode=True)
                        for row in episode['metrics']]} for episode in projected['episodes']]


def intersect_ranges(ranges,episodes):
    output=[]
    for left,right in ranges:
        for e in episodes:
            start,end=max(left,e['startTick']),min(right,e['endTick'])
            if start<end:output.append([start,end])
    merged=[]
    for left,right in sorted(output):
        if merged and left<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],right)
        else:merged.append([left,right])
    return merged


def personal_episode_cache(cache, episodes):
    """Use authoritative personal episodes without narrowing them to old scopes.

    Return a shallow input copy: retained facts and original intervals are preserved.
    A skill cast inside a personal PvP episode remains an attempt even when its
    immediate target is an NPC.
    """
    if set(episodes) != {p['objectId'] for p in cache['players']}:
        raise ValueError('personal episodes must cover the exact cache roster')
    intervals = {}
    for pid, local in episodes.items():
        project_player_episodes([], local)
        intervals[str(pid)] = [[e['startTick'], e['endTick']] for e in local]
    return {**cache, 'intervals': intervals}


def attach_requested_skill_operation(players, *, replay_sha256, cache_path=None,
                                    identity_path=None, game_data_path=None, metric_specs=None,retained_source=None,
                                    episode_base_result=None, evidence_cache=None, player_identity=None):
    if metric_specs is None:
        metric_specs=active_manifest()
    cache,episodes=load_bound_player_episodes(cache_path,identity_path,
        analysis_match_key=replay_sha256,players=players,evidence_cache=evidence_cache,
        player_identity=player_identity)
    for local in episodes.values():project_player_episodes([],local)
    game_data_path=Path(game_data_path)
    if hashlib.sha256(game_data_path.read_bytes()).hexdigest()!=cache['gameDataSha256']:
        raise ValueError('analysis gameDb differs from cache')
    characters={p['characterCode'] for p in players}
    specs=[s for s in metric_specs if s['characterCode'] in characters]
    scoped=personal_episode_cache(cache, episodes)
    catalog=build_projectile_skill_catalog(game_data_path)
    with zipfile.ZipFile(game_data_path) as archive:
        tables={n:json.loads(archive.read(n+'.json')) for n in
                ('Skill','CharacterState','CharacterStateGroup','EffectAndSound','SummonObject','ProjectileSetting')}
    if retained_source is None:
        try:
            from .corpus_runtime_source import select_retained_decode
        except ImportError:
            from decoder.corpus_runtime_source import select_retained_decode
        retained_source=select_retained_decode(ROOT/'local-corpus/replays'/cache['matchKey'],
                                               cache['matchKey'],cache['clientVersion'])
    result=recalculate(scoped,catalog,tables,metric_specs=specs,include_player_observations=True,
                       retained_source=retained_source,personal_episodes=episodes,episode_base_result=episode_base_result)
    by_player={p['playerObjectId']:p for p in result['playerObservations']}
    mapping=player_identity if player_identity is not None else json.loads(Path(identity_path).read_text(encoding='utf-8'))
    mapping={p['replayObjectId']:p['cacheObjectId'] for p in mapping['players']}
    prepared=[];coverage_counts=Counter()
    for player in players:
        pid=mapping[player['objectId']]
        observed={r['metricId']:r for r in by_player[pid]['observations']}
        rows=[]
        for spec in specs:
            if spec['characterCode']!=player['characterCode']:continue
            rows.append(observed.get(spec['metricId'],{**spec,'status':'unavailable-no-metric-result',
                'reason':'calculator emitted no result; no attempt count inferred',
                'attemptCount':None,'hitCount':None,'hitRate':None,'fallbackUsed':False}))
        projected=project_player_episodes(rows,episodes[pid])
        coverage_counts.update(c['status'] for c in projected['coverage'])
        operation={
            'status':'requested-metrics-with-explicit-timeline-coverage',
            'metrics':[public_metric(r) for r in rows],
            'episodes':public_episode_metrics(projected),'coverage':projected['coverage'],
            'scope':'exact personal PvP episodes; casts within episode plus independently verified earlier casts hitting it; existing cast episode retained',
            'fallbackUsed':False}
        from .skill_opening_cast_scope import skill_usage_with_openings
        operation['skillUsage'],usage_by_episode=skill_usage_with_openings(player,episodes[pid],rows)
        for episode in operation['episodes']:episode['skillUsage']=usage_by_episode[episode['episodeId']]
        if 'elenaFreezeCounts' in by_player[pid]:
            if player['characterCode']!=50:raise ValueError('Elena freeze counts attached to another character')
            from .skill_freeze_count_projection import project_elena_freeze_counts
            operation['elenaFreezeCounts']=project_elena_freeze_counts(by_player[pid]['elenaFreezeCounts'],episodes[pid])
        prepared.append((player,operation))
    # Bind only after every identity, episode and metric has passed validation.
    for player,operation in prepared:player['skillOperation']['requestedMetrics']=operation
    return {'status':'requested-metrics-with-explicit-timeline-coverage',
            'playerCount':len(prepared),'coverageCounts':dict(coverage_counts),
            'implementationSha256':result['implementationSha256'],'fallbackUsed':False}
