"""Jan W native sector execution and its owned knockback continuation.

Normal/inner effect 1035300 is also used by R: an active owned ring makes
that packet ambiguous for a simultaneous W. Outer/wall effect 1035301 can
continue after FinishSkill through the actual W knockback state lifetime.
No nearest-cast, fixed-duration, projectile substitute or positive-sample gate.
"""
from collections import Counter,defaultdict
# Explicit 12.3.0 archives whose skill tables are byte-identical under
# skill-game-data-revisions-v1.json. Keep each replay's real archive identity.
JAN_NATIVE_RULE_ARCHIVES=frozenset({
    '5ef9cb5459a2e908099655bf9ffc70ac16b134d1db282fb97b25fe065e7c8e4f',
    '82dc298a2047af0e23637a6a78fcbbb70680e70bcdb7d80ca96ebec2e0f02b7c',
    'ff6e6a5a12424fbe9c04255cc8e3acdf2f89386ceb422ea741a0aa25373a2aa6',
})
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order
    from .skill_action_stage_evidence import load_exact_skill_ids,ORDINARY_ACTIONS
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order
    from skill_action_stage_evidence import load_exact_skill_ids,ORDINARY_ACTIONS


def jan_direct_execution(spec,starts,finishes,actions,damages,states,summons,terminals,
                         player,teams,intervals,catalog,skill_rows,state_rows,state_groups,
                         effect_rows,summon_rows,skill_ids=None,gaps=None,state_inventory=None,state_identity=None,rope_inventory=None):
    fail=lambda reason:_unavailable(spec,reason)
    if any(v is None for v in (starts,finishes,actions,damages,states,summons,terminals)):
        return fail('얀 W 실행 판정에 필요한 피해·상태·소환물 입력이 없음')
    if (spec['characterCode'],spec['skillGroup'],spec['mode'],spec['unit'])!=(35,1035300,'any','skill-cast'):
        return fail('얀 W 직접 실행 전용 경로가 아님')
    definition=catalog['skillGroups'].get('1035300',{})
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    wire=ids.get('JanActive2');codes={r['code'] for r in skill_rows if r['group']==1035300}
    sg={r['group']:r for r in state_groups};state_codes={r['code']:r['group'] for r in state_rows}
    effects={r['code']:r for r in effect_rows};ring={r['code']:r for r in summon_rows}.get(1170,{})
    if ((definition.get('characterCode'),definition.get('skillId'))!=(35,'JanActive2') or wire is None or not codes
        or (sg.get(1035300,{}).get('skillId'),sg.get(1035300,{}).get('stateType'))!=('Knockback','Airborne')
        or not all(state_codes.get(c)==1035300 for c in range(1035301,1035306))
        or state_codes.get(1035311)!=1035310
        or any((sg.get(g,{}).get('skillId'),sg.get(g,{}).get('stateType'))!=('Stun','Stun') for g in (1035320,1035330))
        or effects.get(1035300,{}).get('effectPrefabName')!='FX_BI_Jan_Skill02_Hit'
        or effects.get(1035301,{}).get('effectPrefabName')!='FX_BI_Jan_Skill02_Hit_P'
        or (ring.get('objectType'),ring.get('prefabPath'))!=('SummonArtifact','Jan_Skill04_Ring')):
        return fail('얀 W 직접 피해·밀치기·공유 R 링의 고정 데이터 정의 불일치')
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1035300]
    if not selected or any(s['skillIdCode']!=wire or s['skillCode'] not in codes for s in selected):
        return fail('얀 W 시전 없음 또는 wire 정체성 불일치')
    records,reason=ordered_cast_records(selected,finishes,player)
    if reason:return fail(reason)
    unknown={i:r['reason'] for i,r in enumerate(records) if not r['complete']}
    closures={}
    phase_contacts={k:[set() for _ in records] for k in ('inner','outer-or-reinforced','wall-stun','outer','reinforced')}
    push_history=defaultdict(list)
    reinforce_markers=sorted((command_order(x), x['event']=='add') for x in states
        if x.get('targetObjectId')==player and command_order(x) is not None and
        ((x.get('event')=='add' and x.get('stateCode')==1035311) or
         (x.get('event')=='remove' and x.get('stateGroup')==1035310)))
    uncertain_region=set();baseline_requests=[];baseline_proofs=[]
    def reinforced_at(event):
        prior=[value for order,value in reinforce_markers if order<=command_order(event)]
        if prior:return prior[-1]
        from .skill_jan_state_admission import reinforce_absent
        proof=reinforce_absent(event,player,state_inventory,state_identity)
        if proof:baseline_proofs.append(proof);return False
        return None

    own_actions=[a for a in actions if a.get('sourceObjectId')==player and a.get('skillIdCode')==wire and a.get('wireStatus') in ORDINARY_ACTIONS]
    for i,r in enumerate(records):
        if not r['complete']:continue
        if r['finish']['reason']!=0:
            unknown[i]='W-execution-not-normally-complete';continue
        # Native Process action2 is emitted after the InflictDamage loop.
        matches=[a for a in own_actions if a['actionNo']==2 and a['tick']==r['finish']['tick'] and command_order(a) is not None and command_order(a)>=command_order(r['start'])]
        if len(matches)!=1:unknown[i]='W-native-action2-closure-unavailable'
        else:closures[i]=matches[0]
    def direct_parents(event):
        at=command_order(event)
        if at is None:return []
        return [i for i,r in enumerate(records) if command_order(r['start'])<=at and r['start']['tick']<=event['tick'] and
                (i not in closures or (at<=command_order(closures[i]) and event['tick']<=closures[i]['tick'])) and
                (i in closures or r['finish'] is None or
                 (event['tick']<=r['finish']['tick'] and at<=command_order(r['finish'])))]
    ends=defaultdict(set)
    for t in terminals:
        if t.get('event')=='CmdDestroy':ends[t['objectId']].add(t['tick'])
    rings=[]
    for obj in summons:
        if obj.get('ownerObjectId')!=player or obj.get('summonCode')!=1170:continue
        if obj.get('identityVerifiedAgainstGameDb') is not True:return fail('공유 피해 R 링 객체 정체성 미검증')
        es=ends[obj['objectId']]
        if len(es)>1 or (es and next(iter(es))<obj['tick']):return fail('공유 피해 R 링 소멸 기록 충돌')
        rings.append((obj['tick'],next(iter(es)) if es else None))
    from .skill_development_effect_metrics import development_policy
    dev=development_policy()
    estimate_enabled=(dev.get('sharedProducerActionAttributionEnabled') is True and dev['enabled']
        and gaps is not None and not any(g.get('count',0) and g.get('packetName') in
        {'CmdPlaySkillAction','CmdPlaySkillActionWithTargets','CmdSpawn','CmdDamage'} for g in gaps))
    rope_ids={o['objectId'] for o in summons if o.get('ownerObjectId')==player and o.get('summonCode')==1173}
    rope_ticks={a['tick'] for a in actions if a.get('sourceObjectId') in rope_ids and a.get('skillIdCode')==492}
    from .skill_shared_callback_attribution import unique_damage_marker_action_candidates
    rope_candidates={}
    if estimate_enabled:
        rope_candidates=unique_damage_marker_action_candidates(
            [d for d in damages if d.get('attackerObjectId')==player and d.get('effectCode')==1035300],
            [s for s in states if s.get('event')=='add' and s.get('casterObjectId')==player
             and s.get('stateCode')==1035551 and s.get('targetObjectId') in rope_ids],
            [a for a in actions if a.get('sourceObjectId') in rope_ids and a.get('skillIdCode')==492
             and a.get('actionNo') in {1,2,11,12}])
    estimated_rope_exclusions=defaultdict(list)
    estimated=[set() for _ in records]
    shared_candidates=[]
    direct_emission_ticks=defaultdict(set)
    direct_target_receipts=defaultdict(lambda:defaultdict(list))
    reinforced_direct_receipts=defaultdict(list)
    def possible_ring(tick):return any(begin<=tick and (end is None or tick<=end) for begin,end in rings)
    enemy=lambda target:target in teams and teams[target]!=teams[player]
    events=[]
    for state in states:
        if state.get('casterObjectId')!=player or not enemy(state.get('targetObjectId')):continue
        if ((state.get('event')=='add' and state_codes.get(state.get('stateCode'))==1035300) or
            (state.get('event')=='remove' and state.get('stateGroup')==1035300) or
            (state.get('event')=='add' and state_codes.get(state.get('stateCode')) in {1035320,1035330})):
            events.append(('state',state))
    for d in damages:
        if d.get('attackerObjectId')==player and d.get('effectCode') in {1035300,1035301} and enemy(d.get('targetObjectId')):events.append(('damage',d))
    if any(command_order(e) is None for _,e in events):return fail('얀 W 피해·밀치기 사건의 명령 순서 없음')
    if len({command_order(e) for _,e in events})!=len(events):return fail('얀 W 피해·상태 명령 순서 중복')
    active=defaultdict(list);contacts=[set() for _ in records];packets=Counter();foreign=0;continued=0
    for kind,event in sorted(events,key=lambda v:command_order(v[1])):
        target=event['targetObjectId']
        if kind=='state':
            if event['event']=='add' and state_codes.get(event.get('stateCode')) in {1035320,1035330}:
                owners={h['parent'] for h in push_history[target] if h['start']<=command_order(event)
                    and (h['endTick'] is None or event['tick']<=h['endTick'])}
                if len(owners)==1:
                    phase_contacts['wall-stun'][next(iter(owners))].add((event['tick'],target))
                continue
            if event['event']=='add':
                parents=direct_parents(event)
                if not parents:return fail('W 시전에 연결되지 않는 밀치기 시작')
                if active[target] or len(parents)!=1:
                    for parent in [*parents,*[i for ps in active[target] for i in ps]]:unknown[parent]='overlapping-W-knockback-states'
                active[target].append(parents)
                if len(parents)==1:push_history[target].append(dict(parent=parents[0],start=command_order(event),endTick=None))
            else:
                if not active[target]:return fail('시작 없이 끝나는 W 밀치기 상태')
                active[target]=[]
                for h in push_history[target]:
                    if h['endTick'] is None:h['endTick']=event['tick']
            continue
        direct=set(direct_parents(event));parents=set(direct)
        if event['effectCode']==1035301:parents.update(i for ps in active[target] for i in ps)
        elif possible_ring(event['tick']):
            shared_candidates.append((event,direct))
            continue
        if not parents:return fail('직접 실행·밀치기·R 링 어디에도 귀속되지 않는 얀 피해')
        if len(parents)!=1:
            for i in parents:unknown[i]='ambiguous-W-direct-or-knockback-parent'
            continue
        parent=next(iter(parents));contacts[parent].add((event['tick'],target));packets[parent]+=1
        if event['effectCode']==1035301 and parent in direct and not active[target]:
            direct_emission_ticks[parent].add(event['tick'])
            direct_target_receipts[parent][target].append(event)
        if parent in direct:
            phase_contacts['inner' if event['effectCode']==1035300 else 'outer-or-reinforced'][parent].add((event['tick'],target))
            if event['effectCode']==1035301:
                reinforced=reinforced_at(event)
                if reinforced is None:
                    uncertain_region.add(parent)
                    baseline_requests.append(dict(playerObjectId=player,start=records[parent]['start'],damage=event))
                else:
                    phase_contacts['reinforced' if reinforced else 'outer'][parent].add((event['tick'],target))
                    if reinforced and not active[target]:reinforced_direct_receipts[parent].append(event)
        if parent not in direct:continued+=1
    # Native Process repeats InflictDamage around WaitForFrame.
    # One observed direct hit is not the whole execution interval. A later
    # target can enter the sector before action2 closes the repeated loop.
    outside_emission=0
    retained_cross_tick=[]
    target_dedupe_exclusions=[]
    reinforced_cast_exclusions=[]
    rope_absence_requests=[]
    rope_absence_contacts=[]
    reinforced_cast_starts={}
    if (catalog.get('sourceGameDbSha256') in JAN_NATIVE_RULE_ARCHIVES
            and gaps is not None and not any(g.get('count',0) for g in gaps)):
        for i,witnesses in reinforced_direct_receipts.items():
            if i not in closures or command_order(closures[i])>command_order(records[i]['finish']):continue
            start=records[i]['start'];begin=command_order(start)
            if any(j!=i and start['tick']<=r['start']['tick']<=closures[i]['tick'] for j,r in enumerate(records)):continue
            markers=[s for s in states if s.get('event')=='add' and s.get('stateCode')==1035311
                and s.get('targetObjectId')==player and s['tick']==start['tick']
                and command_order(s) is not None and command_order(s)[0]==begin[0] and command_order(s)<begin]
            if len(markers)==1:reinforced_cast_starts[i]=markers[0]
    for event,direct in shared_candidates:
        direct=set(direct)
        # The private reinforcement latch is written at Start, not at each
        # target or frame. Its dedicated start marker plus an independent
        # reinforced direct hit prove this uninterrupted cast cannot use inner.
        for i in direct & reinforced_cast_starts.keys():
            marker=reinforced_cast_starts[i]
            reinforced_cast_exclusions.append(dict(castTick=records[i]['start']['tick'],
                sharedTick=event['tick'],targetObjectId=event['targetObjectId'],
                markerWireOrder=list(command_order(marker)),
                reinforcedDirectTicks=sorted({e['tick'] for e in reinforced_direct_receipts[i]}),
                closureWireOrder=list(command_order(closures[i])),finishWireOrder=list(command_order(records[i]['finish']))))
        direct.difference_update(reinforced_cast_starts)
        if (catalog.get('sourceGameDbSha256') in JAN_NATIVE_RULE_ARCHIVES
                and gaps is not None and not any(g.get('count',0) for g in gaps)):
            for i in list(direct):
                receipts=direct_target_receipts[i][event['targetObjectId']]
                if len(receipts)!=1 or receipts[0]['tick']==event['tick'] or i not in closures:continue
                if command_order(closures[i])>command_order(records[i]['finish']):continue
                if any(j!=i and records[i]['start']['tick']<=r['start']['tick']<=closures[i]['tick']
                       for j,r in enumerate(records)):continue
                direct.remove(i)
                target_dedupe_exclusions.append(dict(castTick=records[i]['start']['tick'],
                    targetObjectId=event['targetObjectId'],sharedTick=event['tick'],
                    directOuterTick=receipts[0]['tick'],sharedWireOrder=list(command_order(event)),
                    directOuterWireOrder=list(command_order(receipts[0])),
                    closureWireOrder=list(command_order(closures[i])),
                    finishWireOrder=list(command_order(records[i]['finish']))))
        if (len(direct)==1 and catalog.get('sourceGameDbSha256') in JAN_NATIVE_RULE_ARCHIVES
                and gaps is not None and not any(g.get('count',0) for g in gaps)):
            i=next(iter(direct));start=records[i]['start']
            if (i in closures and command_order(closures[i])<=command_order(records[i]['finish'])
                    and not any(j!=i and start['tick']<=r['start']['tick']<=closures[i]['tick']
                                for j,r in enumerate(records))):
                from .skill_jan_rope_admission import admitted_rope_absence
                admission=admitted_rope_absence(start,event,player,rope_inventory,state_identity)
                if admission:
                    contacts[i].add((event['tick'],event['targetObjectId']))
                    phase_contacts['inner'][i].add((event['tick'],event['targetObjectId']))
                    packets[i]+=1
                    rope_absence_contacts.append(admission)
                    continue
                rope_absence_requests.append(dict(playerObjectId=player,start=start,damage=event))
        cross_tick={i for i in direct if len(direct_emission_ticks[i])==1
                    and event['tick'] not in direct_emission_ticks[i]}
        if cross_tick:
            for i in direct:unknown[i]='shared-inner-W-or-R-rope-damage'
            retained_cross_tick.append(dict(tick=event['tick'],targetObjectId=event['targetObjectId'],
                candidateCastTicks=[records[i]['start']['tick'] for i in sorted(direct)]))
            continue
        eligible=set(direct)
        if not eligible:
            foreign+=1
            if direct:outside_emission+=1
            continue
        if estimate_enabled and len(eligible)==1 and command_order(event) in rope_candidates:
            estimated_rope_exclusions[next(iter(eligible))].append(rope_candidates[command_order(event)])
            continue
        if estimate_enabled and len(eligible)==1 and event['tick'] not in rope_ticks:
            estimated[next(iter(eligible))].add((event['tick'],event['targetObjectId']))
        else:
            for i in eligible:unknown[i]='shared-inner-W-or-R-rope-damage'
    for entries in active.values():
        for parents in entries:
            for i in parents:unknown[i]='W-knockback-continuation-not-closed'
    combat=[i for i,r in enumerate(records) if any(a<=r['start']['tick']<b for a,b in intervals)]
    confirmed=[set(c) for c in contacts]
    confirmed_inner=[set(c) for c in phase_contacts['inner']]
    for i in range(len(records)):
        contacts[i].update(estimated[i])
        phase_contacts['inner'][i].update(estimated[i])
    from .skill_partial_cast_lifetimes import user_cancelled_no_recorded_contact_indices
    pending_continuations={i for entries in active.values() for parents in entries for i in parents}
    cancelled_no_contact=user_cancelled_no_recorded_contact_indices(records,contacts,unknown,
        cancellation_reason='W-execution-not-normally-complete',
        pending_continuations=pending_continuations,gaps=gaps)
    phase_unknown=dict(unknown)
    for i in cancelled_no_contact:unknown.pop(i)
    parent_unknown={i:why for i,why in unknown.items() if not (contacts[i] and why in {'shared-inner-W-or-R-rope-damage','W-execution-not-normally-complete','W-knockback-continuation-not-closed'})}
    valid=[i for i in combat if i not in parent_unknown]
    diagnostics=dict(perUseCompletenessTracked=True,observedCombatCastCount=len(combat),verifiedCombatCastCount=sum(i not in cancelled_no_contact for i in valid),
        unresolvedCombatCastCount=len(combat)-len(valid),unresolvedCastReasons=dict(Counter(parent_unknown[i] for i in combat if i in parent_unknown)),
        unresolvedCastTicks=[records[i]['start']['tick'] for i in combat if i in parent_unknown],
        incompleteUsesCountedAsMisses=False,
        cancelledNoRecordedContactCastCount=sum(i in combat for i in cancelled_no_contact),
        cancelledNoRecordedContactCastTicks=[records[i]['start']['tick'] for i in cancelled_no_contact if i in combat],
        cancelledNegativeMeaning='사용자 규칙: 실제 취소 종료까지 기록된 접촉 없음을 미스로 집계. native 완결 또는 기하학적 비적중 증명이 아님.',
        userPolicyMissCastCount=sum(i in combat for i in cancelled_no_contact),
        userPolicyMissCastTicks=[records[i]['start']['tick'] for i in cancelled_no_contact if i in combat],
        userPolicyMissAuthority='explicit-user-rule',
        foreignSharedRDamageExcluded=foreign,postDirectKnockbackDamagePackets=continued,
        sharedDamageOutsideRecordedEmissionExcluded=outside_emission,
        sharedCrossTickDamageRetainedUnknown=retained_cross_tick,
        nativeTargetDedupeExclusions=target_dedupe_exclusions,
        nativeTargetDedupeProof='deliverables/native-jan-w-target-dedup-v1.json',
        reinforcedCastInnerExclusions=reinforced_cast_exclusions,
        reinforcedCastLatchProof='deliverables/native-jan-w-reinforce-latch-v1.json',
        recordedDirectEmissionTicksByCast=[dict(startTick=records[i]['start']['tick'],ticks=sorted(ts)) for i,ts in direct_emission_ticks.items()],
        nativeDirectEffectCodes=[1035300,1035301],staticProducerProof='deliverables/native-damage-effect-assignments-v1.json')
    if combat and not valid:return {**fail('얀 W 실행·후속 밀치기가 완결된 교전 시전 없음'),**diagnostics}
    row=_result(spec,[contacts[i] for i in valid],'native-Jan-W-sector-and-owned-knockback-execution',cast_ticks=[records[i]['start']['tick'] for i in valid])
    row.update(**diagnostics,exactDamagePacketCount=sum(packets[i] for i in valid),
        interpretation='W 직접 피해와 실제 밀치기 수명의 후속 피해를 합산. R과 공유되어 구분할 수 없는 사용은 미확정으로 별도 집계.')
    from .skill_development_effect_metrics import annotate_provisional_rate, development_policy
    policy=development_policy()
    row=annotate_provisional_rate(row,policy)
    def estimate_metadata(target, indices, strict_contacts):
        chosen=[i for i in indices if estimated[i]]
        if not chosen:return
        target.update(developmentLabel='개발 중 · 귀속 추정 적중 포함',attributionEstimateMayOvercount=True,
            estimatedContactCastCount=len(chosen),estimatedOnlyHitCastCount=sum(not strict_contacts[i] for i in chosen),
            confirmedHitCastCount=sum(bool(strict_contacts[i]) for i in indices),
            confirmedHitCastTicks=[records[i]['start']['tick'] for i in indices if strict_contacts[i]],
            estimatedContactsByCast=[dict(startTick=records[i]['start']['tick'],contacts=sorted(estimated[i])) for i in chosen],
            targetCountsAreLowerBounds=False,completeTargetCountsAvailable=False)
    estimate_metadata(row,valid,confirmed)
    def exclusion_metadata(target,indices,phase_contacts):
        chosen=[i for i in indices if estimated_rope_exclusions[i]]
        if not chosen:return
        target.update(developmentLabel='개발 중 · 공용 피해 귀속 추정 포함',
            attributionEstimateMayMisclassify=True,estimatedAttributionCastCount=len(chosen),
            estimatedAttributionCastTicks=[records[i]['start']['tick'] for i in chosen],
            estimatedMissCastCount=sum(not phase_contacts[i] for i in chosen),
            estimatedMissCastTicks=[records[i]['start']['tick'] for i in chosen if not phase_contacts[i]],
            attributionEstimateMeaning='단일 공용 피해→소유 R 로프 상태→작동 명령 순서로 R 귀속 추정. W에서 제외한 피해의 실제 귀속은 미확정.',
            estimatedRopeExclusions=[dict(castTick=records[i]['start']['tick'],evidence=estimated_rope_exclusions[i]) for i in chosen])
    exclusion_metadata(row,valid,contacts)
    row['phaseMetrics']={}
    for key,label in [('inner','W 안쪽 타격'),('outer-or-reinforced','W 바깥 또는 강화 타격'),('wall-stun','W 벽 기절'),('outer','W 바깥 타격'),('reinforced','W 강화 타격')]:
        pc=phase_contacts[key]
        pending={i:why for i,why in phase_unknown.items() if i in combat and not pc[i]
                 and (key=='inner' or why!='shared-inner-W-or-R-rope-damage')}
        if key in {'outer','reinforced'}:
            pending.update({i:'reinforce-state-baseline-unavailable' for i in uncertain_region if i in combat and not pc[i]})
        policy_misses=user_cancelled_no_recorded_contact_indices(records,pc,pending,
            cancellation_reason='W-execution-not-normally-complete',
            pending_continuations=pending_continuations,gaps=gaps)
        for i in policy_misses:pending.pop(i)
        ids=[i for i in combat if i not in pending]
        child=_result(dict(spec,metricId=spec['metricId']+':'+key,label=label),[pc[i] for i in ids],
                      'Jan-W-recorded-phase-contact',cast_ticks=[records[i]['start']['tick'] for i in ids])
        child=annotate_provisional_rate(child,policy)
        child.update(perUseCompletenessTracked=True,unresolvedCombatCastCount=len(pending),
                     unresolvedCastTicks=[records[i]['start']['tick'] for i in pending],
                     unresolvedCastReasons=dict(Counter(pending.values())),observedCombatCastCount=len(combat),
                     userPolicyMissCastCount=len(policy_misses),
                     userPolicyMissCastTicks=[records[i]['start']['tick'] for i in policy_misses],
                     userPolicyMissAuthority='explicit-user-rule',
                     userPolicyMissMeaning='실제 취소 종료까지 기록된 접촉 없음. native 완결 또는 기하학적 비적중 증명이 아님.',
                     incompleteUsesCountedAsMisses=False,regionDisambiguated=key!='outer-or-reinforced',
                     candidateStateMapping=key=='wall-stun',negativeOutcomesAreProvisional=True)
        if not ids and pending:child.update(status='unresolved-evidence',attemptCount=None,hitCount=None,hitRate=None)
        if key=='inner':estimate_metadata(child,ids,confirmed_inner)
        if key=='inner':exclusion_metadata(child,ids,pc)
        row['phaseMetrics'][key]=child
    pending_ticks={t for key in ('outer','reinforced') for t in row['phaseMetrics'][key].get('unresolvedCastTicks',[])}
    baseline_requests=[e for e in baseline_requests if e['start']['tick'] in pending_ticks]
    if baseline_requests:row['janReinforceBaselineRequests']=baseline_requests
    if baseline_proofs:row['janReinforceAbsenceEvidence']=baseline_proofs
    inner_pending=set(row['phaseMetrics']['inner'].get('unresolvedCastTicks',[]))
    rope_absence_requests=[r for r in rope_absence_requests if r['start']['tick'] in inner_pending]
    if rope_absence_requests:row['janRopeAbsenceRequests']=rope_absence_requests
    if rope_absence_contacts:
        row['janRopeAbsenceEvidence']=rope_absence_contacts
        row['targetCountsAreLowerBounds']=True
        row['completeTargetCountsAvailable']=False
        row['phaseMetrics']['inner']['targetCountsAreLowerBounds']=True
        row['phaseMetrics']['inner']['completeTargetCountsAvailable']=False
    return row
