"""W/R ownership of the shared VF drain, through exact state/action targets.

Action assignments are from the pinned FenrirSkillAction metadata enum.
Repeated outcomes use the existing exact-target/same-tick corroboration standard;
they are not damage amounts or counts of indistinguishable damage packets.
"""
from collections import defaultdict
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids,ORDINARY_ACTIONS
    from .skill_persistent_cc_metrics import named_state_lifetimes,state_window_contains
    from .skill_wire_order import command_order
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_action_stage_evidence import load_exact_skill_ids,ORDINARY_ACTIONS
    from skill_persistent_cc_metrics import named_state_lifetimes,state_window_contains
    from skill_wire_order import command_order

PARENTS={1086300:('FenrirActive2_1',34),1086500:('FenrirActive4',52)}


def fenrir_absorb_metric(spec,starts,finishes,scripts,states,actions,damages,player,teams,
                         intervals,catalog,skill_rows,state_rows,state_groups,skill_ids=None):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    group=spec['skillGroup'];mode=spec['mode']
    if group not in PARENTS or mode not in {'absorb','absorb-pulse'} or spec['characterCode']!=86 or spec['unit']!='skill-cast':
        return _unavailable(spec,'펜리르 W/R 흡수 전용 규칙 아님')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    code_groups={s['code']:s['group'] for s in skill_rows}
    for g,(name,_) in PARENTS.items():
        definition=catalog['skillGroups'].get(str(g),{})
        if definition.get('skillId')!=name or definition.get('characterCode')!=86:
            return _unavailable(spec,'펜리르 W/R 정확한 gameDb 정체성 불일치')
    parent_starts=[s for s in starts if s['playerObjectId']==player and s['skillGroup'] in PARENTS]
    if not any(s['skillGroup']==group for s in parent_starts) or any(
        s['skillIdCode']!=ids.get(PARENTS[s['skillGroup']][0]) or code_groups.get(s['skillCode'])!=s['skillGroup'] for s in parent_starts):
        return _unavailable(spec,'본체 시전 누락 또는 wire 정체성 불일치')
    casts,why=exact_cast_lifetimes(parent_starts,finishes,player)
    if why:return _unavailable(spec,why)
    definitions={s['group']:s for s in state_groups}
    first_parent=min(parent_starts,key=command_order) if all(command_order(s) is not None for s in parent_starts) else None
    if first_parent is not None and (len({command_order(s) for s in parent_starts})!=len(parent_starts)
            or any(s['tick']<first_parent['tick'] for s in parent_starts)):first_parent=None
    windows={};pre_parent_finishes=[]
    for sg,name,self_anchor in [(1086160,'FenrirPassiveSelfConnectState',True),(1086170,'FenrirPassiveTargetConnectState',False)]:
        if definitions.get(sg,{}).get('skillId')!=name:return _unavailable(spec,'정확한 흡수 자기·대상 상태 정의 불일치')
        codes={s['code'] for s in state_rows if s['group']==sg}
        if not codes:return _unavailable(spec,'흡수 상태 코드 없음')
        windows[sg],why=named_state_lifetimes(scripts,states,player,sg,name,codes,ids,self_anchor=self_anchor,split_recorded_refresh=True,
            first_parent=first_parent,pre_parent_finishes=pre_parent_finishes)
        if why:return _unavailable(spec,why)
    self_windows=windows[1086160];target_windows=windows[1086170]
    wire=ids['FenrirPassiveSelfConnectState']
    self_actions=[a for a in actions if a['sourceObjectId']==player and a['skillIdCode']==wire and a['actionNo'] in {13,14}]
    if any(a.get('wireStatus')!='decoded-exact-CmdPlayStateSkillAction' or a.get('stateGroup')!=1086160 or a.get('casterObjectId') not in {0,player} for a in self_actions):
        return _unavailable(spec,'흡수 행동의 실제 상태·소유 wire 불일치')
    initiations=[a for a in self_actions if a['actionNo']==13]
    roots={};used_self=set();used_init=set();connections=[set() for _ in casts]
    for j,(s,end) in enumerate(target_windows):
        target=s['sourceObjectId']
        sw=[i for i,(ss,_) in enumerate(self_windows) if ss['tick']==s['tick']]
        init=[i for i,a in enumerate(initiations) if a['tick']==s['tick'] and
              [t['targetObjectId'] for t in a.get('targets',[])]==[target]]
        if len(sw)!=1 or sw[0] in used_self or len(init)!=1 or init[0] in used_init:
            return _unavailable(spec,'흡수 자기·대상 상태 및 초기 대상 행동이 일대일로 연결되지 않음')
        anchors=[a for a in actions if a['sourceObjectId']==player and a['tick']==s['tick'] and a.get('wireStatus') in ORDINARY_ACTIONS and
                 any(a['skillIdCode']==ids[name] and a['actionNo']==number for name,number in PARENTS.values())]
        matches=[i for i,(cs,ce) in enumerate(casts) if cs['tick']<=s['tick']<=ce and
                 any(a['skillIdCode']==cs['skillIdCode'] for a in anchors)]
        if len(anchors)!=1 or len(matches)!=1:return _unavailable(spec,'흡수 시작을 W 돌진 종료 또는 R 돌진 성공의 단일 사용에 연결하지 못함')
        roots[j]=(matches[0],sw[0]);used_self.add(sw[0]);used_init.add(init[0])
        if target in teams and teams[target]!=teams[player]:connections[matches[0]].add((s['tick'],target))
    if len(used_self)!=len(self_windows) or len(used_init)!=len(initiations):
        return _unavailable(spec,'본체 사용에 연결되지 않은 자기 흡수 상태·초기 행동이 남아 있음')
    if not any(connections):return _unavailable(spec,'실제 적 흡수 연결의 검증 표본 없음')
    chosen=[i for i,(s,_) in enumerate(casts) if s['skillGroup']==group and any(l<=s['tick']<r for l,r in intervals)]
    if mode=='absorb':
        row=_result(spec,[connections[i] for i in chosen],'exact-W-R-success-action-and-paired-absorb-state',
                    cast_ticks=[casts[i][0]['tick'] for i in chosen])
    else:
        pulses=[set() for _ in casts];seen=set()
        corroboration={(d['tick'],d['targetObjectId']) for d in damages if d['attackerObjectId']==player and d.get('effectCode')==0}
        for a in self_actions:
            if a['actionNo']!=14:continue
            targets=[t['targetObjectId'] for t in a.get('targets',[])]
            if len(targets)!=1:return _unavailable(spec,'흡수 반복 행동의 단일 실제 대상 미확정')
            target=targets[0];key=(a['tick'],target)
            if key in seen:return _unavailable(spec,'흡수 반복 행동 중복으로 적용 횟수 미확정')
            seen.add(key)
            matches=[j for j,(s,end) in enumerate(target_windows) if s['sourceObjectId']==target and state_window_contains((s,end),a) and
                     state_window_contains(self_windows[roots[j][1]],a)]
            if len(matches)!=1:return _unavailable(spec,'흡수 반복 행동이 닫힌 자기·대상 상태 한 쌍에 연결되지 않음')
            if target not in teams or teams[target]==teams[player]:continue
            if key not in corroboration:return _unavailable(spec,'실제 흡수 반복 대상에 동일 tick의 피해 기록이 없음')
            pulses[roots[matches[0]][0]].add(key)
        if not any(pulses):return _unavailable(spec,'실제 적 흡수 반복 타격의 검증 표본 없음')
        row=_result(spec,[pulses[i] for i in chosen],'exact-rooted-absorb-action-target-same-tick-damage-corroborated',
                    cast_ticks=[casts[i][0]['tick'] for i in chosen])
        row['contactCountMeaning']='actual VF drain action/target applications corroborated by same-tick damage; not damage packet multiplicity'
    row.update(verifiedWholeMatchAbsorbWindows=len(target_windows),
               preParentStateFinishes=pre_parent_finishes,preParentStateFinishUsedAsOutcome=False,
               recordedTargetRefreshCount=sum(bool(s.get('recordedRefreshStart')) for s,_ in target_windows),
               recordedSelfRefreshCount=sum(bool(s.get('recordedRefreshStart')) for s,_ in self_windows),
               stateScriptSkillCodeUsed=False,nearestCastUsed=False,
               fixedDurationWindowUsed=False,interpretation='W/R의 실제 성공 행동으로 흡수 상태를 구분. 연결 성공과 반복 적용은 별도 지표; 상태 종료를 거리 이탈 사유로 추정하지 않음.')
    return row
