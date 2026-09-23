"""Per-use outcomes through an explicit created-object hit marker.

A missing late object termination does not invalidate independent earlier
objects. Incomplete uses remain counted separately, never as misses. Damage
must name the owner and match a same-tick hit action naming one exact object;
an exclusive time window or FX identity alone is insufficient.
"""
from collections import Counter, defaultdict
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS
    from .skill_projectile_active_end import projectile_active_end_records
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS
    from skill_projectile_active_end import projectile_active_end_records

CONFIG={1083300:dict(character=83,skill='HenryActive2',summon=1601,
    prefab='Henry_Skill02',createAction=2,hitAction=1,
    effects={1083301:('FX_BI_Henry_Skill02_Hit','')})}
GAMEPLAY_CANCELS=set(range(1,15))|{16,17}


def created_object_hit_metric(spec,starts,finishes,actions,summons,terminals,
                               damages,player,teams,intervals,catalog,skill_rows,
                               summon_rows,effect_rows,skill_ids=None,gaps=None):
    from .skill_lifecycle_result_policy import reviewed_rule_reuse_policy,annotate_reviewed_rule_reuse
    reuse_policy=reviewed_rule_reuse_policy(gaps,{'CmdStartSkill','CmdFinishSkill','CmdPlaySkillAction','CmdSpawn','CmdDestroy','CmdDamage'},starts,finishes,actions,summons,terminals,damages)
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    cfg=CONFIG.get(spec['skillGroup']);group=spec['skillGroup']
    if not cfg or spec['mode']!='any' or spec['unit']!='skill-cast':
        return _unavailable(spec,'검토된 생성 객체 지정 타격 규칙 없음')
    definition=catalog['skillGroups'].get(str(group),{})
    sd=[s for s in summon_rows if s['code']==cfg['summon']]
    if (definition.get('skillId'),definition.get('characterCode'))!=(cfg['skill'],cfg['character']) or len(sd)!=1 or (
        sd[0].get('objectType'),sd[0].get('prefabPath'))!=('SummonArtifact',cfg['prefab']):
        return _unavailable(spec,'정확한 스킬·생성 객체 정의 불일치')
    for code,identity in cfg['effects'].items():
        ef=[r for r in effect_rows if r['code']==code]
        if len(ef)!=1 or (ef[0].get('effectPrefabName'),ef[0].get('soundName'))!=identity:
            return _unavailable(spec,'정확한 타격 효과 정의 불일치')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids;wire=ids.get(cfg['skill'])
    codes={s['code']:s['group'] for s in skill_rows}
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==group]
    if not selected or any(s['skillIdCode']!=wire or codes.get(s['skillCode'])!=group for s in selected):
        return _unavailable(spec,'시전 누락 또는 wire 정체성 불일치',
                            category='observation-conflict', reason_code='cast-wire-identity')
    casts,reason=exact_cast_lifetimes(selected,finishes,player)
    if reason:return _unavailable(spec,reason)
    own=[a for a in actions if a['sourceObjectId']==player and a['skillIdCode']==wire and a.get('wireStatus') in ORDINARY_ACTIONS]
    if any(a.get('actionNo') not in {cfg['createAction'],cfg['hitAction']} for a in own):
        return _unavailable(spec,'생성/타격 외의 미검토 행동이 있음')
    objects=defaultdict(list)
    for s in summons:
        if s['ownerObjectId']==player and s['summonCode']==cfg['summon']:objects[s['objectId']].append(s)
    # Same actual removal-order validation as collision-only projectiles.
    # The reviewed Henry object contract, not this helper, establishes that
    # object retirement closes this object's hit-marker lifetime.
    active_ends=projectile_active_end_records(terminals)
    roots={};unresolved={};cancelled=set();retired_without_final=0
    missing_end_creators={};ordered_markers=defaultdict(set);proven_positive=set()
    for i,(s,end) in enumerate(casts):
        creators=[a for a in own if a['actionNo']==cfg['createAction'] and s['tick']<=a['tick']<=end]
        if not creators:
            ff=[f for f in finishes if f['playerObjectId']==player and f['skillIdCode']==wire and f['tick']==end]
            if len(ff)==1 and ff[0]['reason'] in GAMEPLAY_CANCELS:cancelled.add(i)
            else:unresolved[i]='normal-use-without-explicit-created-object'
            continue
        if len(creators)!=1 or len(creators[0]['targets'])!=1:
            return _unavailable(spec,'시전과 생성 객체 지정 행동이 일대일 아님')
        a=creators[0];oid=a['targets'][0]['targetObjectId'];ss=objects[oid]
        if len(ss)!=1 or oid in roots:return _unavailable(spec,'생성 객체 정체성 중복 또는 누락')
        obj=ss[0]
        if obj['tick']!=a['tick'] or obj['objectType']!=21 or obj.get('identityVerifiedAgainstGameDb') is not True:
            return _unavailable(spec,'지정 객체의 생성 시각·종류·소유자 불일치')
        end_record=active_ends.get(oid,{})
        endpoint=end_record.get('endTick') if end_record.get('complete') else None
        if endpoint is not None and 'CmdDestroy' not in end_record['observedRemovalTicks']:
            retired_without_final+=1
        if endpoint is None or endpoint<obj['tick']:unresolved[i]='created-object-active-end-unavailable'
        roots[oid]=(i,obj['tick'],endpoint)
        if not end_record and not any(t['objectId']==oid for t in terminals):
            missing_end_creators[i]=a
    if set(roots)!=set(objects):return _unavailable(spec,'명시 사용 없이 남은 소유 객체가 있음')
    markers=defaultdict(set);marker_ticks=defaultdict(set)
    for a in own:
        if a['actionNo']!=cfg['hitAction']:continue
        targets={t['targetObjectId'] for t in a['targets']}
        if len(targets)!=1 or not targets<=set(roots):return _unavailable(spec,'타격 행동이 한 생성 객체를 직접 지정하지 않음')
        oid=next(iter(targets));i,left,right=roots[oid]
        if a['tick']<left or (right is not None and a['tick']>right):
            unresolved[i]='hit-marker-outside-recorded-object-active-lifetime'
        markers[a['tick']].add(oid);marker_ticks[i].add(a['tick'])
        if i in missing_end_creators:
            from .skill_wire_order import command_order
            start=casts[i][0];creator=missing_end_creators[i]
            so,co,ao=map(command_order,(start,creator,a))
            if so is not None and co is not None and ao is not None and so<co<ao and start['tick']<=creator['tick']<=a['tick']:
                ordered_markers[a['tick']].add(oid)
    contacts=[set() for _ in casts];packets=[0 for _ in casts]
    for d in damages:
        target=d['targetObjectId']
        if d['attackerObjectId']!=player or d.get('effectCode') not in cfg['effects'] or target not in teams or teams[target]==teams[player]:continue
        owners=markers[d['tick']]
        if not owners:
            # The damage has no object identity; do not guess which use it came from.
            return _unavailable(spec,'실제 타격이 한 생성 객체 지정 행동과 같은 시각에 연결되지 않음')
        if len(owners)>1:
            for oid in owners:unresolved[roots[oid][0]]='ambiguous-same-tick-object-hit-markers'
            continue
        i,_,_=roots[next(iter(owners))];contacts[i].add((d['tick'],target));packets[i]+=1
        if i in missing_end_creators and ordered_markers[d['tick']]==owners:
            from .skill_wire_order import command_order
            co=command_order(missing_end_creators[i]);at=command_order(d)
            if co is not None and at is not None and co<at:proven_positive.add(i)
    combat=[i for i,(s,_) in enumerate(casts) if any(l<=s['tick']<r for l,r in intervals)]
    recovered=[i for i in combat if i in proven_positive
               and unresolved.get(i)=='created-object-active-end-unavailable']
    for i in recovered:unresolved.pop(i)
    valid=[i for i in combat if i not in unresolved]
    if not any(packets[i] for i in valid) and reuse_policy is None:
        return _unavailable(spec,'객체 타격의 효과 매핑을 독립 규칙으로 확정하는 작업이 남음',
                            category='rule-mapping-incomplete', reason_code='effect-mapping-needs-independent-contract')
    row=_result(spec,[contacts[i] for i in valid],'explicit-created-object-hit-marker-and-per-use-completeness',
                cast_ticks=[casts[i][0]['tick'] for i in valid])
    row.update(observedCombatCastCount=len(combat),verifiedCombatCastCount=len(valid),
        unresolvedCombatCastCount=sum(i in unresolved for i in combat),
        unresolvedAllCastCount=len(unresolved),
        unresolvedCastReasons=dict(Counter(unresolved[i] for i in combat if i in unresolved)),
        perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,
        cancelledBeforeAttackCount=sum(i in cancelled for i in valid),
        exactDamagePacketCount=sum(packets[i] for i in valid),
        recordedHitMarkerCount=sum(len(marker_ticks[i]) for i in valid),
        retiredObjectsWithoutFinalRemovalCount=retired_without_final,
        knownCreatedObjectCount=len(roots),numericSkillStateCodeJoinUsed=False,
        interpretation='명시 생성 객체 지정 타격과 실제 피해를 연결. 미완결 사용은 적중률 분모/실패에서 제외하고 별도 횟수 표시. 반복 접촉과 사용별 서로 다른 적 인원은 별도.')
    row=annotate_reviewed_rule_reuse(row,reuse_policy,positive_gate_unsatisfied=not any(packets[i] for i in valid))
    if recovered:
        from .skill_lifecycle_result_policy import finalize_lifecycle_result
        row=finalize_lifecycle_result(row,combat,unresolved,incomplete_positive=recovered)
    return row
