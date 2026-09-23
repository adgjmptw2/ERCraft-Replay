"""Object-keyed routes for reviewed runtime projectile families."""
from collections import defaultdict, Counter

try:
    from .skill_projectile_active_end import projectile_active_end_records
    from .skill_attempt_timing import exact_outcome
    from .requested_skill_scope import exact_cast_lifetimes
    from .skill_wire_order import event_within_cast, finish_lookup
    from .requested_skill_hit_rates import _result, _unavailable
except ImportError:
    from skill_projectile_active_end import projectile_active_end_records
    from skill_attempt_timing import exact_outcome
    from requested_skill_scope import exact_cast_lifetimes
    from skill_wire_order import event_within_cast, finish_lookup
    from requested_skill_hit_rates import _result, _unavailable


# These are runtime-reviewed object families. The code is accepted only when
# owner/object/terminal/collision/damage links are all closed in the replay.
ROUTES = {
    (13, 1013200): (101301, 'Xiukai Q'),
    (14, 1014400): (101411, 'Chiara E'),
    (18, 1018400): (101830, 'Shoichi E'),
    (32, 1032300): (103231, 'William W'),
    (34, 1034500): (103451, 'Nathapon R'),
}


def observed_projectile_metric(spec, starts, finishes, spawns, collisions,
                               terminals, damages, player, teams, intervals, *,
                               game_terminals=None,gaps=None,all_projectile_spawns=None,projectile_owners=None):
    route = ROUTES.get((spec.get('characterCode'), spec.get('skillGroup')))
    if route is None:
        return _unavailable(spec, 'no reviewed observed projectile route')
    if any(v is None for v in (starts,finishes,spawns,collisions,terminals,damages)):
        return _unavailable(spec,'observed projectile input stream missing')
    projectile_code, name = route
    own = sorted((s for s in starts if s.get('skillGroup') == spec['skillGroup']),
                 key=lambda s: s.get('tick', -1))
    # Reviewed object identities do not need to be relearned from three uses
    # in every match. Each use still passes the same ownership/outcome checks.
    if not own:
        return _unavailable(spec, f'no observed {name} starts')
    lifetimes, reason = exact_cast_lifetimes(own, finishes, player)
    if reason:
        return _unavailable(spec, reason)
    finish_orders = finish_lookup(finishes, player)
    owned = [s for s in spawns if s.get('ownerPlayerObjectId') == player
             and s.get('projectileCode') == projectile_code]
    by_cast = defaultdict(list)
    for shot in owned:
        owners = [i for i, (start, end) in enumerate(lifetimes)
                  if event_within_cast(start, end, shot, finish_orders)]
        if len(owners) != 1:
            return _unavailable(spec, f'{name} ProjectileSnapshot is not exclusive to one CmdStartSkill')
        by_cast[owners[0]].append(shot)
    combat = [i for i, (start, _end) in enumerate(lifetimes)
              if any(a <= start['tick'] < b for a, b in intervals)]
    unknown=defaultdict(set)
    for i in combat:
        if not by_cast.get(i):unknown[i].add('missing-recorded-projectile-emission')
    teams = {int(k): v for k, v in teams.items()}
    collision_by_object = defaultdict(list)
    for event in collisions:
        collision_by_object[event.get('projectileObjectId')].append(event)
    damage_keys = {(d.get('tick'), d.get('targetObjectId')) for d in damages or []
                   if d.get('attackerObjectId') == player}
    # A full command and a compact terminal fact may describe the same expiry.
    # Distinct times remain contradictory; absence never establishes a miss.
    active_ends=projectile_active_end_records(terminals)
    contacts, packet_counts = defaultdict(set), defaultdict(int)
    for index, shots in by_cast.items():
        for shot in shots:
            object_id = shot.get('projectileObjectId')
            expiry=active_ends.get(object_id)
            if not expiry or not expiry['complete']:
                unknown[index].add('projectile-destroy-missing-or-conflicting');continue
            end = expiry['endTick']
            if type(end) is not int or end<shot['tick']:
                unknown[index].add('projectile-destroy-before-spawn');continue
            for collision in collision_by_object.get(object_id, []):
                tick, target = collision.get('tick'), collision.get('targetObjectId')
                if not shot.get('tick') <= tick <= end:
                    unknown[index].add('collision-outside-projectile-lifetime');continue
                if target in teams and teams[target] != teams[player]:
                    if (tick, target) not in damage_keys:
                        unknown[index].add('enemy-collision-without-same-tick-damage');continue
                    contacts[index].add((tick, target)); packet_counts[index] += 1
    from .skill_wire_order import command_order
    policy_evidence=[];policy_blocked=[]
    if (spec.get('characterCode'),spec.get('skillGroup'),spec.get('mode'),spec.get('unit'))==(14,1014400,'any','skill-cast'):
        import json
        from pathlib import Path
        from .skill_ordered_match_end import ordered_winner_match_end
        enabled=json.loads((Path(__file__).resolve().parents[1]/'data/user-hit-rate-scope-20260912.json').read_text(encoding='utf-8'))['recordedUseMissPolicy'].get('enabled') is True
        winner=ordered_winner_match_end(game_terminals,gaps) if enabled else None
        for i in combat:
            if not enabled or unknown[i]!={'projectile-destroy-missing-or-conflicting'} or contacts[i]:continue
            start=lifetimes[i][0];left=command_order(start);right=command_order(winner) if winner else None
            reasons=[];shots=by_cast[i]
            if gaps is None or any(g.get('count',0) for g in gaps) or all_projectile_spawns is None or projectile_owners is None:reasons.append('original-stream-completeness-unavailable')
            if left is None or right is None or left>=right or start['tick']>winner['tick']:reasons.append('actual-ordered-winner-unavailable')
            ff=[f for f in finishes if f.get('playerObjectId')==player and f.get('skillIdCode')==start.get('skillIdCode') and f.get('tick')==lifetimes[i][1]]
            if len(ff)!=1 or ff[0].get('reason')!=0 or command_order(ff[0]) is None or left is None or right is None or not left<command_order(ff[0])<right or not start['tick']<=ff[0]['tick']<=winner['tick']:reasons.append('exact-normal-parent-finish-unavailable')
            def after(e):
                tick=e.get('tick');at=command_order(e)
                if type(tick) is not int or at is None or left is None:return True
                return tick>=start['tick'] or at>=left
            ids={s.get('projectileObjectId') for s in shots}
            if len(shots)!=1 or len(ids)!=len(shots):reasons.append('single-projectile-identity-unavailable')
            for shot in shots:
                oid=shot.get('projectileObjectId');at=command_order(shot)
                raw=[r for r in all_projectile_spawns or [] if r.get('projectileObjectId')==oid]
                if len(raw)!=1 or raw[0].get('projectileCode')!=projectile_code or raw[0].get('tick')!=shot.get('tick') or command_order(raw[0])!=at or (raw[0].get('ownerObjectId')!=player and (projectile_owners or {}).get(raw[0].get('ownerObjectId'))!=player):reasons.append('raw-projectile-owner-identity-unavailable')
                if at is None or left is None or right is None or not left<at<right or not start['tick']<=shot['tick']<=winner['tick']:reasons.append('projectile-order-invalid')
                if any(t.get('objectId')==oid for t in terminals):reasons.append('recorded-terminal-not-absent')
                if collision_by_object.get(oid):reasons.append('recorded-projectile-collision')
            for raw in all_projectile_spawns or []:
                if raw.get('projectileCode')!=projectile_code or raw.get('projectileObjectId') in ids or not after(raw):continue
                owner=raw.get('ownerObjectId');resolved=(projectile_owners or {}).get(owner,owner)
                if resolved==player or resolved not in teams:reasons.append('later-unassigned-primary-projectile')
            if any(after(d) and ((projectile_owners or {}).get(d.get('attackerObjectId'),d.get('attackerObjectId'))==player or (projectile_owners or {}).get(d.get('attackerObjectId'),d.get('attackerObjectId')) not in teams) for d in damages):reasons.append('later-owner-or-unowned-damage')
            proof=dict(startTick=start['tick'],startOrder=left,parentFinish=ff[0] if len(ff)==1 else None,projectiles=shots,winnerEnd=winner,projectileTerminalInvented=False)
            if reasons:policy_blocked.append(dict(proof,reasons=sorted(set(reasons))));continue
            unknown[i].clear();policy_evidence.append(proof)
    use_evidence=[dict(startTick=lifetimes[i][0]['tick'],startOrder=list(command_order(lifetimes[i][0]) or ()),
        reasons=sorted(unknown[i]),hasRecordedEmission=bool(by_cast[i])) for i in combat if unknown[i]]
    valid=[i for i in combat if not unknown[i]]
    diagnostics=dict(perUseCompletenessTracked=True,observedCombatCastCount=len(combat),
        verifiedCombatCastCount=len(valid)-len(policy_evidence),
        userPolicyWinnerMissEvidence=policy_evidence,userPolicyWinnerBlockedEvidence=policy_blocked,unresolvedCombatCastCount=len(combat)-len(valid),
        unresolvedCastReasons=dict(Counter(reason for i in combat for reason in unknown[i])),
        incompleteUsesCountedAsMisses=False,terminalIdentity='exact objectId active removal; duplicate representations collapsed',
        activityEndCommands=['CmdDestroyDelayStart','CmdDestroy'],visualRetirementRequired=False,
        unresolvedUseEvidence=use_evidence)
    if combat and not valid:
        return {**_unavailable(spec,f'no complete unambiguous {name} projectile uses'),**diagnostics}
    row = _result(spec, [contacts[i] for i in valid],
                  f'reviewed-{name.replace(" ", "-")}-CmdSpawn-owner-Collision-ActiveRemoval-CmdDamage')
    row['outcomes'] = [exact_outcome(lifetimes[i][0]['tick'],
                                   min(s['tick'] for s in by_cast[i]), contacts[i])
                       for i in valid]
    row.update(**diagnostics,exactProjectileCode=projectile_code, exactProjectileOwnerKey=True,
               exactDamagePacketCount=sum(packet_counts[i] for i in valid),
               numericSkillStateCodeJoinUsed=False,
               fixedFlightOrChannelDurationUsed=False)
    if policy_evidence:
        from .skill_development_cancellation import annotate_provisional
        annotate_provisional(row,'Explicit user policy: recorded projectile without contact before actual winner match end counts as a miss, not a synthetic projectile retirement.')
        row.update(verifiedCompletionCredit=False,fullRequestedMetricComplete=False)
    return row
