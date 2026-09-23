"""Requested CC linked through actual named state-script lifetimes."""
from collections import defaultdict
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids
    from .skill_wire_order import command_order
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_action_stage_evidence import load_exact_skill_ids
    from skill_wire_order import command_order


def named_state_lifetimes(scripts,states,player,group,name,state_codes,ids,self_anchor=False,allow_open=False,split_recorded_refresh=False,first_parent=None,pre_parent_finishes=None):
    if scripts is None:return None,'전용 상태 스크립트 시작·종료 스트림 미보관'
    relevant=[r for r in scripts if r['stateGroup']==group and
              (r['sourceObjectId']==player if self_anchor else r['casterObjectId']==player)]
    actual_adds=defaultdict(list)
    for state in states:
        if state['event']=='add' and state.get('stateCode') in state_codes:
            actual_adds[state['tick'],state['targetObjectId'],state['casterObjectId']].append(state)
    active={};windows=[];refresh_count=0;seen_starts=set();prefix_keys=set()
    first_event=min(relevant,key=command_order) if first_parent is not None and relevant and all(command_order(e) is not None for e in relevant) else None
    for r in sorted(relevant,key=lambda r:r['tick']):
        if r['skillIdCode']!=ids.get(name) or (self_anchor and r['casterObjectId'] not in {0,player}):
            return None,'전용 상태의 실제 wire 정체성·소유자 불일치'
        key=(r['sourceObjectId'],r['casterObjectId'])
        if r['event']=='CmdStartStateSkill':
            seen_starts.add(key)
            if key in active:
                resets=[s for s in states if s.get('event')=='CmdResetCreateTimeState'
                        and s.get('stateGroup')==group and s.get('targetObjectId')==key[0]
                        and s.get('casterObjectId')==key[1] and s['tick']==r['tick']]
                if (not split_recorded_refresh or len(resets)!=1
                    or any(command_order(s) is None for s in [active[key],resets[0],r])
                    or not command_order(active[key])<command_order(resets[0])<command_order(r)
                    or actual_adds[r['tick'],r['sourceObjectId'],r['casterObjectId']]):
                    return None,'전용 상태 시작이 겹쳐 갱신과 독립 수명을 구분하지 않음'
                # This is a recorded refresh boundary, not an invented finish.
                old=dict(active[key],recordedRefreshEndOrder=command_order(resets[0]))
                windows.append((old,resets[0]['tick']))
                active[key]=dict(r,recordedRefreshStart=True)
                refresh_count+=1
                continue
            actual=actual_adds[r['tick'],r['sourceObjectId'],r['casterObjectId']]
            if len(actual)!=1 or actual[0].get('stateGroup') not in (None,group):
                return None,'전용 스크립트 시작과 실제 상태 추가가 일대일로 연결되지 않음'
            active[key]=r
        elif r['event']=='CmdFinishStateSkill':
            if (first_parent is not None and r is first_event and not active and not seen_starts
                    and not prefix_keys and type(r.get('reason')) is int and r['reason']==6
                    and command_order(r) is not None and command_order(first_parent) is not None
                    and r['tick']<=first_parent['tick'] and command_order(r)<command_order(first_parent)):
                # This ended before every observed parent, so it cannot belong
                # to any requested use. It is not a reconstructed state window.
                prefix_keys.add(key)
                if pre_parent_finishes is not None:pre_parent_finishes.append(dict(r))
                continue
            if key not in active or r.get('reason')==15:
                return None,'전용 상태 시작 없는 종료 또는 재생 종료로 수명 미확정'
            windows.append((active.pop(key),r['tick']))
    if active and not allow_open:return None,'전용 상태의 실제 종료 누락'
    windows.extend((s,None) for s in active.values())
    adds=[s for s in states if s['event']=='add' and s.get('stateCode') in state_codes and
          (s['targetObjectId']==player if self_anchor else s['casterObjectId']==player)]
    if len(adds)+refresh_count!=len(windows):return None,'실제 상태 추가와 완전한 스크립트 수명 수 불일치'
    return windows,None


def state_window_contains(window,event):
    """Use a recorded refresh boundary exclusively; real finish stays inclusive."""
    start,end=window
    if event['tick']<start['tick'] or (end is not None and event['tick']>end):return False
    at=command_order(event);begin=command_order(start)
    if at is not None and begin is not None and at<begin:return False
    refresh=start.get('recordedRefreshEndOrder')
    if refresh is not None and (at is None or at>=tuple(refresh)):return False
    return True


def rooted_state_completeness(windows, roots, contacts):
    incomplete={roots[j] for j,(_,end) in enumerate(windows) if end is None}
    positive={i for i in incomplete if contacts[i]}
    unknown={i:'missing-recorded-state-end' for i in incomplete-positive}
    return unknown,positive


def root_state_windows(casts,windows,finishes,player):
    """Shared cast -> closed self-state graph for damage and CC outcomes.

    Other skills may run during a rooted state without changing its ownership.
    Missing roots are accepted only for explicit gameplay cancellations.
    """
    roots={};by_cast=defaultdict(list);cancelled=set()
    reasons={(f['skillIdCode'],f['tick']):f['reason'] for f in finishes if f['playerObjectId']==player}
    for j,(state,_) in enumerate(windows):
        owners=[i for i,(start,end) in enumerate(casts) if start['tick']<=state['tick']<=end]
        if len(owners)!=1:return None,'전용 자기 상태 시작을 하나의 실제 시전에 연결하지 못함'
        roots[j]=owners[0];by_cast[owners[0]].append(j)
    for i,(start,end) in enumerate(casts):
        if len(by_cast[i])==1:continue
        if not by_cast[i] and reasons.get((start['skillIdCode'],end)) in set(range(1,15))|{16,17}:
            cancelled.add(i)
        else:return None,'정상 시전과 전용 자기 상태가 일대일로 연결되지 않음'
    return (roots,by_cast,cancelled),None


def persistent_damage_metric(spec,starts,finishes,states,scripts,damages,player,teams,
                             intervals,catalog,skill_rows,state_rows,state_groups,effect_rows,skill_ids=None,gaps=None):
    """All observed pulses in the actual E state; effect identity is not a Skill FK."""
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    if gaps and any(g.get('count',0) and g.get('packetName') in {'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdDestroy','CmdDamage','CmdAddState','CmdStartStateSkill','CmdFinishStateSkill'} for g in gaps):
        return _unavailable(spec,'lifecycle evidence has required-stream decode gaps')
    group=spec['skillGroup'];name='PriyaActive3';state_name='PriyaActive3_CollisionArea'
    if (group,spec['mode'],spec['unit'],spec['characterCode'])!=(1051400,'any','skill-cast',51):
        return _unavailable(spec,'검토된 지속 상태 피해 규칙 없음')
    definition=catalog['skillGroups'].get(str(group),{})
    anchors=[s for s in state_groups if s['group']==group]
    effects=[e for e in effect_rows if e['code']==1051303]
    if (definition.get('skillId')!=name or definition.get('characterCode')!=51 or len(anchors)!=1 or
        (anchors[0].get('skillId'),anchors[0].get('stateType'),anchors[0].get('effectType'))!=(state_name,'Common','Buff') or
        len(effects)!=1 or (effects[0].get('effectPrefabName'),effects[0].get('soundName'))!=('FX_BI_Priya_Skill03_Hit','Priya_Skill03_Hit')):
        return _unavailable(spec,'정확한 gameDb의 지속 상태·피해 계열 정의 불일치')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    codes={r['code']:r['group'] for r in skill_rows}
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==group]
    if not selected or any(s['skillIdCode']!=ids.get(name) or codes.get(s['skillCode'])!=group for s in selected):
        return _unavailable(spec,'시전 누락 또는 wire 스킬 검증 불일치')
    casts,why=exact_cast_lifetimes(selected,finishes,player)
    if why:return _unavailable(spec,why)
    state_codes={s['code'] for s in state_rows if s['group']==group}
    if not state_codes:return _unavailable(spec,'전용 자기 상태 코드 없음')
    windows,why=named_state_lifetimes(scripts,states,player,group,state_name,state_codes,ids,self_anchor=True,allow_open=True)
    if why:return _unavailable(spec,why)
    graph,why=root_state_windows(casts,windows,finishes,player)
    if why:return _unavailable(spec,why)
    roots,_,cancelled=graph
    contacts=[set() for _ in casts];packets=[0 for _ in casts]
    for damage in damages:
        target=damage.get('targetObjectId')
        if damage.get('effectCode')!=1051303 or target not in teams or teams[target]==teams[player]:continue
        owner=damage.get('attackerObjectId')
        if owner not in teams:return _unavailable(spec,'전용 피해 계열의 실제 공격자 미확정')
        if owner!=player:continue
        matches=[j for j,(s,end) in enumerate(windows) if s['tick']<=damage['tick'] and (end is None or damage['tick']<=end)]
        if len(matches)!=1:return _unavailable(spec,'전용 피해가 지속 상태 밖에 있거나 여러 상태와 겹침')
        i=roots[matches[0]];contacts[i].add((damage['tick'],target));packets[i]+=1
    chosen=[i for i,(s,_) in enumerate(casts) if any(l<=s['tick']<r for l,r in intervals)]
    unknown,positive=rooted_state_completeness(windows,roots,contacts)
    classified=[i for i in chosen if i not in unknown]
    row=_result(spec,[contacts[i] for i in classified],'exact-self-state-lifetime-reviewed-damage-family',
                cast_ticks=[casts[i][0]['tick'] for i in classified])
    row.update(measuredOutcome='enemy-damage-all-pulses',exactDamagePacketCount=sum(packets[i] for i in chosen),
               verifiedStateLifetimeCount=sum(end is not None for _,end in windows),cancelledBeforeAttackCount=len(cancelled.intersection(chosen)),
               stateScriptSkillCodeUsed=False,nearestCastUsed=False,fixedDurationWindowUsed=False,
               interpretation='실제 E 지속 상태 안의 전체 연타 피해. 적중 사용·사용별 서로 다른 적·반복 접촉을 별도로 집계.')
    from .skill_lifecycle_result_policy import finalize_lifecycle_result
    return finalize_lifecycle_result(row,chosen,unknown,incomplete_positive=positive,observed_positive=any(contacts))


def persistent_cc_metric(spec,starts,finishes,spawns,collisions,terminals,states,scripts,
                         player,teams,intervals,catalog,skill_rows,state_rows,state_groups,effect_rows,skill_ids=None,gaps=None):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    if gaps and any(g.get('count',0) and g.get('packetName') in {'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdDestroy','CmdDamage','CmdAddState','CmdStartStateSkill','CmdFinishStateSkill'} for g in gaps):
        return _unavailable(spec,'lifecycle evidence has required-stream decode gaps')
    group=spec['skillGroup'];priya=group==1051400
    cfg={1051400:(51,'PriyaActive3',1051400,'PriyaActive3_CollisionArea','Common','Buff'),
         1025500:(25,'BerniceActive4',1025510,'BerniceActive4Fetter','Fetter','Debuff')}.get(group)
    if not cfg or spec['mode']!='fetter' or spec['unit']!='skill-cast':return _unavailable(spec,'검증되지 않은 지속 상태 CC 경로')
    ch,name,sg,state_name,state_type,effect_type=cfg
    definitions={r['group']:r for r in state_groups};d=definitions.get(sg,{})
    skill=catalog['skillGroups'].get(str(group),{})
    if (spec['characterCode']!=ch or skill.get('characterCode')!=ch or skill.get('skillId')!=name or
            d.get('skillId')!=state_name or d.get('stateType')!=state_type or d.get('effectType')!=effect_type):
        return _unavailable(spec,'같은 gameDb의 스킬·전용 상태 정의 불일치')
    code_groups={r['code']:r['group'] for r in state_rows};anchor_codes={code for code,g in code_groups.items() if g==sg}
    cc_group=1051430 if priya else sg;cc_codes={code for code,g in code_groups.items() if g==cc_group}
    if not anchor_codes or not cc_codes:return _unavailable(spec,'전용 상태 코드 없음')
    if priya:
        cc=definitions.get(cc_group,{});effects={r['code']:r for r in effect_rows}
        if (cc.get('stateType')!='Fetter' or cc.get('effectType')!='Debuff' or cc.get('startEffectAndSound')!=1051305 or
                effects.get(1051305,{}).get('effectPrefabName')!='FX_BI_Priya_Skill03_Snare'):
            return _unavailable(spec,'프리야 E 속박의 명시 효과 참조 불일치')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    skill_codes={r['code']:r['group'] for r in skill_rows}
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==group]
    if not selected or any(s['skillIdCode']!=ids.get(name) or skill_codes.get(s['skillCode'])!=group for s in selected):
        return _unavailable(spec,'시전 누락 또는 wire 스킬 정의 불일치')
    casts,why=exact_cast_lifetimes(selected,finishes,player)
    if why:return _unavailable(spec,why)
    windows,why=named_state_lifetimes(scripts,states,player,sg,state_name,anchor_codes,ids,self_anchor=priya,allow_open=priya)
    if why:return _unavailable(spec,why)
    by_cast=defaultdict(list);anchor_cast={};cancelled=set();actual_objects=0;transfers=0
    reasons={(f['skillIdCode'],f['tick']):f['reason'] for f in finishes if f['playerObjectId']==player}
    if priya:
        graph,why=root_state_windows(casts,windows,finishes,player)
        if why:return _unavailable(spec,why)
        anchor_cast,by_cast,cancelled=graph
    else:
        expected={102504:'Projectile_FX_BI_Bernice_Skill04',102505:'Projectile_FX_BI_Bernice_Skill04'}
        if any(catalog['projectileDefinitions'].get(str(c),{}).get('prefabName')!=v for c,v in expected.items()):
            return _unavailable(spec,'같은 gameDb의 버니스 R 본체·전이 투사체 계열 불일치')
        ends=defaultdict(set);contact=defaultdict(set)
        for t in terminals:
            if t['event']=='CmdDestroy':ends[t['objectId']].add(t['tick'])
        for c in collisions:contact[c['projectileObjectId']].add((c['tick'],c['targetObjectId']))
        objects={};object_cast={};pending=[]
        for sp in spawns:
            if sp['ownerPlayerObjectId']!=player or sp['projectileCode'] not in expected:continue
            oid=sp['projectileObjectId']
            if oid in objects or len(ends[oid])!=1 or next(iter(ends[oid]))<sp['tick']:
                return _unavailable(spec,'버니스 R 실제 투사체 생성·최종 소멸 불완전')
            end=next(iter(ends[oid]));objects[oid]=(sp,end)
            if any(not sp['tick']<=t<=end for t,_ in contact[oid]):return _unavailable(spec,'버니스 R 충돌이 객체 수명 밖에 존재함')
            if sp['projectileCode']==102504:
                owners=[i for i,(s,e) in enumerate(casts) if s['tick']<=sp['tick']<=e]
                if len(owners)!=1:return _unavailable(spec,'R 첫 투사체가 정확히 한 시전에 연결되지 않음')
                object_cast[oid]=owners[0];by_cast[owners[0]].append(oid)
            else:pending.append(oid)
        # Each extension must follow one already-rooted actual Fetter finish at
        # exactly this tick. No nearest event or fixed transfer delay is used.
        changed=True
        while changed:
            changed=False
            for j,(s,end) in enumerate(windows):
                if j in anchor_cast:continue
                matched=[oid for oid,(sp,e) in objects.items() if oid in object_cast and
                         sp['tick']<=s['tick']<=e and (s['tick'],s['sourceObjectId']) in contact[oid]]
                if len(matched)>1:return _unavailable(spec,'속박 시작이 여러 실제 투사체 충돌과 겹침')
                if len(matched)==1:anchor_cast[j]=object_cast[matched[0]];changed=True
            for oid in list(pending):
                sp,_=objects[oid]
                parents=[j for j,(_,end) in enumerate(windows) if j in anchor_cast and end==sp['tick']]
                if len(parents)>1:return _unavailable(spec,'전이 생성 시각에 여러 속박 종료가 겹쳐 부모 미확정')
                if len(parents)==1:
                    object_cast[oid]=anchor_cast[parents[0]];pending.remove(oid);changed=True
        if pending or len(anchor_cast)!=len(windows):return _unavailable(spec,'전이 투사체·속박 상태를 실제 종료 이벤트로 본체 사용까지 연결하지 못함')
        actual_objects=len(objects);transfers=len(objects)-sum(len(v) for v in by_cast.values())
    for i,(s,end) in enumerate(casts):
        if len(by_cast[i])==1:continue
        if not by_cast[i] and reasons[s['skillIdCode'],end] in set(range(1,15))|{16,17}:cancelled.add(i)
        else:return _unavailable(spec,'정상 사용과 최초 전용 상태·발사체가 일대일로 연결되지 않음')
    contacts=[set() for _ in casts]
    for state in states:
        if state['event']!='add' or state.get('stateCode') not in cc_codes:continue
        target=state['targetObjectId']
        if target not in teams or teams[target]==teams[player]:continue
        if state['casterObjectId'] not in teams:return _unavailable(spec,'적 속박 적용의 실제 시전자 미확정')
        if state['casterObjectId']!=player:continue
        if state.get('stateGroup') not in (None,cc_group):return _unavailable(spec,'실제 속박 코드·그룹 불일치')
        matched=[j for j,(s,end) in enumerate(windows) if
                 (s['tick']<=state['tick'] and (end is None or state['tick']<=end) if priya else s['tick']==state['tick'] and s['sourceObjectId']==target)]
        if len(matched)!=1:return _unavailable(spec,'실제 적 속박이 한 전용 상태 수명에 연결되지 않음')
        contacts[anchor_cast[matched[0]]].add((state['tick'],target))
    chosen=[i for i,(s,_) in enumerate(casts) if any(l<=s['tick']<r for l,r in intervals)]
    unknown,positive=rooted_state_completeness(windows,anchor_cast,contacts)
    classified=[i for i in chosen if i not in unknown]
    row=_result(spec,[contacts[i] for i in classified],
        'exact-Priya-self-state-lifetime-and-FK-fetter-application' if priya else 'exact-Bernice-projectile-fetter-lifetime-finish-transfer-chain',
        cast_ticks=[casts[i][0]['tick'] for i in classified])
    row.update(measuredOutcome='enemy-fetter-application',cancelledBeforeAttackCount=len(cancelled.intersection(chosen)),
        verifiedStateLifetimeCount=sum(end is not None for _,end in windows),verifiedWholeMatchProjectileCount=actual_objects,
        verifiedWholeMatchTransferProjectileCount=transfers,stateScriptSkillCodeUsed=False,nearestCastUsed=False,
        fixedDurationWindowUsed=False,damageUsedAsCCProof=False,
        interpretation='교전 중 본체 사용 대비 실제 적 속박. 전용 상태의 실제 시작·종료를 연결하며 전이·반복 속박의 같은 적은 사용별 한 명.')
    from .skill_lifecycle_result_policy import finalize_lifecycle_result
    return finalize_lifecycle_result(row,chosen,unknown,incomplete_positive=positive,observed_positive=any(contacts))
