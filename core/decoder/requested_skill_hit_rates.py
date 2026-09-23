"""User-requested 12.3 skill success metrics, separate from aim accuracy.

This trial deliberately does not replace the existing UI/population contract.
Only count data leave the exact replay lifetime. A user-selected skill is not
automatically a verified measurement. Delayed, summon, trap and wall-collision
attribution remain unavailable unless their individual evidence route passes.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy

try:
    from .projectile_hit_catalog import (
        _apply_corroborated_action_target_cast_rate,
        _apply_exact_effect_damage_cast_rate,
    )
except ImportError:
    from projectile_hit_catalog import (
        _apply_corroborated_action_target_cast_rate,
        _apply_exact_effect_damage_cast_rate,
    )


POLICY_ID = "user-request-20260905-first19-v1"
CLIENT_VERSION = "12.3.0"
GAME_DB_SHA256 = "5ef9cb5459a2e908099655bf9ffc70ac16b134d1db282fb97b25fe065e7c8e4f"

# Exact SkillGroup identities in the hash above; labels express the request.
# A missing stage is intentionally not synthesized from another stage.
REQUESTS = {
    1: [(1001200, "Q1"), (1001210, "Q2"), (1001400, "E"), (1001510, "R2")],
    2: [(1002300, "W"), (1002500, "R")],
    3: [(1003200, "Q"), (1003300, "W"), (1003400, "E1"), (1003410, "E2"),
        (1003500, "R1"), (1003510, "R2"), (1003520, "R3")],
    4: [(1004200, "Q"), (1004400, "E 벽 기절"), (1004500, "R")],
    5: [(1005200, "Q"), (1005300, "W"), (1005400, "E"), (1005500, "R")],
    6: [(1006200, "Q"), (1006300, "W1 설치"), (1006310, "W2 설치")],
    7: [(1007200, "Q"), (1007400, "E 일반 타격"), (1007500, "R")],
    8: [(1008210, "Q2"), (None, "Q3 단계 구분"), (1008400, "E1"), (1008410, "E2")],
    9: [(1009200, "Q"), (1009300, "W"), (1009500, "R 설치")],
    10: [(1010200, "Q1"), (1010210, "Q2"), (1010220, "Q3"), (1010400, "E"), (1010500, "R")],
    11: [(1011400, "E"), (1011500, "R")],
    12: [(1012200, "Q"), (1012300, "W"), (1012400, "E1"), (1012410, "E2"), (1012500, "R")],
    13: [(1013200, "Q"), (1013400, "E1"), (1013410, "E2"), (1013500, "R")],
    14: [(1014200, "Q"), (1014310, "W2"), (1014400, "E")],
    15: [(1015200, "Q"), (1015300, "W"), (1015400, "E")],
    16: [(1016200, "인간 Q"), (1016300, "인간 W"), (1016400, "인간 E"),
         (1016600, "바이크 Q"), (1016700, "바이크 W"), (1016800, "바이크 E")],
    17: [(1017200, "Q"), (1017300, "W"), (1017400, "E"), (1017500, "R")],
    18: [(1018200, "Q1"), (1018210, "Q2"), (1018300, "W"), (1018400, "E")],
    19: [(1019200, "Q"), (1019300, "W"), (1019500, "R")],
}
INSTALLATIONS = {1006300, 1006310, 1009500}
WALL_STUN = {1004400}


def manifest() -> list[dict]:
    rows = []
    for character, specs in REQUESTS.items():
        for group, label in specs:
            rows.append({
                "metricId": f"{POLICY_ID}:{character}:{group or 'unmapped-q3'}",
                "characterCode": character, "skillGroup": group, "label": label,
                "unit": "projectile-shot" if group == 1002300 else "skill-cast",
                "numerator": "enemy-hit-actual-shots" if group == 1002300 else
                    "wall-stun-success-casts" if group in WALL_STUN else "casts-hitting-any-enemy",
            })
    # E hit rate and wall-stun success are different requested measurements.
    rows.append({"metricId": f"{POLICY_ID}:7:wall-stun", "characterCode": 7,
                 "skillGroup": 1007400, "label": "E 벽 기절", "unit": "skill-cast",
                 "numerator": "wall-stun-success-casts"})
    return rows


def _matches(spawn: dict, start: dict, definitions: dict, window: int) -> bool:
    """Use the same exact action-anchor window as the core link verifier."""
    anchors = start.get("linkAnchorTicks") or [start["tick"]]
    declared = definitions[str(start["skillGroup"])].get("castTiming", {}).get(
        "derivedLinkWindowTicksAt60Hz", 0)
    start_window = max(window, declared)
    actions = sorted(a for a in anchors if isinstance(a, int) and a != start["tick"])
    if actions:
        start_window = min(start_window, max(0, actions[0] - start["tick"]))
    return (0 <= spawn["tick"] - start["tick"] <= start_window or
            any(0 <= spawn["tick"] - tick <= window for tick in actions))


def _result(spec: dict, contacts: list[set[tuple[int, int]]], method: str, *, cast_ticks=None) -> dict:
    """Contacts are exact (hit tick, enemy id), scoped to one attempt each."""
    attempts = len(contacts)
    hits = sum(bool(row) for row in contacts)
    targets = [len({target for _, target in row}) for row in contacts]
    target_ticks=[]
    for contact in contacts:
        first_by_target={}
        for tick,target in contact:
            first_by_target[target]=min(tick,first_by_target.get(target,tick))
        target_ticks.append(sorted(first_by_target.values()))
    row = {
        **spec, "status": "calculable-observed" if attempts else "no-combat-sample",
        "method": method, "attemptCount": attempts, "hitCount": hits,
        "hitRate": round(hits / attempts, 6) if attempts else None,
        "distinctEnemyTargetsSummedAcrossAttempts": sum(targets),
        "distinctEnemyTargetsPerAttempt": targets,
        "distinctEnemyTargetFirstHitTicksPerAttempt": target_ticks,
        "multiTargetAttemptCount": sum(value >= 2 for value in targets),
        "multiTargetAttemptRate": round(sum(value >= 2 for value in targets) / attempts, 6) if attempts else None,
        "meanDistinctEnemyTargetsPerAttempt": round(sum(targets) / attempts, 6) if attempts else None,
        "deduplicatedEnemyContactEventCount": sum(map(len, contacts)),
        "contactCountMeaning": "unique evidence tick/target per attempt; not inferred damage ticks",
        "reason": None, "fallbackUsed": False,
    }
    if cast_ticks is not None:
        try:
            from .skill_attempt_timing import exact_outcome
        except ImportError:
            from skill_attempt_timing import exact_outcome
        if spec.get('unit')!='skill-cast' or len(cast_ticks)!=attempts:
            raise ValueError('cast timing must match the exact cast denominator')
        row['outcomes']=[exact_outcome(t,t,c) for t,c in zip(cast_ticks,contacts)]
    return row


def _unavailable(spec: dict, reason: str, status: str = "unresolved-evidence", *,
                 category=None, reason_code=None) -> dict:
    try:
        from .skill_execution_plan import record_failure, known_failure
    except ImportError:
        from skill_execution_plan import record_failure, known_failure
    if category is None:
        category, inferred_code = known_failure(reason)
        reason_code = reason_code or inferred_code
    import sys
    caller = sys._getframe(1)
    record_failure(spec, reason, category, reason_code,
                   caller.f_globals.get('__name__', '') + '.' + caller.f_code.co_name)
    row = {**spec, "status": status, "reason": reason, "attemptCount": None,
           "hitCount": None, "hitRate": None, "fallbackUsed": False}
    if category is not None:
        row['failureCategory'] = category
    if reason_code is not None:
        row['failureReasonCode'] = reason_code
    return row


def _corroborated_action_contacts(spec,legacy,selected,player,teams,intervals,minimum_observations,*,allow_partial=False,zero_hit_policy=None,finishes=None):
    trial=deepcopy(legacy)
    original=selected
    unknown={i:'missing-recorded-skill-action' for i,s in enumerate(selected) if not s.get('linkActions')}
    zero_target_reuse=zero_hit_policy is not None and legacy.get('actionTargetEvidence',{}).get('enemyPlayerTargetReferenceCount')==0
    if zero_target_reuse:
        from .skill_partial_cast_lifetimes import ordered_cast_records
        records,reason=ordered_cast_records(selected,finishes,player,allow_same_tick_finishes=True)
        if reason:return None,reason
        reasons={id(r['start']):r['reason'] if not r['complete'] else
                 'cast-not-normally-complete' if r['finish']['reason']!=0 else None for r in records}
        for i,s in enumerate(selected):
            if reasons[id(s)]:unknown[i]=reasons[id(s)]
    if allow_partial and (unknown or zero_target_reuse):
        from .projectile_hit_catalog import _summarize_action_target_evidence
        selected=[s for i,s in enumerate(selected) if i not in unknown]
        combat={i for i,s in enumerate(selected) if any(l<=s['tick']<r for l,r in intervals)}
        trial.update(allCastCount=len(selected),castCount=len(combat),
                     actionTargetEvidence=_summarize_action_target_evidence(selected,combat,player,teams))
    trial['hitRateCalculable']=False
    trial['aimModel']['hitRateEligible']=True
    if zero_target_reuse:
        # Preselected reviewed zero-target policy. This does not promote a
        # positive-corroboration result or modify shared extraction code.
        evidence=trial['actionTargetEvidence']
        ordinary={'decoded-exact-CmdPlaySkillAction','decoded-exact-CmdPlaySkillActionWithTargets'}
        statuses=set(evidence.get('wireStatuses') or [])
        if selected and (not statuses or not statuses<=ordinary or
                evidence['castsWithLinkedActionCount']!=len(selected) or
                evidence['combatCastsWithLinkedActionCount']!=trial['castCount'] or
                any(evidence[k] for k in ('enemyPlayerTargetReferenceCount',
                    'enemyPlayerTargetSameTickCorroboratedCount','combatCastsWithEnemyPlayerTargetCount'))):
            return None,'reviewed zero-target rule requires complete ordinary actions with no enemy target'
        trial.update(hitRateCalculable=bool(selected),playerHitAttemptCount=0,
                     _combatAttemptOutcomes=deepcopy(evidence['_combatCastOutcomes']))
    else:
        _apply_corroborated_action_target_cast_rate(trial,minimum_observations)
    if trial['hitRateCalculable']:
        contacts=[]
        for start in selected:
            if not any(l<=start['tick']<r for l,r in intervals): continue
            contacts.append({(a['tick'],t['targetObjectId']) for a in start.get('linkActions',[])
                for t in a.get('targets',[]) if t['targetObjectId'] in teams
                and teams[t['targetObjectId']]!=teams[player]
                and (t.get('sameTickDirectPlayerDamage') or t.get('sameTickOwnedProjectileCollision'))})
        result=_result(spec,contacts,'exact-action-target-same-tick-corroborated')
        if result['hitCount']!=trial['playerHitAttemptCount']:
            raise ValueError('all-90 action target contact counts disagree')
        outcomes=trial['_combatAttemptOutcomes']
        if len(outcomes)!=result['attemptCount'] or sum(o[1] for o in outcomes)!=result['hitCount']:
            raise ValueError('all-90 action timing disagrees with requested denominator')
        result['outcomes']=deepcopy(outcomes)
    elif allow_partial and zero_target_reuse and not selected and unknown:
        result=_result(spec,[],'reviewed-action-target-no-complete-use',cast_ticks=[])
    else:
        return None,trial.get('actionTargetEvidence',{}).get('castHitRateReason') or 'action-target evidence unavailable'
    if allow_partial and (unknown or zero_target_reuse):
        from .skill_lifecycle_result_policy import finalize_lifecycle_result
        from .skill_wire_order import command_order
        combat=[i for i,s in enumerate(original) if any(l<=s['tick']<r for l,r in intervals)]
        result=finalize_lifecycle_result(result,combat,unknown,observed_positive=not zero_target_reuse or bool(result.get('hitCount')))
        result['unresolvedUseEvidence']=[dict(startTick=original[i]['tick'],startOrder=command_order(original[i]),
            reasons=[unknown[i]],hasRecordedEmission=None) for i in combat if i in unknown]
    if zero_target_reuse:
        result.update(perMatchPositiveSampleRequired=False,reviewedRuleReusedWithoutPositiveSample=True)
    return result,None


def _projectile_contacts(spec, legacy, starts, all_starts, spawns, collisions,
                         teams, player, catalog, intervals, window, *, allow_partial=False, emission_finishes=None, explicit_launch_parents=None,winner_unemitted=None,cancelled_unemitted=None):
    """Allow variable real shots and multi-target penetration with exact links.

    Explosive, disabled-collision and persistent objects still need a separate
    damage/lifetime authority. A collision on such an object is not proof of
    its explosion/installation outcome.
    """
    evidence = legacy.get("projectileCodeCandidateEvidence", {})
    runtime_codes = evidence.get("runtimeExclusiveCandidates") or []
    candidates = set(runtime_codes or evidence.get("staticCandidates") or [])
    relevant = [s for s in spawns if s["projectileCode"] in candidates]
    if not starts or (not relevant and not (allow_partial and candidates and spec['unit']=='skill-cast')):
        return None, "시전 또는 연결할 실제 투사체가 관측되지 않음"
    emission_records=None
    if emission_finishes is not None and any(s.get('wireCategory')=='commands' for s in all_starts):
        from .skill_partial_cast_lifetimes import ordered_cast_records
        from .skill_wire_order import command_order, event_within_cast, finish_lookup
        emission_records,why=ordered_cast_records(all_starts,emission_finishes,player,allow_same_tick_finishes=True)
        if why:return None,why
        emission_records={id(r['start']):r for r in emission_records}
        emission_lookup=finish_lookup(emission_finishes,player)
    by_start = defaultdict(list); ambiguous_starts=set(); ambiguous_emissions=[]; narrowed_launch_edges=[]
    for spawn in relevant:
        compatible = []
        for start in all_starts:
            definition = catalog["skillGroups"][str(start["skillGroup"])]
            if not runtime_codes and spawn["projectileCode"] not in definition.get("projectileCodeCandidates", []):
                continue
            if emission_records is None:
                matched=_matches(spawn,start,catalog['skillGroups'],window)
            else:
                rec=emission_records[id(start)];end=rec['finish']
                matched=(event_within_cast(start,end['tick'],spawn,emission_lookup) if end else
                    command_order(spawn) is not None and command_order(start)<=command_order(spawn) and start['tick']<=spawn['tick'])
            if matched:compatible.append(start)
        edge=(explicit_launch_parents or {}).get(spawn['projectileObjectId'])
        positive=any(t>=spawn['tick'] and target in teams and teams[target]!=teams[player]
                     for t,target in collisions.get(spawn['projectileObjectId'],set()))
        if (spec.get('skillGroup')==1086300 and len(compatible)>1 and edge
                and positive and any(s is edge['start'] for s in compatible)):
            compatible=[edge['start']]
            if any(s is edge['start'] for s in starts) and any(l<=edge['start']['tick']<h for l,h in intervals):
                narrowed_launch_edges.append({k:v for k,v in edge.items() if k!='start'})
        if len(compatible) != 1 or compatible[0]["skillGroup"] != spec["skillGroup"]:
            affected=[s for s in compatible if s['skillGroup']==spec['skillGroup']]
            if (allow_partial and spec['unit']=='skill-cast' and emission_records is not None
                    and len(compatible)>1 and affected
                    and all(emission_records[id(s)]['complete'] and emission_records[id(s)]['finish'] is not None for s in compatible)):
                ambiguous_starts.update(id(s) for s in affected)
                ambiguous_emissions.append(dict(projectileObjectId=spawn['projectileObjectId'],projectileCode=spawn['projectileCode'],
                    spawnTick=spawn['tick'],spawnOrder=command_order(spawn),
                    candidateCastTicks=[s['tick'] for s in compatible],candidateSkillGroups=[s['skillGroup'] for s in compatible],
                    candidateLifetimes=[dict(skillGroup=s['skillGroup'],skillIdCode=s['skillIdCode'],startTick=s['tick'],
                        startOrder=command_order(s),finishTick=emission_records[id(s)]['finish']['tick'],
                        finishOrder=command_order(emission_records[id(s)]['finish']),
                        finishReason=emission_records[id(s)]['finish']['reason']) for s in compatible]))
                continue
            return None, "투사체를 단 하나의 시전·단계에 배타적으로 연결하지 못함"
        by_start[id(compatible[0])].append(spawn)
    missing = [start for start in starts if not by_start[id(start)]] if spec['unit'] != 'projectile-shot' else []
    if missing and not allow_partial:
        return None, "일부 시전에 실제 발사체 연결이 없어 누락과 취소를 구분하지 못함"
    for code in {s["projectileCode"] for s in relevant}:
        definition = catalog["projectileDefinitions"].get(str(code))
        if not definition or not definition.get("collisionEnabled"):
            return None, "투사체 충돌 판정이 꺼져 있어 장판·폭발의 실제 피해 연결 필요"
        if definition.get("isExplosion") or definition.get("isExplosionWithoutCollision"):
            return None, "직접 충돌만으로 폭발 범위의 전체 적중 여부를 확정할 수 없음"
        if definition.get("collisionAfterArrival") or (definition.get("lifeTimeAfterArrival") or 0) > 0:
            return None, "지속·설치 판정의 수명 및 종료 결과 연결 필요"
        if "return" in str(definition.get("prefabName", "")).lower():
            return None, "왕복 단계의 전체 충돌 기록 여부 검증 필요"
    try:
        from .skill_attempt_timing import projectile_outcomes
    except ImportError:
        from skill_attempt_timing import projectile_outcomes
    policy_unemitted={**(winner_unemitted or {}),**(cancelled_unemitted or {})}
    admitted={id(start) for start in missing if id(start) not in ambiguous_starts and id(start) in policy_unemitted}
    contacts = []
    timed_uses = []
    for start in starts:
        if id(start) in ambiguous_starts:continue
        if not any(left <= start["tick"] < right for left, right in intervals):
            continue
        if allow_partial and spec['unit'] != 'projectile-shot' and not by_start[id(start)] and id(start) not in admitted:
            continue  # Missing emission is an unknown use, never a missed shot.
        cast_contacts = set()
        timed_shots = []
        for spawn in by_start[id(start)]:
            hits = {(tick, target) for tick, target in collisions.get(spawn["projectileObjectId"], set())
                    if tick >= spawn["tick"] and target in teams and teams[target] != teams[player]}
            timed_shots.append((spawn['tick'], hits))
            if spec["unit"] == "projectile-shot":
                contacts.append(hits)
            else:
                cast_contacts.update(hits)
        if spec["unit"] == "skill-cast":
            contacts.append(cast_contacts)
        timed_uses.append((start['tick'], timed_shots, id(start) in admitted))
    row = _result(spec, contacts, "exclusive-actual-shot-collision" if spec["unit"] == "projectile-shot"
                   else "exclusive-cast-any-enemy-projectile-collision")
    row['outcomes'] = projectile_outcomes(spec['unit'], timed_uses)
    if emission_records is not None:row.update(emissionLink='recorded-ordered-cast-lifetime',fixedEmissionWindowUsed=False)
    if allow_partial and (missing or ambiguous_starts):
        from .skill_wire_order import command_order
        unresolved=[s for s in starts if (s in missing and id(s) not in admitted) or id(s) in ambiguous_starts]
        combat_unresolved=[s for s in unresolved if any(l<=s['tick']<h for l,h in intervals)]
        reason_for=lambda s:'projectile-parent-not-unique' if id(s) in ambiguous_starts else 'missing-recorded-projectile-emission'
        unknown = len(combat_unresolved)
        verified = row['attemptCount']
        if unknown and not row['attemptCount']:
            row = _unavailable(spec, '대인 교전 시전의 발사체 연결이 모두 미확인')
        row.update(perUseCompletenessTracked=True,
                   observedCombatCastCount=verified+unknown,
                   verifiedCombatCastCount=verified,
                   unresolvedCombatCastCount=unknown,unresolvedAllCastCount=len(unresolved),
                   unresolvedCastReasons=dict(Counter(reason_for(s) for s in combat_unresolved)),
                   unresolvedCastTicks=[s['tick'] for s in combat_unresolved],
                   ambiguousEmissionEvidence=ambiguous_emissions,ambiguousEmissionsAssignedToCast=False,
                   unresolvedUseEvidence=[dict(startTick=s['tick'],startOrder=command_order(s),
                       reasons=[reason_for(s)],hasRecordedEmission=None if id(s) in ambiguous_starts else False,
                       hasAmbiguousEmissionCandidate=id(s) in ambiguous_starts)
                       for s in combat_unresolved],
                   incompleteUsesCountedAsMisses=False)
        if unknown:
            row.update(hitRateScope='verified-uses-only',wholeCombatHitRate=None,fullRequestedMetricComplete=False)
    proof=[policy_unemitted[id(s)] for s in starts if id(s) in admitted and any(l<=s['tick']<h for l,h in intervals)]
    if proof:row.update(userPolicyMissEvidence=proof,userPolicyMissCastCount=len(proof),userPolicyMissAuthority='explicit-user-rule',verifiedCompletionCredit=False,fullRequestedMetricComplete=False,verifiedCombatCastCount=max(0,(row.get('verifiedCombatCastCount') or 0)-len(proof)))
    if narrowed_launch_edges and row.get('attemptCount'):
        from .skill_development_cancellation import annotate_provisional
        row=annotate_provisional(row,'Fenrir W launch31 names the exact projectile; recorded enemy collision settles the narrowed positive use.')
        row.update(fenrirLaunchParentEdges=narrowed_launch_edges,fenrirLaunchParentProof='work/fenrir-w-native-launch-edge-review.json')
    return row, None


def calculate_requested_metrics(catalog, runtime, starts, spawns, collisions,
                                teams, intervals, damage_events, *, client_version,
                                game_db_sha256, wall_inputs=None):
    try:
        from .skill_game_data_contract import revision_identity
    except ImportError:
        from skill_game_data_contract import revision_identity
    revision_identity(client_version,game_db_sha256)
    starts_by_player = defaultdict(list)
    spawns_by_player = defaultdict(list)
    collisions_by_id = defaultdict(set)
    for row in starts:
        starts_by_player[row["playerObjectId"]].append(row)
    for row in spawns:
        spawns_by_player[row["ownerPlayerObjectId"]].append(row)
    for row in collisions:
        collisions_by_id[row["projectileObjectId"]].add((row["tick"], row["targetObjectId"]))
    output = {}
    for player, legacy_rows in runtime["players"].items():
        player = int(player)
        by_group = {row["skillGroup"]: row for row in legacy_rows}
        results = []
        for spec in manifest():
            group = spec["skillGroup"]
            if group not in by_group:
                continue
            legacy = by_group[group]
            player_starts = starts_by_player[player]
            skill_starts = [s for s in player_starts if s["skillGroup"] == group]
            common = {"allCastCount": legacy["allCastCount"], "combatCastCount": legacy["castCount"]}
            if spec["numerator"] == "wall-stun-success-casts":
                if wall_inputs is not None:
                    try:
                        from .requested_wall_stun_metrics import calculate_wall_stun_metrics
                    except ImportError:
                        from requested_wall_stun_metrics import calculate_wall_stun_metrics
                    result = calculate_wall_stun_metrics(
                        spec, skill_starts, wall_inputs['finishes'], wall_inputs['states'], player,
                        teams, intervals.get(player, []), wall_inputs['skill_rows'],
                        wall_inputs['state_rows'], wall_inputs['state_groups'])
                else:
                    result = _unavailable(spec, "벽 충돌 원인과 해당 E 시전의 기절 적용 연결 미확정; 일반 타격으로 대체하지 않음")
            elif group in INSTALLATIONS:
                result = _unavailable(spec, "설치 객체·활성화·종료 시점과 시전 연결 미확정; 남은 설치물을 실패로 세지 않음")
            else:
                result, projectile_reason = _projectile_contacts(
                    spec, legacy, skill_starts, player_starts, spawns_by_player[player],
                    collisions_by_id, teams, player, catalog, intervals.get(player, []),
                    runtime["linkWindowTicks"])
                if result is None:
                    result = _unavailable(spec, projectile_reason)
            result.update(common)
            result["castWaysType"] = legacy.get("aimModel", {}).get("castWaysType")
            result["interpretation"] = "actual-shot accuracy" if spec["unit"] == "projectile-shot" else "application-success-per-cast; not target-selection aim accuracy"
            results.append(result)
        output[str(player)] = results
    return output


def validate_requested_result(row):
    expected = next((s for s in manifest() if s['metricId'] == row.get('metricId')), None)
    if expected is None or any(row.get(k) != v for k, v in expected.items()):
        raise ValueError('requested metric identity or unit does not match policy')
    if row.get("fallbackUsed") is not False:
        raise ValueError("requested metric cannot use fallback")
    if row.get("status") == "calculable-observed":
        attempts, hits = row.get("attemptCount"), row.get("hitCount")
        if not isinstance(attempts, int) or attempts <= 0 or not isinstance(hits, int) or not 0 <= hits <= attempts:
            raise ValueError("invalid requested attempt/hit counts")
        if row.get("hitRate") != round(hits / attempts, 6):
            raise ValueError("requested rate disagrees with counts")
        multi = row.get("multiTargetAttemptCount")
        if not isinstance(multi, int) or not 0 <= multi <= hits:
            raise ValueError("invalid requested multi-target count")
        if row.get("multiTargetAttemptRate") != round(multi / attempts, 6):
            raise ValueError("invalid requested multi-target rate")
        target_sum = row.get('distinctEnemyTargetsSummedAcrossAttempts')
        if not isinstance(target_sum, int) or target_sum < hits + multi:
            raise ValueError('invalid requested distinct-target count')
        if row.get('meanDistinctEnemyTargetsPerAttempt') != round(target_sum / attempts, 6):
            raise ValueError('invalid requested mean distinct-target count')
    elif row.get("status") != "no-combat-sample" and any(row.get(k) is not None for k in ["attemptCount", "hitCount", "hitRate"]):
        raise ValueError("unknown requested metric is not zero")
