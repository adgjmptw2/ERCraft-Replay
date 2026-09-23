"""Expose delayed primary execution evidence without fabricating cancellation misses."""
from copy import deepcopy
try:
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_echion_black_mamba_metrics import _unavailable
    from .corpus_echion_primary_inputs import GROUPS,PRIMARY_EFFECTS,SECONDARY_STATE_CODES
    from .skill_server_frame_time import frame_time_preimage,FRAME_STEP,_f32
    from .skill_wire_position_precision import position_cell as fixed_point_cell
    from .requested_skill_hit_rates import _result
except ImportError:
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_echion_black_mamba_metrics import _unavailable
    from corpus_echion_primary_inputs import GROUPS,PRIMARY_EFFECTS,SECONDARY_STATE_CODES
    from skill_server_frame_time import frame_time_preimage,FRAME_STEP,_f32
    from skill_wire_position_precision import position_cell as fixed_point_cell
    from requested_skill_hit_rates import _result


def publish_classified_primary(result, inputs):
    """Expose proved uses while preserving incomplete whole-match coverage."""
    if result.get('skillGroup') not in (1044530,1044540) or result.get('unit')!='skill-cast':return result
    evidence=result.get('primaryExecutionEvidence')
    if not evidence:return result
    combat=[u for u in evidence['uses'] if u['inCombat']]
    known=[u for u in combat if u['status'] in ('hit','miss')]
    unknown=[u for u in combat if u['status']=='unknown']
    result.update(verifiedCombatCastCount=len(known),unresolvedCombatCastCount=len(unknown),
        perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,
        wholeCombatHitRate=None,hitRateScope='classified-primary-casts-only',
        outcomeScope='Verified primary uses only; unknown uses excluded, not misses. Target counts include confirmed contacts only.',
        unresolvedUseEvidence=[dict(castWireOrder=u['castWireOrder'],castTick=u['castTick']) for u in unknown],
        fullRequestedMetricComplete=False)
    if not known:return result
    damages={d['event']['packetId']:d['event'] for d in inputs['damages']}
    contacts=[{(damages[p]['tick'],damages[p]['objectId']) for p in u['contactPacketIds']} for u in known]
    if any(bool(c)!=(u['status']=='hit') for u,c in zip(known,contacts)):
        raise ValueError('classified primary outcomes disagree with positive damage evidence')
    spec={k:v for k,v in result.items() if k in ('metricId','characterCode','skillGroup','label','mode','unit','numerator','targetCohort','reportMultiTarget','scopeTier')}
    calculated=_result(spec,contacts,'native-Echion-primary-classified-uses',cast_ticks=[u['castTick'] for u in known])
    calculated['outcomes']=[[u['castTick'],int(u['status']=='hit'),u['launchTick'],u['firstEnemyHitTick']] for u in known]
    result.update(calculated,targetCountsAreLowerBounds=True,
        wholeCombatHitRate=calculated['hitRate'] if not unknown and not evidence.get('unassignedPrimaryEventCount') else None)
    return result


def _primary_closure(parent,action,inputs,*,stage='primary'):
    if stage not in ('primary','secondary'):raise ValueError('unknown Echion stage')
    finish=parent['finish']
    if stage=='primary' and parent['start']['skillGroup']!=1044530 and finish is not None and finish['reason']==0:
        return dict(method='awaited-normal-parent-finish',wireOrder=finish['wireOrder'])
    if action['status']!='bounded':return None
    deadline=action['upper']+(27 if stage=='primary' else 78)
    for witness in inputs.get('laterFrameWitnesses',[]):
        if (witness['wireOrder'][0]>action['event']['wireOrder'][0]
                and witness['lower']>deadline):
            return dict(method=f'native-{stage}-frame-frontier',wireOrder=witness['wireOrder'],
                        latestPrimaryUpdateSeq=deadline,laterFrameWitness=witness,
                        nativeRulePath='schema/echion-primary-frame-frontier-v1.json')
    return None


def _congestion_excludes_player_contact(parent,action,actor,inputs,*,stage='primary'):
    """A completed primary contact would emit an uncapped +1s self buff update.

    This is a necessary-signal exclusion, not nearest-event attribution. Other
    positive updates can only make the test inconclusive, never create a miss.
    A native frame frontier may bound an interrupted parent's independent
    primary child. It never substitutes for the contact/state evidence below.
    """
    if stage=='secondary' and (parent['start']['skillGroup']!=1044520
            or inputs.get('secondaryStateContractVerified') is not True):return None
    closure=_primary_closure(parent,action,inputs,stage=stage)
    if (closure is None or action['status']!='bounded'
            or not inputs.get('congestionContractVerified')):return None
    launch_order=action['event']['wireOrder'];end=closure['wireOrder']
    life=next((r for r in inputs.get('launchLifeEvidence',[]) if r['targetObjectId']==actor
               and r['wireOrder']==launch_order),None)
    if (not life or life['status']!='recorded-state-inventory' or life['baselineIsAlive'] is not True
            or life['baselineIsDyingCondition'] is not False or life['lifeEventsSinceBaseline'] or life['issues']):return None
    events=[]
    for e in inputs.get('selfStateEvents',[]):
        if e['wireOrder']>end:break
        if e['name']=='barrier':
            affected=e.get('objectIds')
            if affected is None or actor in affected:events.append(e)
            continue
        if e['value'].get('objectId')!=actor:continue
        if (e['name'] in ('CmdDead','CmdDyingCondition','CmdDyingBlockBeforeDead')
                and e['wireOrder'][0]>life['baselineRecord']):return None
        if e.get('stateGroup')==1044500 and (e['value'].get('casterId') or actor)==actor:events.append(e)
    adds=[i for i,e in enumerate(events) if e['name'] in ('CmdAddState','CmdAddStateExtended')
          and e['wireOrder']<launch_order]
    if not adds:return None
    epoch=events[adds[-1]:]
    if any(e['name']!='CmdUpdateState' for e in epoch[1:]):return None
    before=[i for i,e in enumerate(epoch) if e['name']=='CmdUpdateState' and e['wireOrder']<launch_order]
    initial=epoch[0] if not before else None
    updates=epoch[before[-1]:] if before else epoch[1:]
    try:
        if initial is not None:
            if initial['name']=='CmdAddStateExtended' and initial['value'].get('stackCount')!=1:return None
            cells=[fixed_point_cell(initial['value']['duration'])]
            created=None
            # CommonState.Start precedes CmdAddState, which precedes this
            # launch. The action's native upper bound is therefore also an
            # upper bound on the initial creation frame, not an exact time.
            created_hi=_f32(_f32(action['upper'])*FRAME_STEP)
            baseline=dict(method='recorded-state-add-duration',packetId=initial['packetId'],
                          createdFrameUpper=action['upper'],
                          nativeRulePath='schema/state-add-duration-initial-value-v1.json')
        else:
            created=updates[0]['value']['createdTime']['internalValue']
            frames=frame_time_preimage(created)
            if frames is None:return None
            created_hi=_f32(_f32(frames[1])*FRAME_STEP)
            cells=[]
            baseline=dict(method='recorded-prelaunch-state-update',packetId=updates[0]['packetId'])
        for e in updates:
            v=e['value']
            if created is None:
                created=v['createdTime']['internalValue']
                frames=frame_time_preimage(created)
                if frames is None or frames[1]>action['upper']:return None
            if v['stackCount']!=1 or v['createdTime']['internalValue']!=created:return None
            cell=fixed_point_cell(v['duration']['internalValue'])
            cells.append(cell)
        if any(not 0<c[0]<=c[1]<3600 for c in cells):return None
        # GetRemainTime = f32(f32(created + duration) - serverFrameTime).
        # Endpoint arithmetic is monotone, so this includes all wire preimages.
        earliest=_f32(_f32(action['lower']+(27 if stage=='primary' else 78))*FRAME_STEP)
        remaining_hi=_f32(_f32(created_hi+max(c[1] for c in cells))-earliest)
        # Positive headroom guarantees that even a capped contact would emit
        # an update. A completely saturated state could suppress it entirely.
        if remaining_hi>=9.0:return None
        transition_evidence=[]
        for prior,current in zip(cells,cells[1:]):
            if current[1]<prior[0]:return None
            high_lo,high_hi=_f32(prior[0]+1.0),_f32(prior[1]+1.0)
            # Every ModifyStateValue emits its own complete state update.
            # A distinct +5s change cannot be the <=1s primary-contact write.
            if current[0]>high_hi:
                transition_evidence.append('larger-than-maximum-primary-increment')
                continue
            prior_remaining_hi=_f32(_f32(created_hi+prior[1])-earliest)
            if _f32(prior_remaining_hi+1.0)>9.0:return None
            if current[1]>=high_lo and current[0]<=high_hi:return None
            transition_evidence.append('uncapped-one-second-transition-excluded')
    except (KeyError,TypeError,ValueError,OverflowError):return None
    return dict(method=f'closed-{stage}-required-uncapped-congestion-update-absent',
                nativeRulePath=('schema/echion-congestion-required-signal-v3.json' if stage=='primary'
                                else 'schema/echion-secondary-required-signal-v1.json'),
                aliveBaselineRecord=life['baselineRecord'],aliveBaselinePayloadSha256=life['baselinePayloadSha256'],
                stateAddPacketId=epoch[0]['packetId'],stateUpdatePacketIds=[e['packetId'] for e in updates],
                initialStateEvidence=baseline,
                launchWireOrder=launch_order,closure=closure,
                remainingTimeUpper=remaining_hi,requiredPlayerContactIncrementSeconds=1.0,
                transitionExclusions=transition_evidence,
                highGradeUpdatePossible=False,damageAbsenceUsed=False)


def _damage_candidates(launches,damage):
    event=damage['event'];possible=[]
    for launch in launches:
        parent=launch['parent'];a=launch['clock'];g=parent['start']['skillGroup']
        if a['event']['wireOrder']>=event['wireOrder']:continue
        finish=parent['finish']
        for stage,offset in [('primary',27)]+([('secondary',78)] if g==1044520 else []):
            if (stage=='primary' and g!=1044530 and finish is not None and finish['reason']==0
                    and finish['wireOrder']<event['wireOrder']):continue
            if (a['status']=='bounded' and damage['status']=='bounded'
                    and max(a['lower']+offset,damage['lower'])>min(a['upper']+offset,damage['upper'])):continue
            possible.append((launch,stage))
    return possible


def _state_candidates(launches,state):
    possible=[]
    for launch in launches:
        a=launch['clock']
        if launch['parent']['start']['skillGroup']!=1044520 or a['event']['wireOrder']>=state['event']['wireOrder']:continue
        if (a['status']=='bounded' and state['status']=='bounded'
                and max(a['lower']+78,state['lower'])>min(a['upper']+78,state['upper'])):continue
        possible.append((launch,'secondary'))
    return possible


def _propagate_execution_clocks(launches,inputs,actor):
    """Unique producer evidence constrains its shared synchronous execution frame.

    NPC contacts also constrain time; they never become enemy hit evidence.
    Every narrowing is anchored to an original event bound and an exclusive
    candidate at that iteration. Ambiguous events alone cannot bootstrap it.
    """
    evidence=[];anchored={}
    events=[('damage',d) for d in inputs['damages'] if d['event']['attackerId']==actor
            and d['event']['effectCode'] in PRIMARY_EFFECTS]
    if inputs.get('secondaryStateContractVerified'):
        events += [('secondary-state',s) for s in inputs.get('secondaryStates',[])
                   if s['event'].get('casterId')==actor and s['event'].get('code') in SECONDARY_STATE_CODES
                   and s['event'].get('wireCategory')=='commands']
    if any(d['status']=='clock-conflict' for _,d in events):return None
    while True:
        changed=False;proposals=[]
        for kind,d in events:
            if d['status']!='bounded':continue
            possible=(_damage_candidates if kind=='damage' else _state_candidates)(launches,d)
            key=(kind,d['event']['packetId'])
            if key in anchored and (len(possible)!=1 or possible[0]!=anchored[key]):return None
            if len(possible)!=1:continue
            launch,stage=possible[0];a=launch['clock']
            if a['status']!='bounded':continue
            anchored[key]=(launch,stage)
            proposals.append((kind,d,launch,stage))
        # Collect constraints before applying any: two exclusive but
        # inconsistent packets must conflict, not become order-dependent
        # orphans after the first packet narrows the frame.
        for kind,d,launch,stage in proposals:
            a=launch['clock']
            offset=27 if stage=='primary' else 78
            lower=max(a['lower'],d['lower']-offset);upper=min(a['upper'],d['upper']-offset)
            if lower>upper:return None
            if lower==a['lower'] and upper==a['upper']:continue
            evidence.append(dict(launchWireOrder=a['event']['wireOrder'],packetId=d['event']['packetId'],
                packetWireOrder=d['event']['wireOrder'],kind=kind,stage=stage,offset=offset,
                previousLower=a['lower'],previousUpper=a['upper'],lower=lower,upper=upper,
                eventLower=d['lower'],eventUpper=d['upper']))
            a.update(lower=lower,upper=upper);changed=True
        if not changed:return evidence


def echion_primary_execution(spec,player,intervals,inputs):
    result=_unavailable(spec,'primary execution evidence is partial; unresolved outcomes and complete variant/secondary scope remain')
    if not inputs:
        result['reason']='retained native-clock execution inputs unavailable'
        return result
    actor=inputs['players'].get(str(player));group=spec['skillGroup']
    if actor is None or group not in GROUPS:return result
    starts=[s for s in inputs['starts'] if s['objectId']==actor]
    parents,reason=ordered_cast_records(starts,inputs['finishes'],actor,allow_same_tick_finishes=True)
    if reason:
        result['reason']=reason
        return result
    launches=[]
    for action in inputs['actions']:
        event=action['event']
        if event['objectId']!=actor:continue
        candidates=[p for p in parents if p['start']['skillIdCode']==event['skillId']
                    and p['start']['wireOrder']<=event['wireOrder']
                    and (p['finish'] is None or event['wireOrder']<=p['finish']['wireOrder'])]
        if len(candidates)!=1:
            result['reason']='R launch does not have one exact command-ordered parent'
            return result
        launches.append(dict(parent=candidates[0],clock=deepcopy(action),contacts=[],ambiguousContacts=[],
                             secondaryContacts=[],ambiguousSecondaryContacts=[],
                             secondaryStates=[],ambiguousSecondaryStates=[]))
    propagation=_propagate_execution_clocks(launches,inputs,actor)
    if propagation is None or any(l['clock']['status']=='clock-conflict' for l in launches):
        result['reason']='contradictory native clock inputs'
        return result
    orphan=[]
    for damage in inputs['damages']:
        event=damage['event']
        if event['attackerId']!=actor or event['effectCode'] not in PRIMARY_EFFECTS:continue
        possible=_damage_candidates(launches,damage)
        if not possible:orphan.append(event['packetId'])
        for launch,stage in possible:
            keys=('contacts','ambiguousContacts') if stage=='primary' else ('secondaryContacts','ambiguousSecondaryContacts')
            launch[keys[0] if len(possible)==1 else keys[1]].append(event)
    unassigned_states=[]
    if inputs.get('secondaryStateContractVerified'):
        for state in inputs.get('secondaryStates',[]):
            event=state['event']
            if (event.get('casterId')!=actor or event.get('code') not in SECONDARY_STATE_CODES
                    or event.get('wireCategory')!='commands'):continue
            possible=[(l,l['clock']['status']=='bounded' and state['status']=='bounded')
                      for l,_ in _state_candidates(launches,state)]
            if not possible:unassigned_states.append(event['packetId'])
            for launch,bounded in possible:
                key='secondaryStates' if len(possible)==1 and bounded else 'ambiguousSecondaryStates'
                launch[key].append(event)
    teams=inputs['teams'];actor_team=teams.get(str(actor))
    if actor_team is None:
        result['reason']='source player team missing'
        return result
    def enemy(d):return str(d['objectId']) in teams and teams[str(d['objectId'])]!=actor_team
    uses=[]
    for launch in launches:
        start=launch['parent']['start']
        if start['skillGroup']!=group:continue
        finish=launch['parent']['finish'];hits=[d for d in launch['contacts'] if enemy(d)]
        uncertain=[d for d in launch['ambiguousContacts'] if enemy(d)]
        closed=group!=1044530 and finish is not None and finish['reason']==0
        closure=_primary_closure(launch['parent'],launch['clock'],inputs)
        exclusion=_congestion_excludes_player_contact(launch['parent'],launch['clock'],actor,inputs)
        # Recorded positive or ambiguous enemy damage must never be overridden
        # by an exclusion. Disagreement remains visible and unresolved.
        conflict=exclusion is not None and bool(hits or uncertain)
        # An orphan has NO compatible launch after the same native-clock and
        # ordered-lifecycle checks used above. It is unresolved evidence for
        # the source, not a reason to erase independent per-cast evidence.
        # Compatible packets (including unbounded ones) remain attached to
        # their candidates and still participate in conflict/ambiguity checks.
        status=('unknown' if conflict else 'hit' if hits else
                'miss' if exclusion is not None else 'unknown')
        uses.append(dict(castWireOrder=start['wireOrder'],castTick=start['tick'],
            launchWireOrder=launch['clock']['event']['wireOrder'],launchTick=launch['clock']['event']['tick'],
            inCombat=any(left<=start['tick']<right for left,right in intervals),status=status,
            launchClock={k:launch['clock'].get(k) for k in ('status','lower','upper')},
            launchClockWitnessPacketIds=[(launch['clock'].get(k) or {}).get('packetId') for k in ('lowerWitness','upperWitness')],
            primaryClosedByAwaitedNormalFinish=closed,
            primaryClosureEvidence=closure,
            primaryMissEvidence=exclusion,primaryEvidenceConflict=conflict,
            closedWithoutEnemyDamage=closed and not hits and not uncertain,
            contactPacketIds=[d['packetId'] for d in hits],ambiguousContactPacketIds=[d['packetId'] for d in uncertain],
            minimumDistinctEnemyTargets=len({d['objectId'] for d in hits}),
            firstEnemyHitTick=min((d['tick'] for d in hits),default=None)))
    combat=[x for x in uses if x['inCombat']]
    result['primaryExecutionEvidence']=dict(status='partial-native-primary-execution',
        clockPropagationEvidence=propagation,
        sourceMatchKey=inputs['matchKey'],sourceParserSha256=inputs['parserSha256'],
        recordedLaunchCount=len(uses),combatLaunchCount=len(combat),
        knownCombatHitCastCount=sum(x['status']=='hit' for x in combat),
        knownCombatMissCastCount=sum(x['status']=='miss' for x in combat),
        closedCombatWithoutEnemyDamageCount=sum(x['closedWithoutEnemyDamage'] for x in combat),
        unresolvedCombatCastCount=sum(x['status']=='unknown' for x in combat),
        unassignedPrimaryPacketIds=orphan,uses=uses,
        unassignedPrimaryEventCount=len(orphan),
        unassignedEventPolicy='Retain source-level unassigned events separately; never include them as inferred attempts or erase disjoint per-cast evidence.',
        fullRequestedMetricComplete=False,hitRate=None,
        denominatorPolicy='Positive native-linked enemy damage establishes hit evidence. A miss requires awaited closure or a strict native frame frontier plus an uncapped mandatory self-state signal exclusion; damage absence and cancellation alone never establish miss.')
    if group==1044520:
        secondary=[]
        for launch in launches:
            start=launch['parent']['start']
            if start['skillGroup']!=group:continue
            hits=[d for d in launch['secondaryContacts'] if enemy(d)]
            uncertain=[d for d in launch['ambiguousSecondaryContacts'] if enemy(d)]
            state_hits=[d for d in launch['secondaryStates'] if enemy(d)]
            state_uncertain=[d for d in launch['ambiguousSecondaryStates'] if enemy(d)]
            exclusion=_congestion_excludes_player_contact(launch['parent'],launch['clock'],actor,inputs,stage='secondary')
            conflict=exclusion is not None and bool(hits or uncertain or state_hits or state_uncertain)
            secondary.append(dict(castWireOrder=start['wireOrder'],castTick=start['tick'],
                launchWireOrder=launch['clock']['event']['wireOrder'],
                inCombat=any(left<=start['tick']<right for left,right in intervals),
                status='unknown' if conflict else 'hit' if hits or state_hits else 'unknown',nativeFrameOffsetFromLaunch=78,
                contactOutcome=('unknown' if conflict else 'hit' if hits or state_hits else
                                'no-enemy-contact' if exclusion is not None else 'unknown'),
                noEnemyContactEvidence=exclusion,secondaryEvidenceConflict=conflict,
                callbackExecutionStatus='proved-by-contact' if (hits or state_hits) and not conflict else 'unproven',
                contactPacketIds=[d['packetId'] for d in hits],
                ambiguousContactPacketIds=[d['packetId'] for d in uncertain],
                stateContactPacketIds=[d['packetId'] for d in state_hits],
                ambiguousStateContactPacketIds=[d['packetId'] for d in state_uncertain],
                minimumDistinctEnemyTargets=len({d['objectId'] for d in hits+state_hits}),
                firstEnemyHitTick=min((d['tick'] for d in hits+state_hits),default=None)))
        combat=[u for u in secondary if u['inCombat']]
        result['secondaryExecutionEvidence']=dict(status='partial-native-secondary-execution',
            sourceMatchKey=inputs['matchKey'],sourceParserSha256=inputs['parserSha256'],
            nativeRulePath='schema/echion-secondary-execution-v1.json',
            stateRulePath='schema/echion-secondary-state-v1.json',
            unassignedStatePacketIds=unassigned_states,
            recordedParentLaunchCount=len(secondary),combatParentLaunchCount=len(combat),
            knownCombatHitCastCount=sum(u['status']=='hit' for u in combat),
            knownCombatMissCastCount=0,unresolvedCombatCastCount=sum(u['status']=='unknown' for u in combat),
            knownCombatNoEnemyContactCastCount=sum(u['contactOutcome']=='no-enemy-contact' for u in combat),
            unresolvedCombatContactOutcomeCount=sum(u['contactOutcome']=='unknown' for u in combat),
            parentCastOutcomePolicy='A proved absence of enemy contact is a parent-cast outcome; it does not establish a fired secondary attempt or enter an actual-callback hit-rate denominator.',
            uses=secondary,hitRate=None,fullRequestedMetricComplete=False,
            denominatorPolicy='Recorded parent launches are candidates, not proof that a deferred callback executed. Exact linked damage or secondary Airborne application proves contact. Missing damage/state or parent finish never creates a secondary miss.')
        result['secondaryParentCastEvidence']=dict(
            unit='skill-cast',denominator='recorded-BlackMamba-parent-launches-in-combat',
            castCount=len(combat),hitCastCount=sum(u['contactOutcome']=='hit' for u in combat),
            noEnemyContactCastCount=sum(u['contactOutcome']=='no-enemy-contact' for u in combat),
            unresolvedCastCount=sum(u['contactOutcome']=='unknown' for u in combat),
            hitRate=None,fullRequestedMetricComplete=False,actualSecondaryExecutionCount=None,
            uses=[dict(castWireOrder=u['castWireOrder'],castTick=u['castTick'],contactOutcome=u['contactOutcome'],
                       firstEnemyHitTick=u['firstEnemyHitTick'],inCombat=u['inCombat']) for u in secondary],
            interpretation='Secondary enemy contact per recorded R cast. Proved no-contact outcomes include possible cancellation; actual callback executions are not fabricated.')
    return publish_classified_primary(result,inputs)
