"""Project verified per-attempt timings into supplied exact player episodes.

This never allocates an aggregate rate across episodes or guesses missing
timing. Callers must provide episodes using the same object IDs as the input
cache; this module does not infer identity links to another analysis output.
"""
from bisect import bisect_right


def _attempt_episode(row,ordered,starts,cast):
    index=bisect_right(starts,cast)-1
    if index>=0 and cast<ordered[index]['endTick']:return index
    matches=[u for u in row.get('openingEpisodeAssignments',[]) if u.get('castTick')==cast]
    ids={u.get('episodeId') for u in matches}
    if len(ids)!=1:raise ValueError('attempt cannot be assigned to a supplied episode')
    index=next((i for i,e in enumerate(ordered) if e['episodeId'] in ids),None)
    if index is None or any(not (cast<ordered[index]['startTick']<=u.get('firstHitTick',-1)<ordered[index]['endTick']) for u in matches):
        raise ValueError('opening assignment lacks exact causal hit in supplied episode')
    return index


def project_player_episodes(observations, episodes):
    result=_project_direct_player_episodes(observations,episodes)
    for row in observations:
        phases=row.get('phaseMetrics')
        if phases is None:continue
        if not isinstance(phases,dict):raise ValueError('phase metrics must be a named mapping')
        identity={k:row[k] for k in ('metricId','skillGroup','mode','unit','targetCohort','openingEpisodeAssignments') if k in row}
        parent_coverage=next(c for c in result['coverage'] if c.get('metricId')==row.get('metricId'))
        for name,phase in phases.items():
            if not isinstance(name,str) or not isinstance(phase,dict):raise ValueError('invalid named phase metric')
            child={**identity,**phase}
            projected=project_player_episodes([child],episodes)
            parent_coverage.setdefault('phaseCoverage',{})[name]=projected['coverage'][0]
            for episode,local in zip(result['episodes'],projected['episodes']):
                if not local['metrics']:continue
                parent=next((m for m in episode['metrics'] if m.get('metricId')==row.get('metricId')),None)
                if parent is None:
                    # A known child may survive an unknown parent or missing
                    # parent timing. Do not invent an episode-level parent rate.
                    parent={**identity,'status':parent_coverage['status'],
                        'attemptCount':None,'hitCount':None,'hitRate':None,'fallbackUsed':False,
                        'reason':parent_coverage.get('reason'),
                        **{k:row[k] for k in ('calculationConfidence','developmentLabel',
                            'verifiedCompletionCredit','fullRequestedMetricComplete','hitRateScope') if k in row}}
                    episode['metrics'].append(parent)
                parent.setdefault('phaseMetrics',{})[name]=local['metrics'][0]
    return result


def _project_direct_player_episodes(observations, episodes):
    ordered=sorted(episodes,key=lambda e:e['startTick'])
    seen=set()
    for index,episode in enumerate(ordered):
        left,right=episode['startTick'],episode['endTick']
        if (type(left) is not int or type(right) is not int or left>=right or
                episode['episodeId'] in seen or
                (index and ordered[index-1]['endTick']>left)):
            raise ValueError('episodes require unique IDs and disjoint half-open tick ranges')
        seen.add(episode['episodeId'])
    output=[{**e,'metrics':[]} for e in ordered]
    starts=[e['startTick'] for e in ordered]
    coverage=[]
    for row in observations:
        identity={k:row[k] for k in ('metricId','skillGroup','mode','unit','targetCohort') if k in row}
        confirmed=row.get('confirmedCastHitEvidence')
        if confirmed is not None:
            uses=confirmed['outcomes']
            if (confirmed.get('aggregateRateEligible') is not False or confirmed.get('hitRate') is not None
                    or len(uses)!=confirmed['confirmedHitCastCount']
                    or len({tuple(u['castWireOrder']) for u in uses})!=len(uses)):
                raise ValueError('confirmed contacts cannot supply a partial rate or duplicate uses')
            partitions=[[] for _ in ordered]
            for use in uses:
                tick=use.get('castTick');hit=use.get('firstConfirmedEnemyContactTick')
                if type(tick) is not int or type(hit) is not int or hit<tick:
                    raise ValueError('confirmed contact requires exact causal cast/contact ticks')
                index=_attempt_episode(row,ordered,starts,tick)
                partitions[index].append(use)
            for episode,uses in zip(output,partitions):
                if uses:
                    episode.setdefault('confirmedCastHitEvidence',[]).append(dict(
                        **identity,confirmedHitCastCount=len(uses),hitRate=None,aggregateRateEligible=False,
                        completeTargetCountsAvailable=False,outcomes=uses))
        parent=row.get('secondaryParentCastEvidence')
        if parent is not None:
            # This is a separate contact-per-parent-cast result. It must also
            # survive an unresolved main metric; never add it to fired shots.
            partitions=[[] for _ in ordered]
            for use in parent['uses']:
                if type(use.get('castTick'))is not int or use.get('contactOutcome') not in ('hit','no-enemy-contact','unknown'):
                    raise ValueError('invalid secondary parent contact outcome')
                if not use['inCombat']:continue
                index=_attempt_episode(row,ordered,starts,use['castTick'])
                partitions[index].append(use)
            if sum(map(len,partitions))!=parent['castCount']:
                raise ValueError('secondary parent cast count disagrees with timings')
            for episode,uses in zip(output,partitions):
                if uses:
                    episode.setdefault('secondaryParentCastEvidence',[]).append(dict(
                        **{**identity,'unit':'skill-cast'},denominator=parent['denominator'],
                        castCount=len(uses),hitCastCount=sum(u['contactOutcome']=='hit' for u in uses),
                        noEnemyContactCastCount=sum(u['contactOutcome']=='no-enemy-contact' for u in uses),
                        unresolvedCastCount=sum(u['contactOutcome']=='unknown' for u in uses),
                        actualSecondaryExecutionCount=None,hitRate=None,fullRequestedMetricComplete=False,uses=uses))
        if row.get('status') not in {'calculable-observed','calculable-experimental'}:
            coverage.append({**identity,'status':row.get('status','unresolved-evidence'),
                             'reason':row.get('reason')})
            continue
        outcomes=row.get('outcomes')
        if outcomes is None:
            coverage.append({**identity,'status':'timeline-unavailable',
                             'reason':'aggregate metric has no exact attempt timing'})
            continue
        if not isinstance(outcomes,list) or len(outcomes)!=row['attemptCount']:
            raise ValueError('outcome count disagrees with metric denominator')
        if any(not isinstance(o,list) or len(o)!=4 or type(o[0]) is not int or
               type(o[1]) is not int or o[1] not in {0,1} or type(o[2]) is not int or
               o[2]<o[0] or (o[1]==1 and (type(o[3]) is not int or o[3]<o[2])) or
               (o[1]==0 and o[3] is not None) for o in outcomes):
            raise ValueError('invalid exact attempt timing')
        if sum(o[1] for o in outcomes)!=row['hitCount']:
            raise ValueError('outcomes disagree with hit count')
        policies=row.get('policySuccessByAttempt')
        if policies is not None and (not isinstance(policies,list) or len(policies)!=len(outcomes)
                or any(p is not None and (not isinstance(p,dict) or not p.get('policy')
                    or type(p.get('tick')) is not int or p['tick']<o[2] or not o[1])
                    for p,o in zip(policies,outcomes))):
            raise ValueError('policy success evidence disagrees with attempts')
        policy_partition=[[] for _ in ordered]
        tips=row.get('tipEvidenceByAttempt')
        if tips is not None and (not isinstance(tips,list) or len(tips)!=len(outcomes)
                or any(not isinstance(t,dict) or type(t.get('confirmedTip')) is not bool
                    or type(t.get('regionUnresolved')) is not bool
                    or (t['confirmedTip'] or t['regionUnresolved']) and not o[1]
                    for t,o in zip(tips,outcomes))):
            raise ValueError('tip evidence disagrees with overall attempts')
        tip_partition=[[] for _ in ordered]
        neutral=row.get('targetCohort') in {'ally','either-team'}
        counts_key='distinctTargetsPerAttempt' if neutral else 'distinctEnemyTargetsPerAttempt'
        ticks_key='distinctTargetFirstHitTicksPerAttempt' if neutral else 'distinctEnemyTargetFirstHitTicksPerAttempt'
        sum_key='distinctTargetsSummedAcrossAttempts' if neutral else 'distinctEnemyTargetsSummedAcrossAttempts'
        mean_key='meanDistinctTargetsPerAttempt' if neutral else 'meanDistinctEnemyTargetsPerAttempt'
        if neutral and any(row.get(k) is not None for k in ['distinctEnemyTargetsPerAttempt',
                'distinctEnemyTargetFirstHitTicksPerAttempt','distinctEnemyTargetsSummedAcrossAttempts']):
            raise ValueError('team cohort cannot publish enemy-labelled target counts')
        targets=row.get(counts_key)
        target_ticks=row.get(ticks_key)
        if targets is not None:
            if (not isinstance(targets,list) or len(targets)!=len(outcomes) or
                    any(type(n) is not int or n<0 or bool(n)!=bool(o[1])
                        for n,o in zip(targets,outcomes))):
                raise ValueError('per-attempt enemy counts disagree with exact outcomes')
            if (sum(targets)!=row.get(sum_key) or
                    sum(n>=2 for n in targets)!=row.get('multiTargetAttemptCount')):
                raise ValueError('per-attempt enemy counts disagree with aggregate counts')
        if target_ticks is not None:
            if (targets is None or not isinstance(target_ticks,list) or len(target_ticks)!=len(outcomes) or
                    any(not isinstance(ts,list) or len(ts)!=n or
                        any(type(t) is not int or t<o[2] for t in ts) or
                        ts!=sorted(ts) or (ts[0] if ts else None)!=o[3]
                        for ts,n,o in zip(target_ticks,targets,outcomes))):
                raise ValueError('per-enemy first contacts disagree with exact attempt timing')
        partition=[[] for _ in ordered]
        details=row.get('contactDetailsByAttempt')
        if details is not None:
            if not isinstance(details,list) or len(details)!=len(outcomes):
                raise ValueError('contact details disagree with attempt count')
            for ds,o in zip(details,outcomes):
                if (not isinstance(ds,list) or bool(ds)!=bool(o[1]) or any(not isinstance(d,dict)
                        or type(d.get('hitTick')) is not int or d['hitTick']<o[2]
                        or type(d.get('targetObjectId')) is not int for d in ds)
                        or (min((d['hitTick'] for d in ds),default=None)!=o[3])):
                    raise ValueError('contact details disagree with exact hit timing')
        detail_partition=[[] for _ in ordered]
        phases=row.get('projectilePhasesByAttempt')
        if phases is not None:
            if (not isinstance(phases,list) or len(phases)!=len(outcomes)
                or any(not isinstance(p,dict) or type(p.get('spreadBranchObserved')) is not bool
                       or any(type(p.get(k)) is not int or p[k]<0 for k in ('firstProjectileCount','spreadProjectileCount','firstProjectileEnemyCount','spreadEnemyCount'))
                       for p in phases)):
                raise ValueError('projectile phases disagree with exact attempts')
        phase_partition=[[] for _ in ordered]
        phase_outcomes=row.get('phaseOutcomesByAttempt')
        if phase_outcomes is not None:
            if not isinstance(phase_outcomes,list) or len(phase_outcomes)!=len(outcomes):
                raise ValueError('phase outcomes disagree with attempt count')
            for ordinal,(ps,o) in enumerate(zip(phase_outcomes,outcomes)):
                if not isinstance(ps,dict) or not ps:raise ValueError('missing phase outcomes')
                for name,p in ps.items():
                    if (not isinstance(name,str) or not isinstance(p,dict) or type(p.get('executed')) is not bool
                        or type(p.get('hitCount')) is not int or p['hitCount'] not in (0,1)
                        or type(p.get('distinctEnemyCount')) is not int or p['distinctEnemyCount']<0
                        or bool(p['distinctEnemyCount'])!=bool(p['hitCount'])
                        or p['hitCount'] and (not p['executed'] or type(p.get('firstHitTick')) is not int or p['firstHitTick']<o[2])
                        or not p['hitCount'] and p.get('firstHitTick') is not None):
                        raise ValueError('invalid exact phase outcome')
                if (int(any(p['hitCount'] for p in ps.values()))!=o[1]
                    or min((p['firstHitTick'] for p in ps.values() if p['hitCount']),default=None)!=o[3]):
                    raise ValueError('phase hits disagree with whole attempt')
                if details is not None:
                    if any(d.get('phase') not in ps for d in details[ordinal]):
                        raise ValueError('contact has no corresponding phase')
                    for name,p in ps.items():
                        part=[d for d in details[ordinal] if d.get('phase')==name]
                        if (len({d['targetObjectId'] for d in part})!=p['distinctEnemyCount']
                            or min((d['hitTick'] for d in part),default=None)!=p['firstHitTick']):
                            raise ValueError('phase totals disagree with actual contacts')
        phase_outcome_partition=[[] for _ in ordered]
        reinforcement=row.get('reinforcementPerAttempt')
        reinforcement_ticks=row.get('reinforcementTickPerAttempt')
        if reinforcement is not None:
            if (len(reinforcement)!=len(outcomes) or any(type(v) is not bool for v in reinforcement)
                    or not isinstance(reinforcement_ticks,list) or len(reinforcement_ticks)!=len(outcomes)
                    or any((type(t) is not int if flag else t is not None)
                           for flag,t in zip(reinforcement,reinforcement_ticks))):
                raise ValueError('reinforcement evidence disagrees with attempts')
        reinforcement_partition=[[] for _ in ordered]
        target_partition=[[] for _ in ordered]
        tick_partition=[[] for _ in ordered]
        for ordinal,outcome in enumerate(outcomes):
            index=_attempt_episode(row,ordered,starts,outcome[0])
            partition[index].append(outcome)
            if policies is not None:policy_partition[index].append(policies[ordinal])
            if tips is not None:tip_partition[index].append(tips[ordinal])
            if details is not None:detail_partition[index].append(details[ordinal])
            if phases is not None:phase_partition[index].append(phases[ordinal])
            if phase_outcomes is not None:phase_outcome_partition[index].append(phase_outcomes[ordinal])
            if reinforcement is not None:
                reinforcement_partition[index].append((reinforcement[ordinal],reinforcement_ticks[ordinal]))
            if targets is not None:target_partition[index].append(targets[ordinal])
            if target_ticks is not None:tick_partition[index].append(target_ticks[ordinal])
        for episode,local,local_targets,local_ticks,local_reinforce,local_details,local_phases,local_phase_outcomes,local_policies,local_tips in zip(output,partition,target_partition,tick_partition,reinforcement_partition,detail_partition,phase_partition,phase_outcome_partition,policy_partition,tip_partition):
            if not local:continue
            count=len(local); hits=sum(o[1] for o in local)
            projected={**identity,'status':'verified-timed-outcomes',
                'attemptCount':count,'hitCount':hits,'hitRate':round(hits/count,6),
                'outcomes':local,'fallbackUsed':False,
                'multiTargetStatus':'verified-per-attempt-target-counts' if targets is not None else 'unavailable-per-attempt-target-counts'}
            if row.get('calculationConfidence')=='experimental':
                projected.update(status='experimental-timed-outcomes',calculationConfidence='experimental',
                    developmentLabel=row['developmentLabel'],verifiedCompletionCredit=False,
                    multiTargetStatus='provisional-per-attempt-target-counts')
            if row.get('outcomeScope') is not None:
                projected['outcomeScope']=row['outcomeScope']
            if row.get('targetCountsAreLowerBounds') is True:
                projected['targetCountsAreLowerBounds']=True
                if targets is not None:projected['multiTargetStatus']='confirmed-contact-lower-bounds'
            if row.get('hitRateScope') is not None:
                projected['hitRateScope']=row['hitRateScope']
            if 'fullRequestedMetricComplete' in row:
                projected['fullRequestedMetricComplete']=row['fullRequestedMetricComplete']
            for key in ('binaryCastSuccessComplete','completeTargetCountsAvailable','outcomeTimingScope'):
                if key in row:projected[key]=row[key]
            if row.get('attributionEstimateMayOvercount'):
                estimated_local_cast_ticks={u[0] for u in projected['outcomes']}
                estimated_ticks={u['startTick'] for u in row.get('estimatedContactsByCast',[]) if u['startTick'] in estimated_local_cast_ticks}
                confirmed_ticks={u['parent']['start']['tick'] for u,details in zip(row.get('executionEvidenceByAttempt',[]),row.get('confirmedContactDetailsByAttempt',[])) if details}
                if 'confirmedHitCastTicks' in row:
                    confirmed_ticks=set(row['confirmedHitCastTicks'])
                projected.update(attributionEstimateMayOvercount=True,
                    estimatedContactCastCount=len(estimated_ticks),
                    estimatedOnlyHitCastCount=len(estimated_ticks-confirmed_ticks),
                    confirmedHitCastCount=projected['hitCount']-len(estimated_ticks-confirmed_ticks))
            if row.get('regionEstimateMayMisclassify'):
                region_cast_ticks={o[0] for o in local}
                projected.update(regionEstimateMayMisclassify=True,
                    estimatedRegionCastCount=len(region_cast_ticks & set(row.get('estimatedRegionCastTicks',[]))),
                    regionEstimateMeaning=row['regionEstimateMeaning'])
            if row.get('attributionEstimateMayMisclassify'):
                attribution_ticks={o[0] for o in local}
                projected.update(attributionEstimateMayMisclassify=True,
                    estimatedAttributionCastCount=len(attribution_ticks & set(row.get('estimatedAttributionCastTicks',[]))),
                    estimatedMissCastCount=len(attribution_ticks & set(row.get('estimatedMissCastTicks',[]))),
                    attributionEstimateMeaning=row['attributionEstimateMeaning'])
            if tips is not None:
                projected.update(tipEvidenceByAttempt=local_tips,
                    confirmedTipCastCount=sum(t['confirmedTip'] for t in local_tips),
                    tipRegionUnresolvedCastCount=sum(t['regionUnresolved'] for t in local_tips),
                    tipCountMeaning=row['tipCountMeaning'])
            if policies is not None:
                projected['policySuccessByAttempt']=local_policies
                projected['policyClassifiedCastCount']=sum(p is not None for p in local_policies)
                if projected['policyClassifiedCastCount']:
                    projected['status']='recorded-and-user-policy-outcomes'
            if details is not None:
                projected['contactDetailsByAttempt']=local_details
            if phases is not None:
                projected['projectilePhasesByAttempt']=local_phases
                projected['contactDetailMeaning']=row.get('contactDetailMeaning')
            if phase_outcomes is not None:
                projected['phaseOutcomesByAttempt']=local_phase_outcomes
                projected['phaseBreakdown']={name:dict(
                    attemptCount=sum(ps[name]['executed'] for ps in local_phase_outcomes if name in ps),
                    hitCount=sum(ps[name]['hitCount'] for ps in local_phase_outcomes if name in ps),
                    distinctEnemyTargetsSummedAcrossAttempts=sum(ps[name]['distinctEnemyCount'] for ps in local_phase_outcomes if name in ps))
                    for name in sorted({n for ps in local_phase_outcomes for n in ps})}
            if reinforcement is not None:
                projected.update(reinforcementPerAttempt=[v for v,t in local_reinforce],
                    reinforcementTickPerAttempt=[t for v,t in local_reinforce],
                    variantBreakdown={name:dict(attemptCount=sum(v==flag for v,t in local_reinforce),
                        hitCount=sum(o[1] for o,(v,t) in zip(local,local_reinforce) if v==flag))
                        for name,flag in [('normal',False),('W-reinforced',True)]})
            if row.get('perUseCompletenessTracked'):
                projected['perUseCompletenessTracked']=True
                projected['unresolvedUseCountScope']='metric-level; unknown uses are not allocated across episodes'
            if targets is not None:
                target_sum=sum(local_targets);multi=sum(n>=2 for n in local_targets)
                projected.update({counts_key:local_targets,sum_key:target_sum,mean_key:round(target_sum/count,6)})
                projected.update(
                    multiTargetAttemptCount=multi,multiTargetAttemptRate=round(multi/count,6))
            projected['multiTargetTimelineStatus']='verified-first-contact-ticks' if target_ticks is not None else 'unavailable-first-contact-ticks'
            if row.get('targetCountsAreLowerBounds') is True and target_ticks is not None:
                projected['multiTargetTimelineStatus']='confirmed-first-contact-ticks-only'
            if row.get('attributionEstimateMayOvercount') and target_ticks is not None:
                projected['multiTargetTimelineStatus']='recorded-ticks-with-estimated-attribution'
                projected['multiTargetStatus']='estimated-attribution-target-counts'
            if target_ticks is not None:
                projected[ticks_key]=local_ticks
            episode['metrics'].append(projected)
        coverage.append({**identity,'status':'projected-exact-timing',
                         'attemptCount':len(outcomes),'hitCount':row['hitCount'],
                         **{k:row[k] for k in ('observedCombatCastCount','verifiedCombatCastCount',
                             'unresolvedCombatCastCount','outcomeScope') if k in row},
                         'multiTargetStatus':'verified-per-attempt-target-counts' if targets is not None else 'unavailable-per-attempt-target-counts'})
        if row.get('calculationConfidence')=='experimental':
            coverage[-1].update(status='projected-provisional-outcomes',calculationConfidence='experimental',
                verifiedCompletionCredit=False,multiTargetStatus='provisional-per-attempt-target-counts')
        if row.get('targetCountsAreLowerBounds') is True:
            coverage[-1]['targetCountsAreLowerBounds']=True
            if targets is not None:coverage[-1]['multiTargetStatus']='confirmed-contact-lower-bounds'
        if row.get('attributionEstimateMayOvercount'):
            coverage[-1].update(attributionEstimateMayOvercount=True,
                multiTargetStatus='estimated-attribution-target-counts')
        if row.get('hitRateScope') is not None:coverage[-1]['hitRateScope']=row['hitRateScope']
    _project_excluded_uses(observations,output,coverage)
    return {'episodes':output,'coverage':coverage,'fallbackUsed':False,
            'assignment':'cast episode first; explicitly rechecked opening cast assigned to its recorded hit episode; real ticks preserved'}


def _project_excluded_uses(observations,episodes,coverage):
    """Excluded attempts remain visible without becoming misses or hits."""
    fields=(('provisionallyExcludedCancelledCastTicks','provisionallyExcludedCancelledCastCount'),
            ('provisionallyExcludedWinnerInterruptedCastTicks','provisionallyExcludedWinnerInterruptedCastCount'))
    for row in observations:
        for ticks_key,count_key in fields:
            if ticks_key not in row:continue
            ticks=row[ticks_key]
            if (not isinstance(ticks,list) or any(type(t) is not int for t in ticks)
                    or len(set(ticks))!=len(ticks) or row.get(count_key)!=len(ticks)):
                raise ValueError('excluded use ticks disagree with explicit count')
            if set(ticks)&{o[0] for o in row.get('outcomes',[]) or []}:
                raise ValueError('excluded use also appears in calculated attempts')
            partition=[[] for _ in episodes]
            for tick in ticks:
                matches=[i for i,e in enumerate(episodes) if e['startTick']<=tick<e['endTick']]
                if len(matches)!=1:raise ValueError('excluded use has no unique supplied episode')
                partition[matches[0]].append(tick)
            for e,local in zip(episodes,partition):
                if not local:continue
                metric=next((m for m in e['metrics'] if m.get('metricId')==row.get('metricId')),None)
                if metric is None:
                    metric={k:row[k] for k in ('metricId','skillGroup','mode','unit','targetCohort') if k in row}
                    metric.update(status='experimental-excluded-uses',attemptCount=None,hitCount=None,
                                  hitRate=None,outcomes=[],fallbackUsed=False)
                    e['metrics'].append(metric)
                metric.update({ticks_key:local,count_key:len(local)},calculationConfidence='experimental',
                    developmentLabel=row.get('developmentLabel','개발 중 · 잠정 적중률'),
                    verifiedCompletionCredit=False,excludedUsesCountedAsMisses=False)
            cov=next((c for c in coverage if c.get('metricId')==row.get('metricId')),None)
            if cov is not None:cov.update({ticks_key:ticks,count_key:len(ticks)},excludedUseAssignment='castTick in [startTick,endTick)')
