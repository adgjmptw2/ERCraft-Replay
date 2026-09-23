"""Recalculate all requested skills from small anonymous facts, with no network."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parent.parent
if not __package__:
    sys.path.insert(0,str(ROOT))
    __package__='decoder'
sys.path.insert(0,str(ROOT/'decoder'))
sys.path.insert(0,str(ROOT/'acquire'))
from .skill_scope_evidence_cache import read_evidence_cache
from .projectile_hit_catalog import build_projectile_skill_catalog,calculate_projectile_hit_rates
from .requested_skill_scope import calculate_scope_metrics,validate_scope_result,manifest,active_manifest,implementation_fingerprint,POLICY_ID,runtime_source_group
from .requested_skill_hit_rates import CLIENT_VERSION,GAME_DB_SHA256
from .skill_context_casts import context_cast_facts
from .skill_action_stage_evidence import load_exact_skill_ids
from .skill_game_data_contract import revision_identity,archive_for_revision


def missing_detail_source(cache,rows):
    """Load matching retained evidence only for a detail provider that needs it."""
    needs_center=any(r.get('skillGroup') in (1028200,1028510) and
        r.get('phaseMetrics',{}).get('center',{}).get('unknownUseEvidence') for r in rows)
    needs_jan=any(r.get('skillGroup')==1035300 and (r.get('janReinforceBaselineRequests') or r.get('janRopeAbsenceRequests')) for r in rows)
    if not (needs_center or needs_jan):return None
    from .corpus_runtime_source import select_retained_decode
    return select_retained_decode(ROOT/'local-corpus/replays'/cache['matchKey'],
                                  cache['matchKey'],cache['clientVersion'])


def recalculate(cache,catalog,tables,metric_specs=None,*,damage_index=None,direct_heals=None,
                include_player_observations=False,retained_source=None,resolve_retained_details=True,
                personal_episodes=None,episode_base_result=None,_preparation=None,_output_players=None):
    if personal_episodes is not None:
        from .skill_opening_cast_scope import recalculate_opening_scope
        from .requested_skill_scope import verified_implementation_scope
        if not include_player_observations:raise ValueError('personal episode calculation requires player observations')
        with verified_implementation_scope():
            return recalculate_opening_scope(cache,catalog,tables,active_manifest() if metric_specs is None else metric_specs,
                personal_episodes,recalculate,dict(damage_index=damage_index,direct_heals=direct_heals,
                    include_player_observations=True,retained_source=retained_source,
                    resolve_retained_details=resolve_retained_details,_preparation={}),base_result=episode_base_result)
    if episode_base_result is not None:raise ValueError('episode baseline requires explicit personal episodes')
    source_fingerprint=implementation_fingerprint()
    facts=cache['facts']
    if any(s.get('sourceProofSha256')!=cache['matchKey'] or s.get('clientVersion')!=cache['clientVersion']
           for s in facts.get('fioraStateInventory') or []):
        raise ValueError('Fiora state inventory belongs to a different source/version')
    if any(x.get('sourceProofSha256')!=cache['matchKey'] or x.get('clientVersion')!=cache['clientVersion'] or x.get('gameDataSha256')!=cache['gameDataSha256'] for x in [*(facts.get('janStateInventory') or []),*(facts.get('janRopeInventory') or [])]):
        raise ValueError('Jan state inventory belongs to a different source/version/gameDb')
    identity=revision_identity(cache['clientVersion'],cache['gameDataSha256'])
    preparation=_preparation if _preparation is not None else {}
    inputs=(facts.get('skillContexts'),cache['players'],catalog,tables['Skill'],facts['starts'],facts['finishes'])
    prepared=preparation.get('context')
    if prepared is None or any(a is not b for a,b in zip(prepared[0],inputs)):
        extra_starts,extra_finishes=context_cast_facts(inputs[0],inputs[1],catalog,inputs[3],load_exact_skill_ids())
        starts=[*facts['starts'],*extra_starts] if extra_starts else facts['starts']
        finishes=[*facts['finishes'],*extra_finishes] if extra_finishes else facts['finishes']
        prepared=(inputs,extra_starts,extra_finishes,starts,finishes)
        preparation['context']=prepared
    _,extra_starts,extra_finishes,starts,finishes=prepared
    if extra_starts or extra_finishes:
        facts={**facts,'starts':starts,'finishes':finishes}
    if direct_heals is None:direct_heals=facts.get('directHeals')
    damages=facts['damages']; damage_provenance=None
    if damage_index is not None:
        from .skill_index_damage_adapter import indexed_damage_facts
        damages,damage_provenance=indexed_damage_facts(cache,damage_index)
        if direct_heals is None:
            from .skill_index_heal_adapter import indexed_player_heals
            direct_heals=indexed_player_heals(cache,damage_index)
    teams={int(k):v for k,v in cache['teams'].items()}
    intervals={int(k):v for k,v in cache['intervals'].items()}
    from .skill_match_end_scope import match_end_combat_intervals
    end_policy=json.loads((ROOT/'data/user-hit-rate-scope-20260912.json').read_bytes()).get('postMatchCastPolicy',{})
    if end_policy.get('enabled') is True:
        intervals,_=match_end_combat_intervals(intervals,facts['starts'],facts.get('gameTerminals'),facts.get('gaps'))
    owners={s['projectileObjectId']:s['ownerPlayerObjectId'] for s in facts['spawns']}
    specs=active_manifest() if metric_specs is None else metric_specs
    if (retained_source is not None and any(p['characterCode']==3 for p in cache['players'])
            and any(s['skillGroup']==1003200 for s in specs)):
        from .corpus_pose_evidence import retained_pose_events
        poses,pose_proof=retained_pose_events(retained_source,cache,include_motion=True)
        facts={**facts,'poseEvents':poses,'fioraMotionInputs':pose_proof['motionInputs']}
    if facts.get('fioraMotionInputs') is not None and facts['fioraMotionInputs']['matchKey']!=cache['matchKey']:
        raise ValueError('Fiora motion inputs belong to a different source')
    if (retained_source is not None and any(p['characterCode']==26 for p in cache['players'])
            and any(s['skillGroup']==1026400 and s['mode'] in {'inner-hit','outer-hit'} for s in specs)):
        from .corpus_barbara_region_inputs import retained_barbara_region_inputs
        facts={**facts,'barbaraRegionInputs':retained_barbara_region_inputs(retained_source,cache,tables)}
    if facts.get('barbaraRegionInputs') is not None and facts['barbaraRegionInputs']['matchKey']!=cache['matchKey']:
        raise ValueError('Barbara region inputs belong to a different source')
    if (retained_source is not None and any(p['characterCode']==44 for p in cache['players'])
            and any(s['skillGroup'] in {1044510,1044520,1044530,1044540} for s in specs)):
        from .corpus_echion_primary_inputs import retained_echion_primary_inputs
        facts={**facts,'echionPrimaryInputs':retained_echion_primary_inputs(retained_source,cache,tables)}
    from .projectile_hit_catalog import ProjectilePreparation,DEFAULT_LINK_WINDOW_TICKS
    projectile_preparation=preparation.get('projectile')
    if projectile_preparation is None and _preparation is not None:
        projectile_preparation=ProjectilePreparation(catalog,facts['starts'],facts['spawns'],facts['collisions'],teams,DEFAULT_LINK_WINDOW_TICKS)
        preparation['projectile']=projectile_preparation
    runtime=calculate_projectile_hit_rates(catalog,facts['starts'],facts['spawns'],facts['collisions'],teams,intervals,
        _preparation=projectile_preparation,exact_skill_damage_events=[],output_skill_groups={runtime_source_group(s) for s in specs},
        scope_evidence='derived-own-combat-interval-containing-exact-enemy-player-packet')
    metrics=calculate_scope_metrics(catalog,runtime,facts['starts'],facts['spawns'],facts['collisions'],teams,intervals,
        client_version=cache['clientVersion'],game_db_sha256=cache['gameDataSha256'],metric_specs=metric_specs,_output_players=_output_players,
        wall_inputs={'janRopeInventory':facts.get('janRopeInventory'),'janStateInventory':facts.get('janStateInventory'),'janStateIdentity':dict(sourceSha256=cache['matchKey'],clientVersion=cache['clientVersion'],gameDataSha256=cache['gameDataSha256']),'finishes':facts['finishes'],'states':facts['states'],'state_scripts':facts['stateScripts'],'skill_rows':tables['Skill'],
                     'state_rows':tables['CharacterState'],'state_groups':tables['CharacterStateGroup'],
                     'deaths':facts.get('deaths',[]),'movement':facts.get('movement'),'gameTerminals':facts.get('gameTerminals'),'trapEvents':facts.get('trapEvents'),
                     'rotationEvents':facts.get('rotationEvents'),'evasionEvents':facts.get('evasionEvents'),'skillContexts':facts.get('skillContexts'),'poseEvents':facts.get('poseEvents'),
                     'fioraMotionInputs':facts.get('fioraMotionInputs'),'fioraStateInventory':facts.get('fioraStateInventory')},
        damages=damages,effect_rows=tables['EffectAndSound'],projectile_owners=owners,
        projectile_terminals=facts['terminals'],raw_actions=facts['actions'],
        direct_heals=direct_heals,
        _preparation=preparation,
        summon_inputs={'summons':facts['summons'],'objects':facts.get('objects'),'gaps':facts.get('gaps'),'allProjectileSpawns':facts.get('allProjectileSpawns'),
                       'players':cache['players'],'nonPlayerSkillStarts':facts.get('nonPlayerSkillStarts'),
                       'projectileMovement':facts.get('projectileMovement'),
                       'suaCenterGeometry':facts.get('suaCenterGeometry'),
                       'suaStateInventory':facts.get('suaStateInventory'),
                       'adelaStateInventory':facts.get('adelaStateInventory'),
                       'echionPrimaryInputs':facts.get('echionPrimaryInputs'),
                       'barbaraRegionInputs':facts.get('barbaraRegionInputs'),
                       'projectile_rows':tables.get('ProjectileSetting',[]),
                       'summon_rows':tables.get('SummonObject',[])})
    rows=[r for group in metrics.values() for r in group]
    for row in rows:validate_scope_result(row)
    output={'matchKey':cache['matchKey'],'policyId':POLICY_ID,'clientVersion':cache['clientVersion'],'gameDataSha256':cache['gameDataSha256'],
            'playerMatchCount':len(cache['players']),'observedCharacterCodes':sorted({p['characterCode'] for p in cache['players']}),
            'implementationSha256':source_fingerprint,'observations':rows,'rawReplayRetained':False,'fallbackUsed':False,
            'inputMode':'index-damage-and-cache-context' if damage_index else 'de-identified-local-evidence-cache',
            'damageIndexEvidence':damage_provenance,'evidenceCapabilities':cache['capabilities']}
    output['gameDataRuleIdentity']=identity
    output['requestedMetricIds']=sorted(s['metricId'] for s in specs)
    output['requestedScopeSha256']=hashlib.sha256(json.dumps(output['requestedMetricIds'],separators=(',',':')).encode()).hexdigest()
    output['explicitAlternateStartCount']=len(extra_starts)
    output['explicitAlternateFinishCount']=len(extra_finishes)
    if include_player_observations:
        output['playerObservations']=[{
            'playerObjectId':p['objectId'],'characterCode':p['characterCode'],
            'observations':metrics.get(p['objectId'],metrics.get(str(p['objectId']),[]))}
            for p in cache['players']]
        output['playerIdentityScope']='input-evidence-cache-object-ids'
        if any(s['characterCode']==50 for s in specs):
            from .skill_elena_freeze_events import elena_freeze_events
            for p in output['playerObservations']:
                if p['characterCode']==50:
                    pid=p['playerObjectId']
                    p['elenaFreezeCounts']=elena_freeze_events(pid,teams,intervals.get(pid,[]),facts)
    if retained_source is None and resolve_retained_details:
        retained_source=missing_detail_source(cache,rows)
        if retained_source is not None:
            output['retainedDetailSourceAutomaticallySelected']=True
    if retained_source is not None:
        from .corpus_sua_center_geometry import retained_sua_center_geometry
        uses=[]
        for row in rows:
            if row.get('skillGroup') not in (1028200,1028510):continue
            unknown={tuple(e['cast']['wireOrder']) for e in row.get('phaseMetrics',{}).get('center',{}).get('unknownUseEvidence',[])}
            uses.extend(u for u in row.get('executionEvidenceByAttempt',[]) if tuple(u['start']['wireOrder']) in unknown)
        geometry,proof=retained_sua_center_geometry(retained_source,cache,uses)
        proof['beforeCenterTotals']={k:sum(row.get('phaseMetrics',{}).get('center',{}).get(k,0)
            for row in rows if row.get('skillGroup') in (1028200,1028510))
            for k in ('attemptCount','hitCount','unresolvedCombatCastCount')}
        from .corpus_state_inventory import unresolved_sua_state_inventory
        proof['stateInventory']=unresolved_sua_state_inventory(retained_source,output['observations'],tables,
                                                              client_version=cache['clientVersion'])
        from .skill_adela_state_admission import unresolved_adela_state_inventory
        adela_inventory=unresolved_adela_state_inventory(retained_source,output['observations'],tables,
                                                        client_version=cache['clientVersion'])
        from .skill_fiora_state_admission import unresolved_fiora_state_inventory
        fiora_inventory=unresolved_fiora_state_inventory(retained_source,output['observations'],tables,
                                                        client_version=cache['clientVersion'])
        from .skill_jan_state_admission import unresolved_jan_state_inventory
        jan_inventory=unresolved_jan_state_inventory(retained_source,output['observations'],tables,client_version=cache['clientVersion'],source_sha256=cache['matchKey'],game_db_sha256=cache['gameDataSha256'])
        from .skill_jan_rope_admission import unresolved_jan_rope_inventory
        jan_rope_inventory=unresolved_jan_rope_inventory(retained_source,output['observations'],tables,client_version=cache['clientVersion'],source_sha256=cache['matchKey'],game_db_sha256=cache['gameDataSha256'])
        if geometry or proof['stateInventory'] or adela_inventory or fiora_inventory or jan_inventory or jan_rope_inventory:
            # Context casts are derived afresh at entry. Feed original wire
            # starts/finishes into the supplemental pass, not this pass's
            # already expanded arrays (which doubled enhanced attacks).
            augmented={**cache,'facts':{**facts,'starts':cache['facts']['starts'],
                                      'finishes':cache['facts']['finishes'],
                                      'suaCenterGeometry':[*facts.get('suaCenterGeometry',[]),*geometry],
                                      'suaStateInventory':proof['stateInventory'],
                                      'adelaStateInventory':adela_inventory,'fioraStateInventory':fiora_inventory,'janStateInventory':jan_inventory,'janRopeInventory':jan_rope_inventory}}
            # Supplemental providers are character-specific. Keep every input
            # event for ownership/competitor checks, but evaluate only the
            # characters whose state/geometry evidence has actually changed.
            affected=set()
            if geometry or proof['stateInventory']:affected.add(28)
            if adela_inventory:affected.add(24)
            if fiora_inventory:affected.add(3)
            if jan_inventory or jan_rope_inventory:affected.add(35)
            supplemental_specs=[s for s in specs if s['characterCode'] in affected]
            supplemental=recalculate(augmented,catalog,tables,supplemental_specs,
                damage_index=damage_index,direct_heals=direct_heals,include_player_observations=True,
                resolve_retained_details=False)
            replacements={(p['playerObjectId'],r['metricId']):r
                for p in supplemental['playerObservations'] for r in p['observations']}
            originals={(int(player),r['metricId']):r for player,rr in metrics.items() for r in rr
                if r['characterCode'] in affected}
            if replacements.keys()!=originals.keys():
                raise ValueError('supplemental calculation changed player/metric membership')
            by_row_id={id(old):replacements[key] for key,old in originals.items()}
            output['observations']=[by_row_id.get(id(r),r) for r in output['observations']]
            for p in output.get('playerObservations',[]):
                p['observations']=[by_row_id.get(id(r),r) for r in p['observations']]
            output['supplementalCalculation']=dict(characterCodes=sorted(affected),
                requestedMetricCount=len(supplemental_specs),recomputedPlayerMetricCount=len(replacements),
                retainedPlayerMetricCount=len(rows)-len(replacements),fullMatchRecalculated=False,
                allOwnershipAndCompetingEventsRetained=True)
        from .skill_region_blockers import attach_region_blockers
        attach_region_blockers(output,proof)
        output['retainedGeometryEvidence']=proof
        output['retainedAdelaStateInventory']=adela_inventory
        output['retainedFioraStateInventory']=fiora_inventory
        output['retainedJanStateInventory']=jan_inventory
        output['retainedJanRopeInventory']=jan_rope_inventory
    return output


def main():
    # Keep offline retained-cache workers independent of the acquisition-only
    # brotli/client dependency.  The CLI still imports the audit helpers here.
    from audit_requested_skills_once import summarize,write_json,audit_scope
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('caches',nargs='+',type=Path)
    parser.add_argument('--out-dir',required=True,type=Path)
    parser.add_argument('--per-player',action='store_true',
                        help='retain private cache-local player identities and per-player metric results')
    args=parser.parse_args()
    if args.out_dir.exists() and any(args.out_dir.iterdir()):raise ValueError('choose a new empty result directory')
    args.out_dir.mkdir(parents=True,exist_ok=True)
    first_cache=read_evidence_cache(args.caches[0])
    identity=revision_identity(first_cache['clientVersion'],first_cache['gameDataSha256'])
    game_db=archive_for_revision(first_cache['clientVersion'],first_cache['gameDataSha256'])
    catalog=build_projectile_skill_catalog(game_db)
    with zipfile.ZipFile(game_db) as z:
        tables={name:json.loads(z.read(name+'.json')) for name in ['Skill','CharacterState','CharacterStateGroup','EffectAndSound','SummonObject','ProjectileSetting']}
    matches=[]
    for i,path in enumerate(args.caches,1):
        cache=first_cache if i==1 else read_evidence_cache(path)
        if (cache['clientVersion'],cache['gameDataSha256'])!=(identity['clientVersion'],identity['gameDataSha256']):
            raise ValueError('different exact gameDb revisions require separate calculation runs')
        retained_source=None
        if any(p['characterCode'] in (3,24,26,28,44) for p in cache['players']):
            from .corpus_runtime_source import CorpusRuntimeSource
            paths=list((ROOT/'local-corpus/replays'/cache['matchKey']/'decodes').glob('*.sqlite3'))
            if len(paths)==1:retained_source=CorpusRuntimeSource(paths[0],cache['matchKey'],cache['clientVersion'])
        match=recalculate(cache,catalog,tables,retained_source=retained_source,
                          include_player_observations=args.per_player)
        if any(m['matchKey']==match['matchKey'] for m in matches):raise ValueError('duplicate cache match')
        matches.append(match)
        write_json(args.out_dir/f'match-{i:03d}.json',match)
        if i%5==0 or i==len(args.caches):
            print(json.dumps({'phase':'offline-recalculation','completed':i,'total':len(args.caches),'networkCalls':0}),flush=True)
    specs=manifest()
    report={'format':'er-requested-skill-metrics-trial.v1','policyId':POLICY_ID,'clientVersion':identity['clientVersion'],
        'gameDataSha256':identity['gameDataSha256'],'gameDataRuleIdentity':identity,'matchCount':len(matches),'playerMatchCount':sum(m['playerMatchCount'] for m in matches),
        'excludedMatchCount':0,'requestedCharacterCount':90,'registeredCharacterCodes':sorted({s['characterCode'] for s in specs}),
        'observedRequestedCharacterCodes':sorted({c for m in matches for c in m['observedCharacterCodes'] if c<=90}),
        'scope':audit_scope(),'metrics':summarize(matches,policy_id=POLICY_ID,specs=specs),
        'implementationSha256':implementation_fingerprint(),'implementationComplete':False,'rawReplayRetained':False,'fallbackUsed':False,
        'networkCalls':0,'inputMode':'de-identified-local-evidence-cache',
        'evidenceCapabilityMatchCounts':dict(Counter(cap for m in matches for cap in m['evidenceCapabilities']))}
    write_json(args.out_dir/'summary.json',report)
    print(json.dumps({'matchCount':len(matches),'calculableMetrics':sum(r['status']=='calculable-observed' for r in report['metrics']),
                      'networkCalls':0,'result':str(args.out_dir/'summary.json')}))


if __name__=='__main__':main()
