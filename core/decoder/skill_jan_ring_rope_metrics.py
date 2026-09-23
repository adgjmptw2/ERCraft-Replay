"""Jan R: explicit ring-line marker plus same-tick enemy application states."""
from collections import defaultdict, Counter

try:
    from .requested_skill_hit_rates import _result, _unavailable
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable


def recorded_object_end_valid(spawn_tick, destroys, delay_starts):
    """Immediate destruction needs no optional delayed-destruction prelude."""
    return (len(destroys) == 1 and destroys[0] >= spawn_tick and
            len(delay_starts) <= 1 and
            (not delay_starts or spawn_tick <= delay_starts[0] <= destroys[0]))


def jan_ring_rope_metric(spec, starts, finishes, states, summons, terminals,
                         player, teams, intervals, catalog, skill_rows,
                         summon_rows, state_rows, state_groups, game_terminals=None, gaps=None, raw_actions=None):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    fail = lambda reason: _unavailable(spec, reason)
    if (spec.get('characterCode'), spec.get('skillGroup'), spec.get('mode'), spec.get('unit')) != (35, 1035500, 'any', 'skill-cast'):
        return fail('얀 링 로프 접촉 전용 요청이 아님')
    definition = catalog['skillGroups'].get('1035500', {})
    if (definition.get('characterCode'), definition.get('skillId')) != (35, 'JanActive4'):
        return fail('정확한 얀 R 스킬 정의 불일치')
    expected = {
        1170: ('SummonArtifact', 'Jan_Skill04_Ring'),
        1171: ('SummonTrap', 'Jan_Skill04_Column'),
        1173: ('SummonServant', 'Jan_Skill04_RingLine'),
    }
    defs = {r['code']: r for r in summon_rows if r.get('code') in expected}
    if set(defs) != set(expected) or any((defs[c].get('objectType'), defs[c].get('prefabPath')) != expected[c] for c in expected):
        return fail('링·기둥·로프 생성 객체 정의 불일치')
    groups = {r['group']: r for r in state_groups if r.get('group') in {1035510, 1035520, 1035550}}
    codes = {r['code']: r['group'] for r in state_rows if r.get('code') is not None}
    if set(groups) != {1035510, 1035520, 1035550} or not {c for c, g in codes.items() if g == 1035510} >= {1035511, 1035512, 1035513} or not {c for c, g in codes.items() if g == 1035520} >= {1035521, 1035522, 1035523} or 1035551 not in codes or groups[1035510].get('stateType') != 'Airborne' or groups[1035520].get('stateType') != 'Slow':
        return fail('링 로프 접촉 상태 코드·그룹 정의 불일치')
    selected = sorted((s for s in starts if s['playerObjectId'] == player and s['skillGroup'] == 1035500), key=lambda s: s['tick'])
    if not selected:
        return fail('얀 R 시전이 관측되지 않음')
    casts, reason = exact_cast_lifetimes(selected, finishes, player)
    if reason:
        return fail(reason)
    by_tick = defaultdict(list)
    owned = [s for s in summons if s.get('ownerObjectId') == player and s.get('summonCode') in expected]
    for s in owned:
        if s.get('identityVerifiedAgainstGameDb') is not True:
            return fail('링·기둥·로프 소유자/게임DB 정체성 미검증')
        by_tick[s['tick']].append(s)
    ends = defaultdict(lambda: defaultdict(list))
    for t in terminals:
        if t.get('objectId') in {s['objectId'] for s in owned}:
            ends[t['objectId']][t['event']].append(t['tick'])
    from .skill_ordered_match_end import ordered_winner_match_end, precedes_recorded_match_end
    from .skill_wire_order import command_order
    game_end = ordered_winner_match_end(game_terminals, gaps) if game_terminals is not None and gaps is not None else None
    if gaps is not None and any(g.get('count', 0) and g.get('packetName') in {'CmdSpawn','CmdDestroy','CmdDestroyDelayStart','CmdAddState'} for g in gaps):
        game_end = None
    gameplay_ends = {}
    for obj in owned:
        oid = obj['objectId']; types = ends[oid]
        if recorded_object_end_valid(obj['tick'], types['CmdDestroy'], types['CmdDestroyDelayStart']):
            gameplay_ends[oid] = types['CmdDestroy'][0]
        elif (not types['CmdDestroy'] and len(types['CmdDestroyDelayStart']) <= 1 and game_end is not None
              and precedes_recorded_match_end(obj, game_end)
              and all(obj['tick'] <= t <= game_end['tick'] for t in types['CmdDestroyDelayStart'])):
            gameplay_ends[oid] = game_end['tick']
    contacts = []
    unresolved = {}
    forced_contacts = []
    subtype_unknown = set()
    all_used = set()
    for i, (start, finish) in enumerate(casts):
        objects = by_tick[start['tick']]
        ring = [s for s in objects if s['summonCode'] == 1170]
        columns = [s for s in objects if s['summonCode'] == 1171]
        ropes = [s for s in objects if s['summonCode'] == 1173]
        if len(ring) != 1 or len(columns) != 4 or len(ropes) != 4 or any(s['objectId'] in all_used for s in objects):
            return fail('한 R 시전과 링·기둥·로프 객체가 일대일 아님')
        all_used.update(s['objectId'] for s in objects)
        if any(s['objectId'] not in gameplay_ends for s in objects):
            unresolved[i] = 'ring-object-lifetime-incomplete'
        object_ids = {s['objectId'] for s in ropes}
        marker_ticks = defaultdict(set)
        for state in states:
            if state.get('event') == 'add' and state.get('casterObjectId') == player and state.get('stateCode') == 1035551 and state.get('targetObjectId') in object_ids:
                if start['tick'] <= state['tick'] and (state['targetObjectId'] not in gameplay_ends or state['tick'] <= gameplay_ends[state['targetObjectId']]):
                    marker_ticks[state['tick']].add(state['targetObjectId'])
        enemy_contacts = set()
        for state in states:
            if state.get('event') != 'add' or state.get('casterObjectId') != player or state.get('stateCode') not in {1035511, 1035512, 1035513, 1035521, 1035522, 1035523}:
                continue
            if state.get('tick') not in marker_ticks or state.get('targetObjectId') not in teams or teams[state['targetObjectId']] == teams[player]:
                continue
            enemy_contacts.add((state['tick'], state['targetObjectId']))
        if enemy_contacts:
            unresolved.pop(i, None)  # Proven positive survives incomplete retirement.
        force_ticks = set()
        actions_complete=raw_actions is not None and gaps is not None and not any(g.get('count',0) and g.get('packetName') in {'CmdPlaySkillAction','CmdPlaySkillActionWithTargets'} for g in gaps)
        if actions_complete:
            force_ticks = {a['tick'] for a in raw_actions if a.get('sourceObjectId') in object_ids
                and a.get('actionNo') in {11,12} and a.get('skillIdCode') == 492
                and a.get('wireStatus') == 'decoded-exact-CmdPlaySkillActionWithTargets'
                and a.get('sourceObjectId') in marker_ticks.get(a['tick'], set())}
        else:
            subtype_unknown.add(i)
        forced_contacts.append({c for c in enemy_contacts if c[0] in force_ticks})
        contacts.append(enemy_contacts)
    if set(s['objectId'] for s in owned) != all_used:
        return fail('R 사용에 연결되지 않은 링·기둥·로프 객체가 남음')
    combat = [i for i, (s, _) in enumerate(casts) if any(l <= s['tick'] < r for l, r in intervals)]
    valid = [i for i in combat if i not in unresolved]
    selected_contacts = [contacts[i] for i in valid]
    row = _result(spec, selected_contacts, 'explicit-ring-rope-marker-state-and-same-tick-enemy-state',
                  cast_ticks=[casts[i][0]['tick'] for i in valid])
    from .skill_development_effect_metrics import annotate_provisional_rate, development_policy
    row = annotate_provisional_rate(row, development_policy())
    row.update(positiveDoesNotRequireFinalDestroy=True, targetCountsAreLowerBounds=True, completeTargetCountsAvailable=False, perUseCompletenessTracked=True, unresolvedCombatCastCount=sum(i in unresolved for i in combat),
               unresolvedCastReasons=dict(Counter(unresolved[i] for i in combat if i in unresolved)),
               incompleteUsesCountedAsMisses=False,
               knownRingObjectCount=len(casts), knownRopeObjectCount=4 * len(casts),
               fixedDelayJoinUsed=False, damageFallbackUsed=False,
               actualMatchEndClosedObjectCount=sum(o['objectId'] in gameplay_ends and not ends[o['objectId']]['CmdDestroy'] for o in owned),
               syntheticRemovalCreated=False,
               interpretation='R 시전별 소유 로프 객체의 1035551 marker와 같은 tick의 실제 적 Knockback/Slow 상태를 연결. 상태 없는 사용은 실패로 추정하지 않음.')
    unknown_forced = {i for i in combat if i in subtype_unknown or
                      (not forced_contacts[i] and any(o['objectId'] not in gameplay_ends for o in owned if o['tick'] == casts[i][0]['tick']))}
    selected_forced = [i for i in combat if i not in unknown_forced]
    child_spec = dict(spec, metricId=spec['metricId']+':forced-rope-collision', label='R 밀려서 로프 충돌')
    child = _result(child_spec, [forced_contacts[i] for i in selected_forced],
                    'recorded-owned-rope-action-11-12-and-admitted-enemy-state',
                    cast_ticks=[casts[i][0]['tick'] for i in selected_forced])
    child = annotate_provisional_rate(child, development_policy())
    child.update(perUseCompletenessTracked=True, unresolvedCombatCastCount=len(unknown_forced),
                 unresolvedCastTicks=[casts[i][0]['tick'] for i in unknown_forced],
                 observedCombatCastCount=len(combat), incompleteUsesCountedAsMisses=False,
                 targetCountsAreLowerBounds=True, completeTargetCountsAvailable=False,
                 fixedTimeWindowUsed=False, nearestCastUsed=False,
                 interpretation='소유 로프 행동11/12와 같은 tick의 표식·적 상태 적용으로 확인한 강제 충돌. 개발 중.')
    if not selected_forced and unknown_forced:
        child.update(status='unresolved-evidence',attemptCount=None,hitCount=None,hitRate=None)
    row['phaseMetrics'] = {'forced-rope-collision': child}
    return row

