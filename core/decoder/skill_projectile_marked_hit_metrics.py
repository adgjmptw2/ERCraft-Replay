"""Explicit single-projectile stages corroborated by damage and a dedicated mark.

Arrival flags do not replace actual object lifetime/collision evidence. A mark
is independent corroboration, not a requirement to call every damage hit valid.
"""
from collections import Counter,defaultdict
try:
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import event_within_cast,finish_lookup
    from .skill_action_stage_evidence import load_exact_skill_ids
    from .requested_skill_hit_rates import _result,_unavailable
except ImportError:
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import event_within_cast,finish_lookup
    from skill_action_stage_evidence import load_exact_skill_ids
    from requested_skill_hit_rates import _result,_unavailable

CONFIG={1046400:dict(character=46,skill='AidenActive3_BackStep',projectile=104641,
    prefab='Projectile_FX_BI_Aiden_Skill03_Backstep',markCode=1046401,
    markGroup=1046400,markSkill='AIdenBackStepMark')}


def projectile_marked_hit_metric(spec,starts,finishes,spawns,collisions,terminals,
                                 damages,states,player,teams,intervals,catalog,
                                 skill_rows,state_rows,state_groups,skill_ids=None,gaps=None):
    from .skill_lifecycle_result_policy import reviewed_rule_reuse_policy,annotate_reviewed_rule_reuse
    reuse_policy=reviewed_rule_reuse_policy(gaps,{'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdProjectile','CmdDestroy','CmdDamage','CmdAddState'},starts,finishes,spawns,collisions,terminals,damages,states)
    cfg=CONFIG.get(spec['skillGroup'])
    if not cfg or spec['mode']!='any' or spec['unit']!='skill-cast':return _unavailable(spec,'검토된 단일 발사체·표식 규칙 없음')
    definition=catalog['skillGroups'].get(str(spec['skillGroup']),{})
    projectile=catalog['projectileDefinitions'].get(str(cfg['projectile']),{})
    sr=[r for r in state_rows if r['code']==cfg['markCode']];sg=[r for r in state_groups if r['group']==cfg['markGroup']]
    if ((definition.get('characterCode'),definition.get('skillId'))!=(cfg['character'],cfg['skill']) or
        projectile.get('prefabName')!=cfg['prefab'] or projectile.get('collisionEnabled') is not True or
        projectile.get('isExplosion') or projectile.get('isExplosionWithoutCollision') or len(sr)!=1 or
        sr[0]['group']!=cfg['markGroup'] or len(sg)!=1 or sg[0].get('skillId')!=cfg['markSkill']):
        return _unavailable(spec,'스킬·실제 발사체·독립 표식 정체성 불일치')
    wire=(load_exact_skill_ids() if skill_ids is None else skill_ids).get(cfg['skill'])
    codes={r['code']:r['group'] for r in skill_rows}
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==spec['skillGroup']]
    if not selected or any(s['skillIdCode']!=wire or codes.get(s['skillCode'])!=spec['skillGroup'] for s in selected):
        return _unavailable(spec,'시전 없음 또는 정확한 wire 스킬 정체성 불일치')
    records,reason=ordered_cast_records(selected,finishes,player)
    if reason:return _unavailable(spec,reason)
    lookup=finish_lookup(finishes,player);by_use=defaultdict(list);objects={}
    unknown={i:r['reason'] for i,r in enumerate(records) if not r['complete']}
    for s in spawns:
        if s['ownerPlayerObjectId']!=player or s['projectileCode']!=cfg['projectile']:continue
        candidates=[i for i,r in enumerate(records) if (event_within_cast(r['start'],r['finish']['tick'],s,lookup) if r['finish'] else s['tick']>=r['start']['tick'])]
        if len(candidates)!=1 or s['projectileObjectId'] in objects:return _unavailable(spec,'실제 발사체의 사용 귀속 중복 또는 누락')
        i=candidates[0];objects[s['projectileObjectId']]=(i,s);by_use[i].append(s)
    cancelled=set()
    for i,r in enumerate(records):
        if not r['complete']:continue
        if len(by_use[i])==1:continue
        if not by_use[i] and r['finish']['reason'] in (set(range(1,15))|{16,17}):cancelled.add(i)
        else:unknown[i]='normal-use-without-one-recorded-projectile'
    if not objects:return _unavailable(spec,'실제 발사체 없음')
    from .skill_projectile_active_end import projectile_active_end_records
    active_ends=projectile_active_end_records(terminals)
    ends={oid:{e['endTick']} if e.get('complete') else set() for oid,e in active_ends.items()}
    ends=defaultdict(set,ends)
    own_ids={s['projectileObjectId'] for s in spawns if s['ownerPlayerObjectId']==player}
    damage={(d['tick'],d['targetObjectId']) for d in damages if d['attackerObjectId']==player or d['attackerObjectId'] in own_ids}
    hits=[set() for _ in records];contact_owners=defaultdict(set)
    for oid,(i,s) in objects.items():
        if len(ends[oid])!=1 or next(iter(ends[oid]))<s['tick']:unknown[i]='projectile-final-destruction-unavailable'
    for c in collisions:
        oid=c['projectileObjectId'];target=c['targetObjectId']
        if oid not in objects:continue
        i,s=objects[oid]
        if c['tick']<s['tick'] or (len(ends[oid])==1 and c['tick']>next(iter(ends[oid]))):unknown[i]='collision-outside-projectile-lifetime'
        if target not in teams or teams[target]==teams[player]:continue
        contact=(c['tick'],target);contact_owners[contact].add(i)
        if contact not in damage:unknown[i]='enemy-collision-without-same-tick-damage'
        else:hits[i].add(contact)
    marks=[set() for _ in records]
    for s in states:
        target=s.get('targetObjectId')
        if s.get('event')!='add' or s.get('casterObjectId')!=player or s.get('stateCode')!=cfg['markCode'] or target not in teams or teams[target]==teams[player]:continue
        contact=(s['tick'],target);owners=contact_owners[contact]
        if len(owners)!=1:return _unavailable(spec,'전용 표식의 대상과 발사체 충돌 귀속 불일치')
        i=next(iter(owners))
        if contact not in hits[i]:unknown[i]='mark-without-corroborated-projectile-damage'
        marks[i].add(contact)
    if not any(marks) and reuse_policy is None:return _unavailable(spec,'독립된 전용 표식으로 교차 검증할 적 타격 표본 없음')
    combat=[i for i,r in enumerate(records) if any(l<=r['start']['tick']<end for l,end in intervals)]
    valid=[i for i in combat if i not in unknown]
    row=_result(spec,[hits[i] for i in valid],'exact-single-projectile-lifetime-damage-and-dedicated-mark',
                cast_ticks=[records[i]['start']['tick'] for i in valid])
    row.update(perUseCompletenessTracked=True,observedCombatCastCount=len(combat),verifiedCombatCastCount=len(valid),
        unresolvedCombatCastCount=len(combat)-len(valid),unresolvedAllCastCount=len(unknown),
        unresolvedCastReasons=dict(Counter(unknown[i] for i in combat if i in unknown)),
        dedicatedMarkContactCount=sum(len(marks[i]) for i in valid),actualProjectileCount=sum(len(by_use[i]) for i in valid),
        cancelledBeforeAttackCount=sum(i in cancelled for i in valid),incompleteUsesCountedAsMisses=False,
        numericSkillStateCodeJoinUsed=False,markAbsenceCountedAsMiss=False,
        interpretation='실제 사용별 한 발 생성·최종 소멸·대상 충돌·동시 피해를 연결하고 별도 전용 표식으로 검증. 표식 미적용을 피해 미적중으로 간주하지 않음.')
    return annotate_reviewed_rule_reuse(row,reuse_policy,positive_gate_unsatisfied=not any(marks))
