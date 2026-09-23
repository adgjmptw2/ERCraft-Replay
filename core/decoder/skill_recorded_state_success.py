"""Recorded state admission per exact cast, independent of damage success.

Profiles opt in only for synchronous state application during the cast.
Missing/ambiguous evidence never triggers a damage or nearest-cast fallback.
"""
from collections import Counter, defaultdict

from .requested_skill_hit_rates import _result, _unavailable
from .skill_partial_cast_lifetimes import ordered_cast_records
from .skill_wire_order import command_order
from .skill_development_effect_metrics import development_policy, annotate_provisional_rate
from .skill_recorded_servant_target import preceding_servant_records


def recorded_state_success(spec, starts, finishes, states, player, teams,
                           intervals, state_codes, gaps, objects=None):
    policy = development_policy()
    required = {'CmdStartSkill', 'CmdFinishSkill', 'CmdAddState', 'CmdAddStateExtended'}
    if not policy['enabled']:
        return _unavailable(spec, 'Recorded state success mapping requires development policy')
    if any(x is None for x in (starts, finishes, states, gaps)):
        return _unavailable(spec, 'Recorded state success requires cast and state streams')
    if any(g.get('count', 0) and g.get('packetName') in required for g in gaps):
        return _unavailable(spec, 'Recorded state success input stream has decode gaps')
    if player not in teams:
        return _unavailable(spec, 'Recorded state success source team unavailable')
    own = [s for s in starts if s.get('playerObjectId') == player and s.get('skillGroup') == spec['skillGroup']]
    records, reason = ordered_cast_records(own, finishes, player, allow_same_tick_finishes=True)
    if reason:
        return _unavailable(spec, reason)
    events = [s for s in states if s.get('event') == 'add' and
              s.get('casterObjectId') == player and s.get('stateCode') in state_codes]
    if any(command_order(s) is None for s in events):
        return _unavailable(spec, 'Recorded state success needs exact command order')
    if len({command_order(s) for s in events}) != len(events):
        return _unavailable(spec, 'Recorded state success has duplicate command orders')
    contacts = [set() for _ in records]
    uncertain = {}
    unassigned = 0
    non_player_events = 0
    servant_events = []
    object_records = defaultdict(list)
    for obj in objects or []:
        if command_order(obj) is not None:
            object_records[obj.get('objectId')].append(obj)
    for event in events:
        target = event.get('targetObjectId')
        if target in teams and teams[target] == teams[player]:
            continue
        # 12.3 Blis.Common.ObjectType: PlayerCharacter=2, Monster=3.
        # Require an actual preceding object record, never infer an animal
        # merely because its identity is absent from the player/team map.
        typed = {o.get('objectType') for o in object_records[target]
                 if command_order(o) <= command_order(event)}
        if target not in teams and typed == {3}:
            non_player_events += 1
            continue
        servant = preceding_servant_records(objects, target, event, gaps) if target not in teams else None
        if servant:
            servant_events.append(dict(state=dict(event), objectRecords=servant))
            continue
        owners = [i for i, r in enumerate(records) if
                  command_order(r['start']) <= command_order(event) and
                  (r['finish'] is None or command_order(event) <= command_order(r['finish']))]
        if len(owners) != 1:
            unassigned += 1
            for i in owners:
                uncertain[i] = 'ambiguous-state-cast-owner'
            continue
        i = owners[0]
        if target not in teams:
            uncertain[i] = 'state-target-team-unavailable'
        elif event['tick'] < records[i]['start']['tick'] or (records[i]['finish'] is not None and event['tick'] > records[i]['finish']['tick']):
            uncertain[i] = 'state-clock-order-conflict'
        else:
            contacts[i].add((event['tick'], target))
    combat = [i for i, r in enumerate(records) if any(lo <= r['start']['tick'] < hi for lo, hi in intervals)]
    unknown = {i: uncertain[i] for i in combat if i in uncertain}
    for i in combat:
        if i in unknown or contacts[i]:
            continue
        r = records[i]
        if not r['complete'] or r['finish'].get('reason') != 0:
            unknown[i] = 'cast-not-normally-complete'
    # Orphan events cannot be assigned to every previous use. They remain an
    # explicit mapping gap; do not certify negative outcomes in their presence.
    if unassigned:
        for i in combat:
            if not contacts[i]:
                unknown.setdefault(i, 'unassigned-state-evidence')
    valid = [i for i in combat if i not in unknown]
    row = _result(spec, [contacts[i] for i in valid], 'recorded-state-admission-exact-cast',
                  cast_ticks=[records[i]['start']['tick'] for i in valid])
    row = annotate_provisional_rate(row, policy)
    row.update(observedCombatCastCount=len(combat), unresolvedCombatCastCount=len(unknown),
               unresolvedCastReasons=dict(Counter(unknown.values())),
               unresolvedCastTicks=[records[i]['start']['tick'] for i in unknown],
               perUseCompletenessTracked=True, unassignedStateEventCount=unassigned,
               recordedMonsterStateEventsExcluded=non_player_events,
               recordedSummonServantStateEventsExcluded=len(servant_events),
               excludedSummonServantStateEvidence=servant_events,
               damageUsedAsStateSuccess=False, nearestCastUsed=False, fixedTimeWindowUsed=False,
               negativeOutcomesAreProvisional=True, verifiedCompletionCredit=False,
               fullRequestedMetricComplete=False, completeTargetCountsAvailable=False,
               targetCountsAreLowerBounds=True, incompleteUsesCountedAsMisses=False)
    if not valid and unknown:
        row.update(status='unresolved-evidence', attemptCount=None, hitCount=None, hitRate=None)
    return row


def eleven_w_taunt_metric(spec, inputs):
    w = inputs['wall_inputs']
    rows, groups = w.get('state_rows'), w.get('state_groups')
    if rows is None or groups is None:
        return _unavailable(spec, 'Eleven W requires exact state tables')
    expected = {1030301: 1030300, 1030302: 1030300, 1030303: 1030300,
                1030304: 1030300, 1030305: 1030300, 1030311: 1030310,
                1030312: 1030310, 1030313: 1030310, 1030314: 1030310, 1030315: 1030310}
    definitions = {r['code']: r['group'] for r in rows}
    group_defs = {r['group']: r for r in groups}
    if any(definitions.get(c) != g for c, g in expected.items()) or any(
            group_defs.get(g, {}).get(k) != v for g in set(expected.values())
            for k, v in [('skillId', 'Taunt'), ('stateType', 'Taunt'), ('effectType', 'Debuff')]):
        return _unavailable(spec, 'Eleven W taunt state definitions changed; no fallback')
    row = recorded_state_success(spec, inputs['player_starts'], w.get('finishes'), w.get('states'),
        inputs['player'], inputs['teams'], inputs['intervals'].get(inputs['player'], []),
        set(expected), (inputs['summon_inputs'] or {}).get('gaps'),
        objects=(inputs['summon_inputs'] or {}).get('objects'))
    row.update(label='W 도발 성공률', numerator='casts-applying-taunt-to-any-enemy',
               candidateStateMapping=True, nativeConsumerGraphVerified=False,
               interpretation='실제 적 도발 상태 적용으로 집계. 피해만으로 도발 성공을 추정하지 않는 개발 중 지표.')
    return row
