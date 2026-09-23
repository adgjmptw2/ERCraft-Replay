"""Explicit parent screen-charge -> owned child start -> beam emission graph."""
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


def screen_beam_metric(spec,starts,finishes,actions,summons,terminals,child_starts,
                        damages,player,teams,intervals,catalog,skill_rows,summon_rows,
                        effect_rows,skill_ids=None,gaps=None):
    from .skill_lifecycle_result_policy import reviewed_rule_reuse_policy,annotate_reviewed_rule_reuse
    reuse_policy=reviewed_rule_reuse_policy(gaps,{'CmdStartSkill','CmdFinishSkill','CmdPlaySkillAction','CmdSpawn','CmdDestroy','CmdDamage'},starts,finishes,actions,summons,terminals,child_starts,damages)
    route_group=spec['skillGroup']
    child_metric=route_group==1062210
    if route_group not in {1062200,1062210} or spec['mode'] not in {'any','direct-hit','screen-hit'}:
        return _unavailable(spec,'검토된 스크린 재발사 규칙 없음')
    mode='screen-hit' if child_metric else spec['mode']
    if child_starts is None:return _unavailable(spec,'소유 스크린의 실제 후속 시전 기록 없음')
    for group,identity in [(1062200,'TheodoreActive1'),(1062210,'TheodoreActive1Screen')]:
        d=catalog['skillGroups'].get(str(group),{})
        if (d.get('characterCode'),d.get('skillId'))!=(62,identity):return _unavailable(spec,'정확한 본체/스크린 스킬 정의 불일치')
    ss=[r for r in summon_rows if r['code']==1410];ef=[r for r in effect_rows if r['code']==1062202]
    if len(ss)!=1 or (ss[0].get('objectType'),ss[0].get('prefabPath'))!=('SummonServant','Theodore_Skill02_Screen') or len(ef)!=1 or (ef[0].get('effectPrefabName'),ef[0].get('soundName'))!=('FX_BI_Theodore_Skill01_Hit','Theodore_Skill01_Hit'):
        return _unavailable(spec,'스크린 객체·타격 효과 정의 불일치')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids;wire=ids.get('TheodoreActive1');child_wire=ids.get('TheodoreActive1Screen')
    codes={r['code']:r['group'] for r in skill_rows}
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1062200]
    if not selected or any(s['skillIdCode']!=wire or codes.get(s['skillCode'])!=1062200 for s in selected):return _unavailable(spec,'본체 Q 시전 누락 또는 wire 정체성 불일치')
    records,reason=ordered_cast_records(selected,finishes,player)
    if reason:return _unavailable(spec,reason)
    lookup=finish_lookup(finishes,player);unknown={i:r['reason'] for i,r in enumerate(records) if not r['complete']}
    own=[a for a in actions if a['sourceObjectId']==player and a['skillIdCode']==wire and a.get('wireStatus') in ORDINARY_ACTIONS]
    by_use=[[] for _ in records];direct=defaultdict(set);charges=defaultdict(list)
    for a in own:
        if a['actionNo'] not in {31,32,33,34}:return _unavailable(spec,'미검토 본체 Q 행동')
        owners=[i for i,r in enumerate(records) if (event_within_cast(r['start'],r['finish']['tick'],a,lookup) if r['finish'] else a['tick']>=r['start']['tick'])]
        if len(owners)!=1:return _unavailable(spec,'본체 행동의 실제 시전 귀속 불가')
        i=owners[0];by_use[i].append(a)
        if a['actionNo']==33:direct[a['tick']].add(i)
        elif a['actionNo']==34:
            if len(a['targets'])!=1:return _unavailable(spec,'스크린 충전 대상이 한 객체가 아님')
            charges[a['targets'][0]['targetObjectId'],a['tick']].append(i)
    cancelled=set()
    for i,r in enumerate(records):
        if not r['complete']:continue
        counts=Counter(a['actionNo'] for a in by_use[i])
        if counts[31]!=1 or counts[32]>1 or counts[33]>1:unknown[i]='invalid-parent-emission-sequence'
        if counts[33]==0:
            if not counts[34] and r['finish']['reason'] in (set(range(1,15))|{16,17}):cancelled.add(i)
            else:unknown[i]='parent-emission-not-observed'
        elif counts[32]!=1:unknown[i]='parent-pre-emission-not-observed'
    screens=defaultdict(list);ends=defaultdict(set);finals=defaultdict(set)
    for s in summons:
        if s['ownerObjectId']==player and s['summonCode']==1410:screens[s['objectId']].append(s)
    for t in terminals:
        if t['event']=='CmdDestroyDelayStart':ends[t['objectId']].add(t['tick'])
        elif t['event']=='CmdDestroy':finals[t['objectId']].add(t['tick'])
    child=[s for s in child_starts if s['sourceObjectId'] in screens and s['skillIdCode']==child_wire]
    child_actions=[a for a in actions if a['sourceObjectId'] in screens and a['skillIdCode']==child_wire and a.get('wireStatus') in ORDINARY_ACTIONS]
    children={};triggered={i for owners in charges.values() for i in owners};seen_charges=set()
    for s in child:
        key=(s['sourceObjectId'],s['tick']);owners=charges.get(key,[])
        if len(owners)!=1 or key in children or codes.get(s['skillCode'])!=1062210:return _unavailable(spec,'실제 스크린 후속 시전과 본체 충전이 일대일 아님')
        i=owners[0];oid=s['sourceObjectId'];rows=screens[oid]
        if len(rows)!=1 or rows[0].get('identityVerifiedAgainstGameDb') is not True or rows[0]['objectType']!=11:return _unavailable(spec,'스크린의 정확한 소유자·정체성 부족')
        if rows[0]['tick']>s['tick']:return _unavailable(spec,'스크린 생성 전 후속 시전')
        retirement=next(iter(ends[oid])) if len(ends[oid])==1 else next(iter(finals[oid])) if not ends[oid] and len(finals[oid])==1 else None
        if retirement is None or retirement<s['tick']:unknown[i]='screen-active-lifetime-unavailable'
        children[key]=dict(parent=i,start=s,retirement=retirement,prepare=None,shot=None)
        triggered.add(i);seen_charges.add(key)
    if set(charges)!=seen_charges:
        for key in set(charges)-seen_charges:
            for i in charges[key]:unknown[i]='screen-charge-without-child-start'
    # A child starts on an explicit object; its actual shoot action closes the
    # emission. Next-cast time and fixed delay are never used as a completion.
    screen_shots=defaultdict(set)
    for oid in screens:
        events=[(c['start']['tick'],0,key) for key,c in children.items() if key[0]==oid]
        events += [(a['tick'],1,a) for a in child_actions if a['sourceObjectId']==oid]
        active=None
        for tick,kind,value in sorted(events,key=lambda x:(x[0],x[1])):
            if kind==0:
                if active is not None:return _unavailable(spec,'같은 스크린의 후속 시전이 발사 전에 겹침')
                active=value;continue
            a=value
            if active is None or a['actionNo'] not in {32,33}:return _unavailable(spec,'시작 없는 스크린 행동 또는 미검토 행동')
            c=children[active];i=c['parent']
            if a['actionNo']==32:
                if c['prepare'] is not None or tick!=c['start']['tick']:unknown[i]='child-prepare-start-mismatch'
                c['prepare']=tick
            else:
                if c['prepare'] is None or tick<=c['start']['tick']:unknown[i]='child-shoot-sequence-unverified'
                if c['retirement'] is None or tick>c['retirement']:unknown[i]='child-shoot-outside-screen-lifetime'
                c['shot']=tick;screen_shots[tick].add((i,active));active=None
        if active is not None:unknown[children[active]['parent']]='child-without-recorded-shot'
    hits={name:[set() for _ in records] for name in ['direct-hit','screen-hit']};evidence_counts=Counter()
    for d in damages:
        target=d['targetObjectId']
        if d['attackerObjectId']!=player and d['attackerObjectId'] not in screens:continue
        if d.get('effectCode')!=1062202 or target not in teams or teams[target]==teams[player]:continue
        candidates=[('direct-hit',i) for i in direct[d['tick']]]+[('screen-hit',i) for i,key in screen_shots[d['tick']]]
        if len(candidates)!=1:return _unavailable(spec,'광선 피해가 본체/스크린 실제 발사 한 건에 배타적으로 연결되지 않음')
        phase,i=candidates[0];hits[phase][i].add((d['tick'],target));evidence_counts[phase]+=1
    if any(not evidence_counts[phase] for phase in hits) and reuse_policy is None:return _unavailable(spec,'일반/스크린 각각의 실제 적 타격 표본 부족')
    combat=[i for i,r in enumerate(records) if any(l<=r['start']['tick']<end for l,end in intervals)]
    selected_combat=[i for i in combat if mode!='screen-hit' or i in triggered]
    valid=[i for i in selected_combat if i not in unknown]
    contacts=hits[mode] if mode!='any' else [hits['direct-hit'][i]|hits['screen-hit'][i] for i in range(len(records))]
    row=_result(spec,[contacts[i] for i in valid],'explicit-parent-screen-child-start-and-recorded-beam-emission')
    # Keep exact timing before aggregation. A later hit must not appear at the
    # cast start, and a child emission may occur after its parent has finished.
    emissions=defaultdict(list)
    if mode in {'any','direct-hit'}:
        for tick,owners in direct.items():
            for i in owners:emissions[i].append(tick)
    if mode in {'any','screen-hit'}:
        for tick,shots in screen_shots.items():
            for i,_ in shots:emissions[i].append(tick)
    outcomes=[]
    for i in valid:
        cast_tick=records[i]['start']['tick']
        if not emissions[i] and i not in cancelled:
            return _unavailable(spec,'확정 사용에 실제 발사 시각이 없음')
        attempt_tick=min(emissions[i]) if emissions[i] else cast_tick
        first_hit=min((tick for tick,_ in contacts[i]),default=None)
        if attempt_tick<cast_tick or (first_hit is not None and first_hit<attempt_tick):
            return _unavailable(spec,'발사/적중 시각의 실제 순서 불일치')
        outcomes.append([cast_tick,int(bool(contacts[i])),attempt_tick,first_hit])
    row['outcomes']=outcomes
    row['outcomeScope']='verified-selected-parent-uses; incomplete uses excluded'
    row.update(screenBeamCounts=dict(observedQCombatUses=len(combat),screenTriggeredCombatUses=sum(i in triggered for i in combat),
        verifiedSelectedCombatUses=len(valid),unresolvedSelectedCombatUses=len(selected_combat)-len(valid),
        selectedObservedCombatUses=len(selected_combat)),
        unresolvedCastReasons=dict(Counter(unknown[i] for i in selected_combat if i in unknown)),
        cancelledBeforeAttackCount=sum(i in cancelled for i in valid),numericSkillStateCodeJoinUsed=False,
        incompleteUsesCountedAsMisses=False,linkedScreenChildCount=len(children),
        denominatorMeaning='verified screen-triggered Q uses' if mode=='screen-hit' else 'verified Q uses including gameplay cancellations',
        interpretation='본체의명시스크린충전대상→소유스크린의동시후속시작→실제발사행동에광선피해연결. 일반/스크린분리. 스크린분모는스크린을실제로작동시킨Q사용,미완결별도.')
    return annotate_reviewed_rule_reuse(row,reuse_policy,positive_gate_unsatisfied=any(not evidence_counts[phase] for phase in hits))
