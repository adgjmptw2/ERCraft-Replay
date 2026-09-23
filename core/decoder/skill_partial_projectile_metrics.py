"""Opt-in per-use projectile completeness without weakening object evidence.

Objects retain exact recorded parent candidates and collision/terminal data.
Ambiguous or incomplete uses are not misses. Unowned family outcomes still
reject the sample, including outcomes hidden by an unfinished family cast.
"""
from collections import Counter, defaultdict
try:
    from .skill_attempt_timing import projectile_outcomes
    from .skill_complete_projectile_lifetimes import complete_projectile_lifetime_metric, candidate_projectile_family
    from .skill_static_effect_families import candidate_effect_family
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order, finish_lookup, event_within_cast,competing_manual_starts
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_projectile_active_end import projectile_active_end_records, collision_only_outcome
except ImportError:
    from skill_attempt_timing import projectile_outcomes
    from skill_complete_projectile_lifetimes import complete_projectile_lifetime_metric, candidate_projectile_family
    from skill_static_effect_families import candidate_effect_family
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order, finish_lookup, event_within_cast,competing_manual_starts
    from requested_skill_hit_rates import _result,_unavailable
    from skill_projectile_active_end import projectile_active_end_records, collision_only_outcome


def partial_projectile_lifetime_metric(spec,all_starts,spawns,collisions,terminals,finishes,damages,player,teams,intervals,catalog,effect_rows,projectile_owners, *, development=False,unobserved_stage_policy=None,route_inputs=None,gaps=None):
    if spec.get('skillGroup')==1012200 and any(v is None for v in (all_starts,spawns,collisions,terminals,finishes,damages,projectile_owners)):
        return _unavailable(spec,'original projectile input stream missing')
    if spec['mode'] not in {'any','shot'}:
        return _unavailable(spec,'실제 발사체 경로로 벽·부위·처치·카메라 조건을 대신하지 않음')
    # Select the clock representation before evaluating any outcome. Ordered
    # input is evaluated once; no complete-rate failure followed by a retry.
    clock_events=[*all_starts,*(f for f in finishes if f.get('playerObjectId')==player)]
    if any(command_order(e) is None for e in clock_events):
        return complete_projectile_lifetime_metric(spec,all_starts,spawns,collisions,terminals,finishes,damages,player,teams,intervals,catalog,effect_rows,projectile_owners)
    groups,codes=candidate_projectile_family(spec,catalog)
    relevant=[s for s in spawns if s['projectileCode'] in codes]
    starts=[s for s in all_starts if s['skillGroup'] in groups]
    if not relevant or not any(s['skillGroup']==spec['skillGroup'] for s in starts):
        return _unavailable(spec,'같은 스킬 계열의 실제 발사체 또는 요청 단계 시전이 없음',category='observation-incomplete',reason_code='cast-or-family-emission-missing')
    records,reason=ordered_cast_records(starts,finishes,player)
    if reason:return _unavailable(spec,reason)
    competing,reason=ordered_cast_records(competing_manual_starts(all_starts,catalog,groups,player),finishes,player)
    if reason:return _unavailable(spec,reason)
    lookup=finish_lookup(finishes,player)
    def contains(r,e):
        if r['complete']:return event_within_cast(r['start'],r['finish']['tick'],e,lookup)
        if e['tick']!=r['start']['tick']:return e['tick']>r['start']['tick']
        order=command_order(e)
        return order is None or order>=command_order(r['start'])
    unknown={i:r['reason'] for i,r in enumerate(records) if not r['complete']}
    for i,r in enumerate(records):
        if r['complete'] and any(not c['complete'] and contains(c,r['finish']) for c in competing):unknown[i]='unfinished-competing-manual-cast'
    by_use=defaultdict(list);parents={};relaxed_competitors=[]
    for s in relevant:
        oid=s['projectileObjectId']
        if oid in parents:return _unavailable(spec,'duplicate family projectile object identity')
        candidates=[i for i,r in enumerate(records) if contains(r,s)]
        if not candidates or any(not records[i]['complete'] for i in candidates):
            return _unavailable(spec,'actual family projectile lacks a unique completed emission lifetime')
        if len(candidates)>1:
            marked=[i for i in candidates if any(a['tick']==s['tick'] for a in records[i]['start'].get('linkActions',[]))]
            if len(marked)==1:candidates=marked
        parents[oid]=candidates
        for i in candidates:by_use[i].append(s)
        overlapping=[c for c in competing if contains(c,s)]
        if development and len(candidates)==1 and s.get('ownerPlayerObjectId')==player and command_order(s) is not None:
            from .skill_execution_plan import planned_family_candidates
            retained=[]
            for c in overlapping:
                family=planned_family_candidates({**spec,'skillGroup':c['start']['skillGroup']},'projectileCodes',required=False)
                if c['complete'] and family is not None and family[1] and s['projectileCode'] not in family[1]:
                    relaxed_competitors.append(dict(projectileObjectId=oid,projectileCode=s['projectileCode'],
                        parentCastTick=records[candidates[0]]['start']['tick'],
                        competingSkillGroup=c['start']['skillGroup'],competingCandidateProjectileCodes=sorted(family[1])))
                else:retained.append(c)
            overlapping=retained
        if len(candidates)!=1 or overlapping:
            for i in candidates:unknown[i]='projectile-parent-use-ambiguous'
    for s in spawns:
        if str(s['projectileCode']) not in catalog['projectileDefinitions']:
            for i,r in enumerate(records):
                if contains(r,s):unknown[i]='undefined-owned-projectile-within-cast'
    selected=[i for i,r in enumerate(records) if r['start']['skillGroup']==spec['skillGroup']]
    cancelled=set()
    for i in selected:
        if i in unknown or by_use[i] or spec['unit']=='projectile-shot':continue
        if records[i]['finish']['reason'] in set(range(1,15))|{16,17}:cancelled.add(i)
        else:unknown[i]='normal-finish-without-observed-projectile'
    ends=defaultdict(list);contacts=defaultdict(set)
    for e in terminals:ends[e['objectId']].append(e)
    for e in collisions:contacts[e['projectileObjectId']].add((e['tick'],e['targetObjectId']))
    _,effects=candidate_effect_family(spec,catalog,effect_rows)
    own_damage=set();family_damage=set()
    for d in damages:
        if d['attackerObjectId']!=player and projectile_owners.get(d['attackerObjectId'])!=player:continue
        own_damage.add((d['tick'],d['targetObjectId']))
        if d.get('effectCode') in effects and d['targetObjectId'] in teams and teams[d['targetObjectId']]!=teams[player]:family_damage.add((d['tick'],d['targetObjectId']))
    hits={};lifetimes={};complex_codes=set();invalid_objects=set()
    active_ends=projectile_active_end_records(terminals);closure_commands={};final_destroyed=set();positive_before_retirement=set()
    for s in relevant:
        oid=s['projectileObjectId'];definition=catalog['projectileDefinitions'][str(s['projectileCode'])]
        destroys={e['tick'] for e in ends[oid] if e['event']=='CmdDestroy'}
        problem=None
        active=active_ends.get(oid,{})
        # Arrival settings are not an independent producer. A recorded removal
        # ends collision processing even if it continued after arrival. Keep
        # explosion-bearing objects on their separate lifecycle contract.
        collision_closure=(definition.get('collisionEnabled') is True
            and not definition.get('isExplosion') and not definition.get('isExplosionWithoutCollision'))
        if collision_closure and active.get('complete') and active['endTick']>=s['tick']:
            end=active['endTick'];lifetimes[oid]=(s['tick'],end)
            closure_commands[oid]=active['terminalCommands']
            if any(not s['tick']<=t<=end for t,_ in contacts[oid]):problem='projectile-contact-outside-recorded-lifetime'
        elif collision_closure:
            problem='projectile-active-collision-end-incomplete-or-conflicting';lifetimes[oid]=(s['tick'],None)
        elif len(destroys)!=1 or next(iter(destroys))<s['tick']:
            problem='projectile-final-destruction-incomplete';lifetimes[oid]=(s['tick'],None)
        else:
            end=next(iter(destroys));lifetimes[oid]=(s['tick'],end)
            closure_commands[oid]=['CmdDestroy']
            if any(not s['tick']<=t<=end for t,_ in contacts[oid]):problem='projectile-contact-outside-recorded-lifetime'
        if len(destroys)==1 and next(iter(destroys))>=s['tick']:final_destroyed.add(oid)
        explosive=definition.get('isExplosion') or definition.get('isExplosionWithoutCollision')
        complex_outcome=explosive or definition.get('collisionAfterArrival') or (definition.get('lifeTimeAfterArrival') or 0)>0 or not definition.get('collisionEnabled')
        if explosive and problem is None:
            explosions={e['tick'] for e in ends[oid] if e['event']=='CmdProjectileExplosion'}
            if len(explosions)!=1 or not s['tick']<=next(iter(explosions))<=lifetimes[oid][1]:problem='projectile-explosion-incomplete'
        hits[oid]={c for c in contacts[oid] if c[1] in teams and teams[c[1]]!=teams[player]}
        if complex_outcome:
            complex_codes.add(s['projectileCode'])
            if hits[oid]-own_damage:problem='complex-projectile-contact-without-damage'
        if (development and spec['unit']=='skill-cast' and problem=='projectile-final-destruction-incomplete'
                and hits[oid] and not hits[oid]-own_damage and len(parents[oid])==1
                and active.get('complete') and active['endTick']>=s['tick']
                and all(s['tick']<=t<=active['endTick'] for t,_ in contacts[oid])):
            # Exact contact plus damage settles binary success. Preserve the
            # distinction from complete future explosion/target enumeration.
            problem=None;positive_before_retirement.add(oid)
            lifetimes[oid]=(s['tick'],active['endTick']);closure_commands[oid]=active['terminalCommands']
        if problem:
            invalid_objects.add(oid)
            for i in parents[oid]:unknown[i]=problem
    winner_misses=[];winner_blocked=[]
    if (spec.get('characterCode'),spec.get('skillGroup'),spec.get('mode'),spec.get('unit'))==(12,1012200,'any','skill-cast'):
        import json
        from pathlib import Path
        from .skill_ordered_match_end import ordered_winner_match_end
        enabled=json.loads((Path(__file__).resolve().parents[1]/'data/user-hit-rate-scope-20260912.json').read_text(encoding='utf-8'))['recordedUseMissPolicy'].get('enabled') is True
        inputs=route_inputs or {};raw=(inputs.get('summon_inputs') or {}).get('allProjectileSpawns')
        winner=ordered_winner_match_end((inputs.get('wall_inputs') or {}).get('gameTerminals'),gaps) if enabled else None
        for i in selected:
            if not enabled or unknown.get(i)!='projectile-active-collision-end-incomplete-or-conflicting':continue
            r=records[i];start=r['start'];left=command_order(start);right=command_order(winner) if winner else None;reasons=[]
            shots=by_use[i];shotids={x['projectileObjectId'] for x in shots}
            if raw is None or projectile_owners is None or gaps is None or any(g.get('count',0) for g in gaps) or any(inputs.get(k) is None for k in ('damages','collisions','projectile_terminals','spawns')):reasons.append('original-stream-completeness-unavailable')
            if not r['complete'] or r['finish'].get('reason')!=0 or left is None or right is None or not left<command_order(r['finish'])<right or not start['tick']<=r['finish']['tick']<=winner['tick']:reasons.append('normal-parent-before-actual-winner-unavailable')
            def after(e):
                at=command_order(e);tick=e.get('tick')
                return at is None or left is None or type(tick) is not int or tick>=start['tick'] or at>=left
            if len(shots)!=1:reasons.append('single-parent-projectile-unavailable')
            for shot in shots:
                oid=shot['projectileObjectId'];at=command_order(shot)
                matches=[x for x in raw or [] if x.get('projectileObjectId')==oid]
                if shot['projectileCode']!=101201 or parents[oid]!=[i] or len(matches)!=1 or matches[0].get('projectileCode')!=101201 or matches[0].get('tick')!=shot['tick'] or command_order(matches[0])!=at or (matches[0].get('ownerObjectId')!=player and (projectile_owners or {}).get(matches[0].get('ownerObjectId'))!=player):reasons.append('exact-raw-primary-parent-unavailable')
                if at is None or left is None or right is None or not left<at<right or not start['tick']<=shot['tick']<=winner['tick']:reasons.append('projectile-order-invalid')
                if ends[oid] or contacts[oid]:reasons.append('recorded-terminal-or-contact-present')
            for x in raw or []:
                if x.get('projectileCode') not in codes or x.get('projectileObjectId') in shotids or not after(x):continue
                owner=(projectile_owners or {}).get(x.get('ownerObjectId'),x.get('ownerObjectId'))
                if owner==player or owner not in teams:reasons.append('later-unassigned-family-projectile')
            def before_primary(d):
                # This route measures contact by the unique emitted projectile.
                # Both clocks must place the event strictly before that object;
                # damageIsNull is not absence evidence.
                if len(shots)!=1:return False
                shot=shots[0];at=command_order(d);spawn_order=command_order(shot)
                return (type(d.get('tick')) is int and at is not None and spawn_order is not None
                        and d['tick']<shot['tick'] and at<spawn_order)
            before_emission=[d for d in damages if after(d) and before_primary(d)]
            if any(after(d) and not before_primary(d) and ((projectile_owners or {}).get(d.get('attackerObjectId'),d.get('attackerObjectId'))==player or (projectile_owners or {}).get(d.get('attackerObjectId'),d.get('attackerObjectId')) not in teams) for d in damages):reasons.append('later-owner-or-unowned-damage')
            proof=dict(startTick=start['tick'],startOrder=left,parentFinish=r.get('finish'),projectiles=shots,winnerEnd=winner,projectileTerminalInvented=False,
                preEmissionNonProjectileDamageEvidence=[{k:d.get(k) for k in ('tick','wireOrder','attackerObjectId','targetObjectId','effectCode','damageType','damageIsNull')} for d in before_emission])
            if reasons:winner_blocked.append(dict(proof,reasons=sorted(set(reasons))));continue
            unknown.pop(i);winner_misses.append(proof)
            for oid in shotids:invalid_objects.discard(oid);closure_commands[oid]=[]
    all_hits=set().union(*(h for oid,h in hits.items() if oid not in invalid_objects))
    # A missing collision may be a delayed body/follow-up outcome from an
    # earlier use. Another object's overlapping lifetime is not a parent FK.
    # Invalid object contacts must not hide this missing family evidence.
    if family_damage-all_hits:return _unavailable(spec,'같은 스킬 계열의 실제 피해 일부가 발사체 충돌 기록에 없어 전체 결과 미확정')
    valid_objects={oid for oid,p in parents.items() if oid not in invalid_objects and len(p)==1 and p[0] not in unknown}
    if complex_codes and not effects:
        return _unavailable(spec,'복합 발사체의 후보 FX 매핑이 없음',reason_code='complex-projectile-effect-mapping-missing')
    unobserved_code=any(not any(s['projectileCode']==code and s['projectileObjectId'] in valid_objects and hits[s['projectileObjectId']] for s in relevant) for code in complex_codes)
    unobserved_stage=not any(hits[oid] for oid in valid_objects if parents[oid][0] in selected)
    if unobserved_code and not unobserved_stage_policy:
        return _unavailable(spec,'지속·폭발·자동 추적 코드별 적 타격과 전용 FX의 완전성 검증 표본 부족')
    if unobserved_stage and not unobserved_stage_policy:
        return _unavailable(spec,'해당 단계 자체의 실제 적 발사체 타격 표본 없음')
    combat=[i for i in selected if any(l<=records[i]['start']['tick']<r for l,r in intervals)]
    valid=[i for i in combat if i not in unknown]
    if combat and not valid:return {**_unavailable(spec,'no complete combat projectile use'),'unresolvedCombatCastCount':len(combat)}
    whole_complete=not winner_misses and not unknown and all(r['complete'] for r in competing) and not relaxed_competitors
    if whole_complete:
        identity_order={wire:i for i,wire in enumerate(dict.fromkeys(s['skillIdCode'] for s in starts))}
        valid.sort(key=lambda i:(identity_order[records[i]['start']['skillIdCode']],command_order(records[i]['start'])))
    objects=[s for i in valid for s in by_use[i]]
    cast_hits=[set().union(*(hits[s['projectileObjectId']] for s in by_use[i])) for i in valid]
    shot_hits=[hits[s['projectileObjectId']] for s in objects]
    row=_result(spec,shot_hits if spec['unit']=='projectile-shot' else cast_hits,
        'complete-exclusive-cast-projectile-lifetimes-and-contact-outcomes' if whole_complete else 'complete-projectile-outcomes-with-explicit-unknown-uses')
    row['outcomes']=projectile_outcomes(spec['unit'], [
        (records[i]['start']['tick'], [(s['tick'], hits[s['projectileObjectId']])
                                     for s in by_use[i]], i in cancelled) for i in valid])
    def finalized(result):
        if winner_misses or winner_blocked:
            result.update(userPolicyWinnerMissEvidence=winner_misses,userPolicyWinnerBlockedEvidence=winner_blocked)
        if winner_misses:
            from .skill_development_cancellation import annotate_provisional
            annotate_provisional(result,"Explicit user policy: no recorded contact before actual winner end; no projectile terminal is invented.")
            result.update(verifiedCombatCastCount=0,verifiedCompletionCredit=False,fullRequestedMetricComplete=False,collisionActivityEndVerified=False)
        provisional_objects=positive_before_retirement.intersection(s['projectileObjectId'] for s in objects)
        if provisional_objects:
            from .skill_development_cancellation import annotate_provisional
            result=annotate_provisional(result,'Unique projectile enemy collision and same-tick owner damage prove binary hit before final visual destruction; target totals are lower bounds.')
            result.update(positiveBeforeRetirementProjectileCount=len(provisional_objects),targetCountsAreLowerBounds=True,
                completeTargetCountsAvailable=False,perUseCompletenessTracked=True,
                observedCombatCastCount=len(combat),unresolvedCombatCastCount=len(combat)-len(valid),
                incompleteUsesCountedAsMisses=False)
        if unobserved_stage_policy and (unobserved_code or unobserved_stage):
            from .skill_development_effect_metrics import annotate_provisional_rate
            result.update(perUseCompletenessTracked=True,observedCombatCastCount=len(combat),
                          unresolvedCombatCastCount=len(combat)-len(valid),
                          incompleteUsesCountedAsMisses=False)
            result=annotate_provisional_rate(result,unobserved_stage_policy)
            result['perMatchPositiveSampleRequired']=False
        return result
    if whole_complete:
        row.update(actualProjectileCount=len(objects),enemyHitProjectileCount=sum(bool(h) for h in shot_hits),
            projectileContactEventCount=sum(len(h) for h in shot_hits),cancelledBeforeAttackCount=len(cancelled.intersection(valid)),
            exactProjectileCodes=sorted({s['projectileCode'] for s in relevant}),
            finalDestructionVerified=all(s['projectileObjectId'] in final_destroyed for s in relevant),
            collisionActivityEndVerified=True,outcomeClosureCommands=sorted({c for commands in closure_commands.values() for c in commands}),
            fixedShotCountUsed=False,fixedFlightOrChannelDurationUsed=False,numericSkillStateCodeJoinUsed=False)
        return finalized(row)
    row.update(actualProjectileCount=len(objects),enemyHitProjectileCount=sum(bool(h) for h in shot_hits),
        projectileContactEventCount=sum(len(h) for h in shot_hits),cancelledBeforeAttackCount=len(cancelled.intersection(valid)),
        exactProjectileCodes=sorted({s['projectileCode'] for s in relevant}),
        finalDestructionVerified=all(s['projectileObjectId'] in final_destroyed for s in objects),
        collisionActivityEndVerified=True,outcomeClosureCommands=sorted({c for s in objects for c in closure_commands[s['projectileObjectId']]}),
        fixedShotCountUsed=False,fixedFlightOrChannelDurationUsed=False,numericSkillStateCodeJoinUsed=False,
        perUseCompletenessTracked=True,observedCombatCastCount=len(combat),verifiedCombatCastCount=len(valid),
        unresolvedCombatCastCount=len(combat)-len(valid),unresolvedAllCastCount=sum(i in unknown for i in selected),
        unresolvedCastReasons=dict(Counter(unknown[i] for i in combat if i in unknown)),incompleteUsesCountedAsMisses=False,
        observedLinkedProjectileCount=sum(len(by_use[i]) for i in combat),
        strictWholeSampleReason=next(iter(unknown.values()),'incomplete competing cast or provisional ownership'),unownedFamilyHitsDiscarded=False,
        interpretation='검증된 사용의 실제 발사체·충돌만 계산; 사용별 미확정 분리. 발사 전 게임 취소는 사용 실패, 실제 발사 수에는 미발사 탄환을 추가하지 않음.')
    if relaxed_competitors:
        from .skill_development_cancellation import annotate_provisional
        row=annotate_provisional(row,'A unique recorded family cast owns this projectile provisionally when overlapping completed skills have nonempty, disjoint candidate projectile codes. Candidate mappings may be incomplete.')
        row.update(provisionalProjectileParentEvidence=relaxed_competitors,nearestCastUsed=False)
    return finalized(row)


def confirmed_projectile_cast_evidence(spec, all_starts, spawns, collisions, terminals,
                                       finishes, damages, player, teams, intervals,
                                       catalog, effect_rows, projectile_owners):
    """Retain proven existential hits without claiming a complete hit rate.

    One uniquely owned enemy contact proves 'at least one hit'. Destruction,
    all emitted objects and all follow-up targets are needed for negatives or
    complete target totals, not for this positive proposition. Every other use
    remains unknown here. This evidence must never enter rate aggregation.
    """
    if ((spec.get('mode'), spec.get('unit')) != ('any', 'skill-cast') or player not in teams
            or spec.get('targetCohort')):
        return None
    groups, codes = candidate_projectile_family(spec, catalog)
    if not groups or not codes:
        return None
    starts = [s for s in all_starts if s.get('skillGroup') in groups]
    records, reason = ordered_cast_records(starts, finishes, player)
    if reason:
        return None
    selected = [i for i, r in enumerate(records) if r['start']['skillGroup'] == spec['skillGroup']
                and any(a <= r['start']['tick'] < b for a, b in intervals)]
    if not selected:
        return None
    competing,reason=ordered_cast_records(competing_manual_starts(all_starts,catalog,groups,player),finishes,player)
    if reason:return None
    lookup = finish_lookup(finishes, player)

    def possible(record, event):
        if record['complete']:
            return event_within_cast(record['start'], record['finish']['tick'], event, lookup)
        at, start = command_order(event), command_order(record['start'])
        return event['tick'] > record['start']['tick'] or (
            event['tick'] == record['start']['tick'] and (at is None or at >= start))

    by_object = defaultdict(list)
    for spawn in spawns:
        by_object[spawn['projectileObjectId']].append(spawn)
    by_collision, by_terminal = defaultdict(list), defaultdict(list)
    active_ends = projectile_active_end_records(terminals)
    for event in collisions:
        by_collision[event['projectileObjectId']].append(event)
    for event in terminals:
        by_terminal[event['objectId']].append(event)
    _, effects = candidate_effect_family(spec, catalog, effect_rows)
    own_damage = {(d['tick'], d['targetObjectId']) for d in damages
                  if (d['attackerObjectId'] == player or projectile_owners.get(d['attackerObjectId']) == player)
                  and d.get('effectCode') in effects}
    hits = defaultdict(set)
    for oid, objects in by_object.items():
        if not any(s['projectileCode'] in codes for s in objects):
            continue
        if len(objects) != 1:
            return None  # A reused/duplicate family object cannot certify one parent.
        spawn = objects[0]
        if (spawn['projectileCode'] not in codes or spawn.get('ownerPlayerObjectId') != player
                or projectile_owners.get(oid) != player or command_order(spawn) is None):
            return None
        parents = [i for i, r in enumerate(records) if possible(r, spawn)]
        # Preserve the existing whole-family emission guard. An orphan spawn
        # demonstrates that FinishSkill may not bound emission; then even an
        # in-window spawn could be an earlier use's delayed object. Positive
        # contacts do not repair an uncertain parent identity.
        if not parents or any(not records[i]['complete'] for i in parents):
            return None
        if len(parents) != 1 or any(possible(r, spawn) for r in competing):
            continue
        parent = parents[0]
        if parent not in selected or not records[parent]['complete'] or records[parent]['finish'].get('reason') != 0:
            continue
        ends = {e['tick'] for e in by_terminal[oid] if e['event'] == 'CmdDestroy'}
        if len(ends) > 1 or (ends and min(ends) < spawn['tick']):
            continue
        contacts = by_collision[oid]
        if any(e['tick'] < spawn['tick'] or (ends and e['tick'] > min(ends)) for e in contacts):
            continue  # Contradictory lifetime evidence is not a positive proof.
        definition = catalog['projectileDefinitions'][str(spawn['projectileCode'])]
        active = active_ends.get(oid)
        if collision_only_outcome(definition) and active is not None:
            if (not active['complete'] or active['endTick'] < spawn['tick']
                    or any(e['tick'] > active['endTick'] for e in contacts)):
                continue
        complex_outcome = (not definition.get('collisionEnabled') or definition.get('isExplosion')
                           or definition.get('isExplosionWithoutCollision') or definition.get('collisionAfterArrival')
                           or (definition.get('lifeTimeAfterArrival') or 0) > 0)
        for event in contacts:
            target = event['targetObjectId']
            if target not in teams or teams[target] == teams[player]:
                continue
            if event['tick'] == spawn['tick'] and (command_order(event) is None or command_order(event) < command_order(spawn)):
                continue
            contact = (event['tick'], target)
            if complex_outcome and contact not in own_damage:
                continue
            hits[parent].add(contact)
    if not hits:
        return None
    # No object ids or target identities are required by the public evidence.
    outcomes = [dict(castTick=records[i]['start']['tick'],
                     castWireOrder=records[i]['start']['wireOrder'],
                     firstConfirmedEnemyContactTick=min(t for t, _ in hits[i]))
                for i in selected if hits.get(i)]
    return dict(status='confirmed-positive-evidence-only', confirmedHitCastCount=len(outcomes),
                observedCombatCastCount=len(selected), unknownCastCount=len(selected)-len(outcomes),
                confirmedMissCastCount=0, hitRate=None, completeTargetCountsAvailable=False,
                outcomes=outcomes, source='unique-owned-projectile-parent-and-recorded-enemy-contact',
                unknownUsesCountedAsMisses=False, aggregateRateEligible=False)


def promote_complete_binary_cast_result(row):
    """All observed uses positively hit: binary rate is complete, target totals aren't."""
    evidence = row.get('confirmedCastHitEvidence')
    if (row.get('status') != 'unresolved-evidence' or not evidence
            or row.get('skillGroup') == 1043500  # Celine additionally requires the detonation partition.
            or evidence['unknownCastCount'] != 0 or evidence['confirmedHitCastCount'] <= 0
            or evidence['confirmedHitCastCount'] != evidence['observedCombatCastCount']
            or row.get('combatCastCount') != evidence['observedCombatCastCount']):
        return row
    n = evidence['confirmedHitCastCount']
    return dict(row, status='calculable-observed', attemptCount=n, hitCount=n, hitRate=1.0,
                method='every-observed-combat-cast-has-confirmed-owned-projectile-hit',
                priorWholeMetricReason=row.get('reason'), reason=None,
                outcomes=[[u['castTick'], 1, u['castTick'], u['firstConfirmedEnemyContactTick']] for u in evidence['outcomes']],
                perUseCompletenessTracked=True, observedCombatCastCount=n, verifiedCombatCastCount=n,
                unresolvedCombatCastCount=0, incompleteUsesCountedAsMisses=False,
                fullRequestedMetricComplete=row.get('reportMultiTarget') is False,
                binaryCastSuccessComplete=True, completeTargetCountsAvailable=False,
                multiTargetAttemptCount=None, multiTargetAttemptRate=None,
                distinctEnemyTargetsSummedAcrossAttempts=None, distinctEnemyTargetsPerAttempt=None,
                distinctEnemyTargetFirstHitTicksPerAttempt=None, meanDistinctEnemyTargetsPerAttempt=None,
                deduplicatedEnemyContactEventCount=None,
                outcomeTimingScope='first-confirmed-enemy-contact; no inferred earlier hit',
                interpretation='관측된 모든 교전 시전의 적중은 확정. 후속 효과의 전체 대상 수는 미확정이며 별도 집계하지 않음.')
