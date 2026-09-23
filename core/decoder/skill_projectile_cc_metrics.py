"""Actual CC applications linked to exact owned projectile collisions.

The state definition explicitly references its effect. Neither numeric proximity
nor damage is a state-to-skill join. Full emission/cast and object lifetimes plus
same-tick, same-target application/collision are required for the reviewed route.
"""
from collections import defaultdict
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_action_stage_evidence import load_exact_skill_ids


CONFIG={
    1041200:('JohannActive1_1',41,
        {1041210:(1041221,'FX_BI_Johann_Skill01_Fetter')},
        {104111:'Projectile_FX_BI_Johann_Skill01'}),
    1062400:('TheodoreActive3',62,
        {1062410:(1062405,'FX_BI_Theodore_Skill03_Fetter')},
        {106241:'Projectile_FX_BI_Theodore_Skill03',106242:'Projectile_FX_BI_Theodore_Skill03_Reinforce'}),
    1087400:('CoralineActive3',87,
        {1087400:(1087407,'FX_BI_Coraline_Skill03_Fetter'),
         1087410:(1087432,'FX_BI_Coraline_Skill03_Fetter_White'),
         1087440:(1087431,'FX_BI_Coraline_Skill03_Fetter_Black')},
        {108741:'Projectile_FX_BI_Coraline_Skill03',108742:'Projectile_FX_BI_Coraline_Skill03_White',
         108743:'Projectile_FX_BI_Coraline_Skill03_Black'}),
}


def projectile_cc_metric(spec,starts,finishes,spawns,collisions,terminals,states,
                         player,teams,intervals,catalog,skill_rows,state_rows,
                         state_groups,effect_rows,skill_ids=None):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    group=spec['skillGroup'];cfg=CONFIG.get(group)
    if not cfg or spec['mode']!='fetter' or spec['unit']!='skill-cast':
        return _unavailable(spec,'검증되지 않은 투사체 CC 연결 지표')
    name,character,links,projectiles=cfg
    definition=catalog['skillGroups'].get(str(group),{})
    effects={r['code']:r for r in effect_rows}
    if (spec['characterCode']!=character or definition.get('characterCode')!=character or
            definition.get('skillId')!=name or any(catalog['projectileDefinitions'].get(str(code),{}).get('prefabName')!=prefab
            for code,prefab in projectiles.items())):
        return _unavailable(spec,'같은 gameDb의 명시 스킬·투사체 정의 불일치')
    for sg,(effect,prefab) in links.items():
        definitions=[r for r in state_groups if r.get('group')==sg]
        if (len(definitions)!=1 or definitions[0].get('stateType')!='Fetter' or
                definitions[0].get('effectType')!='Debuff' or definitions[0].get('startEffectAndSound')!=effect or
                effects.get(effect,{}).get('effectPrefabName')!=prefab):
            return _unavailable(spec,'속박 상태의 명시 효과 참조·종류 불일치')
    state_code_groups={r['code']:r['group'] for r in state_rows}
    codes={code for code,sg in state_code_groups.items() if sg in links}
    if any(sg not in state_code_groups.values() for sg in links):
        return _unavailable(spec,'속박 그룹의 실제 상태 코드 없음')
    # An effect reused by another state cannot establish the requested skill.
    foreign_groups={r['group'] for r in state_groups if r.get('startEffectAndSound') in
                    {x[0] for x in links.values()} and r['group'] not in links}
    foreign_codes={code for code,sg in state_code_groups.items() if sg in foreign_groups}
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    skill_code_groups={r['code']:r['group'] for r in skill_rows}
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==group]
    if not selected or any(s['skillIdCode']!=ids.get(name) or skill_code_groups.get(s['skillCode'])!=group for s in selected):
        return _unavailable(spec,'시전 누락 또는 wire 스킬 정의 불일치')
    lives,reason=exact_cast_lifetimes(selected,finishes,player)
    if reason:return _unavailable(spec,reason)
    ends=defaultdict(set);contacts=defaultdict(set);by_cast=defaultdict(list);objects=[]
    for t in terminals:
        if t['event']=='CmdDestroy':ends[t['objectId']].add(t['tick'])
    for c in collisions:contacts[c['projectileObjectId']].add((c['tick'],c['targetObjectId']))
    for spawn in spawns:
        if spawn['ownerPlayerObjectId']!=player or spawn['projectileCode'] not in projectiles:continue
        owners=[i for i,(s,end) in enumerate(lives) if s['tick']<=spawn['tick']<=end]
        if len(owners)!=1:
            return _unavailable(spec,'후속 투사체가 본체 시전 밖에서 생성됨; 실제 부모 연결 없이 최근 시전에 귀속하지 않음')
        oid=spawn['projectileObjectId']
        if len(ends[oid])!=1 or next(iter(ends[oid]))<spawn['tick']:
            return _unavailable(spec,'투사체 최종 소멸이 없어 속박 미적용을 실패로 확정하지 않음')
        end=next(iter(ends[oid]))
        if any(not spawn['tick']<=tick<=end for tick,_ in contacts[oid]):
            return _unavailable(spec,'실제 충돌이 투사체 수명 밖에 존재함')
        if any(sp['projectileObjectId']==oid for _,sp,_ in objects):
            return _unavailable(spec,'중복 투사체 생성 정체성으로 사용 연결을 확정하지 않음')
        by_cast[owners[0]].append(oid);objects.append((owners[0],spawn,end))
    cancelled=set()
    reasons={(f['skillIdCode'],f['tick']):f['reason'] for f in finishes if f['playerObjectId']==player}
    for i,(s,end) in enumerate(lives):
        if len(by_cast[i])==1:continue
        if not by_cast[i] and reasons[s['skillIdCode'],end] in set(range(1,15))|{16,17}:cancelled.add(i)
        else:return _unavailable(spec,'정상 사용과 실제 한 발 생성의 대응 불완전; 재발사·확산은 별도 부모 연결 필요')
    applied=[set() for _ in lives]
    for state in states:
        code=state.get('stateCode');target=state['targetObjectId']
        if (state['event']!='add' or code not in codes|foreign_codes or target not in teams or
                teams[target]==teams[player]):continue
        if state['casterObjectId'] not in teams:return _unavailable(spec,'속박 상태의 실제 시전자 미확정')
        if state['casterObjectId']!=player:continue
        if state.get('stateGroup') not in (None,state_code_groups[code]):
            return _unavailable(spec,'실제 상태 코드와 명시 그룹 불일치')
        matches=[i for i,sp,end in objects if sp['tick']<=state['tick']<=end and
                 (state['tick'],target) in contacts[sp['projectileObjectId']]]
        if code in foreign_codes:
            if matches:return _unavailable(spec,'같은 효과를 공유하는 다른 속박 상태가 실제 충돌에 겹침')
            continue
        if len(matches)!=1:
            return _unavailable(spec,'속박 적용이 실제 투사체 충돌 시각·대상 하나에 연결되지 않음; 지연·거울 재발사 부모 연결 필요')
        applied[matches[0]].add((state['tick'],target))
    if not any(applied):return _unavailable(spec,'실제 적 속박 적용과 투사체 충돌의 검증 표본 없음')
    chosen=[i for i,(s,_) in enumerate(lives) if any(left<=s['tick']<right for left,right in intervals)]
    row=_result(spec,[applied[i] for i in chosen],'explicit-state-effect-FK-and-exact-complete-projectile-collision-CC')
    row.update(measuredOutcome='enemy-fetter-application',exactStateGroups=sorted(links),
        sharedEffectOtherStateGroups=sorted(foreign_groups),actualLinkedProjectileCount=sum(len(by_cast[i]) for i in chosen),
        cancelledBeforeAttackCount=len(cancelled.intersection(chosen)),damageUsedAsCCProof=False,
        numericSkillStateCodeJoinUsed=False,nearestCastUsed=False,fixedDurationWindowUsed=False,
        formsSeparated=False,interpretation='교전 중 본체 발사 사용 대비 실제 적 속박 적용. 동일 사용의 같은 대상은 다인 수에서 한 명. 형태별 분모 및 아군 효과는 별도 미확정.')
    return row
