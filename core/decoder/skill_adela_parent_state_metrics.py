"""Whole W/E1 uses include the initial summon and its triggered piece states.

Damage is assigned only when all compatible live handler states agree on one
root cast. Shared effects with a foreign or ambiguous root taint every possible
requested use. Missing terminal evidence is never counted as a miss.
"""
from collections import Counter,defaultdict
try:
    from .skill_parent_state_links import parent_state_links,PRIMARY
    from .skill_action_stage_evidence import load_exact_skill_ids
    from .skill_wire_order import command_order
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_attempt_timing import exact_outcome
    from .skill_ordered_match_end import ordered_winner_match_end
except ImportError:
    from skill_parent_state_links import parent_state_links,PRIMARY
    from skill_action_stage_evidence import load_exact_skill_ids
    from skill_wire_order import command_order
    from requested_skill_hit_rates import _result,_unavailable
    from skill_attempt_timing import exact_outcome
    from skill_ordered_match_end import ordered_winner_match_end

HANDLERS={
    1024210:('AdelaActive1PawnAttack',1024110),
    1024220:('AdelaActive1PawnAttackFromKnight',1024110),
    1024230:('AdelaActive1PawnAttackFromRook',1024110),
    1024240:('AdelaActive1QueenAttack',1024120),
    1024250:('AdelaActive1QueenAttackFromKnight',1024120),
    1024260:('AdelaActive1QueenAttackFromRook',1024120),
    1024310:('AdelaActive2KnightAttack',1024210),
    1024320:('AdelaActive2KnightAttackFromRook',1024210),
    1024420:('AdelaActive3RookAttack',1024310),
    1024430:('AdelaActive3RookAttackFromKing',1024310),
}
EFFECTS={1024110:'FX_BI_Adela_Skill01_PawnHit',1024120:'FX_BI_Adela_Skill01_QueenHit',
         1024210:'FX_BI_Adela_Skill02_KnightHit',1024310:'FX_BI_Adela_Skill03_RookHit'}
# These action1 handlers immediately execute their complete damage loop.
# Pushed pawn/queen and moving rook continue across frames and are excluded.
INSTANT={1024210,1024230,1024240,1024260,1024310,1024320,1024430}


def rook_terminal_variants(actions, own_objects, skill_id):
    """Decode the terminal's native isAirborne bit, without assigning a cast.

    AdelaActive3RookAttack emits actionNo = 2 + isAirborne. A terminal
    therefore distinguishes the two execution modes even when skillCode
    retains the original E context. It does not identify which overlapping
    coroutine emitted it, or prove whether the attack hit an enemy.
    """
    if skill_id is None:
        return []
    return [dict(sourceObjectId=a['sourceObjectId'], tick=a['tick'],
                 wireCategory=a['wireCategory'], wireOrder=a['wireOrder'],
                 actionNo=a['actionNo'], isAirborne=a['actionNo']==3,
                 stateGroup=1024420, skillIdCode=skill_id,
                 castAttributionEstablished=False)
            for a in actions
            if a.get('wireStatus')=='decoded-exact-CmdPlayStateSkillAction'
            and a.get('sourceObjectId') in own_objects
            and a.get('stateGroup')==1024420 and a.get('skillIdCode')==skill_id
            and a.get('actionNo') in (2,3) and command_order(a) is not None]


def adela_parent_state_metric(spec,starts,finishes,scripts,summons,terminals,actions,damages,
                              player,teams,intervals,skill_rows,state_groups,effect_rows,gaps,skill_ids=None,game_terminals=None,state_inventory=None):
    import json
    from pathlib import Path
    user_policy=json.loads((Path(__file__).resolve().parents[1]/'data/user-hit-rate-scope-20260912.json').read_text(encoding='utf-8'))['recordedUseMissPolicy'].get('enabled') is True
    selected=[s for s in starts if s.get('playerObjectId')==player and s.get('skillGroup')==spec['skillGroup']]
    diag=dict(observedCastCount=len(selected),observedCombatCastCount=sum(any(l<=s['tick']<r for l,r in intervals) for s in selected))
    def fail(why):return {**_unavailable(spec,why),**diag}
    if (spec.get('characterCode'),spec.get('mode'),spec.get('unit'))!=(24,'any','skill-cast') or spec['skillGroup'] not in PRIMARY:
        return fail('unsupported parent state metric')
    required={'CmdStartSkill','CmdFinishSkill','CmdStartStateSkill','CmdFinishStateSkill',
              'CmdPlayStateSkillAction','CmdSpawn','CmdDestroy','CmdDamage','SummonSnapshot:11'}
    user_policy=user_policy and gaps is not None and not any(g.get('count',0) for g in gaps)
    if scripts is None or gaps is None or any(g.get('packetName') in required and g.get('count',0) for g in gaps):
        return fail('complete parent/summon/state/damage evidence required')
    definitions={r['group']:r.get('skillId') for r in state_groups}
    if any(definitions.get(g)!=name for g,(name,_) in HANDLERS.items()):return fail('reviewed handler gameDb definitions differ')
    for code,name in EFFECTS.items():
        rows=[r for r in effect_rows if r.get('code')==code]
        if len(rows)!=1 or rows[0].get('effectPrefabName')!=name:return fail('reviewed effect gameDb identity differs')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    graph=parent_state_links(starts,finishes,scripts,summons,terminals,player,teams,skill_rows,state_groups,ids,execution_actions=actions,match_end=ordered_winner_match_end(game_terminals,gaps))
    if graph['status']=='unresolved-evidence':return fail(graph['reason'])
    parents=graph['parents'];states=graph['states'];unknown={i:set() for i in range(len(parents))}
    own_objects={s['objectId'] for s in summons if s.get('ownerObjectId')==player}
    diag['rookTerminalVariantEvidence']=rook_terminal_variants(actions,own_objects,ids.get('AdelaActive3RookAttack'))
    if any(i['event'].get('sourceObjectId') in own_objects for i in graph['issues']):return fail('owned state has invalid wire identity or lifetime')
    code_groups={r['code']:r['group'] for r in skill_rows}
    def possible(state,e):
        s,f=state['start'],state['finish'];left,at=command_order(s),command_order(e)
        if left is None:return e['tick']>=s['tick']
        if at is not None and at<left:return False
        if e['tick']<s['tick']:return False
        replacement=state.get('nextRecordedActivation')
        if replacement is not None and at is not None and at>=command_order(replacement):return False
        if f is None:return True
        right=command_order(f)
        return e['tick']<=f['tick'] and (at is None or right is None or at<=right)
    phase_actions={};boundaries={}
    actions_by_state=defaultdict(list)
    for action in actions:
        if action.get('wireStatus')=='decoded-exact-CmdPlayStateSkillAction' and action.get('actionNo')==1:
            actions_by_state[(action.get('sourceObjectId'),action.get('stateGroup'),
                             action.get('skillIdCode'),action.get('casterObjectId'))].append(action)
    for j,state in enumerate(states):
        if state['stateGroup'] not in INSTANT:continue
        aa=[a for a in actions_by_state[(state['sourceObjectId'],state['stateGroup'],
                state['start']['skillIdCode'],state['casterObjectId'])] if possible(state,a)]
        if state.get('nextRecordedActivation') is not None:
            aa=[a for a in aa if command_order(a) is not None and command_order(a)<command_order(state['nextRecordedActivation'])]
        expected=2 if state['stateGroup']==1024310 else 1
        if len(aa)!=expected or any(command_order(a) is None for a in aa):
            for i in state['parentIndices']:unknown[i].add('instant-piece-emission-actions-incomplete')
            continue
        phase_actions[j]=aa
        fx=HANDLERS[state['stateGroup']][1]
        for action in aa:boundaries.setdefault((fx,action['tick']),[]).append(command_order(action))
    for key in boundaries:
        boundaries[key].sort()
        if len(set(boundaries[key]))!=len(boundaries[key]):return fail('duplicate synchronous emission command identity')
    def damage_possible(j,state,d):
        if not possible(state,d):return False
        if state.get('executionEnd') is not None and command_order(d) is not None and command_order(d)>=command_order(state['executionEnd']):return False
        if j not in phase_actions:return True  # Missing marker cannot exclude this possible owner.
        at=command_order(d)
        aa=[a for a in phase_actions[j] if a['tick']==d['tick']]
        if at is None:return bool(aa)
        points=boundaries.get((d['effectCode'],d['tick']),[])
        for a in aa:
            left=command_order(a);later=[p for p in points if p>left]
            if left<at and (not later or at<later[0]):return True
        return False
    nonexecuted=set()
    for i,p in enumerate(parents):
        if not p['primaryStateIndices'] and p['finish'] is not None and (p['finish'].get('reason') in (set(range(1,15))|{16,17} if user_policy else {1,2,3,4,11}) or p.get('closureKind')=='actual-winner-match-end'):
            nonexecuted.add(i);continue
        if p['linkStatus']!='linked-primary':unknown[i].add('no-unique-normal-parent-primary-state')
        if p['finish'] is None:unknown[i].add('parent-not-completely-closed')
    for j,state in enumerate(states):
        roots=state['parentIndices'];s,f=state['start'],state['finish']
        if not roots and code_groups.get(s.get('skillCode')) in PRIMARY:
            # Could be an unobserved cascade from an earlier use. Do not
            # discard it and turn that use into a miss.
            return fail('inherited requested-skill state has no root cast')
        reason=None
        if state.get('reactivationProducerUnconfirmed'):reason='same-piece-reactivation-producer-not-confirmed'
        elif len(roots)>1:reason='state-compatible-with-multiple-root-casts'
        elif j not in phase_actions and state.get('executionEnd') is None and (not state['complete'] or f is None or f.get('reason') not in {0,1,2,3,4,11}):reason='piece-state-not-completely-closed'
        if reason:
            for i in roots:unknown[i].add(reason)
    for i,p in enumerate(parents):
        if p['linkStatus']!='linked-primary':continue
        primary=states[p['primaryStateIndices'][0]]
        aa=[a for a in actions if a.get('wireStatus')=='decoded-exact-CmdPlayStateSkillAction' and
            a.get('sourceObjectId')==primary['sourceObjectId'] and a.get('stateGroup')==primary['stateGroup'] and
            a.get('skillIdCode')==primary['start']['skillIdCode'] and a.get('casterObjectId')==primary['casterObjectId'] and possible(primary,a)]
        numbers=Counter(a.get('actionNo') for a in aa)
        if any(command_order(a) is None for a in aa):unknown[i].add('primary-phase-action-order-missing')
        if p['start']['skillGroup']==1024300 and numbers[1]!=2:unknown[i].add('both-knight-landings-not-recorded')
        if p['start']['skillGroup']==1024400 and numbers[2]+numbers[3]!=1:
            replaced=(primary.get('executionClosure')=='same-handler-coroutine-replaced' and numbers[1]==1)
            if not replaced:unknown[i].add('rook-arrival-not-recorded')
    contacts=[set() for _ in parents];packets=[0 for _ in parents];child_hits=[set() for _ in parents]
    phase_contacts=[{} for _ in parents];phase_starts=[{} for _ in parents];details=[[] for _ in parents]
    for i,p in enumerate(parents):
        for j in p['primaryStateIndices']:
            state=states[j]
            if p['start']['skillGroup']==1024300:
                for n,a in enumerate(sorted(phase_actions.get(j,[]),key=command_order)):
                    if n<2:phase_starts[i][('first-landing','second-landing')[n]]=a['tick']
            else:phase_starts[i]['rook-path']=state['start']['tick']
        children=[s for s in states if s['parentIndices']==[i] and s['linkStatus']!='primary-new-summon']
        if children:phase_starts[i]['triggered-pieces']=min(s['start']['tick'] for s in children)
        phase_contacts[i]={name:set() for name in phase_starts[i]}
    competing=[];exclusions=[];estimated_contacts=defaultdict(set)
    for d in damages:
        if d.get('attackerObjectId')!=player or d.get('effectCode') not in EFFECTS or d.get('targetObjectId') not in teams or teams[d['targetObjectId']]==teams[player]:continue
        compatible=[s for j,s in enumerate(states) if HANDLERS[s['stateGroup']][1]==d['effectCode'] and damage_possible(j,s,d)]
        if not compatible:return fail('owned piece effect outside every recorded handler lifetime')
        roots={i for s in compatible for i in s['parentIndices']}
        foreign=any(not s['parentIndices'] for s in compatible)
        if roots and (len(roots)!=1 or foreign):
            try:
                from .skill_adela_state_admission import excluded_pushed_pawn
            except ImportError:
                from skill_adela_state_admission import excluded_pushed_pawn
            evidence=excluded_pushed_pawn(d,player,state_inventory)
            if evidence and any(s['stateGroup']==1024220 for s in compatible):
                compatible=[s for s in compatible if s['stateGroup']!=1024220]
                if not compatible:return fail('mandatory pawn state contradicts all recorded damage producers')
                exclusions.append(evidence)
                roots={i for s in compatible for i in s['parentIndices']}
                foreign=any(not s['parentIndices'] for s in compatible)
        if not roots:continue  # Fully accounted for by another skill's handlers.
        if len(roots)!=1 or foreign:
            competing.append(dict(damage=d,playerObjectId=player,
                possibleHandlerGroups=sorted({s['stateGroup'] for s in compatible})))
            from .skill_lifecycle_result_policy import provisional_single_root_contact
            estimated=provisional_single_root_contact(roots,foreign)
            if estimated is not None and command_order(d) is not None and type(d.get('damageIsNull')) is bool:
                estimated_contacts[estimated].add((d['tick'],d['targetObjectId']))
            else:
                for i in roots:unknown[i].add('shared-piece-effect-has-competing-root')
            continue
        i=next(iter(roots))
        if command_order(d) is None or type(d.get('damageIsNull')) is not bool:
            unknown[i].add('damage-order-or-nullable-discriminator-missing');continue
        phases=set()
        for state in compatible:
            if state['linkStatus']!='primary-new-summon':phases.add('triggered-pieces')
            elif state['stateGroup']==1024420:phases.add('rook-path')
            else:
                j=next(j for j,s in enumerate(states) if s is state)
                aa=sorted(phase_actions.get(j,[]),key=command_order)
                preceding=[n for n,a in enumerate(aa) if a['tick']==d['tick'] and command_order(a)<command_order(d)]
                if preceding:phases.add(('first-landing','second-landing')[preceding[-1]])
        if len(phases)!=1 or next(iter(phases)) not in phase_contacts[i]:
            unknown[i].add('piece-damage-phase-is-ambiguous');continue
        phase=next(iter(phases));phase_contacts[i][phase].add((d['tick'],d['targetObjectId']))
        details[i].append(dict(phase=phase,hitTick=d['tick'],targetObjectId=d['targetObjectId'],effectCode=d['effectCode'],
                              pieceObjectIds=sorted({s['sourceObjectId'] for s in compatible}),wireOrder=d['wireOrder']))
        contacts[i].add((d['tick'],d['targetObjectId']));packets[i]+=1
        if all(s['linkStatus']!='primary-new-summon' for s in compatible):child_hits[i].add((d['tick'],d['targetObjectId']))
    combat=[i for i,p in enumerate(parents) if p['start']['skillGroup']==spec['skillGroup'] and any(l<=p['start']['tick']<r for l,r in intervals)]
    policy_parents={i for i in nonexecuted if user_policy and not any(g.get('count',0) for g in gaps)
        and not unknown[i] and not contacts[i] and not estimated_contacts[i]
        and command_order(parents[i]['start']) is not None and command_order(parents[i]['finish']) is not None
        and command_order(parents[i]['start'])<command_order(parents[i]['finish'])
        and parents[i]['start']['tick']<=parents[i]['finish']['tick']}
    valid=[i for i in combat if not unknown[i] and (i not in nonexecuted or i in policy_parents)]
    diag.update(unresolvedCombatCastCount=sum(bool(unknown[i]) for i in combat),unresolvedCastReasons=dict(Counter(x for i in combat for x in unknown[i])),
                unresolvedNonCombatCastCount=sum(bool(unknown[i]) for i,p in enumerate(parents) if p['start']['skillGroup']==spec['skillGroup'] and i not in combat),
                nonExecutedCastCount=sum(i in nonexecuted for i in combat),nonExecutedAllCastCount=sum(i in nonexecuted and p['start']['skillGroup']==spec['skillGroup'] for i,p in enumerate(parents)),
                parentUsesInferred=False,perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,
                triggeredPieceDamageIncluded=True,exactDamagePacketCount=sum(packets[i] for i in valid),
                distinctTriggeredPieceContacts=sum(len(child_hits[i]) for i in valid))
    diag.update(competingDamageEvidence=competing,stateAdmissionExclusions=exclusions)
    diag['unresolvedUseEvidence']=[dict(parent=parents[i],reasons=sorted(unknown[i]),states=[s for s in states if i in s['parentIndices']])
                                  for i,p in enumerate(parents) if p['start']['skillGroup']==spec['skillGroup'] and unknown[i]]
    if combat and not valid and diag['unresolvedCombatCastCount']:return fail('no complete unambiguous whole parent uses')
    r=_result(spec,[contacts[i]|estimated_contacts[i] for i in valid],'reviewed-Adela-parent-summon-inherited-state-lineage',cast_ticks=[parents[i]['start']['tick'] for i in valid])
    diag.update(userPolicyMissCastCount=len(policy_parents.intersection(combat)),
        userPolicyMissCastTicks=[parents[i]['start']['tick'] for i in valid if i in policy_parents],
        userPolicyMissAuthority='explicit-user-rule' if user_policy else None,
        userPolicyMissMeaning='Recorded parent use without phase execution; not native completion or geometric absence proof.')
    r.update(diag,evidenceReview='deliverables/adela-parent-state-producer-proof-v1.json',
             emissionReview='deliverables/adela-instant-piece-emission-proof-v1.json',fixedWindowUsed=False,
             contactDetailsByAttempt=[details[i] for i in valid],phaseMetrics={},
             executionEvidenceByAttempt=[dict(parent=parents[i],states=[s for s in states if s['parentIndices']==[i]]) for i in valid])
    names=('first-landing','second-landing','triggered-pieces') if spec['skillGroup']==1024300 else ('rook-path','triggered-pieces')
    for name in names:
        indices=[i for i in valid if name in phase_starts[i] or (user_policy and (name=='triggered-pieces' or i in policy_parents))]
        phase_ambiguous=[i for i in indices if name=='triggered-pieces' and estimated_contacts[i]]
        indices=[i for i in indices if i not in phase_ambiguous]
        phase=_result(spec,[phase_contacts[i].get(name,set()) for i in indices],'reviewed-Adela-exact-piece-phase',cast_ticks=[parents[i]['start']['tick'] for i in indices])
        phase.update(phase=name,outcomes=[exact_outcome(parents[i]['start']['tick'],phase_starts[i].get(name,parents[i]['start']['tick']),phase_contacts[i].get(name,set())) for i in indices],
                     denominatorMeaning=('Recorded eligible parent uses; absent phase is a user-policy miss' if user_policy else 'Parent uses with this actual recorded phase; no triggered piece is not a failed triggered-piece attempt'),
                     phaseExecutionObserved=[name in phase_starts[i] for i in indices],
                     actualPhaseExecutionCount=sum(name in phase_starts[i] for i in indices),
                     userPolicyMissCastCount=sum(name not in phase_starts[i] for i in indices),
                     userPolicyMissCastTicks=[parents[i]['start']['tick'] for i in indices if name not in phase_starts[i]],
                     phaseTimingProvenance=['recorded-phase-execution' if name in phase_starts[i] else 'user-policy-parent-start' for i in indices],
                     userPolicyMissAuthority='explicit-user-rule' if user_policy else None)
        if phase['userPolicyMissCastCount']:
            phase.update(verifiedCompletionCredit=False,fullRequestedMetricComplete=False)
        r['phaseMetrics'][name]=phase
        if phase_ambiguous:
            phase.update(estimatedParentContactNotAssignedToThisPhase=True,
                         completeTargetCountsAvailable=False,phaseExcludedAmbiguousCastCount=len(phase_ambiguous),
                         denominatorMeaning='Confirmed phase uses only; ambiguous cross-skill contacts are excluded, not phase misses')
    if policy_parents.intersection(valid):r.update(verifiedCompletionCredit=False,fullRequestedMetricComplete=False)
    estimated_valid=[i for i in valid if estimated_contacts[i]]
    if estimated_valid:
        from .skill_development_cancellation import annotate_provisional
        r=annotate_provisional(r,'동일 시전자·효과·실제 상태 수명에 맞는 요청 시전이 하나면 잠정 적중으로 집계. 다른 스킬의 동시 효과일 수 있어 과대 집계 가능.')
        r.update(estimatedContactCastCount=len(estimated_valid),
            developmentLabel='개발 중 · 귀속 추정 적중 포함',
            estimatedOnlyHitCastCount=sum(not contacts[i] for i in estimated_valid),
            confirmedHitCastCount=sum(bool(contacts[i]) for i in valid),
            estimatedContactsByCast=[dict(startTick=parents[i]['start']['tick'],contacts=sorted(estimated_contacts[i])) for i in estimated_valid],
            targetCountsAreLowerBounds=False,completeTargetCountsAvailable=False,
            exactDamagePacketCount=sum(packets[i] for i in valid),attributionEstimateMayOvercount=True)
        r['confirmedContactDetailsByAttempt']=r['contactDetailsByAttempt']
        r['contactDetailsByAttempt']=[details[i]+[dict(hitTick=t,targetObjectId=target,
            attribution='estimated-single-requested-root-with-foreign-competitor',phase='unassigned-estimated')
            for t,target in sorted(estimated_contacts[i])] for i in valid]
    return r
