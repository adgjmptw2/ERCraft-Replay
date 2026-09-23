"""Strict object-linked projectile routes seeded by the static mapping table.

The table only supplies candidate codes. A row is calculable here only when
the replay itself closes every combat cast through owner/object identity,
terminal lifetime, and an enemy-player collision. No tick-window fallback is
used.
"""
from collections import defaultdict

try:
    from .requested_skill_scope import exact_cast_lifetimes
    from .skill_wire_order import event_within_cast, finish_lookup
    from .requested_skill_hit_rates import _result, _unavailable
except ImportError:
    from requested_skill_scope import exact_cast_lifetimes
    from skill_wire_order import event_within_cast, finish_lookup
    from requested_skill_hit_rates import _result, _unavailable


# These rows had one exact projectile code and at least two strict retained
# player-match observations in v94. They remain subject to the runtime gates
# below; this list is not a static assertion of hit rate.
ROUTES = {
    (9, 1009200): ((1009201, 1009202, 1009203, 1009204, 1009205), (100902,), 'Isol Q'),
    (17, 1017300): ((1017301, 1017302, 1017303, 1017304, 1017305), (101720, 101722), 'Adriana W'),
    (47, 1047300): ((1047301, 1047302, 1047303, 1047304, 1047305), (104731,), 'Laura W'),
    (58, 1058200): ((1058201, 1058202, 1058203, 1058204, 1058205), (105821,), 'Haze Q'),
    (61, 1061300): ((1061301, 1061302, 1061303, 1061304, 1061305), (106121,), 'Irem W'),
    (69, 1069200): ((1069201, 1069202, 1069203, 1069204, 1069205), (106903,), 'Leni Q'),
    (77, 1077200): ((1077201, 1077202, 1077203, 1077204, 1077205), (107721,), 'YuMin Q1'),
    (77, 1077210): ((1077211, 1077212, 1077213, 1077214, 1077215), (107722,), 'YuMin Q2'),
    (82, 1082200): ((1082201, 1082202, 1082203, 1082204, 1082205), (108211,), 'Xuelin Q'),
    (34, 1034500): ((1034501, 1034502, 1034503), (103451,), 'Nathapon R'),
    (43, 1043200): ((1043201, 1043202, 1043203, 1043204, 1043205), (104321,), 'Celine Q'),
    (50, 1050500): ((1050501, 1050502, 1050503), (105011,), 'Elena R'),
    (41, 1041200): ((1041201, 1041202, 1041203, 1041204, 1041205), (104111, 104112), 'Johann Q'),
    (75, 1075500): ((1075501, 1075502, 1075503), (107551,), 'Lenore R'),
}


def static_projectile_metric(spec, starts, finishes, spawns, collisions,
                             terminals, player, teams, intervals):
    # Static candidates are retained for diagnostics only.  A collision-free
    # lifetime cannot prove a miss for explosion, persistent, piercing, or
    # shared-projectile skills, so this route must never emit an authoritative
    # hit-rate row until a skill-specific runtime closure is reviewed.
    return _unavailable(spec, 'static candidate only; skill-specific runtime closure required')
    """legacy candidate implementation retained below for audit comparison
    route = ROUTES.get((spec.get('characterCode'), spec.get('skillGroup')))
    if route is None:
        return _unavailable(spec, 'no reviewed static-seeded projectile route')
    skill_codes, projectile_codes, name = route
    own = sorted((s for s in starts if s.get('skillGroup') == spec['skillGroup']
                  and s.get('skillCode') in skill_codes),
                 key=lambda s: s.get('tick', -1))
    if len(own) < 3:
        return _unavailable(spec, f'fewer than three {name} starts')
    lifetimes, reason = exact_cast_lifetimes(own, finishes, player)
    if reason:
        return _unavailable(spec, reason)
    finish_orders = finish_lookup(finishes, player)
    owned = [s for s in spawns
             if s.get('ownerPlayerObjectId') == player
             and s.get('projectileCode') in projectile_codes]
    by_cast = defaultdict(list)
    for shot in owned:
        owners = [i for i, (start, end) in enumerate(lifetimes)
                  if event_within_cast(start, end, shot, finish_orders)]
        if len(owners) != 1:
            # A spawn outside an eligible cast is not evidence for this row;
            # combat casts themselves must still receive exactly one shot.
            continue
        by_cast[owners[0]].append(shot)
    combat = [i for i, (start, _end) in enumerate(lifetimes)
              if any(a <= start['tick'] < b for a, b in intervals)]
    if len(combat) < 3:
        return _unavailable(spec, f'fewer than three combat {name} casts')
    if any(len(by_cast.get(i, [])) != 1 for i in combat):
        return _unavailable(spec, f'normal combat {name} cast is not linked to exactly one owned projectile')
    teams = {int(k): v for k, v in teams.items()}
    collision_by_object = defaultdict(list)
    for event in collisions:
        collision_by_object[event.get('projectileObjectId')].append(event)
    contacts = defaultdict(set)
    for index in combat:
        shot = by_cast[index][0]
        object_id = shot.get('projectileObjectId')
        destroys = [e for e in terminals
                    if e.get('objectId') == object_id and e.get('event') == 'CmdDestroy']
        if len(destroys) != 1:
            return _unavailable(spec, f'{name} projectile lacks one exact CmdDestroy')
        end = destroys[0].get('tick')
        for collision in collision_by_object.get(object_id, []):
            tick, target = collision.get('tick'), collision.get('targetObjectId')
            if not shot.get('tick') <= tick <= end:
                return _unavailable(spec, f'{name} collision is outside projectile lifetime')
            if target in teams and teams[target] != teams[player]:
                contacts[index].add((tick, target))
    row = _result(
        spec, [contacts[i] for i in combat],
        f'reviewed-{name.replace(" ", "-")}-CmdSpawn-owner-Collision-CmdDestroy',
    )
    row.update(
        exactProjectileCodes=list(projectile_codes),
        exactProjectileOwnerKey=True,
        exactCollisionPacketCount=sum(len(contacts[i]) for i in combat),
        collisionIsHitEvidence=True,
        numericSkillStateCodeJoinUsed=False,
        fixedFlightOrChannelDurationUsed=False,
    )
    return row
    """
