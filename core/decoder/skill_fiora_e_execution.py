"""E1 collision marker, completed damage phase and switch-state lineage."""
from bisect import bisect_right
from collections import Counter, defaultdict
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order as order
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order as order
    from skill_action_stage_evidence import load_exact_skill_ids


def fiora_e_execution(spec, starts, finishes, actions, damages, states, player,
                      teams, intervals, catalog, skill_rows, effect_rows, gaps,
                      skill_ids=None):
    selected = [s for s in starts if s['playerObjectId'] == player and s['skillGroup'] == 1003400]
    combat = lambda s: any(a <= s['tick'] < b for a, b in intervals)
    diag = dict(observedCastCount=len(selected), observedCombatCastCount=sum(map(combat, selected)))
    def fail(reason):
        return {**_unavailable(spec, reason), **diag}
    if (spec['characterCode'], spec['skillGroup'], spec['mode'], spec['unit']) != (3, 1003400, 'any', 'skill-cast'):
        return fail('unsupported Fiora E1 execution')
    if any(x is None for x in (actions, damages, states, gaps)):
        return fail('missing collision/damage/switch command stream')
    required = {'CmdStartSkill', 'CmdFinishSkill', 'CmdPlaySkillAction', 'CmdDamage', 'CmdAddState', 'CmdAddStateExtended'}
    if any(g.get('count', 0) and g.get('packetName') in required for g in gaps):
        return fail('incomplete Fiora E1 execution commands')
    ids = load_exact_skill_ids() if skill_ids is None else skill_ids
    fx = [e for e in effect_rows if e['code'] == 1000000]
    code_groups = {s['code']: s['group'] for s in skill_rows}
    if (ids.get('FioraActive3_1') != 67 or catalog['skillGroups'].get('1003400', {}).get('skillId') != 'FioraActive3_1'
            or len(fx) != 1 or (fx[0].get('effectPrefabName'), fx[0].get('soundName')) != ('FX_BI_Common_Normal_Hit', 'hitOneHandSword_r1')
            or any(s['skillIdCode'] != 67 or code_groups.get(s['skillCode']) != 1003400 for s in selected)):
        return fail('pinned Fiora E1 identity mismatch')
    records, why = ordered_cast_records(selected, finishes, player, allow_same_tick_finishes=True)
    if why:
        return fail(why)
    markers = [a for a in actions if a['sourceObjectId'] == player and a['skillIdCode'] == 67 and a['actionNo'] == 11]
    switches = [s for s in states if s.get('event') == 'add' and s['targetObjectId'] == player and s['stateCode'] == 1003401]
    if any(order(a) is None or a.get('wireStatus') != 'decoded-exact-CmdPlaySkillAction' for a in markers):
        return fail('missing exact E1 collision-marker order')
    if any(order(s) is None for s in switches):
        return fail('missing exact E1 switch-state order')
    # Explicit starts keep other manual executions in attribution. A missing
    # finish is an open competing execution, never a fabricated short window.
    others = [s for s in starts if s['playerObjectId'] == player and s['skillGroup'] != 1003400]
    finish_orders = defaultdict(list)
    for finish in finishes:
        if finish['playerObjectId'] == player:
            at = order(finish)
            if at is not None:
                finish_orders[finish['skillIdCode']].append(at)
    for values in finish_orders.values():
        values.sort()
    competing_intervals = []
    unordered_competitor = False
    for start in others:
        lo = order(start)
        if lo is None:
            unordered_competitor = True
            continue
        ends = finish_orders[start['skillIdCode']]
        index = bisect_right(ends, lo)
        competing_intervals.append((lo, ends[index] if index < len(ends) else None))

    def competing(at):
        return unordered_competitor or any(
            lo < at and (hi is None or hi >= at) for lo, hi in competing_intervals
        )
    from .skill_action_stage_evidence import development_terminal_damage
    terminal_uses=[]
    contacts, ticks, details, evidence = [], [], [], []
    unknown = {}; cancelled = []; assigned_markers = set(); assigned_switches = set()
    for i, rec in enumerate(records):
        s, end = rec['start'], rec['finish']
        if not rec['complete']:
            assigned_markers.update(order(a) for a in markers if order(s) < order(a) and (end is None or order(a) < order(end)))
            assigned_switches.update(order(t) for t in switches if order(s) < order(t) and (end is None or order(t) < order(end)))
            unknown[i] = rec['reason']; continue
        lo, hi = order(s), order(end)
        aa = [a for a in markers if lo < order(a) < hi]
        ss = [t for t in switches if lo < order(t) < hi]
        assigned_markers.update(order(a) for a in aa); assigned_switches.update(order(t) for t in ss)
        if len(aa) > 1 or len(ss) > 1:
            unknown[i] = 'ambiguous-collision-or-switch'; continue
        if end['reason'] == 3 and not ss:
            # The hit coroutine is cancelled with its parent. A pending
            # collision callback is not an executed attack or a missed one.
            pending = [d for d in damages if d['attackerObjectId'] == player and d['damageType'] == 2
                       and s['tick'] <= d['tick'] <= end['tick'] and order(d) is not None
                       and (order(aa[0]) if aa else lo) < order(d) < hi]
            if any(d['attackerObjectId'] == player and d['damageType'] == 2 and s['tick'] <= d['tick'] <= end['tick']
                   and order(d) is None for d in damages):
                unknown[i] = 'cancelled-with-unordered-damage'; continue
            if pending:
                unknown[i] = 'cancelled-with-unclosed-damage-phase'; continue
            cancelled.append(i); continue
        if end['reason'] not in (0, 3):
            unknown[i] = 'unsupported-execution-end'; continue
        ds = []
        if not aa and not ss and end['reason'] == 0:
            method = 'normal-dash-without-collision-callback'
        elif len(aa) == len(ss) == 1 and order(aa[0]) < order(ss[0]) and ss[0]['casterObjectId'] == 0:
            marker, switch = aa[0], ss[0]
            candidates = [d for d in damages if d['attackerObjectId'] == player and (d['effectCode'] == 1000000 or development_terminal_damage(d))
                          and d['damageType'] == 2 and d['tick'] == switch['tick']]
            if any(order(d) is None for d in candidates):
                unknown[i] = 'missing-damage-phase-order'; continue
            candidates = [d for d in candidates if order(marker) < order(d) < order(switch)]
            if len(candidates) != 1:
                unknown[i] = 'missing-or-ambiguous-synchronous-damage'; continue
            d = candidates[0]
            if competing(order(d)):
                unknown[i] = 'competing-manual-execution'; continue
            if type(d.get('damageIsNull')) is not bool:
                unknown[i] = 'missing-damage-discriminator'; continue
            if d['targetObjectId'] in teams and teams[d['targetObjectId']] != teams[player]:
                ds = [dict(hitTick=d['tick'], targetObjectId=d['targetObjectId'], phase='dash-contact-attack',
                           damageOrder=d['wireOrder'], collisionMarkerOrder=marker['wireOrder'], switchOrder=switch['wireOrder'])]
            if development_terminal_damage(d) and combat(s):terminal_uses.append(s['tick'])
            method = 'completed-single-target-attack-and-switch'
        else:
            unknown[i] = 'incomplete-collision-attack-switch'; continue
        if combat(s):
            contacts.append({(d['hitTick'], d['targetObjectId']) for d in ds}); ticks.append(s['tick']); details.append(ds)
            evidence.append(dict(startOrder=s['wireOrder'], finishOrder=end['wireOrder'], closureMethod=method,
                                 collisionMarkerOrder=aa[0]['wireOrder'] if aa else None,
                                 switchOrder=ss[0]['wireOrder'] if ss else None))
    if assigned_markers != {order(a) for a in markers} or assigned_switches != {order(s) for s in switches}:
        return fail('orphan collision/switch prevents complete E1 attribution')
    reasons = Counter(why for i, why in unknown.items() if combat(records[i]['start']))
    diag.update(unresolvedCombatCastCount=sum(reasons.values()), unresolvedCastReasons=dict(reasons),
                unresolvedNonCombatCastCount=sum(not combat(records[i]['start']) for i in unknown),
                unresolvedNonCombatCastReasons=dict(Counter(why for i, why in unknown.items() if not combat(records[i]['start']))),
                cancelledBeforeAttackCount=sum(combat(records[i]['start']) for i in cancelled),
                cancelledBeforeAttackCastTicks=[records[i]['start']['tick'] for i in cancelled if combat(records[i]['start'])],
                perUseCompletenessTracked=True, incompleteUsesCountedAsMisses=False)
    if diag['observedCombatCastCount'] and not contacts and not diag['cancelledBeforeAttackCount']:
        return fail('no complete E1 outcomes')
    result = _result(spec, contacts, 'static-Fiora-E1-collision-damage-switch-execution', cast_ticks=ticks)
    result.update(diag, contactDetailsByAttempt=details, executionEvidenceByAttempt=evidence,
                  evidenceReview='deliverables/fiora-e-execution-static-proof-v1.json',
                  effectCodeAloneUsed=False, fixedActionDelayUsed=False, actionTargetUsedAsEnemyHit=False)
    if terminal_uses:
        from .skill_development_effect_metrics import annotate_provisional_rate,development_policy
        result=annotate_provisional_rate(result,development_policy())
        result.update(terminalDamageCallbackUseTicks=terminal_uses,exactDamageAttributionEstablished=False,
            developmentAssumption='처치 피해의 effectCode=0을 배타적인 공격·전환 콜백 구간에서만 잠정 적중으로 집계')
    return result
