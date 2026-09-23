"""Bounded first-19 skill trial using one consumed official session.

Only anonymous counts and replay hashes are retained. The existing population
database and UI fixture are never written. Run with a transient session restored
by the existing CurrentUser DPAPI procedure; it is consumed immediately.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "decoder"))
sys.path.insert(0, str(ROOT / "acquire"))

from requested_skill_hit_rates import (
    POLICY_ID, CLIENT_VERSION, GAME_DB_SHA256, manifest, validate_requested_result,
)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def summarize_detonation_stages(rows):
    if not rows or any('detonationStageCounts' not in r for r in rows):return None
    output={}
    for stage in ['1','2','3','4']:
        parts=[r['detonationStageCounts'][stage] for r in rows]
        attempts=sum(r['attemptCount'] for r in parts);hits=sum(r['hitCount'] for r in parts)
        output[stage]={'attemptCount':attempts,'hitCount':hits,'hitRate':round(hits/attempts,6) if attempts else None,
            'distinctEnemyTargetsSummedAcrossAttempts':sum(r['distinctEnemyTargetsSummedAcrossAttempts'] for r in parts)}
    return output


def summarize_action_stages(rows):
    if not rows or any('actionStageCounts' not in r for r in rows):return None
    keys=set(rows[0]['actionStageCounts'])
    if any(set(r['actionStageCounts'])!=keys for r in rows):return None
    fields=('observedTargetEventCount','hitCastCount',
            'distinctEnemyTargetsSummedAcrossAttempts','recordedHitActionTickCount')
    return {stage:{field:sum(r['actionStageCounts'][stage][field] for r in rows)
                   for field in fields} for stage in sorted(keys)}


def summarize(matches, *, policy_id=POLICY_ID, specs=None):
    specs = manifest() if specs is None else specs
    if any(m.get('calculationStatus') == 'not-run-evidence-only' for m in matches):
        raise ValueError('evidence-only matches require offline calculation before metric summarization')
    if len({m['matchKey'] for m in matches}) != len(matches):
        raise ValueError('duplicate replay cannot enter trial population')
    from skill_game_data_contract import revision_identity
    if any(m['policyId'] != policy_id for m in matches):
        raise ValueError('trial policy, version or gameDb hash mismatch')
    for match in matches:revision_identity(match['clientVersion'],match['gameDataSha256'])
    if len({(m['clientVersion'],m['gameDataSha256']) for m in matches})>1:
        raise ValueError('different exact gameDb revisions cannot be pooled')
    if policy_id != POLICY_ID and (any(not m.get('implementationSha256') for m in matches)
                                  or len({m['implementationSha256'] for m in matches})>1):
        raise ValueError('all-90 trial implementation revisions cannot be pooled')
    results = []
    for spec in specs:
        rows = [r for m in matches for r in m['observations'] if r['metricId'] == spec['metricId']]
        calculable = [r for r in rows if r['status'] == 'calculable-observed']
        denominator = sum(r['attemptCount'] for r in calculable)
        hit = sum(r['hitCount'] for r in calculable)
        include_multi=spec.get('reportMultiTarget') is not False
        multi = sum(r['multiTargetAttemptCount'] for r in calculable) if include_multi else None
        team_cohort=bool(spec.get('targetCohort'))
        target_field='distinctTargetsSummedAcrossAttempts' if team_cohort else 'distinctEnemyTargetsSummedAcrossAttempts'
        contact_field='deduplicatedTargetContactEventCount' if team_cohort else 'deduplicatedEnemyContactEventCount'
        target_sum = sum(r[target_field] for r in calculable) if include_multi else None
        results.append({**spec,
            'status': 'calculable-observed' if denominator else 'unmapped-stage' if spec['skillGroup'] is None
                      else 'unresolved-evidence' if rows else 'no-observed-cast',
            'observedPlayerMatchCount': len(rows), 'calculablePlayerMatchCount': len(calculable),
            'allCastCount': sum(r['allCastCount'] for r in rows),
            'combatCastCount': sum(r['combatCastCount'] for r in rows),
            'attemptCount': denominator if denominator else None,
            'hitCount': hit if denominator else None,
            'hitRate': round(hit / denominator, 6) if denominator else None,
            'multiTargetAttemptCount': multi if denominator else None,
            'multiTargetAttemptRate': round(multi / denominator, 6) if denominator and include_multi else None,
            target_field: target_sum if denominator else None,
            ('meanDistinctTargetsPerAttempt' if team_cohort else 'meanDistinctEnemyTargetsPerAttempt'): round(target_sum / denominator, 6) if denominator and include_multi else None,
            contact_field: sum(r[contact_field] for r in calculable) if denominator and include_multi else None,
            **({'exactDamagePacketCount': sum(r['exactDamagePacketCount'] for r in calculable)
                if calculable and all(type(r.get('exactDamagePacketCount')) is int for r in calculable) else None}
               if policy_id != POLICY_ID else {}),
            'methods': sorted({r['method'] for r in calculable}),
            'unavailableReasons': dict(Counter(r['reason'] for r in rows if r.get('reason'))),
            'actionTargetReasons': dict(Counter(r['actionTargetReason'] for r in rows if r.get('actionTargetReason'))),
            'effectDamageReasons': dict(Counter(r['effectDamageReason'] for r in rows if r.get('effectDamageReason'))),
            **({'projectileLifetimeReasons': dict(Counter(r['projectileLifetimeReason'] for r in rows if r.get('projectileLifetimeReason'))),
                'actionEvidenceWithoutCastStartPlayerMatchCount':sum(r.get('actionStageEvidence',{}).get('recordedPlayerCastStartCount')==0 for r in rows),
                'ownedFollowupGraphVerifiedPlayerMatchCount':sum(r.get('ownedFollowupEvidence',{}).get('status')=='linked' for r in rows),
                'verifiedLinkedChildCastCount':sum(r['ownedFollowupEvidence']['childCastCount'] for r in rows if r.get('ownedFollowupEvidence',{}).get('status')=='linked'),
                'verifiedParentUseCount':sum(r['ownedFollowupEvidence']['parentUseCount'] for r in rows if r.get('ownedFollowupEvidence',{}).get('status')=='linked'),
                'ownedFollowupGraphReasons':dict(Counter(r['ownedFollowupEvidence']['reason'] for r in rows if r.get('ownedFollowupEvidence',{}).get('reason'))),
                'observedActionPacketCount':sum(r.get('actionStageEvidence',{}).get('actionPacketCount',0) for r in rows),
                'observedNonPlayerSkillStartPacketCount':sum(r['actionStageEvidence']['nonPlayerSkillStartPacketCount'] for r in rows if 'actionStageEvidence' in r)
                    if any('actionStageEvidence' in r for r in rows) and all(type(r['actionStageEvidence']['nonPlayerSkillStartPacketCount']) is int for r in rows if 'actionStageEvidence' in r) else None,
                **{key: sum(r[key] for r in calculable)
                   if calculable and all(type(r.get(key)) is int for r in calculable) else None
                   for key in ['actualProjectileCount','enemyHitProjectileCount','projectileContactEventCount',
                               'actualEmissionCount','enemyHitEmissionCount']}}
               if policy_id != POLICY_ID else {}),
            'fallbackUsed': False,
            **({'screenBeamCounts':{
                **{key:sum(r.get('screenBeamCounts',{}).get(key,0) for r in rows) for key in [
                    'observedQCombatUses','screenTriggeredCombatUses','verifiedSelectedCombatUses',
                    'unresolvedSelectedCombatUses','selectedObservedCombatUses']},
                'unclassifiedSampleCombatUses':sum(r['combatCastCount'] for r in rows if 'screenBeamCounts' not in r)},
                'denominatorMeaning':'verified screen-triggered Q uses' if spec['mode']=='screen-hit' else 'verified Q uses including gameplay cancellations',
                'incompleteUsesCountedAsMisses':False}
               if spec['skillGroup']==1062200 and policy_id != POLICY_ID else {}),
            **({'conditionCounts':{
                **{key:sum(r.get('conditionCounts',{}).get(key,0) for r in rows) for key in [
                    'observedAllECombatUses','observedWLinkedCombatUses','verifiedWLinkedCombatUses',
                    'unresolvedWLinkedCombatUses','movementOnlyCombatUses','unresolvedConditionCombatUses']},
                'unclassifiedSampleCombatUses':sum(r['combatCastCount'] for r in rows if 'conditionCounts' not in r)},
                'actualCCApplicationEventCount':sum(r.get('actualCCApplicationEventCount',0) for r in calculable),
                'movementOnlyUsesCountedAsMisses':False,'incompleteUsesCountedAsMisses':False,
                'denominatorMeaning':'verified explicitly W-linked E uses; nonlinked movement and incomplete uses are separately reported'}
               if spec['skillGroup']==1083400 and policy_id != POLICY_ID else {}),
            **({'perUseCompletenessTracked':True,
                'observedCombatCastCount':sum(r['combatCastCount'] for r in rows),
                'verifiedCombatCastCount':denominator,
                'unresolvedCombatCastCount':sum(r['combatCastCount'] for r in rows)-denominator,
                'incompleteUsesCountedAsMisses':False,
                'unresolvedCastReasons':dict(Counter({reason:sum(r.get('unresolvedCastReasons',{}).get(reason,0) for r in rows)
                    for reason in {reason for r in rows for reason in r.get('unresolvedCastReasons',{})}}))}
               if spec['skillGroup'] in {1083300,1042500,1047200,1083500,1046400,1046410} and policy_id != POLICY_ID else {}),
            **({'actionStageCounts':summarize_action_stages(calculable),
                'actualCCApplicationEventCount':sum(r['actualCCApplicationEventCount'] for r in calculable)
                    if calculable and all('actualCCApplicationEventCount' in r for r in calculable) else None,
                'denominatorMeaning':'all selected skill uses in observed combat; stage outcomes are not pre-cast empowerment eligibility'}
               if spec['skillGroup'] in {1034300,1090500} and policy_id != POLICY_ID else {}),
            **({'detonationStageCounts':summarize_detonation_stages(calculable),
                'usesWithoutDetonationCount':sum(r['usesWithoutDetonationCount'] for r in calculable)
                    if calculable and all('usesWithoutDetonationCount' in r for r in calculable) else None}
               if spec['skillGroup']==1043500 else {}),
        })
    return results


def main():
    # Keep summarize()/offline retained-cache imports independent of the
    # acquisition-only brotli/client stack.  These imports are needed only by
    # the live acquisition CLI.
    from analyze_replay_once import load_service_account
    from get_replay import acquire_replay_with_state, in_memory_session_state
    from probe_projectile_spawn_prefixes import build_candidate_prefix_probe, validate_candidate_prefix_probe
    from projectile_prefix_consensus import build_projectile_prefix_consensus, validate_projectile_prefix_consensus
    from audit_projectile_runtime_replay import build_runtime_audit, validate_runtime_audit
    parser = argparse.ArgumentParser()
    parser.add_argument('game_ids', nargs='+', type=int)
    parser.add_argument('--out-dir', required=True, type=Path)
    parser.add_argument('--scope-probe', action='store_true')
    parser.add_argument('--full-scope', action='store_true')
    parser.add_argument('--collect-only',action='store_true')
    parser.add_argument('--target-character-codes', nargs='+', type=int,
                        help='full-scope decode still retains the complete evidence cache, but calculate only these character codes')
    parser.add_argument('--target-metric-ids', nargs='+',
                        help='full-scope decode still retains the complete evidence cache, but calculate only these exact metric ids')
    parser.add_argument('--prefix-consensus',type=Path)
    args = parser.parse_args()
    if args.collect_only and not args.full_scope:raise ValueError('collect-only requires full-scope decoding')
    policy_id, specs, validate_result = POLICY_ID, manifest(), validate_requested_result
    target_codes = None
    target_metric_ids = None
    if args.target_character_codes:
        if not args.full_scope:
            raise ValueError('--target-character-codes requires --full-scope')
        target_codes = sorted(set(args.target_character_codes))
        if any(code < 1 or code > 90 for code in target_codes):
            raise ValueError('target character codes must be between 1 and 90')
    if args.target_metric_ids:
        if not args.full_scope:
            raise ValueError('--target-metric-ids requires --full-scope')
        target_metric_ids = sorted(set(args.target_metric_ids))
        if any(not value.startswith('user-request-20260905-all90-v1:') for value in target_metric_ids):
            raise ValueError('target metric ids must belong to the adopted all-90 policy')
    if args.full_scope:
        from requested_skill_scope import POLICY_ID as FULL_POLICY, manifest as full_manifest, validate_scope_result, implementation_fingerprint
        policy_id, specs, validate_result = FULL_POLICY, full_manifest(), validate_scope_result
        implementation_sha256 = implementation_fingerprint()
        if target_codes:
            specs = [spec for spec in specs if spec['characterCode'] in target_codes]
        if target_metric_ids:
            known_metric_ids = {spec['metricId'] for spec in specs}
            unknown_metric_ids = [value for value in target_metric_ids if value not in known_metric_ids]
            if unknown_metric_ids:
                raise ValueError(f'unknown target metric ids: {unknown_metric_ids}')
            specs = [spec for spec in specs if spec['metricId'] in target_metric_ids]
    if len(args.game_ids) > 30 or len(set(args.game_ids)) != len(args.game_ids) or any(g <= 0 for g in args.game_ids):
        raise ValueError('expected at most 30 distinct candidate games')
    args.out_dir.mkdir(parents=True, exist_ok=True)
    if list(args.out_dir.glob('match-*.json')):
        raise ValueError('choose an empty trial output directory')
    game_data = ROOT / 'acquire/gamedata-20260903071248.zip'
    if hashlib.sha256(game_data.read_bytes()).hexdigest() != GAME_DB_SHA256:
        raise ValueError('trial gameDb hash mismatch')
    fixed_handle = load_service_account()
    matches, probes, failures = [], [], []
    with in_memory_session_state() as state:
        selected = list(args.game_ids)
        print(json.dumps({'phase': 'bounded-candidates', 'matchCount': len(selected)}), flush=True)
        if len(selected) < 3 and args.prefix_consensus is None:
            raise ValueError('at least three matches required for fresh prefix consensus')
        consensus = None
        if args.prefix_consensus:
            consensus=json.loads(args.prefix_consensus.read_text(encoding='utf-8'))
            proof=json.loads(args.prefix_consensus.with_name('summary.json').read_text(encoding='utf-8'))
            if proof.get('clientVersion')!=CLIENT_VERSION or proof.get('gameDataSha256')!=GAME_DB_SHA256:
                raise ValueError('saved prefix evidence is not from the exact replay/gameDb contract')
            validate_projectile_prefix_consensus(consensus)
            versions=[v for v in consensus['versions'] if v['clientVersion']==CLIENT_VERSION]
            if len(versions)!=1:raise ValueError('saved prefix version is ambiguous')
            version=versions[0]
            write_json(args.out_dir/'prefix-consensus.json',consensus)
            print(json.dumps({'phase':'prefix-reused','clientVersion':CLIENT_VERSION,'additionalDownloads':0}),flush=True)
        for i, game in enumerate([] if consensus is not None else selected[:10], 1):
            with acquire_replay_with_state(game, fixed_handle, state) as replay:
                path = Path(replay['path'])
                probe = build_candidate_prefix_probe(path, game_data)
                validate_candidate_prefix_probe(probe)
                if probe['clientVersion'] != CLIENT_VERSION:
                    raise ValueError('selected replay version mismatch')
                probes.append({'gameId': game, 'clientVersion': CLIENT_VERSION,
                               'projectilePrefixCandidateProbe': probe})
            if path.exists():
                raise ValueError('raw replay survived acquisition context')
            print(json.dumps({'phase': 'prefix', 'completed': i, 'rawRemoved': True}), flush=True)
            consensus = build_projectile_prefix_consensus(probes)
            validate_projectile_prefix_consensus(consensus)
            write_json(args.out_dir / 'prefix-consensus.json', consensus)
            version = next(v for v in consensus['versions'] if v['clientVersion'] == CLIENT_VERSION)
            if version['status'] == 'candidate-layout-consensus-ready-for-explicit-version-promotion':
                break
        if version['status'] != 'candidate-layout-consensus-ready-for-explicit-version-promotion':
            raise ValueError('fresh exact prefix consensus is not ready')
        layout = {int(k): v for k, v in version['candidateLayout'].items()}
        del probes
        for i, game in enumerate(selected, 1):
            if args.full_scope and implementation_fingerprint()!=implementation_sha256:
                raise ValueError('implementation changed during batch; start a new revision instead of mixing counts')
            try:
                with acquire_replay_with_state(game, fixed_handle, state) as replay:
                    path = Path(replay['path'])
                    from decoder.corpus_runtime_source import CorpusRuntimeSource
                    audit = build_runtime_audit(path, game_data, verified_prefix_layout=layout,
                        full_decode_path=CorpusRuntimeSource.path_from_acquisition(replay),
                        prefix_layout_authority='multi-match-exact-prefix-consensus-v1',
                        include_requested_skill_metrics=not args.full_scope,
                        include_full_requested_skill_metrics=args.full_scope,
                        skill_scope_evidence_out_path=args.out_dir/f'evidence-{i:03d}.json.gz' if args.full_scope else None,
                        skill_scope_identity_out_path=args.out_dir/f'evidence-{i:03d}.player-map.private.json' if args.full_scope else None,
                        calculate_full_requested_skill_metrics=not args.collect_only,
                        include_skill_scope_probe=args.scope_probe,
                        requested_metric_specs=specs if args.full_scope and target_codes else None)
                    validate_runtime_audit(audit)
                    metric_key = 'fullRequestedSkillMetrics' if args.full_scope else 'requestedSkillMetrics'
                    observations = [] if args.collect_only else [row for player in audit['players'] for row in player[metric_key]]
                    for row in observations:
                        validate_result(row)
                    match = {'matchKey': audit['source']['replaySha256'], 'policyId': policy_id,
                             'clientVersion': CLIENT_VERSION, 'gameDataSha256': GAME_DB_SHA256,
                             'playerMatchCount': len(audit['players']),
                             'observedCharacterCodes': sorted({p['characterCode'] for p in audit['players']}),
                             'observations': observations, 'rawReplayRetained': False,
                             'calculationStatus': 'not-run-evidence-only' if args.collect_only else 'completed',
                             'rawSourceArchiveRetained': bool(replay.get('replayArchive')),
                             'sourceArchiveSha256': (replay.get('replayArchive') or {}).get('sourceSha256'),
                             'sourceArchivePath': (replay.get('replayArchive') or {}).get('archivePath'),
                             'fallbackUsed': False}
                    if args.full_scope:
                        match['implementationSha256']=implementation_sha256
                        match['evidenceCacheStorage']=audit['skillScopeEvidenceCacheStorage']
                    if (args.scope_probe or (args.full_scope and not target_codes)) and not args.collect_only:
                        write_json(args.out_dir / f'scope-probe-{i:03d}.json', {
                            'matchKey': match['matchKey'], 'clientVersion': CLIENT_VERSION,
                            'gameDataSha256': GAME_DB_SHA256, **audit['skillScopeProbe']})
                        write_json(args.out_dir / f'deep-scope-probe-{i:03d}.json', {
                            'matchKey':match['matchKey'],'clientVersion':CLIENT_VERSION,
                            'gameDataSha256':GAME_DB_SHA256,**audit['deepSkillScopeProbe']})
                    del audit
            except ValueError as exc:
                if str(exc) != 'game user identity/team fields are incomplete':
                    raise
                if path.exists():
                    raise ValueError('raw replay survived failed acquisition context')
                failures.append({'candidateOrdinal': i, 'reason': 'incomplete-exact-player-team-identity',
                                 'rawReplayRetained': False, 'fallbackUsed': False})
                write_json(args.out_dir / 'unavailable-matches.json', failures)
                print(json.dumps({'phase': 'excluded', 'candidateOrdinal': i,
                                  'reason': failures[-1]['reason'], 'rawRemoved': True}), flush=True)
                continue
            except RuntimeError as exc:
                # A public candidate can have a successful battle lookup but no
                # bounded participant list. This is a typed candidate failure,
                # not a session failure. Record it and continue to the next
                # distinct candidate in the same authenticated batch. Never
                # broaden the participant set or retry through another identity.
                if not str(exc).startswith('official battle result has no bounded participant list'):
                    raise
                failures.append({'candidateOrdinal': i,
                                 'reason': 'official-battle-missing-bounded-participant-list',
                                 'detail': str(exc), 'rawReplayRetained': False,
                                 'fallbackUsed': False})
                write_json(args.out_dir / 'unavailable-matches.json', failures)
                print(json.dumps({'phase': 'excluded', 'candidateOrdinal': i,
                                  'reason': failures[-1]['reason'], 'rawRemoved': True}), flush=True)
                continue
            if path.exists():
                raise ValueError('raw replay survived acquisition context')
            if any(m['matchKey'] == match['matchKey'] for m in matches):
                raise ValueError('duplicate replay cannot enter trial population')
            write_json(args.out_dir / f'match-{i:03d}.json', match)
            matches.append(match)
            report = {'format': 'er-requested-skill-metrics-trial.v1', 'policyId': policy_id,
                      'clientVersion': CLIENT_VERSION, 'gameDataSha256': GAME_DB_SHA256,
                      'matchCount': len(matches), 'playerMatchCount': sum(m['playerMatchCount'] for m in matches),
                      'excludedMatchCount': len(failures),
                      'observedRequestedCharacterCodes': sorted({c for m in matches for c in m['observedCharacterCodes'] if c <= (90 if args.full_scope else 19)}),
                      'scope': audit_scope(),
                      'calculationStatus': 'not-run-evidence-only' if args.collect_only else 'completed',
                      'requestedMetricIds': [spec['metricId'] for spec in specs],
                      'metrics': [] if args.collect_only else summarize(matches,policy_id=policy_id,specs=specs),
                      **({'requestedCharacterCount':len(target_codes) if target_codes else 90,'registeredCharacterCodes':sorted({s['characterCode'] for s in specs}),
                          'implementationSha256':implementation_sha256,'implementationComplete':False} if args.full_scope else {}),
                      'rawReplayRetained': False,
                      'rawSourceArchiveRetained': all(m.get('rawSourceArchiveRetained') is True for m in matches),
                      'sourceArchiveSha256s': [m.get('sourceArchiveSha256') for m in matches],
                      'fallbackUsed': False}
            write_json(args.out_dir / 'summary.json', report)
            print(json.dumps({'phase': 'audit', 'completed': i, 'total': len(selected),
                              'calculableMetrics': None if args.collect_only else sum(r['status']=='calculable-observed' for r in report['metrics']),
                              'collectionOnly':args.collect_only,
                              'rawRemoved': True}), flush=True)
            expected_codes = set(target_codes) if target_codes else set(range(1, 91 if args.full_scope else 20))
            if set(report['observedRequestedCharacterCodes']) == expected_codes:
                break


def audit_scope():
    return ('exact own combat intervals containing an enemy-player packet; cast start determines scope by default; '
            'metrics with scopeEvent=projectile-spawn or attack-emission use actual emission tick')


if __name__ == '__main__':
    main()
