"""User-selected phases and object lifetimes for development rates.

These paths do not confer verified completion. A first pulse is not the first
observed damage: a later pulse must never rescue a missed initial attack.
"""
from collections import defaultdict,Counter
from .skill_partial_cast_lifetimes import ordered_cast_records
from .skill_wire_order import event_within_cast,finish_lookup,command_order
from .requested_skill_hit_rates import _result,_unavailable

PHASE_EFFECTS={
    (82,1082400,'any'):{1082401},
    (47,1047500,'first-hit'):{1047501},
    (47,1047500,'end-hit'):{1047503},
}

# Explicit development profile: this object has an empty gameDb prefab name.
# Its recorded owner and ordered E spawn establish the provisional phase link.
PHASE_PROJECTILES={(82,1082400,'any'):{108241}}

def recorded_phase_activity_ends(spec,starts,finishes,spawns,terminals,player):
    from .skill_projectile_active_end import projectile_active_end_records
    codes=PHASE_PROJECTILES.get((spec['characterCode'],spec['skillGroup'],spec['mode']))
    if codes is None or spawns is None or terminals is None:return {}
    records,reason=_records(spec,starts,finishes,player)
    if reason:return {}
    ends=projectile_active_end_records(terminals);by_cast=defaultdict(list)
    for shot in spawns:
        if shot.get('ownerPlayerObjectId')!=player or shot['projectileCode'] not in codes:continue
        key=command_order(shot)
        if key is None:return {}
        parents=[r for r in records if r['complete'] and command_order(r['start'])<=key<=command_order(r['finish'])]
        if len(parents)!=1:return {}
        by_cast[command_order(parents[0]['start'])].append(shot)
    result={}
    for cast,shots in by_cast.items():
        if len({s['projectileObjectId'] for s in shots})!=len(shots):continue
        activity=[]
        for shot in shots:
            end=ends.get(shot['projectileObjectId'],{})
            explosions={e['tick'] for e in terminals if e.get('objectId')==shot['projectileObjectId'] and e.get('event')=='CmdProjectileExplosion'}
            if not end.get('complete') or len(explosions)!=1 or not shot['tick']<=next(iter(explosions))<=end['endTick']:break
            activity.append(end['endTick'])
        if len(activity)==len(shots):result[cast]=max(activity)
    return result

def _records(spec,starts,finishes,player):
    own=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==spec['skillGroup']]
    return ordered_cast_records(own,finishes,player)

def _finish_row(spec,records,contacts,unknown,intervals,method,**details):
    combat=[i for i,r in enumerate(records) if any(l<=r['start']['tick']<end for l,end in intervals)]
    # Positive contact can close a canceled use, but absent contact cannot.
    valid=[i for i in combat if i not in unknown or contacts[i]]
    unresolved=[i for i in combat if i not in valid]
    # Preserve already selected records, without another attribution pass or
    # feeding optional cancellation policies a new emission claim.
    diagnostics=dict(unresolvedCastTicks=[records[i]['start']['tick'] for i in unresolved],
        unresolvedPhaseEvidence=[dict(start=dict(records[i]['start']),
            finish=dict(records[i]['finish']) if records[i].get('finish') is not None else None,
            reason=unknown[i]) for i in unresolved])
    if combat and not valid:
        return {**_unavailable(spec,'phase-object linkage unresolved'),
            'unresolvedCombatCastCount':len(combat),
            'unresolvedCastReasons':dict(Counter(unknown[i] for i in combat if i not in valid)),**details,**diagnostics}
    row=_result(spec,[contacts[i] for i in valid],method,cast_ticks=[records[i]['start']['tick'] for i in valid])
    row.update(perUseCompletenessTracked=True,observedCombatCastCount=len(combat),
        verifiedCombatCastCount=len(valid),unresolvedCombatCastCount=len(combat)-len(valid),
        unresolvedCastReasons=dict(Counter(unknown[i] for i in combat if i not in valid)),
        incompleteUsesCountedAsMisses=False,**details,**diagnostics)
    return row

def _celine_fusion_parents(starts,finishes,states,actions,bombs,ends,player):
    """Recorded R consumption -> explicitly targeted fused object, no window."""
    roots=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1043500]
    records,reason=ordered_cast_records(roots,finishes,player)
    if reason:return {}
    lookup=finish_lookup(finishes,player);candidates=defaultdict(set)
    consumptions=defaultdict(list)
    for state in states:
        parent=state.get('targetObjectId')
        if (state.get('event')=='add' and state.get('casterObjectId')==player
                and state.get('stateCode')==1043521 and parent in bombs
                and bombs[parent]['summonCode']==1220):
            consumptions[state['tick']].append(state)
    for action in actions:
        at=command_order(action)
        if (action.get('sourceObjectId')!=player or action.get('skillIdCode')!=598
                or action.get('actionNo')!=1 or at is None):continue
        children={t['targetObjectId'] for t in action.get('targets',[]) if
            t['targetObjectId'] in bombs and bombs[t['targetObjectId']]['summonCode'] in {1224,1225,1226,1227}
            and bombs[t['targetObjectId']]['tick']==action['tick']}
        owners=[r for r in records if r['complete'] and event_within_cast(r['start'],r['finish']['tick'],action,lookup)]
        if len(children)!=1 or len(owners)!=1:continue
        child=next(iter(children));owner=owners[0]
        for state in consumptions[action['tick']]:
            parent=state.get('targetObjectId');order=command_order(state)
            if (state.get('event')!='add' or state.get('casterObjectId')!=player
                    or state.get('stateCode')!=1043521 or state['tick']!=action['tick']
                    or parent not in bombs or bombs[parent]['summonCode']!=1220
                    or order is None or order>=at
                    or not event_within_cast(owner['start'],owner['finish']['tick'],state,lookup)):
                continue
            terminal=ends.get(parent,[])
            if len(terminal)!=1 or command_order(terminal[0]) is None or command_order(terminal[0])<at:continue
            candidates[parent].add(child)
    return {parent:next(iter(children)) for parent,children in candidates.items() if len(children)==1}


def celine_bomb_metric(spec,starts,finishes,damages,states,actions,summons,terminals,player,teams,intervals):
    """Q creation -> owned bomb; W ignition state -> same bomb -> burst.

    W finishes before the burst. Its ignition event, not its finish timestamp,
    owns the bomb outcome. R is an optional producer, never an admission gate.
    """
    records,reason=_records(spec,starts,finishes,player)
    if reason:return _unavailable(spec,reason)
    if not records:return _unavailable(spec,'no observed requested casts')
    lookup=finish_lookup(finishes,player)
    # Exact SummonObject 1221 is Celine_Connect_line / SummonRope, not a
    # bomb. Waiting for a rope burst falsely leaves an otherwise closed Q open.
    bombs={s['objectId']:s for s in summons if s['ownerObjectId']==player and s['summonCode'] in {1220,1224,1225,1226,1227}}
    ignition_by_bomb=defaultdict(list);damage_by_tick_effect=defaultdict(list)
    for s in states:
        if s.get('event')=='add' and s.get('casterObjectId')==player and s.get('stateCode') in {1043301,1043302}:
            ignition_by_bomb[s.get('targetObjectId')].append(s)
    for d in damages:
        target=d['targetObjectId']
        if d['attackerObjectId']==player and target in teams and teams[target]!=teams[player]:
            damage_by_tick_effect[d['tick'],d.get('effectCode')].append(d)
    ends=defaultdict(list)
    for t in terminals:
        if t['event']=='CmdDestroyDelayStart' and t.get('objectId') in bombs:ends[t['objectId']].append(t)
    normal=[a for a in actions if a['sourceObjectId']==player and a['skillIdCode']==595 and a['actionNo']==1]
    fused=[a for a in actions if a['sourceObjectId']==player and a['skillIdCode']==598 and a['actionNo'] in {5,6,7,8}]
    markers=[]
    for a in normal:
        ids={t['targetObjectId'] for t in a.get('targets',[]) if t['targetObjectId'] in bombs}
        if len(ids)==1:markers.append((a,next(iter(ids)),{1043300}))
    for a in fused:
        ids={o for o,s in bombs.items() if s['summonCode'] in {1224,1225,1226,1227} and any(t['tick']==a['tick'] for t in ends[o])}
        if len(ids)==1:markers.append((a,next(iter(ids)),{1043500+a['actionNo']-5}))
    markers.sort(key=lambda x:(x[0]['tick'],command_order(x[0]) or ()))
    contacts=[set() for _ in records];unknown={i:r['reason'] for i,r in enumerate(records) if not r['complete']}
    linked=defaultdict(set)
    for obj,bomb in bombs.items():
        if spec['skillGroup']==1043200:
            owners=[i for i,r in enumerate(records) if r['complete'] and r['start']['tick']<=bomb['tick']<=r['finish']['tick'] and bomb['summonCode']==1220]
        else:
            ignition=ignition_by_bomb[obj]
            owners=[i for i,r in enumerate(records) if r['complete'] and any(event_within_cast(r['start'],r['finish']['tick'],s,lookup) for s in ignition)]
        if len(set(owners))==1:linked[owners[0]].add(obj)
        elif owners:
            for i in owners:unknown[i]='bomb-has-multiple-cast-owners'
    root={obj:i for i,objects in linked.items() for obj in objects}
    events_by_bomb=defaultdict(set);observed_bursts=set()
    for index,(a,obj,effects) in enumerate(markers):
        if not any(t['tick']==a['tick'] for t in ends[obj]):continue
        at=command_order(a)
        if at is None:continue
        previous=[command_order(other) for other,_,fx in markers[:index] if other['tick']==a['tick'] and fx&effects and command_order(other) is not None]
        lower=max(previous) if previous else None
        observed_bursts.add(obj)
        for d in (d for fx in effects for d in damage_by_tick_effect[a['tick'],fx]):
            order=command_order(d);target=d['targetObjectId']
            if d['attackerObjectId']==player and d['tick']==a['tick'] and d.get('effectCode') in effects and order is not None and order<at and (lower is None or lower<order) and target in teams and teams[target]!=teams[player]:
                events_by_bomb[obj].add((d['tick'],target))
    transferred={}
    for parent,child in _celine_fusion_parents(starts,finishes,states,actions,bombs,ends,player).items():
        bursts=[a for a,obj,_ in markers if obj==child and command_order(a) is not None]
        if child not in observed_bursts or len(bursts)!=1:continue
        if command_order(ends[parent][0])>command_order(bursts[0]):continue
        observed_bursts.add(parent)
        events_by_bomb[parent].update(events_by_bomb[child])
        transferred[parent]=child
    retired_unignited=set();connection_only_actions=[]
    connection_ticks={s['tick'] for s in summons if s.get('ownerObjectId')==player and s.get('summonCode')==1221
        and s.get('snapshotType')=='SummonRopeSnapshot' and s.get('identityVerifiedAgainstGameDb') is True}
    for obj,bomb in bombs.items():
        terminal=ends[obj]
        if bomb['summonCode']!=1220 or len(terminal)!=1 or terminal[0]['tick']<bomb['tick']:continue
        if ignition_by_bomb[obj] or obj in observed_bursts:continue
        if any(s.get('event')=='add' and s.get('targetObjectId')==obj and s.get('stateCode')==1043521 for s in states):continue
        # Missing consumption metadata during a possible R cannot turn an
        # absorbed parent into a miss. A true unignited retirement can.
        if any(s['playerObjectId']==player and s['skillGroup']==1043500 and
               bomb['tick']<=s['tick']<=terminal[0]['tick'] for s in starts):continue
        targeted=[a for a in actions if a.get('sourceObjectId')==player and any(t.get('targetObjectId')==obj for t in a.get('targets',[]))]
        # Q action 2 plus an actual same-tick owned connection rope is a
        # provisional connection marker, not W ignition or an R consumption.
        if any(a.get('skillIdCode')!=594 or a.get('actionNo')!=2 or a['tick'] not in connection_ticks
            or command_order(a) is None for a in targeted):continue
        connection_only_actions.extend(dict(objectId=obj,actionOrder=a['wireOrder'],tick=a['tick']) for a in targeted)
        retired_unignited.add(obj)
    for i,r in enumerate(records):
        for obj in linked[i]:contacts[i].update(events_by_bomb[obj])
        if not linked[i]:unknown.setdefault(i,'no-owned-ignited-or-created-bomb')
        elif not linked[i]<=(observed_bursts|retired_unignited):unknown.setdefault(i,'bomb-absorbed-or-burst-not-observed')
        elif r['complete']:unknown.pop(i,None)
    return _finish_row(spec,records,contacts,unknown,intervals,'development-owned-bomb-ignition-to-recorded-burst',
        rCastRequired=False,castFinishClosesBomb=False,linkedBombCount=len(root),
        excludedAuxiliarySummonCodes=[1221],
        fusedParentBombCount=len(transferred),
        fusionParentLinkEvidence='ordered-R-consumption-state-targeted-fused-object-parent-retirement-and-child-burst',
        observedBombBurstCount=len(observed_bursts),retiredUnignitedBombCount=len(retired_unignited),
        retiredUnignitedBombs=[dict(objectId=o,createdTick=bombs[o]['tick'],removedTick=ends[o][0]['tick']) for o in sorted(retired_unignited)],
        bombDamageCountedTwice=False,provisionalConnectionOnlyActions=connection_only_actions,
        numeratorScope='W uses with any owned ignited bomb hit' if spec['skillGroup']==1043300 else 'Q bombs with recorded burst hit',
        interpretation='Q 설치/폭탄별 결과와 W 기폭 사용별 성공은 서로 다른 분모. 피해량에 두 결과를 더하지 않음. R 융합은 필수 조건 아님.')

def yumin_first_pulse_metric(spec,starts,finishes,damages,actions,spawns,summons,terminals,player,teams,intervals):
    records,reason=_records(spec,starts,finishes,player)
    if reason:return _unavailable(spec,reason)
    if not records:return _unavailable(spec,'no observed enhanced Q cast')
    lookup=finish_lookup(finishes,player)
    shots=[s for s in spawns if s.get('ownerPlayerObjectId')==player and s.get('projectileCode')==107722]
    field=[s for s in summons if s.get('ownerObjectId')==player and s.get('summonCode')==1553]
    actions=[a for a in actions if a['sourceObjectId']==player and a['skillIdCode']==1118 and a['actionNo']==1]
    contacts=[set() for _ in records];unknown={i:r['reason'] for i,r in enumerate(records) if not r['complete']};first_ticks=defaultdict(set)
    for shot in shots:
        parents=[i for i,r in enumerate(records) if (r['complete'] and event_within_cast(r['start'],r['finish']['tick'],shot,lookup)) or (not r['complete'] and r['reason']=='open-final-cast' and command_order(r['start']) is not None and command_order(shot) is not None and command_order(r['start'])<command_order(shot))]
        if len(parents)!=1:continue
        ticks={t['tick'] for t in terminals if t.get('objectId')==shot['projectileObjectId'] and t['event'] in {'CmdProjectileArrived','CmdDestroyDelayStart'}}
        children=[s for s in field if s['tick'] in ticks and any(a['tick']==s['tick'] for a in actions)]
        if len(children)!=1:continue
        # Explicit provisional scheduling assumption: the zero-delay custom
        # action starts on the next 60-Hz replay tick. Never use first DAMAGE.
        first_ticks[parents[0]].add(children[0]['tick']+1)
    by_tick=defaultdict(set)
    for i,ticks in first_ticks.items():
        for tick in ticks:by_tick[tick].add(i)
    for d in damages:
        target=d['targetObjectId']
        if d['attackerObjectId']!=player or d.get('effectCode')!=1077213 or target not in teams or teams[target]==teams[player]:continue
        owners=by_tick[d['tick']]
        if len(owners)==1:contacts[next(iter(owners))].add((d['tick'],target))
        elif owners:
            for i in owners:unknown[i]='overlapping-first-pulse-owners'
    for i,r in enumerate(records):
        if len(first_ticks[i])!=1:unknown[i]='first-pulse-object-link-missing-or-ambiguous'
        elif r['complete'] and unknown.get(i)!='overlapping-first-pulse-owners':unknown.pop(i,None)
    return _finish_row(spec,records,contacts,unknown,intervals,'development-enhanced-Q-owned-field-first-scheduled-pulse',
        firstObservedDamageUsedAsFirstPulse=False,laterPulsesCounted=False,
        firstPulseTimingAssumption='zero-delay custom action runs one replay tick after field spawn; provisional, not a verified clock contract',
        firstPulseTimingVerified=False,phaseEffectCodes=[1077213],
        interpretation='강화 Q 최초 공격만 계산. 첫 타격이 빗나가고 후속 회오리가 맞은 경우 성공으로 바꾸지 않음. 최초 공격 시각은 개발 중 프레임 가정을 사용.')

def absent_conditional_phases(records, phase_events, phase_damages):
    """Normal parents with no prerequisite hit or child phase in source order.

    The caller must supply a reviewed conditional phase and complete streams.
    A started-but-unclosed child is never an absent phase. No duration estimate.
    """
    absent=set()
    for i,r in enumerate(records):
        if not r['complete'] or r['finish'].get('reason')!=0:continue
        left=command_order(r['start'])
        if left is None:continue
        later=[command_order(other['start']) for other in records if command_order(other['start']) is not None and command_order(other['start'])>left]
        right=min(later) if later else None
        def possible(event):
            at=command_order(event)
            return at is None or (left<=at and (right is None or at<right))
        if not any(possible(e) for e in phase_events) and not any(possible(d) for d in phase_damages):absent.add(i)
    return absent


def laura_explosion_metric(spec,starts,finishes,damages,state_scripts,player,teams,intervals,*,allow_absent_phase=False,gaps=None):
    records,reason=_records(spec,starts,finishes,player)
    if reason:return _unavailable(spec,reason)
    if not records:return _unavailable(spec,'no observed R cast')
    lookup=finish_lookup(finishes,player);contacts=[set() for _ in records]
    unknown={i:'R delayed explosion state not linked' for i in range(len(records))}
    events=sorted((s for s in state_scripts if s.get('sourceObjectId')==player and s.get('stateGroup')==1047520 and s.get('skillIdCode')==679),key=lambda s:command_order(s) or ())
    opened=[];end_owners=defaultdict(set);state_finish_evidence=[]
    state_links=[];opened_diagnostics=[]
    def compact(event):
        return {k:event[k] for k in ('event','tick','wireOrder','wireCategory','sourceObjectId',
            'stateGroup','skillIdCode','skillCode','reason','attackerObjectId','targetObjectId','effectCode') if k in event}
    def parent_ref(i):
        r=records[i]
        return dict(startTick=r['start']['tick'],startOrder=command_order(r['start']),
            complete=r['complete'],recordReason=r.get('reason'),
            finishTick=r['finish']['tick'] if r.get('finish') else None,
            finishOrder=command_order(r['finish']) if r.get('finish') else None,
            finishReason=r['finish'].get('reason') if r.get('finish') else None)

    for event in events:
        if event['event']=='CmdStartStateSkill':
            owners=[i for i,r in enumerate(records) if r['complete'] and event.get('skillCode')==r['start']['skillCode'] and event_within_cast(r['start'],r['finish']['tick'],event,lookup)]
            # Evaluate the same recorded window predicate for diagnostics only.
            within=[i for i,r in enumerate(records) if r.get('finish') is not None
                and event_within_cast(r['start'],r['finish']['tick'],event,lookup)]
            rejected=[dict(parent=parent_ref(i),reasons=
                ([] if records[i]['complete'] else ['parent-record-incomplete'])+
                ([] if event.get('skillCode')==records[i]['start']['skillCode'] else ['skill-code-mismatch']))
                for i in within if i not in owners]
            link=dict(stateStart=compact(event),stateFinish=None,
                parentCandidates=[parent_ref(i) for i in owners],windowCandidates=[parent_ref(i) for i in within],
                rejectedWindowCandidates=rejected,
                startLinkReason='unique-parent' if len(owners)==1 else 'multiple-parents' if owners else 'no-matching-complete-parent',
                finishLinkReason='state-finish-unobserved')
            state_links.append(link);opened_diagnostics.append(link)
            opened.append((event,owners))
        elif event['event']=='CmdFinishStateSkill':
            why=('state-finish-without-start' if not opened else
                 'multiple-open-state-starts' if len(opened)!=1 else
                 'state-start-parent-not-unique' if len(opened[0][1])!=1 else
                 'state-finish-reason-not-normal' if event.get('reason')!=0 else 'linked-normal-state-finish')
            if not opened_diagnostics:
                state_links.append(dict(stateStart=None,stateFinish=compact(event),parentCandidates=[],
                    windowCandidates=[],rejectedWindowCandidates=[],finishLinkReason=why))
            for link in opened_diagnostics:link.update(stateFinish=compact(event),finishLinkReason=why)
            opened_diagnostics=[]
            if len(opened)==1 and len(opened[0][1])==1:
                i=opened[0][1][0]
                state_finish_evidence.append(dict(castTick=records[i]['start']['tick'],
                    stateStartTick=opened[0][0]['tick'],stateFinishTick=event['tick'],
                    stateFinishReason=event.get('reason'),
                    parentFinishReason=records[i]['finish'].get('reason')))
            if len(opened)==1 and len(opened[0][1])==1 and event.get('reason')==0:
                i=opened[0][1][0];end_owners[event['tick']].add(i);unknown.pop(i,None)
            opened=[]
    unassigned_explosions=[]
    for d in damages:
        target=d['targetObjectId']
        if d['attackerObjectId']!=player or d.get('effectCode')!=1047503 or target not in teams or teams[target]==teams[player]:continue
        owners=end_owners[d['tick']]
        if len(owners)==1:contacts[next(iter(owners))].add((d['tick'],target))
        else:
            unassigned_explosions.append(d)
            for i in owners:unknown[i]='overlapping-explosion-state-ends'
    absent=set()
    if allow_absent_phase:
        # Native LauraActive4.Process has a zero-target early exit before its
        # shield and self-delay-state creation. Include damage to ANY target;
        # hitting a non-player can still create the secondary phase.
        all_phase_events=[s for s in state_scripts if s.get('sourceObjectId')==player and s.get('stateGroup')==1047520]
        all_phase_damage=[d for d in damages if d.get('attackerObjectId')==player and d.get('effectCode') in {1047501,1047503}]
        absent=absent_conditional_phases(records,all_phase_events,all_phase_damage)
        for i in absent:unknown.pop(i,None)
    unassigned_explosions=[d for d in damages if d.get('attackerObjectId')==player and d.get('effectCode')==1047503 and len(end_owners[d['tick']])!=1]
    import json
    from pathlib import Path
    policy=json.loads((Path(__file__).resolve().parents[1]/'data/user-hit-rate-scope-20260912.json').read_text(encoding='utf-8'))['recordedUseMissPolicy'].get('enabled') is True
    user_misses=[];blocked=[]
    # Include malformed identities in blockers, not in ownership assignment.
    raw_delay=[e for e in state_scripts if e.get('sourceObjectId') in {None,player}
        and (e.get('stateGroup')==1047520 or e.get('skillIdCode')==679)]
    def ordered_pair(left,right):
        return right is not None and command_order(left) is not None and command_order(right) is not None and command_order(left)<command_order(right) and left['tick']<=right['tick']
    for i,r in enumerate(records):
        if not policy or i not in unknown or contacts[i]:continue
        start=r['start'];finish=r.get('finish');left=command_order(start)
        later=[command_order(z['start']) for z in records if command_order(z['start']) is not None and left is not None and command_order(z['start'])>left]
        right=min(later) if later else None
        def pending(e):
            at=command_order(e)
            return at is None or left is None or (left<=at and (right is None or at<right))
        reasons=[];kind=None
        relevant=[e for e in raw_delay if pending(e)]
        links=[e for e in state_links if e['stateStart'] and any(p['startOrder']==left for p in e['parentCandidates'])]
        if gaps is None or any(g.get('count',0) for g in gaps):reasons.append('stream-completeness-unavailable')
        if not r['complete'] or not ordered_pair(start,finish) or finish.get('reason') not in set(range(15))|{16,17}:reasons.append('actual-parent-finish-unresolved')
        if any(command_order(d) is None or left is None or command_order(d)>=left for d in unassigned_explosions):reasons.append('unassigned-explosion-damage')
        if not relevant and not links:
            kind='cancelled-parent-without-delay-state'
            if not finish or finish.get('reason') not in set(range(1,15))|{16,17}:reasons.append('parent-not-actually-cancelled')
            if any(pending(d) for d in damages if d.get('attackerObjectId')==player and d.get('effectCode') in {1047501,1047503}):reasons.append('phase-producing-or-explosion-damage-observed')
        else:
            kind='cancelled-delay-state'
            if len(links)!=1:reasons.append('delay-state-parent-not-unique')
            else:
                link=links[0];ss=link['stateStart'];ff=link['stateFinish']
                if len(link['parentCandidates'])!=1 or link['finishLinkReason']!='state-finish-reason-not-normal':reasons.append('delay-state-pair-not-unique-cancel')
                if not ordered_pair(ss,ff) or not ff or ff.get('reason') not in set(range(1,15))|{16,17}:reasons.append('actual-delay-state-cancel-unresolved')
                # Exact event identity; a repeated/orphan/malformed event remains blocking.
                expected={tuple(ss.get('wireOrder',[])),tuple((ff or {}).get('wireOrder',[]))}
                if any(e.get('sourceObjectId')!=player or e.get('stateGroup')!=1047520 or e.get('skillIdCode')!=679 or tuple(e.get('wireOrder',[])) not in expected for e in relevant):reasons.append('additional-or-malformed-delay-state-event')
                if len(relevant)!=2:reasons.append('delay-state-window-not-exclusive')
        evidence=dict(parent=parent_ref(i),policyKind=kind)
        if kind=='cancelled-delay-state' and len(links)==1:evidence.update(stateStart=links[0]['stateStart'],stateFinish=links[0]['stateFinish'])
        if reasons:blocked.append(dict(evidence,reasons=sorted(set(reasons))));continue
        unknown.pop(i);user_misses.append(dict(evidence,phaseExecutionObserved=kind=='cancelled-delay-state'))
    result=_finish_row(spec,records,contacts,unknown,intervals,'development-R-owned-delay-state-actual-end-explosion',
        userPolicyMissEvidence=user_misses,userPolicyBlockedEvidence=blocked,
        unassignedExplosionDamageEvidence=[compact(d) for d in unassigned_explosions],

        unexecutedConditionalPhaseCastTicks=[records[i]['start']['tick'] for i in sorted(absent)],
        conditionalPhaseAssumption='정상 종료와 첫 피해·지연 상태 부재를 폭발 미발동으로 잠정 분류; 시작된 상태의 종료 누락은 미확인 유지',
        conditionalPhaseStaticEvidence='deliverables/laura-r-conditional-phase-static-v1.json',
        phaseEffectCodes=[1047503],delayStateGroup=1047520,castFinishClosesExplosion=False,
        observedDelayStateFinishes=state_finish_evidence,
        lauraDelayLinkDiagnostics=dict(
            casts=[dict(parent=parent_ref(i),combat=any(l<=r['start']['tick']<h for l,h in intervals),
                reason=unknown[i],positiveContactRetained=bool(contacts[i]),
                stateLinkIndexes=[n for n,e in enumerate(state_links) if any(
                    p['startOrder']==command_order(r['start']) for p in e['windowCandidates'])])
                for i,r in enumerate(records) if i in unknown],
            stateLinks=state_links,scope='own1047520-wire679-only; recorded-window candidates; diagnostics do not alter outcomes'),
        interpretation='R에서 시작한 본인 지연 상태의 실제 종료와 같은 프레임의 폭발 피해를 연결. 첫 타격과 별도 집계.')
    combat_misses=[e for e in user_misses if any(l<=e['parent']['startTick']<h for l,h in intervals)]
    result.update(userPolicyMissCastCount=len(combat_misses),userPolicyMissCastTicks=[e['parent']['startTick'] for e in combat_misses],
        userPolicyMissAuthority='explicit-user-rule',userPolicyMissMeaning='Actual cancellation with no recorded phase contact; not native completion or geometric absence proof.')
    if combat_misses:result.update(verifiedCombatCastCount=max(0,(result.get('verifiedCombatCastCount') or 0)-len(combat_misses)),verifiedCompletionCredit=False,fullRequestedMetricComplete=False)
    return result


def exclude_adela_r_piece_damage(damages,state_scripts,summons,player):
    """Recorded R skillCode on an owned piece is a foreign Q/E producer."""
    owned={s['objectId'] for s in summons if s.get('ownerObjectId')==player}
    effects={1024210:1024110,1024220:1024110,1024230:1024110,
             1024240:1024120,1024250:1024120,1024260:1024120,
             1024310:1024210,1024320:1024210,1024420:1024310,1024430:1024310}
    foreign={(s['tick'],effects[s['stateGroup']]) for s in state_scripts
        if s.get('event')=='CmdStartStateSkill' and s.get('sourceObjectId') in owned
        and s.get('stateGroup') in effects and s.get('skillCode') in {1024501,1024502,1024503}}
    rejected=[d for d in damages if d['attackerObjectId']==player and (d['tick'],d.get('effectCode')) in foreign]
    keys={id(d) for d in rejected}
    return [d for d in damages if id(d) not in keys],rejected
