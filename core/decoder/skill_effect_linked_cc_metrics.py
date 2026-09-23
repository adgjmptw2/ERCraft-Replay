"""CC application through explicit state/effect FKs and exclusive cast lifetimes."""
import json
from pathlib import Path
from functools import lru_cache
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids
    from .skill_wire_order import competing_manual_starts
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_action_stage_evidence import load_exact_skill_ids
    from skill_wire_order import competing_manual_starts


CONFIG={
    (54,1054500,'stun'):('KarlaActive4','Stun',{1054520:(1054503,'FX_BI_Karla_Skill04_Hit_02')}),
    (19,1019500,'fetter'):('EmmaActive4','Fetter',{1019500:(1019507,'FX_BI_Emma_Skill04_Line_Fetter')}),
    (49,1049320,'fetter'):('FelixActive2_3','Fetter',{1049310:(1049304,'FX_BI_Felix_Skill02_03_Fetter')}),
    (46,1046300,'fetter'):('AidenActive2','Fetter',{1046350:(1046311,'FX_BI_Aiden_Skill02_Active_Fetter')}),
    (75,1075300,'fetter'):('LenoreActive2','Fetter',{1075330:(1075303,'FX_BI_Lenore_Skill02_03_hit')}),
    (76,1076300,'fetter'):('GarnetActive2','Fetter',{1076310:(1076302,'FX_BI_Garnet_Skill02_Fetter')}),
    (83,1083500,'stun'):('HenryActive4','Stun',{1083510:(1083506,'FX_BI_Henry_Skill04_Stun')}),
    (87,1087400,'fetter'):('CoralineActive3','Fetter',{
        1087400:(1087407,'FX_BI_Coraline_Skill03_Fetter'),
        1087410:(1087432,'FX_BI_Coraline_Skill03_Fetter_White'),
        1087440:(1087431,'FX_BI_Coraline_Skill03_Fetter_Black')}),
}


@lru_cache(maxsize=1)
def native_state_producers():
    # Runtime consumes the reviewed static artifact; it does not inspect code
    # or learn a state identity from the current match's positive examples.
    data=json.loads((Path(__file__).resolve().parents[1]/'schema/native-cc-state-producers-v1.json').read_text(encoding='utf8'))
    if (data['clientVersion'],data['gameDbSha256'])!=('12.3.0','5ef9cb5459a2e908099655bf9ffc70ac16b134d1db282fb97b25fe065e7c8e4f'):
        raise ValueError('native CC producer contract version mismatch')
    return data['rules']


def effect_linked_cc_metric(spec,starts,finishes,states,player,teams,intervals,
                            catalog,skill_rows,state_rows,state_groups,effect_rows,skill_ids=None,gaps=None):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    from .skill_lifecycle_result_policy import reviewed_rule_reuse_policy,annotate_reviewed_rule_reuse
    reuse=reviewed_rule_reuse_policy(gaps,{'CmdStartSkill','CmdFinishSkill','CmdAddState'},starts,finishes,states)
    if any(stream is None for stream in (starts,finishes,states)):
        return _unavailable(spec,'CC 시전·종료·상태 입력 스트림 없음')
    cfg=CONFIG.get((spec['characterCode'],spec['skillGroup'],spec['mode']))
    if not cfg or spec['unit']!='skill-cast':return _unavailable(spec,'검증되지 않은 상태·효과 연결 지표')
    name,state_type,links=cfg;group=spec['skillGroup']
    definition=catalog['skillGroups'].get(str(group),{})
    if definition.get('skillId')!=name or definition.get('characterCode')!=spec['characterCode']:
        return _unavailable(spec,'같은 gameDb의 실제 스킬 정의 불일치')
    effects={r['code']:r for r in effect_rows}
    for sg,(effect,prefab) in links.items():
        definitions=[r for r in state_groups if r.get('group')==sg]
        if (len(definitions)!=1 or definitions[0].get('stateType')!=state_type or
                definitions[0].get('effectType')!='Debuff' or definitions[0].get('startEffectAndSound')!=effect or
                effects.get(effect,{}).get('effectPrefabName')!=prefab):
            return _unavailable(spec,'전용 상태의 EffectAndSound 명시 참조·효과 정의 불일치')
    code_groups={r['code']:r['group'] for r in state_rows}
    own_codes={code for code,sg in code_groups.items() if sg in links}
    if any(not any(sg==wanted for sg in code_groups.values()) for wanted in links):
        return _unavailable(spec,'요청 상태 그룹의 실제 상태 코드 없음')
    shared_effects={link[0] for link in links.values()}
    foreign_groups={r['group'] for r in state_groups if r.get('startEffectAndSound') in shared_effects and r['group'] not in links}
    foreign_codes={code for code,sg in code_groups.items() if sg in foreign_groups}
    native=next((r for r in native_state_producers() if
        (r['characterCode'],r['skillGroup'],r['mode'],r['skillId'])==(spec['characterCode'],group,spec['mode'],name)
        and own_codes==set(r['stateCodes']) and set(links)=={r['stateGroup']}
        and all(code_groups.get(int(code))==sg for code,sg in r['disjointStateCodes'].items())),None)
    disjoint_codes={int(c) for c in native['disjointStateCodes']} if native else set()
    ignored_disjoint=0
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    skill_code_groups={r['code']:r['group'] for r in skill_rows}
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==group]
    if not selected or any(s['skillIdCode']!=ids.get(name) or skill_code_groups.get(s['skillCode'])!=group for s in selected):
        return _unavailable(spec,'시전 누락 또는 wire 스킬 정체성 불일치')
    lives,reason=exact_cast_lifetimes(selected,finishes,player)
    if reason:return _unavailable(spec,reason)
    competing,reason=exact_cast_lifetimes(competing_manual_starts(starts,catalog,{group},player),finishes,player)
    if reason:return _unavailable(spec,'다른 수동 스킬의 실제 수명이 불완전하여 상태 중복 귀속을 배제하지 못함')
    from .skill_wire_order import event_within_cast,finish_lookup
    ordered_finishes=finish_lookup(finishes,player)
    contacts=[set() for _ in lives]
    unknown={};unassigned=[]
    for state in states:
        target=state['targetObjectId'];code=state.get('stateCode')
        if (state['event']!='add' or code not in own_codes|foreign_codes or
                target not in teams or teams[target]==teams[player]):continue
        if state['casterObjectId'] not in teams:return _unavailable(spec,'전용 CC 상태의 실제 시전자 미확정')
        if state['casterObjectId']!=player:continue
        sg=code_groups[code]
        if state.get('stateGroup') not in (None,sg):return _unavailable(spec,'실제 상태 코드와 명시 그룹 불일치')
        owners=[i for i,(s,end) in enumerate(lives) if event_within_cast(s,end,state,ordered_finishes)]
        if code in foreign_codes:
            if owners and code in disjoint_codes:
                ignored_disjoint+=1
            elif owners:return _unavailable(spec,'같은 효과를 재사용하는 다른 상태가 겹쳐 단독 스킬 CC 판정을 확정하지 않음')
            continue
        rivals=[(s,end) for s,end in competing if event_within_cast(s,end,state,ordered_finishes)]
        if len(owners)!=1 or rivals:
            # Only recorded, closed candidate lifetimes bound uncertainty.
            # No state outside all candidates is rescued by the latest cast.
            from .skill_wire_order import command_order
            bounds=[lives[i] for i in owners]+rivals
            bound_evidence=[]
            ordered=command_order(state) is not None
            for start,end in bounds:
                ends=[e for e in finishes if e.get('playerObjectId')==player
                      and e.get('skillIdCode')==start['skillIdCode'] and e['tick']==end]
                ordered=ordered and command_order(start) is not None and len(ends)==1 and command_order(ends[0]) is not None
                if ordered:ordered=command_order(start)<=command_order(state)<=command_order(ends[0])
                if len(ends)==1:bound_evidence.append(dict(start=dict(start),finish=dict(ends[0])))
            complete_streams=gaps is not None and not any(g.get('count',0) and
                any(str(g.get('packetName','')).startswith(n) for n in ('CmdStartSkill','CmdFinishSkill','CmdAddState')) for g in gaps)
            if not owners or not ordered or not complete_streams:
                return _unavailable(spec,'실제 CC 적용이 한 스킬의 완전한 수명에 배타적으로 연결되지 않음')
            for i in owners:unknown[i]='state-parent-not-exclusive'
            unassigned.append(dict(state=dict(state),candidateCastTicks=[lives[i][0]['tick'] for i in owners],
                candidateCastLifetimes=bound_evidence[:len(owners)],
                competingCastLifetimes=bound_evidence[len(owners):],assignedAsHit=False))
            continue
        contacts[owners[0]].add((state['tick'],target))
    positive_gate_unsatisfied=not native and not any(c for i,c in enumerate(contacts) if i not in unknown)
    if positive_gate_unsatisfied and reuse is None:
        row=_unavailable(spec,'해당 전용 CC가 실제 적에게 적용된 검증 표본 없음')
        if unassigned:row['unassignedStateEvidence']=unassigned
        return row
    selected=[i for i,(s,_) in enumerate(lives) if any(left<=s['tick']<right for left,right in intervals)]
    valid=[i for i in selected if i not in unknown]
    chosen=[contacts[i] for i in valid]
    row=_result(spec,chosen,'explicit-state-effect-FK-and-exclusive-complete-cast-CC-application',
                cast_ticks=[lives[i][0]['tick'] for i in valid])
    row.update(measuredOutcome='enemy-'+spec['mode']+'-application',
        exactStateGroups=sorted(links),sharedEffectOtherStateGroups=sorted(foreign_groups),
        damageUsedAsCCProof=False,numericSkillStateCodeJoinUsed=False,
        interpretation='교전 중 전체 해당 스킬 사용 대비 실제 CC 상태 적용. 일반 피해와 별도이며 같은 사용의 같은 대상 반복 적용은 다인 수에서 한 명.')
    if native:
        row['nativeStateProducer']=dict(dataClass=native['dataClass'],field=native['fullChargeField']['name'],stateCodes=native['stateCodes'])
        row['nativeDisjointStateApplicationsIgnored']=ignored_disjoint
    if unassigned:
        from .skill_lifecycle_result_policy import finalize_lifecycle_result
        row=finalize_lifecycle_result(row,selected,unknown)
        row.update(unassignedStateEvidence=unassigned,unresolvedCastTicks=[lives[i][0]['tick'] for i in selected if i in unknown])
        if row['unresolvedCombatCastCount']:
            row.update(hitRateScope='verified-uses-only',wholeCombatHitRate=None,fullRequestedMetricComplete=False)
    return annotate_reviewed_rule_reuse(row,reuse,positive_gate_unsatisfied=positive_gate_unsatisfied)
