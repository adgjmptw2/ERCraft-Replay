"""Ian possession E: synchronous first strike and separately recorded pull CC.

The native pull coroutine can survive CmdFinishSkill. Its float32 duration
provides an upper bound, not a guessed grace window or a synthetic command.
Unidentified effect-0 damage is never silently attributed to the pull.
"""
import struct
from collections import Counter
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order as order
    from .skill_attempt_timing import exact_outcome
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order as order
    from skill_attempt_timing import exact_outcome

def f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]

def pull_upper_bound_tick(finish_tick, duration):
    """Normal parent completion is no earlier than synchronous child launch.

    Reproduce the native float32 clock and strict `now > start+duration`
    condition. Using the later finish time only widens the proven bound.
    """
    clock = lambda t: f32(f32(t) * f32(1 / 60))
    deadline = f32(clock(finish_tick) + f32(duration))
    tick = finish_tick
    while clock(tick) <= deadline:
        tick += 1
    return tick  # First frame at which this producer cannot emit.

def ian_possession_e_execution(spec, starts, finishes, player, teams, intervals,
                               catalog, inputs, state_rows):
    selected = [s for s in starts if s['playerObjectId'] == player and s['skillGroup'] == 1063420]
    combat = lambda e: any(a <= e['tick'] < b for a, b in intervals)
    diag = dict(observedCastCount=len(selected), observedCombatCastCount=sum(map(combat, selected)))
    fail = lambda why: {**_unavailable(spec, why), **diag}
    required = ('actions', 'damages', 'states', 'stateScripts', 'gaps')
    if player not in teams or any(inputs.get(k) is None for k in required):
        return fail('missing Ian E execution/state streams')
    if (spec['characterCode'], spec['skillGroup'], spec['mode'], spec['unit']) != (63, 1063420, 'any', 'skill-cast'):
        return fail('unsupported Ian possession E scope')
    group = catalog.get('skillGroups', {}).get('1063420', {})
    state = [s for s in state_rows if s.get('code') == 1063401]
    if (group.get('skillId') != 'LyanhPossessionActive3' or len(state) != 1
            or state[0].get('duration') != 0.115
            or any(s['skillIdCode'] != 948 or s['skillCode'] not in range(1063421, 1063426) for s in selected)):
        return fail('pinned Ian possession E identity/duration mismatch')
    packets = {'CmdStartSkill', 'CmdFinishSkill', 'CmdPlaySkillAction', 'CmdDamage', 'CmdAddState', 'CmdAddStateExtended', 'CmdStartStateSkill'}
    if any(g.get('count', 0) and g.get('packetName') in packets for g in inputs['gaps']):
        return fail('incomplete Ian E command stream')
    records, why = ordered_cast_records(selected, finishes, player, allow_same_tick_finishes=True)
    if why:
        return fail(why)
    actions = [a for a in inputs['actions'] if a['sourceObjectId'] == player and a['skillIdCode'] == 948 and a['actionNo'] == 1]
    damages = [d for d in inputs['damages'] if d['attackerObjectId'] == player]
    adds = [s for s in inputs['states'] if s.get('event') == 'add' and s.get('stateCode') == 1063401 and s.get('casterObjectId') == player]
    scripts = [s for s in inputs['stateScripts'] if s.get('event') == 'CmdStartStateSkill' and s.get('skillIdCode') == 10 and s.get('stateGroup') == 1063400 and s.get('casterObjectId') == player]
    if any(order(x) is None for x in actions + adds + scripts):
        return fail('missing exact Ian E action/state order')
    enemy = lambda target: target in teams and teams[target] != teams[player]
    # An actual later command proves that the retained stream extends beyond
    # the native deadline. A cache/file end alone is not a producer terminal.
    observed_through = max((r.get('tick', -1) for k in ('states', 'stateScripts', 'damages', 'actions') for r in inputs[k]), default=-1)
    uses = []; unknown = {}; range_unknown = {}; pull_unknown = {}
    for i, rec in enumerate(records):
        start, finish = rec['start'], rec['finish']
        aa = [a for a in actions if order(start) < order(a) and (finish is None or order(a) < order(finish))]
        if rec['complete'] and finish is not None and not aa:
            unknown[i] = range_unknown[i] = pull_unknown[i] = 'missing-recorded-skill-action'
            continue
        if not rec['complete'] or finish is None or finish.get('reason') not in set(range(15))|{16,17} or len(aa) != 1:
            unknown[i] = range_unknown[i] = pull_unknown[i] = 'incomplete-or-ambiguous-possession-E-execution'
            continue
        marker = aa[0]; bound = pull_upper_bound_tick(finish['tick'], state[0]['duration'])
        u = dict(index=i, start=start, finish=finish, marker=marker, upperBoundExclusive=bound, first=[], pulls=[], ambiguousDamages=[])
        uses.append(u)
        if finish.get('reason')!=0:
            unknown[i]=range_unknown[i]=pull_unknown[i]='cast-not-normally-complete'
        if observed_through < bound:
            unknown[i] = pull_unknown[i] = 'retained-command-stream-does-not-reach-native-pull-bound'
    # The same skill script may be reused. Overlap is not resolved by nearest
    # cast, matching level, or a shared caster. Keep all affected uses unknown.
    for u in uses:
        for rec in records:
            if rec['start'] is u['start']:
                continue
            if u['start']['tick'] <= rec['start']['tick'] < u['upperBoundExclusive'] and order(rec['start']) > order(u['start']):
                unknown[u['index']] = pull_unknown[u['index']] = 'overlapping-possession-E-producers'
    for d in damages:
        if d['effectCode'] != 1063421:
            continue
        owners = [u for u in uses if d['tick'] == u['marker']['tick'] and order(d) is not None and order(u['marker']) < order(d) < order(u['finish'])]
        if len(owners) != 1:
            return fail('dedicated first-strike effect outside unique synchronous action callback')
        u = owners[0]
        if d.get('damageType') != 2 or type(d.get('damageIsNull')) is not bool:
            unknown[u['index']] = range_unknown[u['index']] = 'first-strike-damage-discriminator-mismatch'
        else:
            u['first'].append(d)
    for s in adds:
        ss = [x for x in scripts if x['tick'] == s['tick'] and x['sourceObjectId'] == s['targetObjectId'] and order(s) < order(x)]
        if len(ss) != 1:
            return fail('pull add lacks unique same-frame recorded knockback script')
        script = ss[0]
        owners = [u for u in uses if u['start']['skillCode'] == script['skillCode'] and order(u['marker']) < order(s) and s['tick'] < u['upperBoundExclusive']]
        if len(owners) != 1:
            return fail('pull state lacks unique native-bounded E producer')
        owners[0]['pulls'].append(dict(add=s, script=script))
    for u in uses:
        for d in damages:
            if (d.get('effectCode') == 0 and d.get('damageType') == 2 and enemy(d['targetObjectId'])
                    and u['marker']['tick'] <= d['tick'] < u['upperBoundExclusive']
                    and (order(d) is None or order(d) > order(u['marker']))):
                # A successful pull proves contact, but a missing pull state
                # cannot exclude damage against an immune target. Effect 0
                # is shared, so retain ambiguity rather than count a miss.
                u['ambiguousDamages'].append(d)
        if not any(enemy(d['targetObjectId']) for d in u['first']) and not any(enemy(p['add']['targetObjectId']) for p in u['pulls']) and u['ambiguousDamages']:
            unknown[u['index']] = 'shared-effect-zero-cannot-exclude-second-strike-hit'
    def contacts(u, phase):
        first = {(d['tick'], d['targetObjectId']) for d in u['first'] if enemy(d['targetObjectId'])}
        pull = {(p['add']['tick'], p['add']['targetObjectId']) for p in u['pulls'] if enemy(p['add']['targetObjectId'])}
        return first if phase == 'firstRange' else pull if phase == 'actualPull' else first | pull
    def project(phase, unresolved):
        from .skill_partial_cast_lifetimes import retain_cancelled_hits,annotate_cancelled_hits
        from .skill_lifecycle_result_policy import settle_cancelled_bounded_phase
        closed_cancelled=[]
        if phase == 'actualPull':
            for u in uses:
                # The action callback starts the non-cancelled coroutine
                # synchronously. A finish in a later frame cannot precede it.
                if settle_cancelled_bounded_phase(unresolved,u['index'],
                        launch_completed=u['marker']['tick'] < u['finish']['tick'],
                        survives_parent_finish=True,upper_bound=u['upperBoundExclusive'],
                        observed_through=observed_through):
                    closed_cancelled.append(u['start']['tick'])
        lifetimes=[(r['start'],r['finish']['tick'] if r['finish'] else None) for r in records]
        phase_contacts=[set() for _ in records]
        for u in uses:phase_contacts[u['index']]=contacts(u,phase)
        recovered=retain_cancelled_hits(unresolved,phase_contacts,lifetimes,finishes,player)
        chosen = [u for u in uses if combat(u['start']) and u['index'] not in unresolved]
        cc = [contacts(u, phase) for u in chosen]
        r = _result(spec, cc, 'static-Ian-E-first-action-and-native-bounded-pull-state', cast_ticks=[u['start']['tick'] for u in chosen])
        r['outcomes'] = [exact_outcome(u['start']['tick'], u['marker']['tick'], c) for u, c in zip(chosen, cc)]
        r.update(unresolvedCombatCastCount=sum(combat(records[i]['start']) for i in unresolved),
                 unresolvedCastReasons=dict(Counter(v for i, v in unresolved.items() if combat(records[i]['start']))),
                 unresolvedUseEvidence=[dict(startTick=records[i]['start']['tick'],
                     startOrder=records[i]['start'].get('wireOrder'),reasons=[reason])
                     for i,reason in unresolved.items() if combat(records[i]['start'])],
                 attemptDefinition='executed parent E cast; phase success is measured per E cast')
        if closed_cancelled:
            r['independentlyClosedCancelledPhaseCastTicks']=closed_cancelled
        return annotate_cancelled_hits(r,recovered,lifetimes,[u['index'] for u in chosen])
    result = project('any', unknown)
    result.update(diag, phaseMetrics=dict(firstRange=project('firstRange', range_unknown), actualPull=project('actualPull', pull_unknown)),
                  unresolvedNonCombatCastCount=sum(not combat(records[i]['start']) for i in unknown),
                  perUseCompletenessTracked=True, incompleteUsesCountedAsMisses=False,
                  evidenceReview='deliverables/ian-possession-e-execution-static-proof-v1.json',
                  executionEvidence=uses, fallbackUsed=False, staticNativeDurationBound=True,
                  sharedEffectZeroAttributedByTime=False)
    return result
