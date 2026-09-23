"""Successive explosion waves linked by exact, exclusive lifecycle transitions."""
from collections import defaultdict
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids
    from .skill_wire_order import command_order, event_within_cast, finish_lookup
    from .skill_complete_projectile_lifetimes import candidate_projectile_family
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_action_stage_evidence import load_exact_skill_ids
    from skill_wire_order import command_order, event_within_cast, finish_lookup
    from skill_complete_projectile_lifetimes import candidate_projectile_family


def sequential_explosion_metric(spec, starts, finishes, actions, spawns, collisions,
                                 terminals, damages, player, teams, intervals, catalog, skill_rows,gaps=None):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    from .skill_lifecycle_result_policy import reviewed_rule_reuse_policy,annotate_reviewed_rule_reuse
    reuse_policy=reviewed_rule_reuse_policy(gaps,{'CmdStartSkill','CmdFinishSkill','CmdPlaySkillAction','CmdSpawn','CmdProjectile','CmdDestroy','CmdDamage'},starts,finishes,actions,spawns,collisions,terminals,damages)
    group=spec['skillGroup'];mode=spec['mode']
    if (spec['characterCode']!=77 or group!=1077500 or spec['unit']!='skill-cast'
            or mode not in {'any','first-hit','end-hit','both-hit'}):
        return _unavailable(spec,'검토된 연속 폭발 전이 규칙 없음')
    definition=catalog['skillGroups'].get(str(group),{})
    if (definition.get('characterCode'),definition.get('skillId'))!=(77,'YuMinActive4'):
        return _unavailable(spec,'정확한 연속 폭발 스킬 정체성 불일치')
    expected={107751:'Projectile_YuMin_Skill04_Range',107752:'Projectile_YuMin_Skill04_Range_Second'}
    family,codes=candidate_projectile_family(spec,catalog)
    if family!={group} or codes!=set(expected):
        return _unavailable(spec,'전체 연속 폭발 후보 계열이 검토한 두 단계와 다름')
    for code,name in expected.items():
        p=catalog['projectileDefinitions'].get(str(code),{})
        if p.get('prefabName')!=name or not p.get('isExplosion') or not p.get('isExplosionWithoutCollision'):
            return _unavailable(spec,'정확한 연속 폭발 ProjectileSetting 정의 불일치')
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==group]
    wire=load_exact_skill_ids()['YuMinActive4'];code_groups={s['code']:s['group'] for s in skill_rows}
    if not selected or any(s['skillIdCode']!=wire or code_groups.get(s['skillCode'])!=group for s in selected):
        return _unavailable(spec,'연속 폭발 시전 누락 또는 wire 정체성 불일치')
    casts,why=exact_cast_lifetimes(selected,finishes,player)
    if why:return _unavailable(spec,why)
    lookup=finish_lookup(finishes,player)
    from .skill_projectile_active_end import projectile_active_end_records
    active_ends=projectile_active_end_records(terminals)
    objects={};by_use=defaultdict(list);roots={};ends=defaultdict(list)
    for e in terminals:ends[e['objectId']].append(e)
    for spawn in spawns:
        if spawn['ownerPlayerObjectId']!=player or spawn['projectileCode'] not in expected:continue
        oid=spawn['projectileObjectId']
        if oid in objects:return _unavailable(spec,'연속 폭발 객체 ID 재사용 또는 중복 생성')
        ev=ends[oid]
        get=lambda name:[e for e in ev if e['event']==name]
        arrived,exploded=get('CmdProjectileArrived'),get('CmdProjectileExplosion')
        closure=active_ends.get(oid,{})
        if len(arrived)!=1 or len(exploded)!=1 or not closure.get('complete'):
            return _unavailable(spec,'연속 폭발의 실제 도착·폭발·소멸 수명 미완결')
        # Both explosion phases must already exist; activity removal closes
        # this object without waiting for its later visual retirement.
        d=next(e for e in ev if e['event'] in closure['terminalCommands'] and e['tick']==closure['endTick'])
        a,x=arrived[0],exploded[0]
        position=a.get('arrivedPosVector2')
        if (not isinstance(position,list) or len(position)!=2 or any(type(v) not in {int,float} for v in position)
                or not spawn['tick']<=a['tick']<x['tick']<=d['tick']):
            return _unavailable(spec,'연속 폭발의 실제 위치 또는 도착·폭발 순서 미확정')
        objects[oid]=(spawn,a,x,d)
        if spawn['projectileCode']==107751:
            order=command_order(spawn)
            owners=[i for i,(s,end) in enumerate(casts) if command_order(s) is not None and order is not None
                    and command_order(s)[0]==order[0] and s['tick']==spawn['tick']
                    and event_within_cast(s,end,spawn,lookup)]
            if len(owners)!=1:return _unavailable(spec,'첫 폭발 객체가 같은 원본 명령 묶음의 한 시전에 연결되지 않음')
            i=owners[0]
            marks=[a for a in actions if a['sourceObjectId']==player and a['skillIdCode']==wire
                   and a['tick']==spawn['tick'] and a['actionNo']==1]
            if len(marks)!=1:return _unavailable(spec,'첫 폭발 생성의 정확한 스킬 행동 표본 부족')
            if any(s['playerObjectId']==player and s['skillGroup']!=group and command_order(s) is not None
                   and command_order(s)[0]==order[0] for s in starts):
                return _unavailable(spec,'같은 생성 명령 묶음에 경쟁 시전이 있어 배타성 미확정')
            roots[oid]=i;by_use[i].append(oid)
    cancelled=set()
    for i,(s,end) in enumerate(casts):
        if len(by_use[i])==1:continue
        finish=[f for f in finishes if f['playerObjectId']==player and f['skillIdCode']==wire and f['tick']==end]
        if not by_use[i] and len(finish)==1 and finish[0]['reason'] in set(range(1,15))|{16,17}:
            cancelled.add(i)
        else:return _unavailable(spec,'정상 시전의 첫 폭발 객체 누락·중복')
    children={}
    for oid,(spawn,a,x,d) in objects.items():
        if spawn['projectileCode']!=107752:continue
        # Exact explosion -> spawn/arrival at the identical recorded location.
        # A fixed delay, closest cast, and numeric effect-code join are absent.
        parents=[pid for pid,(p,pa,px,pd) in objects.items() if p['projectileCode']==107751
                 and px['tick']==spawn['tick']==a['tick'] and pa['arrivedPosVector2']==a['arrivedPosVector2']]
        if len(parents)!=1 or parents[0] in children:
            return _unavailable(spec,'첫 폭발 종료와 둘째 생성의 위치·사건 전이가 일대일이 아님')
        parent=parents[0];children[parent]=oid;roots[oid]=roots[parent]
    if set(children)!={o for o,v in objects.items() if v[0]['projectileCode']==107751}:
        return _unavailable(spec,'첫 폭발 뒤 둘째 폭발 객체가 누락됨')
    hits={phase:[set() for _ in casts] for phase in ['first-hit','end-hit']}
    all_contacts=defaultdict(set)
    for c in collisions:
        oid=c['projectileObjectId'];target=c['targetObjectId']
        if oid not in objects or target not in teams or teams[target]==teams[player]:continue
        spawn,a,x,d=objects[oid]
        if c['tick']!=x['tick']:return _unavailable(spec,'적 충돌이 해당 객체의 실제 폭발 시점과 다름')
        phase='first-hit' if spawn['projectileCode']==107751 else 'end-hit'
        hits[phase][roots[oid]].add((c['tick'],target));all_contacts[c['tick'],target].add(oid)
    damage={(d['tick'],d['targetObjectId']) for d in damages if d['attackerObjectId']==player
            and d.get('effectCode') in {1077503,1077504} and d['targetObjectId'] in teams and teams[d['targetObjectId']]!=teams[player]}
    if set(all_contacts)!=damage or any(len(o)!=1 for o in all_contacts.values()):
        return _unavailable(spec,'두 폭발의 적 충돌과 실제 전체 피해가 배타적으로 일치하지 않음')
    if any(not any(h) for h in hits.values()) and reuse_policy is None:
        return _unavailable(spec,'각 폭발 단계의 실제 적 충돌·피해 검증 표본 부족')
    first,last=hits.values()
    if mode=='both-hit':
        contacts=[{(min(t for t,x in b if x==enemy),enemy) for enemy in {x for _,x in a}&{x for _,x in b}} for a,b in zip(first,last)]
    else:contacts=[a|b for a,b in zip(first,last)] if mode=='any' else hits[mode]
    chosen=[i for i,(s,end) in enumerate(casts) if any(l<=s['tick']<r for l,r in intervals)]
    row=_result(spec,[contacts[i] for i in chosen],'exact-explosion-spawn-location-transition-two-waves',
                cast_ticks=[casts[i][0]['tick'] for i in chosen])
    row.update(verifiedProjectileCount=len(objects),verifiedFollowupTransitionCount=len(children),
        cancelledBeforeAttackCount=len(cancelled.intersection(chosen)),bothHitRequiresSameEnemy=mode=='both-hit',
        fixedDurationWindowUsed=False,nearestCastUsed=False,effectCodeUsedAsStage=False,
        arrivalCollisionFlagUsedAsHit=False,interpretation='두 폭발 객체를 실제 폭발→생성·도착 위치 전이로 연결. 적중 사용·사용별 서로 다른 적·같은 적 양쪽 타격·반복 접촉을 분리.')
    return annotate_reviewed_rule_reuse(row,reuse_policy,positive_gate_unsatisfied=any(not any(h) for h in hits.values()))
