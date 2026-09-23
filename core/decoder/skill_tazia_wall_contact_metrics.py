"""Tazia W contact: an explicit W action names the retiring glass wall.

W finishes before the wall appears. A bounded, non-overlapping W use is
therefore closed by its object-naming action, not a guessed spawn delay.
Only collider contacts are requested; wall explosion damage is not counted.
"""
from collections import defaultdict

try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS


def tazia_wall_contact_metric(spec, starts, finishes, actions, spawns, collisions,
                              terminals, player, teams, intervals, catalog,
                              skill_rows, projectile_rows, skill_ids=None, *, game_terminals=None,gaps=None,all_projectile_spawns=None):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes

    fail = lambda reason: _unavailable(spec, reason)
    if any(v is None for v in (starts,finishes,actions,spawns,collisions,terminals)):
        return fail('original wall input stream missing')
    if (spec.get('characterCode'), spec.get('skillGroup'), spec.get('mode'), spec.get('unit')) != (60, 1060300, 'any', 'skill-cast'):
        return fail('타지아 W 유리벽 접촉 전용 요청이 아님')
    definition = catalog['skillGroups'].get('1060300', {})
    projectiles = [r for r in projectile_rows if r.get('code') == 106031]
    expected = dict(prefabName='Projectile_FX_BI_Tazia_Skill02', type='Point',
                    enableObjectCollisionCheck=True, enableObjectCollsionCheckAfterArrival=True,
                    collisionObjectType='Box', collisionObjectWidth=4, collisionObjectDepth=0.2)
    if ((definition.get('characterCode'), definition.get('skillId')) != (60, 'TaziaActive2')
            or len(projectiles) != 1 or any(projectiles[0].get(k) != v for k, v in expected.items())):
        return fail('정확한 타지아 W·유리벽 충돌체 정의 불일치')
    codes = {r['code'] for r in skill_rows if r.get('group') == 1060300}
    if codes != {1060301, 1060302, 1060303, 1060304, 1060305}:
        return fail('정확한 타지아 W 레벨별 스킬 정의 불일치')
    ids = load_exact_skill_ids() if skill_ids is None else skill_ids
    wire = ids.get('TaziaActive2')
    selected = sorted((s for s in starts if s['playerObjectId'] == player and s['skillGroup'] == 1060300), key=lambda s: s['tick'])
    if not selected or any(s['skillIdCode'] != wire or s['skillCode'] not in codes for s in selected):
        return fail('W 시전 누락 또는 wire 정체성 불일치')
    casts, reason = exact_cast_lifetimes(selected, finishes, player)
    if reason:
        return fail(reason)
    own = [a for a in actions if a['sourceObjectId'] == player and a['skillIdCode'] == wire and a.get('wireStatus') in ORDINARY_ACTIONS]
    if any(a.get('actionNo') not in {1, 2} for a in own):
        return fail('유리벽 생성·종료 외의 미검토 W 행동')
    objects = defaultdict(list)
    for s in spawns:
        if s.get('ownerPlayerObjectId') == player and s.get('projectileCode') == 106031:
            objects[s['projectileObjectId']].append(s)
    ends = defaultdict(lambda: defaultdict(list))
    from .skill_projectile_active_end import projectile_active_end_records
    active_ends = projectile_active_end_records(terminals)
    for t in terminals:
        if t['objectId'] in objects:
            ends[t['objectId']][t['event']].append(t['tick'])
    roots = {}; used_actions = set(); contacts = []; unknown = {}; unreferenced = []
    for i, (start, finish) in enumerate(casts):
        right = casts[i + 1][0]['tick'] if i + 1 < len(casts) else float('inf')
        aa = [(j, a) for j, a in enumerate(own) if start['tick'] <= a['tick'] < right]
        create = [(j, a) for j, a in aa if a['actionNo'] == 1]
        close = [(j, a) for j, a in aa if a['actionNo'] == 2]
        if len(create) != 1 or len(close) != 1 or create[0][1]['tick'] != start['tick']:
            if i == len(casts)-1 and len(create)==1 and not close and create[0][1]['tick']==start['tick']:
                from .skill_partial_cast_lifetimes import ordered_cast_records
                from .skill_wire_order import command_order
                records,why=ordered_cast_records(selected,finishes,player,allow_same_tick_finishes=True)
                ordered=[*selected,*own,*(s for rows in objects.values() for s in rows)]
                if (why or any(not r['complete'] for r in records)
                        or any(command_order(e) is None for e in ordered)
                        or command_order(create[0][1])<command_order(start)
                        or any(a['actionNo']==2 and (a['tick']>=start['tick'] or command_order(a)>=command_order(start)) for a in own)):
                    return fail('마지막 W 종료 누락의 독립된 명령 경계를 확인하지 못함')
                unknown[i]='wall-close-action-missing'
                used_actions.update(j for j,_ in aa);contacts.append(set())
                continue
            return fail('W 시전과 명시 생성·종료 행동이 일대일 아님')
        marker = close[0][1]
        if len(marker.get('targets', [])) != 1:
            return fail('W 종료 행동이 한 유리벽 객체를 직접 지정하지 않음')
        oid = marker['targets'][0]['targetObjectId']
        if oid in roots or len(objects[oid]) != 1:
            return fail('W 행동이 지정한 소유 유리벽 객체 누락·중복')
        obj = objects[oid][0]
        if obj.get('ownerObjectId') != player or not start['tick'] <= finish < obj['tick'] <= marker['tick'] < right:
            return fail('W 시전·소유 벽 생성·종료 순서 또는 소유자 불일치')
        end = ends[oid]
        if (end['CmdProjectileExplosion'] != [marker['tick']]
                or end['CmdDestroyDelayStart'] != [marker['tick']]
                or not active_ends.get(oid,{}).get('complete')
                or active_ends[oid]['endTick'] != marker['tick']):
            return fail('명시 유리벽 종료 행동과 실제 객체 종료 기록 불일치')
        roots[oid] = (obj['tick'], marker['tick'], i)
        used_actions.update(j for j, _ in aa)
        contacts.append(set())
    remaining=set(objects)-set(roots)
    if unknown:
        # Preserve unidentified objects as evidence, never assign one merely
        # because it is the only wall left after the final recorded start.
        last=casts[-1][0]
        for oid,(_,_,i) in roots.items():
            obj=objects[oid][0]
            marker=next(a for a in own if a['actionNo']==2 and a['targets'][0]['targetObjectId']==oid)
            if not command_order(casts[i][0])<command_order(obj)<command_order(marker)<command_order(last):
                return fail('앞선 완결 유리벽과 마지막 미확정 사용의 명령 영역이 겹침')
        if len(remaining)>1:return fail('마지막 W 이후 미귀속 유리벽 객체가 복수임')
        for oid in remaining:
            rows=objects[oid]
            if (len(rows)!=1 or rows[0].get('ownerObjectId')!=player
                    or rows[0]['tick']<=last['tick'] or command_order(rows[0])<=command_order(last)):
                return fail('미귀속 유리벽이 마지막 W 이후로 독립되지 않음')
            obj=rows[0]
            events=[e for e in terminals if e['objectId']==oid]+[e for e in collisions if e['projectileObjectId']==oid]
            if any(command_order(e) is None or e['tick']<obj['tick'] or command_order(e)<command_order(obj) for e in events):
                return fail('미귀속 유리벽 이벤트 순서 불명 또는 생성 이전 이벤트')
            unreferenced.append(dict(projectileObjectId=oid,spawnTick=obj['tick'],spawnOrder=command_order(obj),
                events=events,assignedToCast=False))
    if (remaining and not unknown) or len(used_actions) != len(own):
        return fail('W 사용에 연결되지 않은 유리벽 또는 행동이 남음')
    for collision in collisions:
        oid = collision['projectileObjectId']
        if oid not in roots:
            continue
        left, right, i = roots[oid]
        if not left <= collision['tick'] <= right:
            return fail('유리벽 접촉이 기록된 객체 활성 수명 밖에 있음')
        if unknown and (command_order(collision) is None or not command_order(objects[oid][0])<=command_order(collision)<command_order(casts[-1][0])):
            return fail('앞선 유리벽 접촉의 명령 영역을 분리하지 못함')
        target = collision['targetObjectId']
        if target in teams and teams[target] != teams[player]:
            contacts[i].add((collision['tick'], target))
    combat=[i for i,(s,_) in enumerate(casts) if any(l<=s['tick']<r for l,r in intervals)]
    policy_evidence=[];policy_blocked=[]
    if unknown:
        import json
        from pathlib import Path
        from .skill_ordered_match_end import ordered_winner_match_end
        enabled=json.loads((Path(__file__).resolve().parents[1]/'data/user-hit-rate-scope-20260912.json').read_text(encoding='utf-8'))['recordedUseMissPolicy'].get('enabled') is True
        winner=ordered_winner_match_end(game_terminals,gaps) if enabled else None
        for i in list(unknown):
            if not enabled or unknown[i]!='wall-close-action-missing' or contacts[i]:continue
            start=casts[i][0];left=command_order(start);right=command_order(winner) if winner else None;reasons=[]
            if all_projectile_spawns is None or gaps is None or any(g.get('count',0) for g in gaps):reasons.append('original-stream-completeness-unavailable')
            rr=[r for r in records if r['start'] is start]
            if len(rr)!=1 or not rr[0]['complete'] or rr[0]['finish'].get('reason')!=0 or left is None or right is None or not left<command_order(rr[0]['finish'])<right or not start['tick']<=rr[0]['finish']['tick']<=winner['tick']:reasons.append('normal-parent-before-winner-unavailable')
            for oid,rows in objects.items():
                raw=[p for p in all_projectile_spawns or [] if p.get('projectileObjectId')==oid]
                if len(raw)!=1 or len(rows)!=1 or raw[0].get('ownerObjectId')!=player or raw[0].get('projectileCode')!=106031 or raw[0].get('tick')!=rows[0]['tick'] or command_order(raw[0])!=command_order(rows[0]):reasons.append('raw-filtered-wall-inventory-mismatch')
            possible=[];arrivals=[]
            for shot in all_projectile_spawns or []:
                if shot.get('projectileCode')!=106031:continue
                at=command_order(shot);tick=shot.get('tick');owner=shot.get('ownerObjectId')
                if owner in teams and owner!=player:continue
                if at is not None and left is not None and type(tick) is int and tick<start['tick'] and at<left:
                    if owner!=player or shot.get('projectileObjectId') not in roots:reasons.append('earlier-wall-not-exactly-closed')
                    continue
                possible.append(shot);oid=shot.get('projectileObjectId')
                if owner!=player or at is None or left is None or right is None or not left<at<right or not start['tick']<tick<=winner['tick']:reasons.append('possible-wall-identity-or-order-unavailable')
                if any(c.get('projectileObjectId')==oid for c in collisions):reasons.append('possible-wall-recorded-contact')
                tt=[t for t in terminals if t.get('objectId')==oid]
                if len(tt)!=1 or tt[0].get('event')!='CmdProjectileArrived' or tt[0].get('isCollision') is not False or command_order(tt[0]) is None or at is None or right is None or not at<command_order(tt[0])<right or not tick<=tt[0]['tick']<=winner['tick']:reasons.append('exact-noncollision-arrival-unavailable')
                else:arrivals.append(tt[0])
            if len(possible)!=1 or len({s.get('projectileObjectId') for s in possible})!=len(possible):reasons.append('all-possible-wall-inventory-not-unique')
            aa=[a for a in actions if a.get('skillIdCode')==wire and (a.get('sourceObjectId')==player or a.get('sourceObjectId') not in teams) and (command_order(a) is None or left is None or a.get('tick',start['tick'])>=start['tick'] or command_order(a)>=left)]
            if len(aa)!=1 or aa[0].get('actionNo')!=1 or aa[0].get('wireStatus') not in ORDINARY_ACTIONS or command_order(aa[0]) is None or left is None or right is None or not left<=command_order(aa[0])<right:reasons.append('unresolved-later-W-action')
            proof=dict(startTick=start['tick'],startOrder=left,parentFinish=rr[0].get('finish') if len(rr)==1 else None,possibleWalls=possible,arrivals=arrivals,winnerEnd=winner,unreferencedWallsAssignedToCast=False,wallCloseActionInvented=False,wallTerminalInvented=False)
            if reasons:policy_blocked.append(dict(proof,reasons=sorted(set(reasons))));continue
            unknown.pop(i);policy_evidence.append(proof)
    selected_contacts = [contacts[i] for i in combat if i not in unknown]
    row = _result(spec, selected_contacts, 'explicit-W-action-wall-object-reference-and-complete-collision-lifetime',
                  cast_ticks=[casts[i][0]['tick'] for i in combat if i not in unknown])
    row.update(knownWallObjectCount=len(roots), exactWallObjectReferences=len(roots),
               fixedDelayJoinUsed=False, explosionDamageCounted=False,
               interpretation='W 후속 행동이 직접 지정한 소유 유리벽의 완결된 수명 안에서 적 실험체 접촉을 셈. 시전 뒤 지연을 추정하지 않으며 폭발 피해는 별도.')
    if unknown:
        from .skill_lifecycle_result_policy import finalize_lifecycle_result
        row=finalize_lifecycle_result(row,combat,unknown)
        row.update(unreferencedWallEvidence=unreferenced,unreferencedWallsAssignedToCast=False,
            unresolvedWallActionEvidence=[dict(start=casts[i][0],createActions=[a for a in own if a['tick']==casts[i][0]['tick']],
                finishRecords=[r['finish'] for r in records if r['start'] is casts[i][0]]) for i in unknown],
            unresolvedCastTicks=[casts[i][0]['tick'] for i in combat if i in unknown],
            unresolvedUseEvidence=[dict(startTick=casts[i][0]['tick'],startOrder=command_order(casts[i][0]),
                reasons=[unknown[i]],hasRecordedEmission=None) for i in combat if i in unknown])
        if row.get('unresolvedCombatCastCount'):
            row.update(hitRateScope='verified-uses-only',wholeCombatHitRate=None,fullRequestedMetricComplete=False)
    if policy_evidence or policy_blocked:
        row.update(userPolicyWinnerMissEvidence=policy_evidence,userPolicyWinnerBlockedEvidence=policy_blocked,unreferencedWallEvidence=unreferenced,unreferencedWallsAssignedToCast=False)
    if policy_evidence:
        from .skill_development_cancellation import annotate_provisional
        annotate_provisional(row,'Explicit user W contact miss before actual winner across all possible remaining walls; no wall-parent edge or close command invented.')
        row.update(verifiedCombatCastCount=0,verifiedCompletionCredit=False,fullRequestedMetricComplete=False)
    return row
