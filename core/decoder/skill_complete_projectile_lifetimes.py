"""Count actual emitted objects through their complete, exclusive lifetimes.

Names nominate a whole skill family. Actual cast/action events assign each
object to a stage; actual destruction closes the outcome. Neither a fixed
bullet count nor an estimated flight/channel duration is used.
"""
from collections import Counter, defaultdict
import re

try:
    from .skill_attempt_timing import projectile_outcomes
    from .skill_wire_order import finish_lookup,event_within_cast,competing_manual_starts
    from .requested_skill_scope import exact_cast_lifetimes
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_static_effect_families import candidate_effect_family
    from .skill_projectile_active_end import projectile_active_end_records, collision_only_outcome
except ImportError:
    from skill_attempt_timing import projectile_outcomes
    from skill_wire_order import finish_lookup,event_within_cast,competing_manual_starts
    from requested_skill_scope import exact_cast_lifetimes
    from requested_skill_hit_rates import _result, _unavailable
    from skill_static_effect_families import candidate_effect_family
    from skill_projectile_active_end import projectile_active_end_records, collision_only_outcome


def candidate_projectile_family(spec,catalog):
    # During a match this must be a lookup in the exact-version plan. The
    # pure builder is used only outside evaluation (compilation and audits).
    try:
        from .skill_execution_plan import planned_family_candidates
        from .skill_rule_candidates import build_projectile_candidates
    except ImportError:
        from skill_execution_plan import planned_family_candidates
        from skill_rule_candidates import build_projectile_candidates
    planned=planned_family_candidates(spec,'projectileCodes')
    if planned is not None:return planned
    return build_projectile_candidates(spec,catalog)


# Native constructor -> callback -> parameter consumer contracts. Add a rule
# only after establishing its actual producer and object-bound effect lifetime.
NATIVE_OBJECT_EFFECTS = {
    1017500: dict(character=17, skill='AdrianaActive4', wire=237,
        projectile=101741, prefab='Projectile_FX_BI_Adriana_Skill04',
        effects={1017501:'bomb-impact'}, synchronousEffectsBeforeRemoval=True, requireCompleteStreams=True,
        proof='deliverables/native-adriana-r-bomb-object-contract-v1.json'),
    1058200: dict(character=58, skill='HazeActive1_1', wire=860,
        projectile=105821, prefab='Projectile_FX_BI_Haze_Skill01',
        effects={1058201:'grenade-impact'}, synchronousEffectsInRemovalFrame=True,
        effectsAtRemovalOnly=True,
        proof='deliverables/native-haze-q-execution-contract-v1.json'),
    1082200: dict(character=82, skill='XuelinActive1', wire=1205, mode='first-hit',
        projectiles={108211:'Projectile_FX_BI_Xuelin_Skill01_SwordField'},
        effects={}, collisionCallbackDamage=True, synchronousEffectsBeforeRemoval=True,
        secondaryDamageExcludedByUser=True,
        proof='deliverables/native-xuelin-q-initial-collision-v1.json'),
    1031300: dict(character=31, skill='RioActive2Short', wire=436,
        projectiles={103131:'Projectile_FX_BI_Rio_Skill02_ShortBow'},
        effects={}, collisionCallbackDamage=True, synchronousEffectsBeforeRemoval=True,
        multipleEmissions=True, binaryContactOnly=True,
        proof='deliverables/native-rio-w-collision-contract-v1.json'),
    1050200: dict(character=50, skill='ElenaActive1', wire=733, projectile=105001,
        prefab='Projectile_FX_BI_Elena_Skill01',
        effects={1050201:'arrival-first-hit', 1050203:'destruction-second-hit'},
        synchronousEffectsBeforeRemoval=True,
        proof='deliverables/native-elena-q-object-effects-v1.json'),
    1052200: dict(character=52, skill='AdinaActive1', wire=758,
        projectiles={105221:'Projectile_FX_BI_Adina_Skill01_Sun',
                     105222:'Projectile_FX_BI_Adina_Skill01_Moon',
                     105223:'Projectile_FX_BI_Adina_Skill01_Star'},
        effects={}, collisionCallbackDamage=True, synchronousEffectsBeforeRemoval=True,
        proof='deliverables/native-adina-q-collision-contract-v1.json'),
    1052210: dict(character=52, skill='AdinaActive1ReinforceSun', wire=759,
        projectiles={105225:'Projectile_FX_BI_Adina_Skill01_Sun_02'},
        effects={}, collisionCallbackDamage=True, synchronousEffectsBeforeRemoval=True,
        proof='deliverables/native-adina-q-collision-contract-v1.json'),
}


def native_projectile_effect_metric(spec, starts, finishes, spawns, terminals,
                                    damages, player, teams, intervals, catalog, skill_rows,
                                    collisions=None, gaps=None, game_terminals=None, all_projectile_spawns=None):
    """Apply proven effects over owned object lifetimes, not cast animation time."""
    cfg = NATIVE_OBJECT_EFFECTS.get(spec['skillGroup'])
    fail = lambda reason: _unavailable(spec, reason)
    if spec['skillGroup']==1031300 and any(x is None for x in (starts,finishes,spawns,terminals,damages)):
        return fail('Rio cast/object/damage stream missing')
    if not cfg or (spec['characterCode'], spec['mode'], spec['unit']) != (cfg['character'], cfg.get('mode','any'), 'skill-cast'):
        return fail('native object-effect contract not registered')
    binary_only = cfg.get('binaryContactOnly') is True and spec.get('reportMultiTarget') is False
    if cfg.get('binaryContactOnly') and not binary_only:
        return fail('native binary contact contract does not establish target totals')
    definition = catalog['skillGroups'].get(str(spec['skillGroup']), {})
    projectiles = cfg.get('projectiles') or {cfg['projectile']:cfg['prefab']}
    if (definition.get('skillId') != cfg['skill'] or definition.get('characterCode') != cfg['character']
            or any(catalog['projectileDefinitions'].get(str(code), {}).get('prefabName') != prefab
                   for code,prefab in projectiles.items())):
        return fail('native object-effect game data identity mismatch')
    if cfg.get('collisionCallbackDamage'):
        if collisions is None or gaps is None or any(g.get('count',0) and g.get('packetName') in {
                'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdProjectileCollision',
                'CmdDestroyDelayStart','CmdDestroy'} for g in gaps):
            return fail('native collision contract requires complete cast/object/collision command streams')
    code_groups = {s['code']: s['group'] for s in skill_rows}
    selected = [s for s in starts if s['playerObjectId'] == player and s['skillGroup'] == spec['skillGroup']]
    if not selected or any(s['skillIdCode'] != cfg['wire'] or code_groups.get(s['skillCode']) != spec['skillGroup'] for s in selected):
        return fail('native object-effect cast wire identity missing or mismatched')
    from .skill_partial_cast_lifetimes import partial_cast_windows, partial_window_contains
    lives, unfinished, why = partial_cast_windows(selected, finishes, player)
    if why:
        return fail(why)
    finish_orders = finish_lookup(finishes, player)
    own = [s for s in spawns if s['ownerPlayerObjectId'] == player and s['projectileCode'] in projectiles]
    # Some synchronous callbacks call DestroySelf first, then DamageTo in
    # the same frame. An explicit contract permits that callback tail; it
    # never extends activity into a subsequent frame or a delayed explosion.
    removal_frame = cfg.get('synchronousEffectsInRemovalFrame') is True
    if not removal_frame and cfg.get('synchronousEffectsBeforeRemoval') is not True:
        return fail('native callback effect completion at removal not established')
    if (removal_frame or cfg.get('requireCompleteStreams')) and (gaps is None or any(g.get('count',0) and g.get('packetName') in {
            'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdDamage',
            'CmdDestroyDelayStart','CmdDestroy'} for g in gaps)):
        return fail('native object effects require complete cast/object/damage streams')
    ends = projectile_active_end_records(terminals)
    objects, by_cast = {}, defaultdict(list)
    unclosed = set()
    for s in own:
        oid = s['projectileObjectId']
        owners = [i for i, (start, end) in enumerate(lives) if partial_window_contains(start, end, s, finish_orders)]
        if oid in objects or len(owners) != 1:
            return fail('native effect projectile has no unique cast identity')
        end = ends.get(oid, {})
        if not end.get('complete') or end['endTick'] < s['tick']:
            # An absent end cannot disprove an already recorded hit. A
            # conflicting end still invalidates this object's evidence.
            if not binary_only or end:
                return fail('native effect projectile removal missing or conflicting')
            unclosed.add(owners[0])
        objects[oid] = (owners[0], s['tick'], end.get('endTick'))
        by_cast[owners[0]].append(s)
    reasons = {(f['skillIdCode'], f['tick']): f.get('reason') for f in finishes if f['playerObjectId'] == player}
    cancelled = set()
    for i, (s, end) in enumerate(lives):
        if i in unfinished:continue
        if len(by_cast[i]) == 1 or (cfg.get('multipleEmissions') and by_cast[i]):
            continue
        if not by_cast[i] and reasons.get((s['skillIdCode'], end)) in set(range(1,15)) | {16,17}:
            cancelled.add(i)
        else:
            return fail('native effect normal cast emission missing or duplicated')
    contacts = [set() for _ in lives]
    phase_contacts = {phase: [set() for _ in lives] for phase in cfg['effects'].values()}
    if cfg.get('collisionCallbackDamage'):
        # The native callback takes the collided target and synchronously calls
        # DamageTo. Object ID, not damage FX or overlap with another manual
        # skill's animation, identifies the attack that contacted this target.
        for c in collisions:
            if c['projectileObjectId'] not in objects:continue
            i,start,end = objects[c['projectileObjectId']]
            if c['tick'] < start or (end is not None and c['tick'] > end):
                return fail('native collision outside actual projectile activity lifetime')
            target=c['targetObjectId']
            if target in teams and teams[target] != teams[player]:
                contacts[i].add((c['tick'],target))
    for d in damages:
        target = d['targetObjectId']
        if d['attackerObjectId'] != player or d.get('effectCode') not in cfg['effects'] or target not in teams or teams[target] == teams[player]:
            continue
        owners = [i for i, start, end in objects.values() if start <= d['tick'] <= end
                  and (not cfg.get('effectsAtRemovalOnly') or d['tick'] == end)]
        if len(owners) != 1:
            return fail('native callback damage has no unique owned projectile lifetime')
        i = owners[0]
        contact = (d['tick'], target)
        contacts[i].add(contact)
        phase_contacts[cfg['effects'][d['effectCode']]][i].add(contact)
    chosen = [i for i, (s, _) in enumerate(lives) if any(a <= s['tick'] < b for a,b in intervals)]
    completeness = {}
    policy_misses=[];policy_blocked=[]
    if binary_only or unfinished:
        observed = chosen
        unknown = [i for i in observed if i in unfinished or (i in unclosed and not contacts[i])]
        if spec['skillGroup']==1031300:
            import json
            from pathlib import Path
            from .skill_ordered_match_end import ordered_winner_match_end
            from .skill_wire_order import command_order
            enabled=json.loads((Path(__file__).resolve().parents[1]/'data/user-hit-rate-scope-20260912.json').read_bytes())['recordedUseMissPolicy'].get('enabled') is True
            winner=ordered_winner_match_end(game_terminals,gaps) if enabled else None
            for i in list(unknown):
                if not enabled or unfinished.get(i)!='open-final-cast' or contacts[i]:continue
                start=lives[i][0];left=command_order(start);right=command_order(winner) if winner else None
                blockers=[];shots=by_cast[i];ids={s['projectileObjectId'] for s in shots}
                if left is None or right is None or left>=right or start['tick']>winner['tick']:blockers.append('actual-ordered-winner-unavailable')
                if gaps is None or any(g.get('count',0) for g in gaps):blockers.append('stream-gap')
                def after(e):
                    if type(e.get('tick')) is int and e['tick']<start['tick']:return False
                    if type(e.get('tick')) is int and e['tick']>start['tick']:return True
                    return left is None or command_order(e) is None or command_order(e)>=left
                if all_projectile_spawns is None: blockers.append('unfiltered-object-stream-missing')
                elif any(s.get('projectileCode') in projectiles and s.get('ownerObjectId') not in teams and after(s) for s in all_projectile_spawns):blockers.append('unowned-relevant-projectile')
                if all_projectile_spawns is not None:
                    raw_own=[s for s in all_projectile_spawns if s.get('ownerObjectId')==player and s.get('projectileCode') in projectiles and after(s)]
                    identity=lambda s:(s.get('projectileObjectId'),s.get('tick'),command_order(s))
                    if Counter(map(identity,raw_own))!=Counter(map(identity,shots)):blockers.append('raw-and-owned-emission-disagree')
                if not shots or any(command_order(s) is None or left is None or right is None or not left<=command_order(s)<right or not start['tick']<=s['tick']<=winner['tick'] for s in shots):blockers.append('ordered-emission-before-winner-not-proven')
                # This native contract makes object collision the damage event.
                # Other Rio attacks' damage and collisions cannot belong to W.
                if any(c.get('projectileObjectId') in ids for c in collisions):blockers.append('projectile-contact-needs-classification')
                if any(e.get('objectId') in ids for e in terminals):blockers.append('existing-object-terminal-needs-classification')
                proof=dict(startTick=start['tick'],startOrder=left,winnerEnd=winner,
                    projectiles=shots,skillFinishInvented=False,projectileEndInvented=False)
                if blockers:policy_blocked.append(dict(proof,reasons=blockers));continue
                unknown.remove(i);policy_misses.append(proof)
        chosen = [i for i in observed if i not in unknown]
        completeness = dict(perUseCompletenessTracked=True,
            observedCombatCastCount=len(observed), verifiedCombatCastCount=len(chosen)-len(policy_misses),
            unresolvedCombatCastCount=len(unknown),
            unresolvedCastTicks=[lives[i][0]['tick'] for i in unknown],
            unresolvedCastReasons=dict(Counter(unfinished.get(i,'projectile-end-missing-without-confirmed-hit') for i in unknown)),
            incompleteUsesCountedAsMisses=False)
        if observed and not chosen:
            return {**fail('no native collision hit or complete no-contact use'), **completeness,
                    **({'userPolicyMissEvidence':policy_misses,'userPolicyBlockedEvidence':policy_blocked} if spec['skillGroup']==1031300 else {})}
    row = _result(spec, [contacts[i] for i in chosen],
        'native-damage-callback-owned-projectile-collisions' if cfg.get('collisionCallbackDamage')
        else 'native-effects-within-owned-projectile-lifetimes')
    row['outcomes'] = projectile_outcomes('skill-cast', [
        (lives[i][0]['tick'], [(s['tick'], contacts[i]) for s in by_cast[i]], i in cancelled) for i in chosen])
    row.update(exactProjectileCodes=sorted(projectiles), exactEffectCodes=sorted(cfg['effects']),
        phaseHitCastCounts={phase:sum(bool(hits[i]) for i in chosen) for phase,hits in phase_contacts.items()},
        actualProjectileCount=sum(len(by_cast[i]) for i in chosen), cancelledBeforeAttackCount=len(cancelled.intersection(chosen)),
        callbackEffectsCompleteBeforeRemoval=cfg.get('synchronousEffectsBeforeRemoval') is True,
        castFinishUsedAsEffectEnd=False, positiveSampleRequired=False,
        fixedDurationWindowUsed=False, nearestCastUsed=False, evidenceReview=cfg['proof'])
    if removal_frame:
        row.update(callbackEffectsCompleteInRemovalFrame=True,
                   effectDamageMustMatchRemovalTick=bool(cfg.get('effectsAtRemovalOnly')))
    if cfg.get('collisionCallbackDamage'):
        row.update(collisionTargetIsNativeDamageTarget=True, effectCodeMatchingRequired=False,
                   nonDamageChildObjectsExcluded=not cfg.get('secondaryDamageExcludedByUser',False))
    if cfg.get('secondaryDamageExcludedByUser'):
        row.update(secondaryDamageExcludedByUser=True,measuredOutcome='initial-projectile-contact',
            interpretation='최초 투사체의 적 실험체 접촉만 집계; 후속 주기 피해와 회수 후 피해 제외')
    if completeness:
        row.update(**completeness)
        if completeness['unresolvedCombatCastCount']:
            row.update(hitRateScope='verified-uses-only',wholeCombatHitRate=None,
                       fullRequestedMetricComplete=False)
    if binary_only:
        row.update(anyOneProjectileHitIsCastSuccess=True,
            allProjectilesMustHit=False, allProjectileEndsRequiredForHit=False,
            targetTotalsRequested=False)
        for key in ('multiTargetAttemptCount','multiTargetAttemptRate','distinctEnemyTargetsSummedAcrossAttempts',
                    'meanDistinctEnemyTargetsPerAttempt','deduplicatedEnemyContactEventCount',
                    'distinctEnemyTargetsPerAttempt','distinctEnemyTargetFirstHitTicksPerAttempt'):
            row[key] = None
        if completeness['unresolvedCombatCastCount']:
            row.update(hitRateScope='verified-uses-only',wholeCombatHitRate=None,
                       fullRequestedMetricComplete=False)
    if spec['skillGroup']==1031300:
        row.update(userPolicyMissEvidence=policy_misses,userPolicyBlockedEvidence=policy_blocked,
            userPolicyMissCastCount=len(policy_misses),userPolicyMissAuthority='explicit-user-rule')
        if policy_misses:row.update(verifiedCompletionCredit=False,fullRequestedMetricComplete=False)
    return row


def complete_projectile_lifetime_metric(spec, all_starts, spawns, collisions, terminals,
                                        finishes, damages, player, teams, intervals,
                                        catalog, effect_rows, projectile_owners):
    if spec['mode'] not in {'any', 'shot'}:
        return _unavailable(spec, '실제 발사체 경로로 벽·부위·처치·카메라 조건을 대신하지 않음')
    groups, codes = candidate_projectile_family(spec, catalog)
    relevant = [s for s in spawns if s['projectileCode'] in codes]
    starts = [s for s in all_starts if s['skillGroup'] in groups]
    if not relevant or not any(s['skillGroup'] == spec['skillGroup'] for s in starts):
        return _unavailable(spec, '같은 스킬 계열의 실제 발사체 또는 요청 단계 시전이 없음',
                            category='observation-incomplete', reason_code='cast-or-family-emission-missing')
    lifetimes, reason = exact_cast_lifetimes(starts, finishes, player)
    if reason:
        return _unavailable(spec, reason)
    finish_by_identity = {(f['skillIdCode'], f['tick']): f for f in finishes if f.get('playerObjectId') == player}
    if any(finish_by_identity[(s['skillIdCode'], end)].get('reason') == 15 for s, end in lifetimes):
        return _unavailable(spec, '재생 종료로 닫힌 시전은 실제 게임 내 발사 결과를 확정하지 않음')
    other_lifetimes,other_reason=exact_cast_lifetimes(competing_manual_starts(all_starts,catalog,groups,player),finishes,player)
    if other_reason:
        return _unavailable(spec, '다른 수동 스킬의 시전 수명 불완전으로 발사체 중복 귀속 배제 불가')
    by_cast = defaultdict(list)
    finish_orders = finish_lookup(finishes,player)
    for spawn in relevant:
        owners = [i for i, (s, end) in enumerate(lifetimes) if event_within_cast(s,end,spawn,finish_orders)]
        if len(owners) > 1:
            # An exact same-tick skill action can distinguish a fire stage from
            # an overlapping preparation stage. No nearest-event choice.
            owners = [i for i in owners if any(a['tick'] == spawn['tick'] for a in lifetimes[i][0].get('linkActions', []))]
        if len(owners) != 1 or any(event_within_cast(s,end,spawn,finish_orders) for s, end in other_lifetimes):
            return _unavailable(spec, '실제 생성 발사체가 한 단계의 시전·타격 행동에 배타적으로 연결되지 않음')
        by_cast[owners[0]].append(spawn)
    for spawn in spawns:
        if str(spawn['projectileCode']) not in catalog['projectileDefinitions'] and any(event_within_cast(s,end,spawn,finish_orders) for s, end in lifetimes):
            return _unavailable(spec, '시전 중 같은 소유자의 미정의 발사체가 있어 전체 발사 수 완전성 미확정')
    selected = [i for i, (s, end) in enumerate(lifetimes) if s['skillGroup'] == spec['skillGroup']]
    cancelled = set()
    for i in selected:
        if by_cast[i] or spec['unit'] == 'projectile-shot':
            continue
        s, end = lifetimes[i]
        if finish_by_identity[(s['skillIdCode'], end)].get('reason') in set(range(1, 15)) | {16, 17}:
            cancelled.add(i)
        else:
            return _unavailable(spec, '발사체가 없는 정상 종료 시전은 몸체·후속 발사 누락과 구분되지 않음')
    terminal_by_id, collision_by_id = defaultdict(list), defaultdict(set)
    for event in terminals:
        terminal_by_id[event['objectId']].append(event)
    for collision in collisions:
        collision_by_id[collision['projectileObjectId']].add((collision['tick'], collision['targetObjectId']))
    own_damage = set()
    family_damage = set()
    _, effects = candidate_effect_family(spec, catalog, effect_rows)
    for damage in damages:
        actor, target = damage['attackerObjectId'], damage['targetObjectId']
        if actor != player and projectile_owners.get(actor) != player:
            continue
        own_damage.add((damage['tick'], target))
        if damage.get('effectCode') in effects and target in teams and teams[target] != teams[player]:
            family_damage.add((damage['tick'], target))
    object_hits = {}
    object_cast = {}
    complex_codes = set()
    collision_counts_by_code = defaultdict(int)
    active_ends = projectile_active_end_records(terminals)
    closure_commands = set()
    final_destroyed = True
    for i, objects in by_cast.items():
        for spawn in objects:
            object_id = spawn['projectileObjectId']
            definition = catalog['projectileDefinitions'][str(spawn['projectileCode'])]
            events = terminal_by_id[object_id]
            destroys = {e['tick'] for e in events if e['event'] == 'CmdDestroy'}
            active = active_ends.get(object_id, {})
            if collision_only_outcome(definition):
                if not active.get('complete') or active['endTick'] < spawn['tick']:
                    return _unavailable(spec, '생성 발사체의 실제 충돌 종료가 없거나 상충하여 미적중 확정 불가')
                end = active['endTick']
                closure_commands.update(active['terminalCommands'])
            else:
                if len(destroys) != 1 or next(iter(destroys)) < spawn['tick']:
                    return _unavailable(spec, '생성 발사체의 최종 소멸이 확인되지 않아 미적중 확정 불가')
                end = next(iter(destroys))
                closure_commands.add('CmdDestroy')
            final_destroyed = final_destroyed and len(destroys) == 1
            contacts = collision_by_id[object_id]
            if any(not spawn['tick'] <= tick <= end for tick, _ in contacts):
                return _unavailable(spec, '충돌 기록이 실제 발사체 수명 밖에 존재함')
            explosive = definition.get('isExplosion') or definition.get('isExplosionWithoutCollision')
            complex_outcome = explosive or definition.get('collisionAfterArrival') or (definition.get('lifeTimeAfterArrival') or 0) > 0 or not definition.get('collisionEnabled')
            if explosive:
                explosions = {e['tick'] for e in events if e['event'] == 'CmdProjectileExplosion'}
                if len(explosions) != 1 or not spawn['tick'] <= next(iter(explosions)) <= end:
                    return _unavailable(spec, '폭발형 발사체의 실제 폭발 시점·소멸 수명 미확정')
            if complex_outcome:
                complex_codes.add(spawn['projectileCode'])
                if any(contact not in own_damage for contact in contacts if contact[1] in teams and teams[contact[1]] != teams[player]):
                    return _unavailable(spec, '지속·폭발·자동 추적의 충돌 대상에 같은 시전자·대상의 실제 피해 근거 없음')
            hits = {contact for contact in contacts if contact[1] in teams and teams[contact[1]] != teams[player]}
            collision_counts_by_code[spawn['projectileCode']] += len(hits)
            object_hits[object_id] = hits
            object_cast[object_id] = i
    # Complete collision lifetimes can measure persistence, but each such code
    # must have a real enemy contact sample and an explicit damage-family check.
    if complex_codes and (not effects or any(not collision_counts_by_code[code] for code in complex_codes)):
        return _unavailable(spec, '지속·폭발·자동 추적 코드별 적 타격과 전용 FX의 완전성 검증 표본 부족')
    all_hits = set().union(*object_hits.values())
    if family_damage - all_hits:
        return _unavailable(spec, '같은 스킬 계열의 실제 피해 일부가 발사체 충돌 기록에 없어 전체 결과 미확정')
    if not any(object_hits[oid] for oid, i in object_cast.items() if i in selected):
        return _unavailable(spec, '해당 단계 자체의 실제 적 발사체 타격 표본 없음')
    chosen = [i for i in selected if any(left <= lifetimes[i][0]['tick'] < right for left, right in intervals)]
    actual_objects = [s for i in chosen for s in by_cast[i]]
    shot_contacts = [object_hits[s['projectileObjectId']] for s in actual_objects]
    cast_contacts = [set().union(*(object_hits[s['projectileObjectId']] for s in by_cast[i])) for i in chosen]
    row = _result(spec, shot_contacts if spec['unit'] == 'projectile-shot' else cast_contacts,
                  'complete-exclusive-cast-projectile-lifetimes-and-contact-outcomes')
    row['outcomes'] = projectile_outcomes(spec['unit'], [
        (lifetimes[i][0]['tick'], [(s['tick'], object_hits[s['projectileObjectId']])
                                 for s in by_cast[i]], i in cancelled) for i in chosen])
    row.update(actualProjectileCount=len(actual_objects),
               enemyHitProjectileCount=sum(bool(hits) for hits in shot_contacts),
               projectileContactEventCount=sum(len(hits) for hits in shot_contacts),
               cancelledBeforeAttackCount=len(cancelled.intersection(chosen)),
               exactProjectileCodes=sorted({s['projectileCode'] for s in relevant}),
               finalDestructionVerified=final_destroyed, collisionActivityEndVerified=True,
               outcomeClosureCommands=sorted(closure_commands), fixedShotCountUsed=False,
               fixedFlightOrChannelDurationUsed=False, numericSkillStateCodeJoinUsed=False)
    return row
