"""Exact projectile outcome route for Tazia normal Q (12.3.0).

Tazia Q is a direction cast whose normal shot is projectile code 106021.  The
shot is assigned to a cast by the exact owner and cast lifetime; enemy contact
comes only from that projectile's decoded collision packets.  No damage FX or
fixed flight duration is used.
"""
from __future__ import annotations

try:
    from .requested_skill_scope import exact_cast_lifetimes
    from .skill_wire_order import event_within_cast, finish_lookup
    from .requested_skill_hit_rates import _result, _unavailable
except ImportError:
    from requested_skill_scope import exact_cast_lifetimes
    from skill_wire_order import event_within_cast, finish_lookup
    from requested_skill_hit_rates import _result, _unavailable


CHARACTER = 60
GROUP = 1060200
PROJECTILE = 106021
SKILL_CODES = {1060201, 1060202, 1060203, 1060204, 1060205}


def tazia_q_projectile_metric(spec, starts, finishes, spawns, collisions,
                              terminals, player, teams, intervals, skill_rows,
                              projectile_rows, route_inputs=None, gaps=None):
    if spec.get('characterCode') != CHARACTER or spec.get('skillGroup') != GROUP:
        return _unavailable(spec, '타지아 일반 Q 전용 경로가 아님')
    definitions = {row.get('code') for row in skill_rows if row.get('group') == GROUP}
    if definitions != SKILL_CODES:
        return _unavailable(spec, '12.3.0 타지아 일반 Q의 레벨별 Skill code 정의 불일치')
    projectile = next((row for row in projectile_rows
                       if row.get('projectileCode', row.get('code')) == PROJECTILE), None)
    if not projectile or not projectile.get('enableObjectCollisionCheck', projectile.get('collisionEnabled', False)) or projectile.get('isExplosion'):
        return _unavailable(spec, '타지아 Q 전용 직선 발사체 정의가 없음')
    cast_starts = [row for row in starts
                   if row.get('playerObjectId') == player and row.get('skillGroup') == GROUP]
    if not cast_starts:
        return _unavailable(spec, '타지아 일반 Q 반복 시전 표본 부족')
    from .skill_partial_cast_lifetimes import partial_cast_windows, partial_window_contains
    from .skill_wire_order import command_order
    lifetimes, unknown, reason = partial_cast_windows(cast_starts, finishes, player)
    if reason:
        return _unavailable(spec, reason)
    owned = [row for row in spawns
             if row.get('ownerPlayerObjectId') == player and row.get('projectileCode') == PROJECTILE]
    if len({s['projectileObjectId'] for s in owned})!=len(owned):
        return _unavailable(spec,'duplicate owned projectile identity')
    finish_orders = finish_lookup(finishes, player)
    assigned = [[] for _ in lifetimes]
    for shot in owned:
        owners = [i for i, (start, end) in enumerate(lifetimes)
                  if partial_window_contains(start, end, shot, finish_orders)]
        if len(owners) != 1:
            return _unavailable(spec, '타지아 Q 발사체가 한 실제 시전 수명에 배타적으로 연결되지 않음')
        assigned[owners[0]].append(shot)
    for i,rows in enumerate(assigned):
        if len(rows)!=1:
            unknown.setdefault(i,'missing-recorded-projectile-emission' if not rows else 'multiple-recorded-projectile-emissions')
    terminal_by_id = {}
    owned_ids = {row['projectileObjectId'] for row in owned}
    for event in terminals:
        if event.get('objectId') in owned_ids:
            terminal_by_id.setdefault(event['objectId'], []).append(event)
    collisions_by_id = {}
    for event in collisions:
        collisions_by_id.setdefault(event['projectileObjectId'], []).append(event)
    player_team = teams.get(player)
    if player_team is None:
        return _unavailable(spec, '시전자 팀 정보가 없음')
    from .skill_projectile_active_end import projectile_gameplay_end_records
    active_ends=projectile_gameplay_end_records(terminals,owned,collisions,
        ((route_inputs or {}).get('wall_inputs') or {}).get('gameTerminals'),gaps)
    outcomes = []; incomplete_positive=set()
    for i, rows in enumerate(assigned):
        if len(rows)!=1:
            outcomes.append(set())
            continue
        shot = rows[0]
        shot_id = shot['projectileObjectId']
        ends = terminal_by_id.get(shot_id, [])
        active=active_ends.get(shot_id,{})
        end_tick=active.get('endTick') if active.get('complete') else None
        if active and not active.get('complete'):
            unknown[i]='conflicting-projectile-removal-records'
        contacts = set()
        for event in collisions_by_id.get(shot_id, []):
            if event['tick']<shot['tick'] or (end_tick is not None and event['tick']>end_tick):
                return _unavailable(spec, '타지아 Q 충돌 기록이 발사체 수명 밖에 있음')
            target = event.get('targetObjectId')
            if target in teams and teams[target] != player_team:
                contacts.add((event['tick'], target))
        if end_tick is None and i not in unknown:
            if contacts:incomplete_positive.add(i)
            else:unknown[i]='projectile-activity-end-missing-without-positive'
        outcomes.append(contacts)
    from .skill_partial_cast_lifetimes import ordered_cast_records,user_cancelled_no_recorded_contact_indices
    records,order_error=ordered_cast_records(cast_starts,finishes,player,allow_same_tick_finishes=True)
    by_order={command_order(r['start']):r for r in records or []}
    policy_records=[by_order.get(command_order(s)) for s,end in lifetimes]
    exact=not order_error and len(by_order)==len(lifetimes) and all(r and r['start']==s and (r['finish']['tick'] if r.get('finish') else None)==end for r,(s,end) in zip(policy_records,lifetimes))
    raw=((route_inputs or {}).get('summon_inputs') or {}).get('allProjectileSpawns')
    pending=set();blocked={}
    for i,why in unknown.items():
        reasons=[];start=lifetimes[i][0];left=command_order(start)
        if raw is None:reasons.append('original-projectile-stream-missing')
        if not exact or start.get('skillIdCode')!=876:reasons.append('exact-normal-Q-parent-record-unavailable')
        for shot in raw or []:
            if shot.get('projectileCode')!=PROJECTILE:continue
            if shot.get('ownerObjectId') in teams and shot.get('ownerObjectId')!=player:continue
            if shot.get('tick') is not None and shot['tick']<start['tick']:continue
            so=command_order(shot)
            # A uniquely recorded different Q already owns this emission. Do not donate it.
            matches=[j for j,rows in enumerate(assigned) for p in rows if p['projectileObjectId']==shot.get('projectileObjectId') and command_order(p)==so and p['tick']==shot.get('tick')]
            if shot.get('ownerObjectId')==player and so is not None and len(matches)==1 and matches[0]!=i:
                j=matches[0];other=policy_records[j]
                if other and other.get('finish') and command_order(other['start'])<=so<=command_order(other['finish']) and other['start']['tick']<=shot['tick']<=other['finish']['tick']:continue
            if so is None or left is None or so>=left or shot.get('tick',start['tick'])>start['tick']:reasons.append('unassigned-or-owner-unknown-normal-Q-emission')
        if reasons:pending.add(i)
        blocked[i]=reasons
    admitted=user_cancelled_no_recorded_contact_indices(policy_records,outcomes,unknown,
        cancellation_reason='missing-recorded-projectile-emission',pending_continuations=pending,gaps=gaps) if exact else []
    def proof(i):
        r=policy_records[i];s=lifetimes[i][0];f=r.get('finish') if r else None
        return dict(startTick=s['tick'],startOrder=command_order(s),finishTick=f['tick'] if f else None,
            finishOrder=command_order(f) if f else None,finishReason=f.get('reason') if f else None,recordedEmission=False)
    miss_evidence=[proof(i) for i in admitted]
    blocked_evidence=[dict(proof(i),reasons=blocked.get(i,[])+[why]) for i,why in unknown.items() if i not in admitted]
    for i in admitted:unknown.pop(i)
    combat=[i for i,(start,_) in enumerate(lifetimes)
            if any(left<=start['tick']<right for left,right in intervals)]
    valid=[i for i in combat if i not in unknown]
    selected=[outcomes[i] for i in valid]
    row = _result(spec, selected, 'reviewed-Tazia-Q-exact-106021-projectile-lifetime')
    from .skill_attempt_timing import exact_outcome
    row['outcomes']=[exact_outcome(lifetimes[i][0]['tick'],assigned[i][0]['tick'] if assigned[i] else lifetimes[i][0]['tick'],outcomes[i]) for i in valid]
    row.update(winnerMatchEndClosedProjectileCount=sum(active_ends.get(s['projectileObjectId'],{}).get('closureKind')=='actual-winner-match-end' for s in owned),
               actualProjectileCount=len(owned), enemyHitProjectileCount=sum(bool(x) for x in selected),
               projectileContactEventCount=sum(len(x) for x in selected), exactProjectileCodes=[PROJECTILE],
               projectileOwnerExact=True, terminalCoverageCount=len(terminal_by_id),
               terminalOutcomeUsesCmdProjectileArrived=False, fixedFlightOrChannelDurationUsed=False,
               numericSkillStateCodeJoinUsed=False)
    from .skill_lifecycle_result_policy import finalize_lifecycle_result
    row=finalize_lifecycle_result(row,combat,unknown,incomplete_positive=incomplete_positive)
    row.update(userPolicyMissEvidence=miss_evidence,userPolicyBlockedEvidence=blocked_evidence,
        userPolicyMissCastCount=sum(i in combat for i in admitted),userPolicyMissAuthority='explicit-user-rule')
    if admitted:row.update(verifiedCompletionCredit=False,fullRequestedMetricComplete=False,verifiedCombatCastCount=max(0,(row.get('verifiedCombatCastCount') or 0)-sum(i in combat for i in admitted)))
    row['unresolvedCastTicks']=[lifetimes[i][0]['tick'] for i in combat if i in unknown]
    row['unresolvedUseEvidence']=[dict(startTick=lifetimes[i][0]['tick'],
        startOrder=command_order(lifetimes[i][0]),reasons=[unknown[i]],
        hasRecordedEmission=bool(assigned[i]),emissionKind='projectile') for i in combat if i in unknown]
    if row['unresolvedCastTicks']:
        row.update(hitRateScope='verified-uses-only',wholeCombatHitRate=None,fullRequestedMetricComplete=False)
    return row
