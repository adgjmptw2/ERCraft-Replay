"""One cast -> one exact owned object -> outcomes during its closed lifetime.

The same graph supports damage and actual CC. Prefab/effect names establish
reviewed candidates, never numeric foreign keys or inferred CC from damage.
"""
from collections import defaultdict
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_action_stage_evidence import load_exact_skill_ids

CONFIG={
    1036300:dict(character=36,skill='EvaActive2',summon=1160,objectType='SummonTrap',wireType=10,
                prefab='Eva_Skill02_Circle',effects={},hitActions={4001,4002},removalClosesOutcome=True),
    1060500:dict(character=60,skill='TaziaActive4',summon=1370,objectType='SummonServant',wireType=11,
                prefab='FX_BI_Tazia_Skill04',effects={
                    1060501:('FX_BI_Tazia_Skill04_Hit','Tazia_Skill04_Hit'),
                    1060502:('FX_BI_Tazia_Skill04_ExplosionHit','Tazia_Skill04_ExplosionHit')}),
    1054500:dict(character=54,skill='KarlaActive4',summon=1293,objectType='SummonArtifact',wireType=21,
                prefab='FX_BI_Karla_Skill04_Spear',effects={
                    1054501:('FX_BI_Karla_Skill04_Hit',''),
                    1054502:('FX_BI_Karla_Skill04_Hit','Karla_Skill04_EndHit'),
                    1054503:('FX_BI_Karla_Skill04_Hit_02','')},stunGroup=1054520,stunEffect=1054503),
}
GAMEPLAY_CANCELS=set(range(1,15))|{16,17}


def owned_object_cast_graph(group,starts,finishes,summons,terminals,player,catalog,
                            skill_rows,summon_rows,skill_ids=None):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    cfg=CONFIG[group];definition=catalog['skillGroups'].get(str(group),{})
    objects=[r for r in summon_rows if r['code']==cfg['summon']]
    if (definition.get('skillId')!=cfg['skill'] or definition.get('characterCode')!=cfg['character'] or
        len(objects)!=1 or (objects[0].get('objectType'),objects[0].get('prefabPath'))!=(cfg['objectType'],cfg['prefab'])):
        return None,'정확한 gameDb의 스킬·소환 객체 정체성 불일치'
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    code_groups={r['code']:r['group'] for r in skill_rows}
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==group]
    if not selected or any(s['skillIdCode']!=ids.get(cfg['skill']) or code_groups.get(s['skillCode'])!=group for s in selected):
        return None,'본체 시전 누락 또는 wire 정체성 불일치'
    casts,why=exact_cast_lifetimes(selected,finishes,player)
    if why:return None,why
    ends=defaultdict(set);spawn_counts=defaultdict(int)
    for t in terminals:
        if t['event']=='CmdDestroy':ends[t['objectId']].add(t['tick'])
    if cfg.get('removalClosesOutcome'):
        from .skill_projectile_active_end import projectile_active_end_records
        removal=projectile_active_end_records(terminals)
        requested_objects={s['objectId'] for s in summons if s.get('ownerObjectId')==player and s.get('summonCode')==cfg['summon']}
        for oid,end in removal.items():
            if oid not in requested_objects:continue
            if not end['complete']:return None,'conflicting owned object removal records'
            ends[oid]={end['endTick']}
    for s in summons:spawn_counts[s['objectId']]+=1
    roots={};by_cast=defaultdict(list);cancelled=set()
    for s in summons:
        if s['ownerObjectId']!=player or s['summonCode']!=cfg['summon']:continue
        oid=s['objectId']
        if (spawn_counts[oid]!=1 or s.get('identityVerifiedAgainstGameDb') is not True or
            s['objectType']!=cfg['wireType'] or len(ends[oid])>1 or (ends[oid] and next(iter(ends[oid]))<s['tick'])):
            return None,'전용 객체의 소유·생성·최종 소멸 기록 불완전'
        parents=[i for i,(start,end) in enumerate(casts) if start['tick']<=s['tick']<=end]
        if len(parents)!=1:return None,'전용 객체 생성을 단 하나의 실제 시전에 연결하지 못함'
        i=parents[0];roots[oid]=dict(start=s['tick'],end=next(iter(ends[oid]),None),castIndex=i);by_cast[i].append(oid)
    reasons={(f['skillIdCode'],f['tick']):f.get('reason') for f in finishes if f['playerObjectId']==player}
    for i,(s,end) in enumerate(casts):
        if len(by_cast[i])==1:continue
        if not by_cast[i] and reasons.get((s['skillIdCode'],end)) in GAMEPLAY_CANCELS:cancelled.add(i)
        else:return None,'정상 사용과 전용 객체 생성이 일대일로 연결되지 않음'
    return dict(casts=casts,roots=roots,cancelled=cancelled),None


def owned_object_metric(spec,starts,finishes,summons,terminals,damages,states,player,teams,
                         intervals,catalog,skill_rows,summon_rows,state_rows,state_groups,effect_rows,skill_ids=None,gaps=None,actions=None):
    if gaps and any(g.get('count',0) and g.get('packetName') in {'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdDestroy','CmdDamage','CmdAddState','CmdStartStateSkill','CmdFinishStateSkill'} for g in gaps):
        return _unavailable(spec,'lifecycle evidence has required-stream decode gaps')
    group=spec['skillGroup'];cfg=CONFIG.get(group)
    if not cfg or spec['characterCode']!=cfg['character'] or spec['unit']!='skill-cast' or spec['mode'] not in {'any','stun'}:
        return _unavailable(spec,'검토된 전용 객체 결과 규칙 없음')
    if spec['mode']=='stun' and 'stunGroup' not in cfg:return _unavailable(spec,'해당 객체의 기절 규칙 없음')
    effects=defaultdict(list)
    for e in effect_rows:effects[e['code']].append(e)
    for code,identity in cfg['effects'].items():
        if len(effects[code])!=1 or (effects[code][0].get('effectPrefabName'),effects[code][0].get('soundName'))!=identity:
            return _unavailable(spec,'정확한 전용 타격 효과 계열 정의 불일치')
    graph,why=owned_object_cast_graph(group,starts,finishes,summons,terminals,player,catalog,skill_rows,summon_rows,skill_ids)
    if why:return _unavailable(spec,why)
    casts=graph['casts'];roots=graph['roots'];contacts=[set() for _ in casts];packets=[0 for _ in casts]
    action_unknown={}
    if cfg.get('hitActions'):
        if actions is None or gaps and any(g.get('count',0) and g.get('packetName') in {'CmdPlaySkillAction','CmdPlaySkillActionWithTargets','CmdDestroyDelayStart'} for g in gaps):
            return _unavailable(spec,'owned action outcome stream missing or incomplete')
        ids=load_exact_skill_ids() if skill_ids is None else skill_ids
        for action in actions:
            if action.get('sourceObjectId')!=player or action.get('skillIdCode')!=ids.get(cfg['skill']):continue
            if action.get('actionNo') not in cfg['hitActions']:continue
            if action.get('wireStatus')!='decoded-exact-CmdPlaySkillActionWithTargets':
                return _unavailable(spec,'owned hit action target format is not exact')
            matching=[r for r in roots.values() if r['start']<=action['tick'] and (r['end'] is None or action['tick']<=r['end'])]
            if len(matching)!=1:return _unavailable(spec,'owned hit action has no unique recorded object lifetime')
            i=matching[0]['castIndex']
            for target in action.get('targets',[]):
                oid=target.get('targetObjectId')
                if oid not in teams or teams[oid]==teams[player]:continue
                if target.get('sameTickDirectPlayerDamage') is not True:
                    action_unknown[i]='owned-hit-action-without-damage-corroboration';continue
                contacts[i].add((action['tick'],oid));packets[i]+=1
        outcomes=[]
    elif spec['mode']=='stun':
        sg=cfg['stunGroup'];definitions=[s for s in state_groups if s['group']==sg]
        if len(definitions)!=1 or (definitions[0].get('stateType'),definitions[0].get('effectType'),definitions[0].get('startEffectAndSound'))!=('Stun','Debuff',cfg['stunEffect']):
            return _unavailable(spec,'전용 기절의 실제 상태·효과 FK 정의 불일치')
        codes={s['code'] for s in state_rows if s['group']==sg}
        if not codes:return _unavailable(spec,'전용 기절 상태 코드 없음')
        outcomes=[s for s in states if s['event']=='add' and s.get('stateCode') in codes]
    else:outcomes=[d for d in damages if d.get('effectCode') in cfg['effects']]
    before_first_cast=[]
    first_cast_tick=min(s['tick'] for s,_ in casts)
    for event in outcomes:
        target=event.get('targetObjectId')
        if target not in teams or teams[target]==teams[player]:continue
        owner=event.get('casterObjectId' if spec['mode']=='stun' else 'attackerObjectId')
        if owner not in teams and owner not in roots:return _unavailable(spec,'전용 결과 이벤트의 실제 공격자·시전자 미확정')
        if owner!=player and owner not in roots:continue
        if spec['mode']=='stun' and event.get('stateGroup') not in (None,sg):return _unavailable(spec,'실제 기절 코드·그룹 불일치')
        matching=[r for oid,r in roots.items() if (owner==player or owner==oid) and r['start']<=event['tick'] and (r['end'] is None or event['tick']<=r['end'])]
        if len(matching)!=1:
            if not matching and event['tick']<first_cast_tick:
                # An earlier event cannot belong to a later recorded cast.
                # Retain its existence without inventing an absent denominator.
                before_first_cast.append(dict(tick=event['tick'],effectCode=event.get('effectCode'),stateCode=event.get('stateCode')))
                continue
            return _unavailable(spec,'전용 결과가 소유 객체 수명 밖에 있거나 여러 객체와 겹침')
        i=matching[0]['castIndex'];contacts[i].add((event['tick'],target));packets[i]+=1
    incomplete={r['castIndex'] for r in roots.values() if r['end'] is None}
    unknown={i:'owned-object-retirement-missing-without-positive' for i in incomplete if not contacts[i]}
    unknown.update({i:reason for i,reason in action_unknown.items() if not contacts[i]})
    chosen=[i for i,(s,_) in enumerate(casts) if any(l<=s['tick']<r for l,r in intervals)]
    valid=[i for i in chosen if i not in unknown]
    row=_result(spec,[contacts[i] for i in valid],
                'exact-owned-object-lifetime-reviewed-damage-family' if spec['mode']=='any' else 'exact-owned-object-lifetime-and-state-FK-stun',
                cast_ticks=[casts[i][0]['tick'] for i in valid])
    row.update(verifiedWholeMatchRootObjectCount=len(roots),cancelledBeforeAttackCount=len(graph['cancelled'].intersection(chosen)),
               nearestCastUsed=False,fixedDurationWindowUsed=False,numericSkillStateCodeJoinUsed=False,
               measuredOutcome='all-root-object-damage' if spec['mode']=='any' else 'enemy-stun-application',
               interpretation='본체 사용에 연결된 전용 객체의 실제 수명 내 결과. 사용별 적중·서로 다른 적·반복 접촉을 구분.')
    if spec['mode']=='any':row['exactDamagePacketCount']=sum(packets[i] for i in valid)
    if before_first_cast:
        row.update(preFirstRecordedCastOutcomeCount=len(before_first_cast),
                   preFirstRecordedCastOutcomes=before_first_cast,unrecordedCastDenominatorInvented=False)
    from .skill_lifecycle_result_policy import finalize_lifecycle_result
    row=finalize_lifecycle_result(row,chosen,unknown,incomplete_positive=incomplete-set(unknown),observed_positive=any(contacts))
    if cfg.get('hitActions'):
        from .skill_development_cancellation import annotate_provisional
        row=annotate_provisional(row,'Exact summon owner/lifetime and skill-specific hit action with same-tick player damage; recorded removal closes the outcome provisionally.')
        row.update(method='owned-summon-skill-action-corroborated-damage',exactSummonCodes=[cfg['summon']],
                   hitActionNumbers=sorted(cfg['hitActions']),passiveProjectileUsed=False,
                   measuredOutcome='owned-summon-initial-and-removal-hit',
                   exactDamagePacketCount=None,corroboratedActionTargetCount=sum(packets[i] for i in valid))
    return row
