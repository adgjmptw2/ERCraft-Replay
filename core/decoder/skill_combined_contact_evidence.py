"""One candidate contact graph for reviewed mixed-source basic cast rates.

All channels are required inputs, read together. No alternate rate evaluator,
nearest-cast assignment, or invented effect code is used. Candidate producer
completeness makes the resulting rates explicitly experimental.
"""
from collections import Counter,defaultdict
from functools import lru_cache
from pathlib import Path
import json
from .skill_partial_cast_lifetimes import ordered_cast_records
from .skill_wire_order import command_order,finish_lookup,event_within_cast,competing_manual_starts
from .skill_static_effect_families import candidate_effect_family
from .skill_complete_projectile_lifetimes import candidate_projectile_family
from .skill_projectile_active_end import projectile_active_end_records,collision_only_outcome
from .requested_skill_hit_rates import _result,_unavailable
from .skill_development_effect_metrics import development_policy,annotate_provisional_rate


@lru_cache(maxsize=1)
def contact_contracts():
    path=Path(__file__).resolve().parents[1]/'data/combined-contact-routes-v1.json'
    return {e['spec']['metricId']:e for e in json.loads(path.read_bytes())['entries']}


def combined_contact_metric(spec,starts,finishes,spawns,collisions,terminals,damages,
                            player,teams,intervals,catalog,effect_rows,projectile_owners,
                            gaps=None,route_inputs=None):
    policy=development_policy()
    if not policy['enabled']:
        return _unavailable(spec,'combined candidate contact policy is disabled')
    if spec['mode']!='any' or spec['unit']!='skill-cast':
        return _unavailable(spec,'combined contact rule requires a basic cast denominator')
    contract=contact_contracts().get(spec.get('metricId'))
    if contract is None or contract['spec']!=spec:
        return _unavailable(spec,'combined contact rule lacks its exact requested-scope contract')
    required={'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdDamage','CmdProjectileCollision',
              'CmdDestroy','CmdDestroyDelayStart','CmdProjectileExplosion','CmdProjectileArrived',
              'CmdPlaySkillAction','CmdPlaySkillActionWithTargets'}
    raw=route_inputs or {}
    if (gaps is None or any(g.get('count',0) and g.get('packetName') in required for g in gaps)
            or any(raw.get(k) is None for k in ('raw_actions','damages','projectile_terminals'))
            or any(s is None for s in (starts,finishes,spawns,collisions,terminals,damages))):
        return _unavailable(spec,'combined contact graph requires complete action/object/damage streams')
    groups,effects=candidate_effect_family(spec,catalog,effect_rows)
    projectile_groups,codes=candidate_projectile_family(spec,catalog)
    projectile_only=contract.get('projectileContactOnly') is True
    effect_only=contract.get('effectDamageOnly') is True
    if effect_only and (projectile_only or not effects):
        return _unavailable(spec,'effect-damage predicate requires an exclusive nonempty effect contract')
    if projectile_only:effects=set()
    if not effects and not codes:
        return _unavailable(spec,'combined rule has no compiled candidate producer mapping')
    groups=set(groups)|set(projectile_groups)|{spec['skillGroup']}
    own=[s for s in starts if s.get('playerObjectId')==player]
    family=[s for s in own if s['skillGroup'] in groups]
    records,reason=ordered_cast_records(family,finishes,player)
    if reason:return _unavailable(spec,reason)
    selected=[i for i,r in enumerate(records) if r['start']['skillGroup']==spec['skillGroup']]
    if not selected:return _unavailable(spec,'no recorded cast for combined contact rule')
    competing,reason=ordered_cast_records(competing_manual_starts(own,catalog,groups,player),finishes,player)
    if reason:return _unavailable(spec,reason)
    lookup=finish_lookup(finishes,player)
    def inside(r,e):
        if r['complete']:return event_within_cast(r['start'],r['finish']['tick'],e,lookup)
        return command_order(e) is not None and command_order(e)>=command_order(r['start'])
    def enemy(target):return target in teams and teams[target]!=teams[player]
    contacts=[set() for _ in records];channels=[set() for _ in records]
    contact_sources=[defaultdict(set) for _ in records];action_complete=set()
    def record_contact(i,contact,channel):
        contacts[i].add(contact);channels[i].add(channel);contact_sources[i][contact].add(channel)
    unknown={i:r['reason'] for i,r in enumerate(records) if not r['complete']}
    for i,r in enumerate(records):
        if r['complete'] and r['finish'].get('reason')!=0:unknown[i]='cast-not-normally-complete'
    family_objects={s['projectileObjectId'] for s in spawns
                    if s.get('ownerPlayerObjectId')==player and s['projectileCode'] in codes}
    family_collision_contacts={(c['tick'],c['targetObjectId']) for c in collisions
                               if c['projectileObjectId'] in family_objects}
    def corroborated(action,target):
        # An arbitrary same-owner collision may be a target marker or another
        # skill. Its actual object code must belong to this compiled family.
        return bool(target.get('sameTickDirectPlayerDamage') or
                    (target.get('sameTickOwnedProjectileCollision') and
                     (action['tick'],target.get('targetObjectId')) in family_collision_contacts))
    # Linked ordinary actions carry their recorded cast identity. Accept a
    # positive target only with the existing same-tick corroboration facts.
    for i,r in enumerate(records):
        actions=[] if projectile_only or effect_only or contract.get('actionContacts') is False else r['start'].get('linkActions',[])
        ordinary={'decoded-exact-CmdPlaySkillAction','decoded-exact-CmdPlaySkillActionWithTargets'}
        if (contract.get('actionCoverageClosesCast') and actions and r['complete'] and r['finish'].get('reason')==0
                and all(a.get('wireStatus') in ordinary and a['tick']>=r['start']['tick'] for a in actions)
                and all(corroborated(a,t)
                        for a in actions for t in a.get('targets',[]) if enemy(t.get('targetObjectId')))):
            action_complete.add(i)
        for action in actions:
            if action.get('wireStatus')!='decoded-exact-CmdPlaySkillActionWithTargets':continue
            if action['tick']<r['start']['tick']:continue
            for target in action.get('targets',[]):
                oid=target.get('targetObjectId')
                if enemy(oid) and corroborated(action,target):
                    record_contact(i,(action['tick'],oid),'corroborated-action-target')
    active_ends=projectile_active_end_records(terminals)
    objects=defaultdict(list);object_parents={};object_contacts=defaultdict(set)
    anonymous_codes=set()
    for s in spawns:
        if s.get('ownerPlayerObjectId')!=player:continue
        accepted=s['projectileCode'] in codes
        definition=catalog['projectileDefinitions'].get(str(s['projectileCode']),{})
        if (not accepted and not effect_only and contract.get('allowUnnamedProjectileCandidates') is not False and policy.get('lifecycleEstimatesEnabled')
                and definition.get('prefabName')=='' and collision_only_outcome(definition)):
            parents=[i for i,r in enumerate(records) if inside(r,s)]
            if len(parents)==1 and records[parents[0]]['complete'] and not any(inside(r,s) for r in competing):
                accepted=True;anonymous_codes.add(s['projectileCode'])
        if accepted:objects[s['projectileObjectId']].append(s)
    uncertain=[];uncertain_parents={}
    for oid,values in objects.items():
        if len(values)!=1:return _unavailable(spec,'duplicate candidate projectile identity')
        s=values[0]
        possible=[i for i,r in enumerate(records) if inside(r,s)]
        if len(possible)!=1 or not records[possible[0]]['complete'] or any(inside(r,s) for r in competing):
            uncertain.append((s,'projectile-parent-not-unique'));continue
        object_parents[oid]=possible[0]
        end=active_ends.get(oid,{})
        if not end.get('complete') or end['endTick']<s['tick']:
            unknown[possible[0]]='projectile-activity-end-incomplete'
    if contract.get('requireOwnedProducerForNegative'):
        parents=set(object_parents.values())
        for i in selected:
            if i not in parents and i not in unknown:unknown[i]='missing-recorded-projectile-emission'
    own_damage={(d['tick'],d['targetObjectId']) for d in damages if
                d.get('attackerObjectId')==player or projectile_owners.get(d.get('attackerObjectId'))==player}
    for c in collisions:
        oid=c['projectileObjectId'];target=c['targetObjectId']
        if oid not in object_parents or not enemy(target):continue
        s=objects[oid][0];end=active_ends.get(oid,{})
        if c['tick']<s['tick'] or (end.get('complete') and c['tick']>end['endTick']):
            unknown[object_parents[oid]]='contact-outside-projectile-lifetime';continue
        definition=catalog['projectileDefinitions'].get(str(s['projectileCode']),{})
        if not definition:return _unavailable(spec,'candidate projectile definition missing')
        # An unnamed target object may signal acquisition before the attack.
        # Candidate admission can establish its lifetime, never a hit by itself.
        if s['projectileCode'] in anonymous_codes and (c['tick'],target) not in own_damage:continue
        if not collision_only_outcome(definition) and (c['tick'],target) not in own_damage:
            if effect_only:continue
            unknown[object_parents[oid]]='complex-contact-without-damage';continue
        contact=(c['tick'],target);i=object_parents[oid]
        if not effect_only:record_contact(i,contact,'owned-projectile-contact')
        object_contacts[contact].add(i)
    live_effect_contacts=[]
    for d in damages:
        actor=d.get('attackerObjectId');target=d.get('targetObjectId')
        if (actor!=player and projectile_owners.get(actor)!=player) or not enemy(target) or d.get('effectCode') not in effects:continue
        contact=(d['tick'],target)
        # An exact object attacker or same-tick collision joins a delayed hit
        # without extending the cast's process lifetime or choosing a recent cast.
        possible=({object_parents[actor]} if actor in object_parents else object_contacts.get(contact,set()))
        live_parents=set();live_objects=[];incomplete_live=False
        bounded_candidates=None
        if not possible:
            possible={i for i,r in enumerate(records) if inside(r,d)}
            cast_candidates=set(possible)
            if any(inside(r,d) for r in competing):possible=set()
            if policy.get('attributeCandidateEffectToUniqueActiveOwnedProjectile') and command_order(d) is not None:
                # The cast may have finished while its owned object is still
                # active. Candidate damage belongs to the union of actual
                # cast and object lifetimes, not an invented post-cast window.
                bounded_candidates=cast_candidates;unbounded_live=False
                for oid,ss in objects.items():
                    s=ss[0];end=active_ends.get(oid,{})
                    if s['tick']>d['tick'] or (end.get('complete') and end['endTick']<d['tick']):continue
                    at=command_order(s)
                    if at is not None and s['tick']==d['tick'] and at>command_order(d):continue
                    if at is None or oid not in object_parents:
                        unbounded_live=True
                    else:bounded_candidates.add(object_parents[oid])
                    if at is None or not end.get('complete') or oid not in object_parents:
                        incomplete_live=True;continue
                    live_parents.add(object_parents[oid]);live_objects.append(oid)
                possible.update(live_parents)
                if incomplete_live:possible=set()
                if unbounded_live or not bounded_candidates:bounded_candidates=None
        if contract.get('requireActiveOwnedProducerForEffect'):
            # A character-owned damage packet is not itself a projectile edge.
            # This profile requires a real, closed owned producer even during
            # the parent cast. Never rescue a missing emission via its FX.
            at=command_order(d)
            supported=set()
            for oid,i in object_parents.items():
                s=objects[oid][0];end=active_ends.get(oid,{})
                if (at is not None and command_order(s) is not None and command_order(s)<=at
                        and end.get('complete') and s['tick']<=d['tick']<=end['endTick']):
                    supported.add(i)
            possible.intersection_update(supported)
        if len(possible)==1 and records[next(iter(possible))]['complete']:
            i=next(iter(possible));record_contact(i,contact,'candidate-effect')
            if i in live_parents:
                record_contact(i,contact,'candidate-effect-live-owned-object')
                live_effect_contacts.append(dict(castTick=records[i]['start']['tick'],effectTick=d['tick'],
                    effectCode=d['effectCode'],targetObjectId=target,projectileObjectIds=sorted(live_objects)))
        else:
            uncertain.append((d,'effect-parent-not-unique'))
            if bounded_candidates is not None:uncertain_parents[id(d)]=bounded_candidates
    # Silvia's human W callback applies its dedicated slow after damage.
    # The state supplies an independent positive; the shared damage remains
    # unassigned. Do not select a damage parent by proximity or form alone.
    silvia_state_contacts=[]
    wall=raw.get('wall_inputs') or {}
    if (spec.get('characterCode')==16 and spec['skillGroup']==1016300
            and catalog.get('sourceGameDbSha256')=='5ef9cb5459a2e908099655bf9ffc70ac16b134d1db282fb97b25fe065e7c8e4f'
            and groups=={1016300,1016700} and wall.get('states') is not None
            and not any(g.get('count',0) for g in gaps)):
        for state in wall['states']:
            at=command_order(state)
            if (state.get('event')!='add' or state.get('casterObjectId')!=player
                    or state.get('stateCode') not in range(1016301,1016306)
                    or not enemy(state.get('targetObjectId')) or at is None):continue
            receipts=[d for d in damages if d.get('attackerObjectId')==player
                and d.get('targetObjectId')==state['targetObjectId']
                and d.get('effectCode')==1016301 and d.get('isCritical') is False
                and d['tick']==state['tick'] and command_order(d) is not None
                and command_order(d)[0]==at[0] and command_order(d)<at]
            if len(receipts)!=1:continue
            human={i for i,r in enumerate(records) if r['start']['skillGroup']==1016300 and inside(r,state)}
            active=[];unresolved=False
            for oid,ss in objects.items():
                s=ss[0];end=active_ends.get(oid,{})
                if s['projectileCode']!=101603 or s['tick']>state['tick']:continue
                if end.get('complete') and end['endTick']<state['tick']:continue
                parent=object_parents.get(oid);spawn_order=command_order(s)
                if (parent is None or spawn_order is None or not end.get('complete')):
                    unresolved=True;continue
                if end['endTick']==state['tick']:
                    endings=[t for t in terminals if t.get('objectId')==oid
                        and t.get('tick')==end['endTick'] and t.get('event') in end.get('terminalCommands',[])]
                    if not endings or any(command_order(t) is None or command_order(t)<=at for t in endings):
                        unresolved=True;continue
                if spawn_order>command_order(receipts[0]):continue
                if (records[parent]['start']['skillGroup']!=1016300
                        or records[parent]['start'].get('skillIdCode')!=222):
                    unresolved=True;continue
                human.add(parent);active.append((oid,parent))
            if unresolved or len(human)!=1 or len(active)!=1:continue
            oid,i=active[0]
            if human!={i} or not records[i]['complete']:continue
            record_contact(i,(state['tick'],state['targetObjectId']),'native-human-W-slow-application')
            silvia_state_contacts.append(dict(castTick=records[i]['start']['tick'],
                stateTick=state['tick'],stateCode=state['stateCode'],targetObjectId=state['targetObjectId'],
                projectileObjectId=oid,stateWireOrder=list(at),damageParentAssigned=False))
    # Unowned events can taint earlier negatives, but cannot disprove an
    # independently recorded positive. Never assign an unowned event to one use.
    for event,why in uncertain:
        for i,r in enumerate(records):
            # Apply the same reviewed cast/object candidate union used above
            # without choosing a parent. Unbounded or orphan events retain
            # the conservative earlier-use scope; known candidates cannot
            # contaminate unrelated retired uses across the whole match.
            if id(event) in uncertain_parents and i not in uncertain_parents[id(event)]:continue
            if r['start']['tick']<=event['tick'] and not contacts[i] and i not in action_complete:unknown[i]=why
    for i in action_complete:
        if unknown.get(i) in {'projectile-activity-end-incomplete'}:unknown.pop(i)
    closed_emitted_cancellations=set()
    for i in selected:
        if unknown.get(i)!='cast-not-normally-complete' or effect_only:continue
        emitted=[oid for oid,parent in object_parents.items() if parent==i]
        if emitted and all(active_ends.get(oid,{}).get('complete') and
                collision_only_outcome(catalog['projectileDefinitions'].get(str(objects[oid][0]['projectileCode']),{}))
                for oid in emitted):
            # Cancellation ends the caster's coroutine, not a previously
            # emitted projectile. Its recorded activity end closes contacts.
            unknown.pop(i);closed_emitted_cancellations.add(i)
    lower_bound_positive={i for i in unknown if contacts[i]}
    for i,r in enumerate(records):
        if contacts[i] and r['complete']:unknown.pop(i,None)
    from .skill_partial_cast_lifetimes import user_cancelled_no_recorded_contact_indices
    pending_objects={parent for oid,parent in object_parents.items()
        if not active_ends.get(oid,{}).get('complete') or active_ends[oid]['endTick']<objects[oid][0]['tick']}
    user_misses=user_cancelled_no_recorded_contact_indices(records,contacts,unknown,
        cancellation_reason='cast-not-normally-complete',pending_continuations=pending_objects,gaps=gaps)
    cancellation_diagnostics=[]
    for i in selected:
        r=records[i];f=r.get('finish')
        if not f or f.get('reason') not in set(range(1,15))|{16,17}:continue
        emitted=[dict(objectId=oid,spawnTick=objects[oid][0]['tick'],
            activityEndComplete=active_ends.get(oid,{}).get('complete',False),
            activityEndTick=active_ends.get(oid,{}).get('endTick'))
            for oid,parent in object_parents.items() if parent==i]
        cancellation_diagnostics.append(dict(startTick=r['start']['tick'],startOrder=list(command_order(r['start'])),
            finishTick=f['tick'],finishOrder=list(command_order(f)),finishReason=f['reason'],
            emittedObjects=emitted,hasRecordedContact=bool(contacts[i]),
            admittedByUserPolicy=i in user_misses,
            pendingReason=unknown.get(i) if i not in user_misses else None,
            openProjectileContinuation=i in pending_objects))
    for i in user_misses:unknown.pop(i)
    combat=[i for i in selected if any(a<=records[i]['start']['tick']<b for a,b in intervals)]
    valid=[i for i in combat if i not in unknown]
    uncertain_evidence=[dict(tick=e['tick'],reason=why,
        candidateCastTicks=[records[i]['start']['tick'] for i in sorted(uncertain_parents[id(e)])]
            if id(e) in uncertain_parents else None,
        candidateParentSetBounded=id(e) in uncertain_parents)
        for e,why in uncertain]
    if combat and not valid:
        return {**_unavailable(spec,'no classified combined-contact use'),
                'unresolvedCombatCastCount':len(combat),
                'unownedEventEvidence':uncertain_evidence,
                'userPolicyCancellationDiagnostics':cancellation_diagnostics,
                'unresolvedCastReasons':dict(Counter(unknown[i] for i in combat))}
    row=_result(spec,[contacts[i] for i in valid],'combined-recorded-contact-evidence',
                cast_ticks=[records[i]['start']['tick'] for i in valid])
    if projectile_only or contract.get('attemptOnProjectileSpawn'):
        from .skill_attempt_timing import exact_outcome
        row['outcomes']=[exact_outcome(records[i]['start']['tick'],
            min((objects[oid][0]['tick'] for oid,parent in object_parents.items() if parent==i),
                default=records[i]['start']['tick']),contacts[i]) for i in valid]
    if silvia_state_contacts:
        row.update(silviaHumanWStateContactEvidence=silvia_state_contacts,
            silviaHumanWStateProof='deliverables/native-silvia-human-w-slow-contact-v1.json')
    row.update(userPolicyCancellationDiagnostics=cancellation_diagnostics,
        userPolicyMissCastCount=sum(i in combat for i in user_misses),
        userPolicyMissCastTicks=[records[i]['start']['tick'] for i in user_misses if i in combat],
        userPolicyMissAuthority='explicit-user-rule',
        userPolicyMissMeaning='No recorded contact before actual cancellation; user denominator rule, not native completion proof.',
        observedCombatCastCount=len(combat),verifiedCombatCastCount=0,
        unresolvedCombatCastCount=len(combat)-len(valid),
        unresolvedCastReasons=dict(Counter(unknown[i] for i in combat if i in unknown)),
        perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,
        evidenceChannelsUsed=sorted(set().union(*(channels[i] for i in valid))),
        combinedUseEvidence=[dict(castTick=records[i]['start']['tick'],castWireOrder=records[i]['start'].get('wireOrder'),
            actionCoverageComplete=i in action_complete,
            contacts=[dict(tick=tick,targetObjectId=target,channels=sorted(contact_sources[i][tick,target]))
                      for tick,target in sorted(contacts[i])]) for i in valid],
        closedEmittedCancelledCastCount=len(set(combat)&closed_emitted_cancellations),
        unnamedOwnedProjectileCandidateCodes=sorted(anonymous_codes),
        unnamedProjectileMappingVerified=False,
        unownedEventCount=len(uncertain),unownedEventsAssignedToCast=False,
        unownedEventEvidence=uncertain_evidence,
        liveOwnedObjectEffectContacts=live_effect_contacts,
        liveOwnedObjectEffectAttributionCount=len(live_effect_contacts),
        liveObjectEffectMappingVerified=False,
        projectileContactOnly=projectile_only,
        effectDamageOnly=effect_only,
        liveObjectEffectAssumption='Candidate effect is attributed only when recorded active candidate objects and active same-family casts identify one parent; exact attacker/collision object edges retain priority.',
        targetCountsAreLowerBounds=bool(uncertain or lower_bound_positive),
        completeTargetCountsAvailable=not (uncertain or lower_bound_positive),
        nearestCastUsed=False,fixedTimeWindowUsed=False)
    return annotate_provisional_rate(row,{**policy,'relaxedConditions':[
        'candidate-producer-completeness-with-joint-recorded-contact-evidence']})
