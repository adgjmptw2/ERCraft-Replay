"""Experimental Leon R initial wave contact from exact recorded events.

Never certifies the complete native producer graph. Exact packet identity,
owner, target and command order replace latest-cast/time-window guesses.
The checked-in evidence review lives in skill-hit-rate-work-20260910/.
"""
from collections import Counter

from decoder.requested_skill_hit_rates import _result, _unavailable
from decoder.skill_action_stage_evidence import load_exact_skill_ids
from decoder.skill_development_effect_metrics import annotate_provisional_rate, development_policy
from decoder.skill_partial_cast_lifetimes import ordered_cast_records
from decoder.skill_wire_order import command_order

GROUP = 1029500
WAVE_STATE = 1029521
WAVE_GROUP = 1029520
WAVE_EFFECT = 1029501


def leon_r_metric(spec, starts, finishes, damages, states, player, teams, intervals,
                  catalog, skill_rows, state_rows, state_groups, effect_rows, gaps,*,spawns=None,collisions=None,terminals=None):
    spec=dict(spec,label='R 첫 타격',phaseScope='initial-wave-hit-only')
    policy = development_policy()
    if (spec.get('characterCode'), spec.get('skillGroup'), spec.get('mode'), spec.get('unit')) != (29, GROUP, 'any', 'skill-cast'):
        return _unavailable(spec, 'Leon R detail route scope mismatch')
    if not policy['enabled']:
        return _unavailable(spec, 'Leon R phase mapping is experimental and development policy is disabled')
    streams = (starts, finishes, damages, states, skill_rows, state_rows, state_groups, effect_rows, gaps)
    if any(s is None for s in streams):
        return _unavailable(spec, 'Leon R details require complete cast, damage, state and table streams')
    required = {'CmdStartSkill', 'CmdFinishSkill', 'CmdDamage', 'CmdAddState', 'CmdAddStateExtended'}
    if any(g.get('count', 0) and g.get('packetName') in required for g in gaps):
        return _unavailable(spec, 'Leon R detail input stream has decode gaps')
    definition = catalog['skillGroups'].get(str(GROUP), {})
    wire_id = load_exact_skill_ids().get('LeonActive4')
    if definition.get('characterCode') != 29 or definition.get('skillId') != 'LeonActive4' or wire_id != 419:
        return _unavailable(spec, 'Leon R exact skill identity changed')
    skill_codes = {s['code']: s['group'] for s in skill_rows}
    state_codes = {s['code']: s['group'] for s in state_rows}
    definitions = {s['group']: s for s in state_groups}
    effects = {s['code']: s for s in effect_rows}
    for code, group in ((WAVE_STATE, WAVE_GROUP),):
        if state_codes.get(code) != group or any(definitions.get(group, {}).get(k) != v for k, v in
                (('skillId', 'KnockUp'), ('stateType', 'Airborne'), ('stateBehaviourType', 'Airborne'), ('effectType', 'Debuff'))):
            return _unavailable(spec, 'Leon R candidate airborne state definition changed')
    for code, sound in ((WAVE_EFFECT, 'Leon_skill04_Hit'),):
        if effects.get(code, {}).get('effectPrefabName') != 'FX_BI_Leon_Skill04_Hit' or effects.get(code, {}).get('soundName') != sound:
            return _unavailable(spec, 'Leon R candidate effect definition changed')
    own = [s for s in starts if s.get('playerObjectId') == player and s.get('skillGroup') == GROUP]
    if any(s.get('skillIdCode') != wire_id or skill_codes.get(s.get('skillCode')) != GROUP for s in own):
        return _unavailable(spec, 'Leon R cast code does not match pinned skill identity')
    records, reason = ordered_cast_records(own, finishes, player, allow_same_tick_finishes=True)
    if reason:
        return _unavailable(spec, reason)
    if player not in teams:
        return _unavailable(spec, 'Leon R source team is unavailable')
    enemy = lambda target: target in teams and teams[target] != teams[player]
    relevant_states = [s for s in states if s.get('casterObjectId') == player and enemy(s.get('targetObjectId'))
        and s.get('event') == 'add' and s.get('stateCode') == WAVE_STATE]
    relevant_damage = [d for d in damages if d.get('attackerObjectId') == player and enemy(d.get('targetObjectId'))
                       and d.get('effectCode') == WAVE_EFFECT]
    relevant = relevant_states + relevant_damage
    if any(command_order(e) is None for e in relevant):
        return _unavailable(spec, 'Leon R detail events need exact command order')
    if len({command_order(e) for e in relevant}) != len(relevant):
        return _unavailable(spec, 'Leon R detail events have duplicate command order')
    ordered = sorted(relevant, key=command_order)
    if any(a['tick'] > b['tick'] for a, b in zip(ordered, ordered[1:])):
        return _unavailable(spec, 'Leon R detail clocks contradict command order')
    timeline = sorted(relevant + own + [r['finish'] for r in records if r['finish'] is not None], key=command_order)
    if len({command_order(e) for e in timeline}) != len(timeline) or any(a['tick'] > b['tick'] for a, b in zip(timeline, timeline[1:])):
        return _unavailable(spec, 'Leon R cast and detail clocks/orders are inconsistent')

    def within(i, event):
        record = records[i]
        return command_order(record['start']) <= command_order(event) and (
            record['finish'] is None or command_order(event) <= command_order(record['finish']))

    contact_parents={}
    if spawns is not None and collisions is not None:
        if catalog.get('projectileDefinitions',{}).get('102950',{}).get('prefabName')!='Projectile_FX_BI_Leon_Skill04':
            return _unavailable(spec,'Leon R projectile identity changed')
        if any(g.get('count',0) and g.get('packetName') in {'CmdSpawn','CmdProjectileCollision'} for g in gaps):
            return _unavailable(spec,'Leon R projectile contact stream has gaps')
        from .skill_projectile_contact_parents import projectile_contact_parents
        contact_parents,why=projectile_contact_parents(records,spawns,collisions,player,{102950})
        if why:return _unavailable(spec,why)

    wave = [set() for _ in records]
    uncertain = {}
    def unresolved(owners,reason):
        for i in owners or range(len(records)):uncertain.setdefault(i,set()).add(reason)
    # A collision links an explicit wave state/damage to its original cast;
    # collision alone does not prove that the requested application succeeded.
    unassigned_wave=[]
    for event in relevant:
        owners={i for i in range(len(records)) if within(i,event)}
        owners.update(i for i,at in contact_parents.get((event['tick'],event['targetObjectId']),[])
                      if at<command_order(event))
        if len(owners)!=1:
            unassigned_wave.append(event)
            unresolved(owners,'initial-wave-event-parent-unresolved');continue
        wave[next(iter(owners))].add((event['tick'],event['targetObjectId']))

    combat = [i for i,r in enumerate(records) if any(lo<=r['start']['tick']<hi for lo,hi in intervals)]
    complete = {i for i,r in enumerate(records) if r['complete'] and r['finish'].get('reason')==0}
    unknown = {i:sorted(uncertain[i])[0] if i in uncertain else 'initial-wave-cast-incomplete'
               for i in combat if not wave[i] and (i in uncertain or i not in complete)}
    from decoder.skill_partial_cast_lifetimes import user_cancelled_no_recorded_contact_indices
    from decoder.skill_projectile_active_end import projectile_active_end_records
    ends=projectile_active_end_records(terminals or [])
    pending=set();object_proofs={i:[] for i in combat};blocked={i:[] for i in unknown}
    if spawns is None or collisions is None or terminals is None:
        pending.update(unknown)
        for i in unknown:blocked[i].append('projectile-streams-unavailable')
    for shot in spawns or []:
        if shot.get('projectileCode')==102950 and shot.get('ownerPlayerObjectId') is None:
            for i in unknown:
                if command_order(shot) is None or command_order(records[i]['start'])<=command_order(shot):
                    pending.add(i);blocked[i].append('wave-projectile-owner-missing')
        if shot.get('ownerPlayerObjectId')!=player or shot.get('projectileCode')!=102950:continue
        at=command_order(shot)
        owners=[i for i in range(len(records)) if at is not None and within(i,shot)]
        if len(owners)!=1:
            for i in unknown:
                if at is None or command_order(records[i]['start'])<=at:
                    pending.add(i);blocked[i].append('projectile-emission-parent-unresolved')
            continue
        i=owners[0]
        if i not in unknown:continue
        oid=shot.get('projectileObjectId');end=ends.get(oid,{})
        ts=[t for t in terminals or [] if t.get('objectId')==oid and t.get('event') in {'CmdDestroy','CmdDestroyDelayStart'}]
        proof=dict(projectileObjectId=oid,spawnTick=shot['tick'],spawnOrder=list(at),
            terminals=[{k:t[k] for k in ('event','tick','wireOrder','wireCategory') if k in t} for t in ts])
        object_proofs[i].append(proof)
        if (not end.get('complete') or end['endTick']<shot['tick'] or not ts
            or any(command_order(t) is None or command_order(t)<=at or t['tick']<shot['tick'] for t in ts)):
            pending.add(i);blocked[i].append('projectile-terminal-missing-or-conflicting')
    for i in unknown:
        if any(command_order(e) is None or command_order(e)>=command_order(records[i]['start']) for e in unassigned_wave):
            pending.add(i);blocked[i].append('unassigned-wave-event')
    admitted=user_cancelled_no_recorded_contact_indices(records,wave,unknown,
        cancellation_reason='initial-wave-cast-incomplete',pending_continuations=pending,gaps=gaps)
    def evidence(i):
        r=records[i];f=r.get('finish')
        return dict(startTick=r['start']['tick'],startOrder=list(command_order(r['start'])),
            finishTick=f['tick'] if f else None,finishOrder=list(command_order(f)) if f else None,
            finishReason=f.get('reason') if f else None,emittedObjects=object_proofs[i])
    policy_evidence=[evidence(i) for i in admitted]
    blocked_evidence=[dict(evidence(i),reasons=sorted(set(blocked[i]+[unknown[i]]))) for i in unknown if i not in admitted]
    for i in admitted:unknown.pop(i)
    valid = [i for i in combat if i not in unknown]
    row = _result(spec,[wave[i] for i in valid],'leon-r-recorded-initial-wave-contact',
                  cast_ticks=[records[i]['start']['tick'] for i in valid])
    row = annotate_provisional_rate(row,policy)
    row.update(userPolicyMissEvidence=policy_evidence,userPolicyBlockedEvidence=blocked_evidence,
        userPolicyMissCastCount=len(admitted),userPolicyMissCastTicks=[records[i]['start']['tick'] for i in admitted],
        userPolicyMissAuthority='explicit-user-rule',
        observedCombatCastCount=len(combat),unresolvedCombatCastCount=len(unknown),
        unresolvedCastReasons=dict(Counter(unknown.values())),perUseCompletenessTracked=True,
        verifiedCompletionCredit=False,fullRequestedMetricComplete=False,
        nativeConsumerGraphVerified=False,candidateStateMapping=True,
        nearestCastUsed=False,fixedTimeWindowUsed=False,incompleteUsesCountedAsMisses=False,
        negativeOutcomesAreProvisional=True,normalFinishProvesAllEffectsClosed=False,
        targetCountsAreLowerBounds=True,completeTargetCountsAvailable=False,
        damageAmountInferred=False,unresolvedCastTicks=[records[i]['start']['tick'] for i in unknown],
        phaseScope='initial-wave-hit-only',
        interpretation='레온 R 최초 파도 타격의 명시 상태·피해·투사체 접촉만 집계하는 개발 중 지표.')
    details=[dict(castTick=records[i]['start']['tick'],reasons=sorted(uncertain[i]))
             for i in valid if i in uncertain]
    if details:row.update(incompleteContactEvidence=details,binaryPositiveWithIncompleteDetailsCount=len(details))
    if not valid and unknown:
        row.update(status='unresolved-evidence',attemptCount=None,hitCount=None,hitRate=None,
                   reason='All observed combat uses are unresolved for the initial wave')
    if unknown:row.update(wholeCombatHitRate=None,hitRateScope='classified-combat-casts-only')
    return row
