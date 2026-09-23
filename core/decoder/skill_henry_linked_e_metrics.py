"""Henry E: explicitly found W linkage, explosion damage, and actual fetter."""
from collections import Counter,defaultdict
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids,ORDINARY_ACTIONS
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import event_within_cast,finish_lookup
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_action_stage_evidence import load_exact_skill_ids,ORDINARY_ACTIONS
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import event_within_cast,finish_lookup


def henry_linked_e_metric(spec,starts,finishes,actions,summons,terminals,damages,states,
                           player,teams,intervals,catalog,skill_rows,summon_rows,effect_rows,
                           state_rows,state_groups,skill_ids=None,gaps=None):
    from .skill_lifecycle_result_policy import reviewed_rule_reuse_policy,annotate_reviewed_rule_reuse
    reuse_policy=reviewed_rule_reuse_policy(gaps,{'CmdStartSkill','CmdFinishSkill','CmdPlaySkillAction','CmdSpawn','CmdDestroy','CmdDamage','CmdAddState'},starts,finishes,actions,summons,terminals,damages,states)
    if spec['skillGroup']!=1083400 or spec['mode'] not in {'any','fetter'}:
        return _unavailable(spec,'검토된 헨리 E 조건 규칙 없음')
    definition=catalog['skillGroups'].get('1083400',{})
    sd=[r for r in summon_rows if r['code']==1601]
    ef=[r for r in effect_rows if r['code']==1083402]
    st=[r for r in state_rows if r['code']==1083411]
    sg=[r for r in state_groups if r['group']==1083410]
    if ((definition.get('characterCode'),definition.get('skillId'))!=(83,'HenryActive3') or len(sd)!=1 or
        (sd[0].get('objectType'),sd[0].get('prefabPath'))!=('SummonArtifact','Henry_Skill02') or len(ef)!=1 or
        (ef[0].get('effectPrefabName'),ef[0].get('soundName'))!=('FX_BI_Henry_Skill03_Hit','') or
        len(st)!=1 or st[0]['group']!=1083410 or len(sg)!=1 or sg[0].get('stateType')!='Fetter'):
        return _unavailable(spec,'정확한 E·W객체·피해효과·속박 정의 불일치')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids;wire=ids.get('HenryActive3')
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1083400]
    codes={r['code']:r['group'] for r in skill_rows}
    if not selected or any(s['skillIdCode']!=wire or codes.get(s['skillCode'])!=1083400 for s in selected):
        return _unavailable(spec,'E 시전 누락 또는 wire 정체성 불일치')
    records,reason=ordered_cast_records(selected,finishes,player)
    if reason:return _unavailable(spec,reason)
    lookup=finish_lookup(finishes,player)
    own=[a for a in actions if a['sourceObjectId']==player and a['skillIdCode']==wire and a.get('wireStatus') in ORDINARY_ACTIONS]
    per_use=[[] for _ in records]
    for a in own:
        if a['actionNo'] not in {1,3,4,5,6}:return _unavailable(spec,'미검토 E 행동')
        owners=[
            i for i,r in enumerate(records) if (event_within_cast(r['start'],r['finish']['tick'],a,lookup) if r['finish'] else a['tick']>=r['start']['tick'])]
        if len(owners)!=1:return _unavailable(spec,'E 행동의 정확한 사용 귀속 불가')
        per_use[owners[0]].append(a)
    objects=defaultdict(list);ends=defaultdict(set)
    for s in summons:
        if s['ownerObjectId']==player and s['summonCode']==1601:objects[s['objectId']].append(s)
    for t in terminals:
        if t['event']=='CmdDestroyDelayStart':ends[t['objectId']].add(t['tick'])
    condition={};unknown={i:r['reason'] for i,r in enumerate(records) if not r['complete']};cancelled=set()
    markers=defaultdict(set)
    for i,(r,aa) in enumerate(zip(records,per_use)):
        numbers=Counter(a['actionNo'] for a in aa)
        if numbers[4]==1 and numbers[5]==1 and not numbers[3]:condition[i]='w-linked'
        elif numbers[3]==1 and not any(numbers[n] for n in [4,5,6,1]):condition[i]='movement-only'
        else:unknown[i]='unverified-E-linkage-condition';continue
        if not r['complete']:continue
        if condition[i]=='movement-only':continue
        if numbers[6]==0 and numbers[1]==0 and r['finish']['reason'] in (set(range(1,15))|{16,17}):
            cancelled.add(i);continue
        if numbers[6]!=1:unknown[i]='missing-or-duplicate-linked-explosion';continue
        explosion=next(a for a in aa if a['actionNo']==6)
        for a in aa:
            if a['actionNo']!=1:continue
            targets={t['targetObjectId'] for t in a['targets']}
            if a['tick']!=explosion['tick'] or len(targets)!=1:
                unknown[i]='hit-not-at-exact-linked-explosion';continue
            oid=next(iter(targets));ss=objects[oid]
            if len(ss)!=1 or ss[0].get('identityVerifiedAgainstGameDb') is not True or ss[0]['objectType']!=21:
                unknown[i]='unverified-W-root-identity';continue
            if ss[0]['tick']>a['tick'] or len(ends[oid])!=1 or a['tick']>next(iter(ends[oid])):
                unknown[i]='W-root-active-lifetime-unavailable';continue
            markers[a['tick']].add((i,oid))
    hits=[set() for _ in records];cc=[set() for _ in records];actual_damage=0
    for d in damages:
        target=d['targetObjectId']
        if d['attackerObjectId']!=player or d.get('effectCode')!=1083402 or target not in teams or teams[target]==teams[player]:continue
        owners=markers[d['tick']]
        if len(owners)!=1:return _unavailable(spec,'E 피해에 배타적인 W 객체 지정 타격이 없음')
        i,_=next(iter(owners));hits[i].add((d['tick'],target));actual_damage+=1
    for s in states:
        target=s.get('targetObjectId')
        if s.get('event')!='add' or s.get('casterObjectId')!=player or s.get('stateCode')!=1083411 or target not in teams or teams[target]==teams[player]:continue
        owners=markers[s['tick']]
        if len(owners)!=1:return _unavailable(spec,'실제 속박에 명시 W 연계 타격이 없음')
        i,_=next(iter(owners))
        if (s['tick'],target) not in hits[i]:unknown[i]='fetter-without-corroborated-E-damage'
        else:cc[i].add((s['tick'],target))
    if not actual_damage and reuse_policy is None:return _unavailable(spec,'실제 W 연계 E 피해 검증 표본 없음')
    combat=[i for i,r in enumerate(records) if any(l<=r['start']['tick']<end for l,end in intervals)]
    linked=[i for i in combat if condition.get(i)=='w-linked'];valid=[i for i in linked if i not in unknown]
    row=_result(spec,[(hits if spec['mode']=='any' else cc)[i] for i in valid],'exact-W-linked-E-condition-object-hit-and-actual-fetter',
                cast_ticks=[records[i]['start']['tick'] for i in valid])
    row.update(conditionCounts=dict(observedAllECombatUses=len(combat),observedWLinkedCombatUses=len(linked),
        verifiedWLinkedCombatUses=len(valid),unresolvedWLinkedCombatUses=sum(i in unknown for i in linked),
        movementOnlyCombatUses=sum(condition.get(i)=='movement-only' for i in combat),
        unresolvedConditionCombatUses=sum(i not in condition for i in combat)),
        unresolvedCastReasons=dict(Counter(unknown[i] for i in combat if i in unknown)),
        cancelledBeforeAttackCount=sum(i in cancelled for i in valid),
        actualCCApplicationEventCount=sum(len(cc[i]) for i in valid),
        movementOnlyUsesCountedAsMisses=False,incompleteUsesCountedAsMisses=False,numericSkillStateCodeJoinUsed=False,
        interpretation='분모는 W 연계가 명시된 E 사용 중 결과가 완결된 사용. 비연계 이동은 별도 횟수이며 실패 아님. 실제 피해와 실제 속박을 별도 계산. 마지막 미완결은 분리.')
    return annotate_reviewed_rule_reuse(row,reuse_policy,positive_gate_unsatisfied=not actual_damage)
