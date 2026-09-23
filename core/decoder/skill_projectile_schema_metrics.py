"""Schema-backed projectile routes for the retained 12.3.0 replay facts.

These routes use the decoded object keys from CmdSpawn/ProjectileSnapshot and
CmdProjectileCollision/Explosion.  A damage effect is accepted only when its
tick identifies one owned projectile object; a generic time window is never a
linking key.
"""
from collections import defaultdict

try:
    from .skill_attempt_timing import exact_outcome
    from .requested_skill_scope import exact_cast_lifetimes
    from .skill_wire_order import event_within_cast, finish_lookup, command_order
    from .requested_skill_hit_rates import _result, _unavailable
except ImportError:
    from skill_attempt_timing import exact_outcome
    from requested_skill_scope import exact_cast_lifetimes
    from skill_wire_order import event_within_cast, finish_lookup, command_order
    from requested_skill_hit_rates import _result, _unavailable


def _bad(spec, reason):
    return {**spec, 'status': 'unresolved-evidence', 'attemptCount': None,
            'hitCount': None, 'hitRate': None, 'multiTargetAttemptCount': None,
            'multiTargetAttemptRate': None,
            'distinctEnemyTargetsSummedAcrossAttempts': None,
            'meanDistinctEnemyTargetsPerAttempt': None,
            'deduplicatedEnemyContactEventCount': None,
            'exactDamagePacketCount': None, 'methods': [], 'reason': reason,
            'fallbackUsed': False}


def _family(spec, starts, finishes, spawns, player, intervals, group, projectile_codes):
    own = sorted((s for s in starts if s.get('playerObjectId') == player and s.get('skillGroup') == group),
                 key=lambda s: s.get('tick', -1))
    if not own:return None, None, _bad(spec, 'projectile stage has no observed starts')
    unknown={}
    # Choose the retained input representation before evaluating it. Legacy
    # tick-only facts remain strict; a failed ordered parse is never retried.
    if all(command_order(s) is not None for s in own):
        from .skill_partial_cast_lifetimes import ordered_cast_records
        records,reason=ordered_cast_records(own,finishes,player,allow_same_tick_finishes=True)
        if reason:return None,None,_bad(spec,reason)
        lifetimes=[(r['start'],r['finish']['tick'] if r['finish'] else None) for r in records]
        unknown={i:r['reason'] for i,r in enumerate(records) if not r['complete']}
    else:
        lifetimes,reason=exact_cast_lifetimes(own,finishes,player)
        if reason:return None,None,_bad(spec,reason)
    finish_orders=finish_lookup(finishes,player)
    owned=[s for s in spawns if s.get('ownerPlayerObjectId')==player and s.get('projectileCode') in projectile_codes]
    if len({s['projectileObjectId'] for s in owned})!=len(owned):
        return None,None,_bad(spec,'duplicate owned projectile identity')
    by_cast=defaultdict(list)
    for shot in owned:
        owners=[i for i,(start,end) in enumerate(lifetimes) if
                (event_within_cast(start,end,shot,finish_orders) if end is not None else
                 command_order(shot) is not None and command_order(start)<=command_order(shot) and start['tick']<=shot['tick'])]
        if len(owners)!=1:return None,None,_bad(spec,'ProjectileSnapshot owner/object is not exclusive to one CmdStartSkill')
        by_cast[owners[0]].append(shot)
    combat=[i for i,(start,_) in enumerate(lifetimes) if any(a<=start['tick']<b for a,b in intervals)]
    for i in combat:
        if not by_cast[i]:unknown.setdefault(i,'missing-recorded-projectile-emission')
    return lifetimes,by_cast,(combat,finish_orders,unknown)


def _finish(spec, lifetimes, by_cast, combat, contacts, packets, method, unknown):
    from .skill_lifecycle_result_policy import finalize_lifecycle_result
    # Independently linked positive objects establish binary success even
    # when another object of that use lacks a terminal. Keep target totals partial.
    partial_positive={i for i in unknown if contacts[i] and unknown[i] in {
        'open-final-cast','replay-end-is-not-gameplay-finish','missing-recorded-phase-terminal',
        'missing-recorded-projectile-activity-end'}}
    unknown={i:why for i,why in unknown.items() if i not in partial_positive}
    valid=[i for i in combat if i not in unknown]
    row=_result(spec,[contacts[i] for i in valid],method)
    row['outcomes']=[exact_outcome(lifetimes[i][0]['tick'],min(s['tick'] for s in by_cast[i]),contacts[i]) for i in valid]
    row.update(exactProjectileOwnerKey=True,exactDamagePacketCount=sum(packets[i] for i in valid),
               numericSkillStateCodeJoinUsed=False,fixedFlightOrChannelDurationUsed=False)
    row=finalize_lifecycle_result(row,combat,unknown,incomplete_positive=partial_positive,observed_positive=any(contacts.values()))
    row['unresolvedUseEvidence']=[dict(startTick=lifetimes[i][0]['tick'],startOrder=command_order(lifetimes[i][0]),
         reasons=[unknown[i]],hasRecordedEmission=bool(by_cast[i])) for i in combat if i in unknown]
    return row


def sua_q_schema_metric(spec, starts, finishes, spawns, terminals, damages,
                        player, teams, intervals):
    # 1028202 is also emitted by Sua's other state/skill family.  The
    # CmdStartSkill group 1028200 is only paired with the normal Q projectile
    # 1028201 in the retained packet stream; accepting 1028202 would mix R.
    group, codes, effects = 1028200, {1028201}, {1028201}
    lifetimes, by_cast, extra = _family(spec, starts, finishes, spawns, player,
                                        intervals, group, codes)
    if lifetimes is None:
        return extra
    combat, _, unknown = extra
    delay_by_object = defaultdict(list)
    owned_ids = {s['projectileObjectId'] for rows in by_cast.values() for s in rows}
    for event in terminals:
        if event.get('objectId') in owned_ids and event.get('event') == 'CmdDestroyDelayStart':
            delay_by_object[event['objectId']].append(event.get('tick'))
    for i,shots in by_cast.items():
        if any(not delay_by_object[s['projectileObjectId']] for s in shots):unknown.setdefault(i,'missing-recorded-phase-terminal')
    at_tick = defaultdict(list)
    for object_id, ticks in delay_by_object.items():
        for tick in ticks:
            at_tick[tick].append(object_id)
    contacts, packets = defaultdict(set), defaultdict(int)
    for damage in damages or []:
        if damage.get('attackerObjectId') != player or damage.get('effectCode') not in effects:
            continue
        objects = at_tick.get(damage.get('tick'), [])
        if len(objects) != 1:
            return _bad(spec, 'Sua Q CmdDamage does not identify one projectile delay event')
        owner = [i for i, rows in by_cast.items()
                 if any(s['projectileObjectId'] == objects[0] for s in rows)]
        if len(owner) != 1:
            return _bad(spec, 'Sua Q projectile object has no unique cast owner')
        target = damage.get('targetObjectId')
        if target in teams and teams[target] != teams[player]:
            contacts[owner[0]].add((damage.get('tick'), target)); packets[owner[0]] += 1
    return _finish(spec, lifetimes, by_cast, combat, contacts, packets,
                   'reviewed-Sua-Q-CmdSpawn-owner-CmdDestroyDelayStart-effectCode', unknown)


def nathapon_q_schema_metric(spec, starts, finishes, spawns, terminals, damages,
                             player, teams, intervals):
    group, code, effect = 1034200, 103421, 1034201
    lifetimes, by_cast, extra = _family(spec, starts, finishes, spawns, player,
                                        intervals, group, {code})
    if lifetimes is None:
        return extra
    combat, _, unknown = extra
    explosion_by_tick = defaultdict(list)
    owned_ids = {s['projectileObjectId'] for rows in by_cast.values() for s in rows}
    for event in terminals:
        if event.get('objectId') in owned_ids and event.get('event') == 'CmdProjectileExplosion':
            explosion_by_tick[event.get('tick')].append(event['objectId'])
    exploded={oid for objects in explosion_by_tick.values() for oid in objects}
    for i,shots in by_cast.items():
        if any(s['projectileObjectId'] not in exploded for s in shots):unknown.setdefault(i,'missing-recorded-phase-terminal')
    contacts, packets = defaultdict(set), defaultdict(int)
    for damage in damages or []:
        if damage.get('attackerObjectId') != player or damage.get('effectCode') != effect:
            continue
        objects = explosion_by_tick.get(damage.get('tick'), [])
        if len(objects) != 1:
            return _bad(spec, 'Nathapon Q CmdDamage does not identify one projectile explosion')
        owner = [i for i, rows in by_cast.items()
                 if any(s['projectileObjectId'] == objects[0] for s in rows)]
        if len(owner) != 1:
            return _bad(spec, 'Nathapon Q projectile object has no unique cast owner')
        target = damage.get('targetObjectId')
        if target in teams and teams[target] != teams[player]:
            contacts[owner[0]].add((damage.get('tick'), target)); packets[owner[0]] += 1
    return _finish(spec, lifetimes, by_cast, combat, contacts, packets,
                   'reviewed-Nathapon-Q-CmdSpawn-owner-CmdProjectileExplosion-effectCode', unknown)


def nathapon_e_schema_metric(spec, starts, finishes, spawns, terminals, collisions,
                             damages, player, teams, intervals):
    group, code, effect = 1034400, 103441, 1034401
    lifetimes, by_cast, extra = _family(spec, starts, finishes, spawns, player,
                                        intervals, group, {code})
    if lifetimes is None:
        return extra
    combat, _, unknown = extra
    by_object = defaultdict(list)
    for event in collisions:
        by_object[event.get('projectileObjectId')].append(event)
    damage_keys = {(d.get('tick'), d.get('targetObjectId')) for d in damages or []
                   if d.get('attackerObjectId') == player and d.get('effectCode') == effect}
    contacts, packets = defaultdict(set), defaultdict(int)
    from .skill_projectile_active_end import projectile_active_end_records
    active_ends = projectile_active_end_records(terminals)
    for i, rows in by_cast.items():
        for shot in rows:
            active=active_ends.get(shot['projectileObjectId'],{})
            if not active:
                unknown.setdefault(i,'missing-recorded-projectile-activity-end')
            elif not active.get('complete') or active['endTick']<shot['tick']:
                unknown[i]='conflicting-recorded-projectile-activity-end';continue
            for collision in by_object.get(shot['projectileObjectId'], []):
                target = collision.get('targetObjectId'); key = (collision.get('tick'), target)
                if collision['tick']<shot['tick'] or (active.get('complete') and collision['tick']>active['endTick']):
                    unknown[i]='contact-outside-projectile-lifetime';continue
                if target in teams and teams[target] != teams[player]:
                    if key not in damage_keys:
                        unknown[i]='enemy-contact-without-effect-damage';continue
                    contacts[i].add(key); packets[i] += 1
    return _finish(spec, lifetimes, by_cast, combat, contacts, packets,
                   'reviewed-Nathapon-E-CmdProjectileCollision-owner-effectCode', unknown)
