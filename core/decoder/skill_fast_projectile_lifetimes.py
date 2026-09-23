"""Fast exact route for self-contained projectile families.

The old generic route required every other active skill lifetime to close
before accepting one projectile family.  That made unrelated missing finish
records block otherwise exact casts.  This route requires the whole nominated
family (all same-character stages sharing the static projectile family) to
close, then uses only each projectile's own arrival/collision lifetime.
"""
from collections import defaultdict
import re

try:
    from .skill_attempt_timing import projectile_outcomes
    from .skill_complete_projectile_lifetimes import candidate_projectile_family
    from .skill_wire_order import event_within_cast, finish_lookup
    from .requested_skill_scope import exact_cast_lifetimes
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_projectile_active_end import projectile_active_end_records, collision_only_outcome
except ImportError:
    from skill_attempt_timing import projectile_outcomes
    from skill_complete_projectile_lifetimes import candidate_projectile_family
    from skill_wire_order import event_within_cast, finish_lookup
    from requested_skill_scope import exact_cast_lifetimes
    from requested_skill_hit_rates import _result, _unavailable
    from skill_projectile_active_end import projectile_active_end_records, collision_only_outcome


def fast_projectile_lifetime_metric(spec, all_starts, spawns, collisions, terminals,
                                    finishes, player, teams, intervals, catalog,
                                    projectile_owners=None):
    if spec.get('mode') not in {'any', 'shot'}:
        return _unavailable(spec, '고속 발사체 경로는 일반 적중/발사 결과만 처리함')
    groups, codes = candidate_projectile_family(spec, catalog)
    if not groups or not codes:
        return _unavailable(spec, '정적 이름으로 확정된 발사체 family가 없음')
    stage = catalog['skillGroups'].get(str(spec.get('skillGroup')), {})
    if stage.get('projectileStatus') != 'candidate-static-name-only':
        return _unavailable(spec, '정적 prefab 이름만으로는 발사체 foreign key를 확정하지 않음')
    definitions = [catalog['projectileDefinitions'].get(str(code)) for code in codes]
    if any(not d or not collision_only_outcome(d) for d in definitions):
        return _unavailable(spec, '폭발·도착 후 충돌 발사체는 고속 경로에서 제외함')
    family_starts = [s for s in all_starts if s.get('skillGroup') in groups]
    target_starts = [s for s in family_starts if s.get('skillGroup') == spec.get('skillGroup')]
    if not target_starts:
        return _unavailable(spec, '요청 단계 시전이 관측되지 않음',
                            category='observation-incomplete', reason_code='no-observed-stage-cast')
    lifetimes, reason = exact_cast_lifetimes(family_starts, finishes, player)
    if reason:
        return _unavailable(spec, reason)
    finish_orders = finish_lookup(finishes, player)
    finish_by_identity = {(f.get('skillIdCode'), f.get('tick')): f for f in finishes
                          if f.get('playerObjectId') == player}
    relevant = [s for s in spawns if s.get('ownerPlayerObjectId') == player and
                s.get('projectileCode') in codes]
    by_cast = defaultdict(list)
    for spawn in relevant:
        owners = [i for i, (start, end) in enumerate(lifetimes)
                  if event_within_cast(start, end, spawn, finish_orders)]
        if len(owners) != 1:
            return _unavailable(spec, 'family 발사체가 한 시전 수명에 배타적으로 연결되지 않음')
        by_cast[owners[0]].append(spawn)
    # Reason 0 is a normal successful skill finish and cannot excuse a
    # missing projectile.  Only explicit cancellation/interrupt reasons are
    # allowed to have no emitted object.
    cancelled = set(range(1, 15)) | {16, 17}
    for index, (start, end) in enumerate(lifetimes):
        if by_cast.get(index):
            continue
        finish = finish_by_identity.get((start.get('skillIdCode'), end))
        if not finish or finish.get('reason') not in cancelled:
            return _unavailable(spec, '정상 종료 family 시전에 발사체가 없어 전체 발사 수를 확정할 수 없음')
    terminal_by_id = defaultdict(list)
    for event in terminals:
        terminal_by_id[event.get('objectId')].append(event)
    collision_by_id = defaultdict(list)
    for event in collisions:
        collision_by_id[event.get('projectileObjectId')].append(event)
    player_team = teams.get(player)
    if player_team is None:
        return _unavailable(spec, '시전자 팀 정보가 없음')
    object_hits = {}
    active_ends=projectile_active_end_records(terminals)
    closure_commands=set();final_destroyed=True
    for objects in by_cast.values():
        for spawn in objects:
            events = terminal_by_id.get(spawn.get('projectileObjectId'), [])
            active=active_ends.get(spawn.get('projectileObjectId'),{})
            if not active.get('complete') or active['endTick']<spawn['tick']:
                return _unavailable(spec, '발사체 실제 충돌 종료가 없거나 상충함; 도착만으로 종료를 추정하지 않음')
            end_tick=active['endTick'];closure_commands.update(active['terminalCommands'])
            final_destroyed=final_destroyed and any(e['event']=='CmdDestroy' for e in events)
            hits = set()
            for collision in collision_by_id.get(spawn.get('projectileObjectId'), []):
                if not spawn.get('tick') <= collision.get('tick') <= end_tick:
                    return _unavailable(spec, '충돌 기록이 발사체 수명 밖에 있음')
                target = collision.get('targetObjectId')
                if target in teams and teams[target] != player_team:
                    hits.add((collision.get('tick'), target))
            object_hits[spawn.get('projectileObjectId')] = hits
    selected = [i for i, (start, _end) in enumerate(lifetimes)
                if start.get('skillGroup') == spec.get('skillGroup') and
                any(left <= start.get('tick') < right for left, right in intervals)]
    cast_contacts = [set().union(*(object_hits[s.get('projectileObjectId')] for s in by_cast.get(i, [])))
                     for i in selected]
    shot_contacts = [object_hits[s.get('projectileObjectId')]
                     for i in selected for s in by_cast.get(i, [])]
    contacts = shot_contacts if spec.get('unit') == 'projectile-shot' else cast_contacts
    if not contacts:
        return _unavailable(spec, '확인된 전투 발사체 결과가 없음')
    result = _result(spec, contacts, 'fast-exact-exclusive-projectile-family-lifetime')
    result['outcomes'] = projectile_outcomes(spec['unit'], [
        (lifetimes[i][0]['tick'], [(s['tick'], object_hits[s['projectileObjectId']])
                                 for s in by_cast.get(i, [])], not by_cast.get(i))
        for i in selected])
    result.update(actualProjectileCount=len(shot_contacts),
                  enemyHitProjectileCount=sum(bool(x) for x in shot_contacts),
                  projectileContactEventCount=sum(len(x) for x in shot_contacts),
                  exactProjectileCodes=sorted(codes), finalDestructionVerified=final_destroyed,
                  collisionActivityEndVerified=True,outcomeClosureCommands=sorted(closure_commands),
                  terminalArrivalOrDestructionVerified=True, fixedFlightOrChannelDurationUsed=False,
                  numericSkillStateCodeJoinUsed=False)
    return result
