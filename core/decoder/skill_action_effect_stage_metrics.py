"""Reviewed same-tick action/effect stages with explicit per-use completeness.

One calculator serves emission callbacks and on-hit callbacks. FX names are
identity checks for reviewed event codes, never a numeric Skill foreign key.
"""
from collections import Counter,defaultdict
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import event_within_cast,finish_lookup
    from .skill_action_stage_evidence import load_exact_skill_ids,ORDINARY_ACTIONS
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import event_within_cast,finish_lookup
    from skill_action_stage_evidence import load_exact_skill_ids,ORDINARY_ACTIONS

CONFIG={
    1046410:dict(character=46,skill='AidenActive3_VoltRush',emissionAction=1,
        phases={'any':(1,1046421,'FX_BI_Aiden_Skill03_Voltrush_Hit','Aiden_Skill03_Rush_Hit')}),
    1047200:dict(character=47,skill='LauraActive1_1',emissionAction=1,
        phases={'any':(1,1047204,'FX_BI_Laura_Skill01_Hit02_S','Laura_Skill01_Hit02_S')}),
    1083500:dict(character=83,skill='HenryActive4',
        phases={'first-hit':(2,1083503,'FX_BI_Henry_Skill04_Hit',''),
                'end-hit':(3,1083504,'FX_BI_Henry_Skill04_Hit_Explode','')},
        cc=dict(mode='stun',phase='end-hit',code=1083511,group=1083510,stateType='Stun',skillId='Stun')),
}


def action_effect_stage_metric(spec,starts,finishes,actions,damages,states,player,
                                teams,intervals,catalog,skill_rows,effect_rows,
                                state_rows,state_groups,skill_ids=None,gaps=None):
    from .skill_lifecycle_result_policy import reviewed_rule_reuse_policy,annotate_reviewed_rule_reuse
    reuse_policy=reviewed_rule_reuse_policy(gaps,{'CmdStartSkill','CmdFinishSkill','CmdPlaySkillAction','CmdDamage','CmdAddState'},starts,finishes,actions,damages,states)
    group=spec['skillGroup'];cfg=CONFIG.get(group);mode=spec['mode']
    if not cfg or mode not in {'any',*cfg['phases'],cfg.get('cc',{}).get('mode')} or spec['unit']!='skill-cast':
        return _unavailable(spec,'검토된 행동·피해 단계 규칙 없음')
    definition=catalog['skillGroups'].get(str(group),{})
    if (definition.get('characterCode'),definition.get('skillId'))!=(cfg['character'],cfg['skill']):
        return _unavailable(spec,'정확한 스킬 정체성 불일치')
    for _,effect,prefab,sound in cfg['phases'].values():
        ef=[r for r in effect_rows if r['code']==effect]
        if len(ef)!=1 or (ef[0].get('effectPrefabName'),ef[0].get('soundName'))!=(prefab,sound):
            return _unavailable(spec,'정확한 단계별 EffectAndSound 정체성 불일치')
    cc=cfg.get('cc')
    if cc:
        sr=[r for r in state_rows if r['code']==cc['code']];sg=[r for r in state_groups if r['group']==cc['group']]
        if len(sr)!=1 or sr[0]['group']!=cc['group'] or len(sg)!=1 or (sg[0].get('stateType'),sg[0].get('skillId'))!=(cc['stateType'],cc['skillId']):
            return _unavailable(spec,'독립된 실제 CC 정의 불일치')
    wire=(load_exact_skill_ids() if skill_ids is None else skill_ids).get(cfg['skill'])
    codes={r['code']:r['group'] for r in skill_rows}
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==group]
    if not selected or any(s['skillIdCode']!=wire or codes.get(s['skillCode'])!=group for s in selected):
        return _unavailable(spec,'시전 없음 또는 정확한 wire 스킬 정체성 불일치')
    records,reason=ordered_cast_records(selected,finishes,player)
    if reason:return _unavailable(spec,reason)
    unknown={i:r['reason'] for i,r in enumerate(records) if not r['complete']}
    lookup=finish_lookup(finishes,player);markers=defaultdict(set);by_use=[[] for _ in records]
    own=[a for a in actions if a['sourceObjectId']==player and a['skillIdCode']==wire and a.get('wireStatus') in ORDINARY_ACTIONS]
    numbers={v[0] for v in cfg['phases'].values()}
    for a in own:
        if a['actionNo'] not in numbers:return _unavailable(spec,'미검토 단계 행동')
        owners=[i for i,r in enumerate(records) if (event_within_cast(r['start'],r['finish']['tick'],a,lookup) if r['finish'] else a['tick']>=r['start']['tick'])]
        if len(owners)!=1:return _unavailable(spec,'단계 행동이 한 실제 시전에 연결되지 않음')
        i=owners[0];markers[a['actionNo'],a['tick']].add(i);by_use[i].append(a)
    cancelled=set()
    for i,r in enumerate(records):
        if not r['complete'] or 'emissionAction' not in cfg:continue
        emissions=sum(a['actionNo']==cfg['emissionAction'] for a in by_use[i])
        if emissions==1:continue
        if emissions==0 and r['finish']['reason'] in (set(range(1,15))|{16,17}):cancelled.add(i)
        else:unknown[i]='missing-or-duplicate-recorded-attack-action'
    hits={phase:[set() for _ in records] for phase in cfg['phases']};packets={phase:Counter() for phase in cfg['phases']}
    phases_by_effect={v[1]:phase for phase,v in cfg['phases'].items()}
    for d in damages:
        target=d['targetObjectId'];phase=phases_by_effect.get(d.get('effectCode'))
        if d['attackerObjectId']!=player or phase is None or target not in teams or teams[target]==teams[player]:continue
        owners=markers[cfg['phases'][phase][0],d['tick']]
        if len(owners)!=1:return _unavailable(spec,'단계 피해와 같은 시각의 배타적 스킬 행동이 없음')
        i=next(iter(owners));r=records[i]
        if r['finish'] and not event_within_cast(r['start'],r['finish']['tick'],d,lookup):
            return _unavailable(spec,'실제 명령 순서상 종료 뒤 또는 시전 전 단계 피해')
        hits[phase][i].add((d['tick'],target));packets[phase][i]+=1
    applications=[set() for _ in records]
    if cc:
        for s in states:
            target=s.get('targetObjectId')
            if s.get('event')!='add' or s.get('casterObjectId')!=player or s.get('stateCode')!=cc['code'] or target not in teams or teams[target]==teams[player]:continue
            owners=[i for i,contacts in enumerate(hits[cc['phase']]) if (s['tick'],target) in contacts]
            if len(owners)!=1:return _unavailable(spec,'실제 CC와 해당 단계의 적 피해 연결 불가')
            applications[owners[0]].add((s['tick'],target))
    required=cc['phase'] if cc and mode==cc['mode'] else mode
    needed=list(hits) if mode=='any' else [required]
    if any(not sum(packets[phase].values()) for phase in needed) and reuse_policy is None:
        return _unavailable(spec,'요청 단계 자체의 실제 적 타격 표본 부족')
    combat=[i for i,r in enumerate(records) if any(l<=r['start']['tick']<end for l,end in intervals)]
    valid=[i for i in combat if i not in unknown]
    contacts=applications if cc and mode==cc['mode'] else (hits[mode] if mode!='any' else [set().union(*(h[i] for h in hits.values())) for i in range(len(records))])
    row=_result(spec,[contacts[i] for i in valid],'reviewed-action-effect-stage-and-ordered-use-completeness',
                cast_ticks=[records[i]['start']['tick'] for i in valid])
    row.update(perUseCompletenessTracked=True,observedCombatCastCount=len(combat),verifiedCombatCastCount=len(valid),
        unresolvedCombatCastCount=len(combat)-len(valid),unresolvedAllCastCount=len(unknown),
        unresolvedCastReasons=dict(Counter(unknown[i] for i in combat if i in unknown)),incompleteUsesCountedAsMisses=False,
        cancelledBeforeAttackCount=sum(i in cancelled for i in valid),numericSkillStateCodeJoinUsed=False,
        exactDamagePacketCount=sum(packets[phase][i] for phase in needed for i in valid),
        actualCCApplicationEventCount=sum(len(applications[i]) for i in valid) if cc else None,
        interpretation='정확한 스킬 행동·단계별 피해 코드·같은 시각의 실제 피해를 연결. 단계별 타격과 다인 누적, 실제 CC 분리. 미완결은 별도이며 실패 아님.')
    return annotate_reviewed_rule_reuse(row,reuse_policy,positive_gate_unsatisfied=any(not sum(packets[phase].values()) for phase in needed))
