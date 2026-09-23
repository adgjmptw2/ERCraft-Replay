"""Owned Nina Q execution, including death during its post-hit recovery.

Pinned SkillGroup1040250 has zero casting/reservation time. Its Process calls
DamageProcess synchronously and returns without yielding; only FinishDelay
waits 0.2s. A later interrupted finish cannot erase the completed attack.
"""
from collections import defaultdict, Counter

try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order as order
    from .skill_summon_ownership import live_summon_owner_resolver
    from .skill_action_stage_evidence import load_exact_skill_ids
    from .skill_attempt_timing import exact_outcome
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order as order
    from skill_summon_ownership import live_summon_owner_resolver
    from skill_action_stage_evidence import load_exact_skill_ids
    from skill_attempt_timing import exact_outcome


def chloe_q_execution_metric(spec, starts, finishes, player, teams, intervals,
                             catalog, inputs):
    parent_mode = spec['skillGroup'] == 1040200
    roots = [s for s in starts if s['playerObjectId'] == player and s['skillGroup'] == 1040200]
    combat = lambda s: any(a <= s['tick'] < b for a, b in intervals)
    diag = dict(observedCastCount=len(roots), observedCombatCastCount=sum(map(combat, roots)))
    fail = lambda why: {**_unavailable(spec, why), **diag}
    if spec['characterCode'] != 40 or spec['skillGroup'] not in (1040200, 1040250) or spec['mode'] != 'any' or spec['unit'] != 'skill-cast':
        return fail('unsupported Chloe Q scope')
    if any(inputs.get(k) is None for k in ('nonPlayerSkillStarts', 'summons', 'terminals', 'damages', 'gaps')):
        return fail('missing complete owned Nina Q evidence')
    ids = load_exact_skill_ids()
    if ids.get('ChloeActive1') != 560 or ids.get('NinaActive1') != 571:
        return fail('pinned Q wire IDs differ')
    if any(catalog['skillGroups'].get(str(g), {}).get('skillId') != name for g, name in ((1040200, 'ChloeActive1'), (1040250, 'NinaActive1'))):
        return fail('pinned Q game data identities differ')
    if any(s['skillIdCode'] != 560 or s['skillCode'] not in range(1040201, 1040206) for s in roots):
        return fail('parent Q wire/code mismatch')
    required = {'CmdStartSkill', 'CmdFinishSkill', 'CmdDamage', 'CmdSpawn', 'CmdSpawnBatch', 'CmdDestroy'}
    if any(g.get('count', 0) and (g.get('packetName') in required or str(g.get('packetName', '')).startswith('SummonSnapshot:')) for g in inputs['gaps']):
        return fail('incomplete Q execution evidence')
    parents, why = ordered_cast_records(roots, finishes, player, allow_same_tick_finishes=True)
    if why:
        return fail(why)
    resolve = live_summon_owner_resolver(inputs['summons'], inputs['terminals'], set(teams))
    by_actor = defaultdict(list)
    for child in inputs['nonPlayerSkillStarts']:
        if child['skillIdCode'] != 571 and child['skillCode'] != 1040251:
            continue
        owner, path, why = resolve(child['sourceObjectId'], child['tick'])
        if why:
            return fail('Nina Q owner: ' + why)
        if owner != player:
            continue
        if path != [1191] or child['skillIdCode'] != 571 or child['skillCode'] != 1040251:
            return fail('Nina Q actor/code mismatch')
        by_actor[child['sourceObjectId']].append({**child, 'playerObjectId': child['sourceObjectId'], 'skillGroup': 1040250})
    children = []
    for actor, rows in by_actor.items():
        records, why = ordered_cast_records(rows, finishes, actor, allow_same_tick_finishes=True)
        if why:
            return fail('child Q lifetime: ' + why)
        children.extend(records)
    children.sort(key=lambda r: order(r['start']))
    if not parent_mode:
        diag = dict(observedCastCount=len(children), observedCombatCastCount=sum(combat(r['start']) for r in children))
    child_parent, parent_children = {}, defaultdict(list)
    for i, child in enumerate(children):
        s = child['start']
        candidates = [j for j, r in enumerate(parents) if order(r['start']) < order(s) and
                      r['finish'] is not None and order(s) < order(r['finish'])]
        if len(candidates) != 1:
            return fail('owned Nina Q has no unique ordered parent request')
        child_parent[i] = candidates[0]
        parent_children[candidates[0]].append(i)
    if any(len(v) > 1 for v in parent_children.values()):
        return fail('multiple Nina Q executions in one parent request')
    contacts = [set() for _ in children]
    damage_rows = [[] for _ in children]
    for d in inputs['damages']:
        if d.get('effectCode') != 1040011:
            continue
        owner, path, why = resolve(d['attackerObjectId'], d['tick'])
        if why:
            return fail('Q effect actor ownership is missing: ' + why)
        if owner != player:
            continue
        if path != [1191] or d['attackerObjectId'] not in by_actor:
            return fail('owned Q effect has no explicit Nina Q start')
        # Atomic DamageProcess is in the same recorded frame as child start.
        candidates = [i for i, r in enumerate(children) if r['start']['playerObjectId'] == d['attackerObjectId'] and
                      r['start']['tick'] == d['tick'] and order(d) is not None and order(r['start']) < order(d) and
                      (r['finish'] is None or order(d) < order(r['finish']))]
        if len(candidates) != 1 or type(d.get('damageIsNull')) is not bool:
            return fail('Q damage lacks one exact synchronous child execution')
        i = candidates[0]
        damage_rows[i].append(d)
        target = d['targetObjectId']
        if target in teams and teams[target] != teams[player]:
            contacts[i].add((d['tick'], target))
    valid, unknown = {}, {}
    for i, r in enumerate(children):
        s, end = r['start'], r['finish']
        if not r['complete']:
            unknown[i] = 'child Q completion stream remains open'
        elif end['reason'] == 0 or (end['reason'] == 3 and (end['tick'] > s['tick'] or damage_rows[i])):
            valid[i] = r
        else:
            unknown[i] = 'child Q may be interrupted before synchronous execution'
    uses, use_unknown, nonexecuted = [], {}, []
    if parent_mode:
        for j, r in enumerate(parents):
            if not r['complete']:
                use_unknown[j] = 'parent Q lifetime remains open'
            elif not parent_children[j]:
                if r['finish']['reason'] in (0, 3):
                    # Q requests Nina.UseSkill synchronously, with no reserved
                    # cast. No actual child start means no emitted Nina attack.
                    nonexecuted.append(r)
                else:
                    use_unknown[j] = 'parent Q stopped without verified execution'
            else:
                i = parent_children[j][0]
                if i in unknown:
                    use_unknown[j] = unknown[i]
                else:
                    uses.append((r['start'], i))
        source_uses = parents
    else:
        uses = [(r['start'], i) for i, r in valid.items()]
        use_unknown, source_uses = unknown, children
    selected = [(s, i) for s, i in uses if combat(s)]
    row = _result(spec, [contacts[i] for s, i in selected], 'static-Chloe-Q-synchronous-owned-Nina-execution',
                  cast_ticks=[s['tick'] for s, i in selected])
    reasons = Counter(why for i, why in use_unknown.items() if combat(source_uses[i]['start']))
    row.update(diag, outcomes=[exact_outcome(s['tick'], children[i]['start']['tick'], contacts[i]) for s, i in selected],
               unresolvedCombatCastCount=sum(reasons.values()), unresolvedCastReasons=dict(reasons),
               unresolvedNonCombatCastCount=sum(not combat(source_uses[i]['start']) for i in use_unknown),
               nonExecutedCastCount=sum(combat(r['start']) for r in nonexecuted),
               nonExecutedAllCastCount=len(nonexecuted),
               nonExecutionEvidence=[r for r in nonexecuted if combat(r['start'])],
               postExecutionCancelledCount=sum(children[i]['finish']['reason'] == 3 for s, i in selected),
               contactDetailsByAttempt=[[dict(hitTick=t, targetObjectId=who, ninaObjectId=children[i]['start']['playerObjectId']) for t, who in sorted(contacts[i])] for s, i in selected],
               executionEvidenceByAttempt=[dict(parent=parents[child_parent[i]], child=children[i], damageCommands=damage_rows[i]) for s, i in selected],
               perUseCompletenessTracked=True, incompleteUsesCountedAsMisses=False, damageAmountInferred=False,
               denominatorMeaning='Actual recorded Nina Q attacks; parent requests with no child execution separately counted',
               evidenceReview='deliverables/chloe-q-execution-static-proof-v1.json', fixedDurationWindowUsed=False)
    return row
