"""Static-confirmed Barbara explosion child uses, with exact summon ownership."""
from collections import Counter,defaultdict
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_summon_ownership import live_summon_owner_resolver
    from .skill_wire_order import command_order
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_summon_ownership import live_summon_owner_resolver
    from skill_wire_order import command_order

ROUTES={1026420:('BarbaraActive3_ProjectileExplosion',356,1102,'Barbara_Skill03_Explosion',{1026401}),
        1026430:('BarbaraActive3_ReinforceProjectileExplosion',358,1103,'Barbara_Skill03_Reinforce_Explosion',{1026402,1026407})}


def barbara_child_metric(spec,starts,finishes,summons,terminals,damages,player,teams,intervals,catalog,skill_rows,summon_rows,effect_rows,gaps):
    diag=dict(observedOwnedChildCastCount=0,observedCombatChildCastCount=0,childCastsCountedAsIndependentUses=True,parentUsesInferred=False)
    def fail(reason):return {**_unavailable(spec,reason),**diag}
    if spec.get('skillGroup') not in ROUTES or spec.get('mode')!='any':return fail('unsupported static explosion child')
    name,wire,code,prefab,effects=ROUTES[spec['skillGroup']]
    if starts is None or gaps is None or any(g.get('count',0) and g.get('packetName') in {'CmdSpawn','SummonSnapshot:11','CmdStartSkill','CmdFinishSkill','CmdDamage'} for g in gaps):return fail('complete Barbara explosion child streams required')
    sr=[s for s in summon_rows if s.get('code')==code]
    if len(sr)!=1 or sr[0].get('useAttackerType')!='Owner' or sr[0].get('prefabPath')!=prefab or catalog['skillGroups'].get(str(spec['skillGroup']),{}).get('skillId')!=name:
        return fail('static child/summon/attacker-policy identity mismatch')
    if not effects<={r.get('code') for r in effect_rows}:return fail('exact explosion effects absent in gameDb')
    codes={s['code'] for s in skill_rows if s.get('group')==spec['skillGroup']}
    resolve=live_summon_owner_resolver(summons,terminals,set(teams));records=[];by_actor=defaultdict(list)
    for s in starts:
        if s.get('skillIdCode')!=wire:continue
        owner,path,reason=resolve(s['sourceObjectId'],s['tick'])
        if reason:return fail('explosion ownership unavailable: '+reason)
        if owner!=player:continue
        if path!=[code] or s['skillCode'] not in codes:return fail('explosion child wire/summon/code mismatch')
        by_actor[s['sourceObjectId']].append(s)
    for actor,ss in by_actor.items():
        fs=[f for f in finishes if f['playerObjectId']==actor and f['skillIdCode']==wire]
        for s in ss:
            unknown=set();end=fs[0] if len(ss)==len(fs)==1 else None
            if end is None or end['tick']<s['tick'] or end.get('reason')!=0:unknown.add('incomplete-or-ambiguous-explosion-use')
            if end and command_order(s) and command_order(end) and command_order(s)>command_order(end):unknown.add('invalid-explosion-command-order')
            records.append(dict(start=s,finish=end,unknown=unknown,contacts=set()))
    diag['observedOwnedChildCastCount']=len(records)
    def within(r,d):
        s=r['start'];f=r['finish']
        if d['tick']<s['tick'] or f and d['tick']>f['tick']:return False
        a,b,x=command_order(s),command_order(f) if f else None,command_order(d)
        if x is not None and a is not None and d['tick']==s['tick'] and x<a:return False
        if x is not None and b is not None and d['tick']==f['tick'] and x>b:return False
        return True
    for d in damages:
        if d.get('effectCode') not in effects or d.get('attackerObjectId')!=player:continue
        target=d.get('targetObjectId')
        if target not in teams or teams[target]==teams.get(player):continue
        matches=[r for r in records if within(r,d)]
        if len(matches)!=1:
            for r in matches:r['unknown'].add('simultaneous-owner-explosion-attribution')
            if not matches:return fail('owned explosion damage outside child lifetimes')
            continue
        matches[0]['contacts'].add((d['tick'],target))
    combat=[r for r in records if any(l<=r['start']['tick']<h for l,h in intervals)];valid=[r for r in combat if not r['unknown']]
    diag.update(observedCombatChildCastCount=len(combat),verifiedCombatCastCount=len(valid),unresolvedCombatCastCount=len(combat)-len(valid),
        unresolvedCastReasons=dict(Counter(reason for r in combat for reason in r['unknown'])),perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False)
    if not valid and combat:return fail('no complete unambiguous owned explosion uses')
    row=_result(spec,[r['contacts'] for r in valid],'static-Barbara-owned-explosion-child-exact-finish-and-owner-damage',cast_ticks=[r['start']['tick'] for r in valid])
    row.update(diag,exactSummonCode=code,exactDamageEffectCodes=sorted(effects),
        denominatorMeaning='explicit owned explosion child skill uses; repeated ticks count as one use',
        outcomeScope='complete unambiguous owned child uses; incomplete or overlapping uses unknown',
        evidenceReview='deliverables/barbara-explosion-static-proof-v1.json')
    return row

def barbara_central_stun_metric(spec,starts,finishes,spawns,summons,terminals,child_starts,damages,states,player,teams,intervals,catalog,skill_rows,state_rows,state_groups,gaps,game_terminals=None):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
        from .skill_wire_order import event_within_cast,finish_lookup
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
        from skill_wire_order import event_within_cast,finish_lookup
    def fail(reason):return _unavailable(spec,reason)
    if spec.get('characterCode')!=26 or spec.get('skillGroup')!=1026400 or spec.get('mode') not in {'any','central-stun'} or spec.get('unit')!='skill-cast':return fail('unsupported Barbara parent explosion metric')
    central=spec['mode']=='central-stun'
    required={'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdProjectileArrived','CmdDamage','SummonSnapshot:11'}
    if central:required.update({'CmdAddState','CmdAddStateExtended'})
    if any(x is None for x in (starts,finishes,spawns,summons,terminals,child_starts,damages,states,gaps)) or any(g.get('count',0) and g.get('packetName') in required for g in gaps):return fail('complete parent/projectile/explosion/stun streams required')
    if catalog['skillGroups'].get('1026400',{}).get('skillId')!='BarbaraActive3_MagneticForce':return fail('Barbara base E identity mismatch')
    sg=[s for s in state_groups if s.get('group')==1026400 and s.get('stateType')=='Stun'];sc={s['code'] for s in state_rows if s.get('group')==1026400}
    if central and (len(sg)!=1 or sc!=set(range(1026401,1026406))):return fail('exact base E central Stun definition mismatch')
    selected=[s for s in starts if s.get('playerObjectId')==player and s.get('skillGroup')==1026400]
    if any(s['skillIdCode']!=355 for s in selected):return fail('base E wire skill mismatch')
    lives,reason=exact_cast_lifetimes(selected,finishes,player)
    if reason:return fail(reason)
    lookup=finish_lookup(finishes,player);by_cast=defaultdict(list);by_id={};unknown=defaultdict(set)
    for s in spawns:
        if s.get('ownerObjectId')!=player or s.get('projectileCode')!=102641:continue
        matches=[i for i,(start,end) in enumerate(lives) if event_within_cast(start,end,s,lookup)]
        if len(matches)!=1:return fail('base E projectile has no unique exact parent cast')
        i=matches[0];by_cast[i].append(s);by_id[s['projectileObjectId']]=i
    arrivals=defaultdict(set)
    for t in terminals:
        if t.get('event')=='CmdProjectileArrived' and t['objectId'] in by_id:arrivals[t['tick']].add(t['objectId'])
    children=defaultdict(list)
    for s in summons:
        if s.get('ownerObjectId')==player and s.get('summonCode')==1102 and s.get('identityVerifiedAgainstGameDb') is True:children[s['tick']].append(s)
    child_by_tick={};linked=set()
    for tick,oids in arrivals.items():
        parents={by_id[o] for o in oids}
        if len(oids)!=1 or len(children[tick])!=1:
            for i in parents:unknown[i].add('arrived-projectile-to-explosion-ambiguous')
            continue
        i=next(iter(parents));child=children[tick][0];cid=child['objectId']
        ss=[s for s in child_starts if s.get('sourceObjectId')==cid and s.get('skillIdCode')==356]
        fs=[s for s in finishes if s.get('playerObjectId')==cid and s.get('skillIdCode')==356]
        if len(ss)!=1 or len(fs)!=1 or ss[0]['tick']!=tick or fs[0]['tick']!=tick or fs[0].get('reason')!=0:
            unknown[i].add('normal-explosion-did-not-complete-on-arrival-frame');continue
        child_by_tick[tick]=i;linked.add(i)
    for i in range(len(lives)):
        if len(by_cast[i])!=1 or i not in linked:unknown[i].add('missing-complete-projectile-and-explosion')
    contacts=[set() for _ in lives]
    evidence={(d['tick'],d['targetObjectId']) for d in damages if d.get('attackerObjectId')==player and d.get('effectCode')==1026401}
    if not central:
        for tick,target in evidence:
            if target not in teams or teams[target]==teams[player]:continue
            i=child_by_tick.get(tick)
            if i is None:return fail('base E damage has no exact parent projectile arrival and owned explosion')
            contacts[i].add((tick,target))
    for s in states if central else []:
        if s.get('event')!='add' or s.get('stateCode') not in sc or s.get('casterObjectId')!=player:continue
        target=s.get('targetObjectId')
        if target not in teams or teams[target]==teams[player]:continue
        i=child_by_tick.get(s['tick'])
        if i is None:return fail('central Stun has no exact parent explosion frame')
        if (s['tick'],target) not in evidence:unknown[i].add('stun-without-same-event-explosion-damage');continue
        contacts[i].add((s['tick'],target))
    from .skill_partial_cast_lifetimes import ordered_cast_records,user_cancelled_no_recorded_contact_indices
    ordered,order_reason=ordered_cast_records(selected,finishes,player,allow_same_tick_finishes=True)
    # Match the old lifetime indices exactly before applying any new policy.
    record_map={command_order(r['start']):r for r in ordered or []}
    policy_records=[];identity_ok=not order_reason and len(record_map)==len(lives)
    for start,end in lives:
        record=record_map.get(command_order(start))
        if (command_order(start) is None or not record or record['start']!=start
            or record.get('finish') is None or record['finish']['tick']!=end):identity_ok=False
        policy_records.append(record)
    linked_children={c['objectId'] for tick,cs in children.items() if tick in child_by_tick for c in cs}
    owner_children={c['objectId'] for c in summons if c.get('ownerObjectId')==player}
    unlinked=[]
    unlinked.extend(d for d in damages if d.get('attackerObjectId')==player and d.get('effectCode')==1026401 and d['tick'] not in child_by_tick)
    unlinked.extend(x for x in states or [] if x.get('casterObjectId')==player and (x.get('stateCode') in sc or x.get('stateGroup')==1026400) and x['tick'] not in child_by_tick)
    unlinked.extend(c for c in summons if c.get('ownerObjectId') in {None,player} and c.get('summonCode')==1102 and c['objectId'] not in linked_children)
    unlinked.extend(c for c in child_starts if c.get('skillIdCode')==356 and c.get('sourceObjectId') in owner_children and c.get('sourceObjectId') not in linked_children)
    unlinked.extend(x for x in spawns if x.get('projectileCode')==102641 and x.get('ownerObjectId') is None)
    pending=set();blocked={}
    for i,(start,end) in enumerate(lives):
        if not unknown[i]:continue
        reasons=[]
        if not identity_ok:reasons.append('ordered-parent-lifetime-identity-unavailable')
        if by_cast[i]:reasons.append('recorded-projectile-emission-requires-existing-arrival-proof')
        if states is None:reasons.append('state-stream-unavailable')
        if any(command_order(e) is None or command_order(start) is None or command_order(e)>=command_order(start) for e in unlinked):reasons.append('unlinked-damage-child-or-state')
        if reasons:pending.add(i)
        blocked[i]=reasons
    eligible={i:next(iter(reasons)) for i,reasons in unknown.items() if reasons=={'missing-complete-projectile-and-explosion'}}
    admitted=user_cancelled_no_recorded_contact_indices(policy_records,contacts,eligible,
        cancellation_reason='missing-complete-projectile-and-explosion',pending_continuations=pending,gaps=gaps) if identity_ok else []
    import json
    from pathlib import Path
    from .skill_ordered_match_end import ordered_winner_match_end
    enabled=json.loads((Path(__file__).resolve().parents[1]/'data/user-hit-rate-scope-20260912.json').read_text(encoding='utf-8'))['recordedUseMissPolicy'].get('enabled') is True
    winner=ordered_winner_match_end(game_terminals,gaps) if enabled else None
    winner_admitted=[]
    if winner and identity_ok and not any(g.get('count',0) for g in gaps):
        for i in eligible:
            r=policy_records[i];f=r['finish'];shots=by_cast[i]
            if contacts[i] or len(shots)!=1 or set(blocked.get(i,[]))-{'recorded-projectile-emission-requires-existing-arrival-proof'}:continue
            shot=shots[0];at=command_order(shot);left=command_order(r['start']);end=command_order(f);right=command_order(winner)
            if not r['complete'] or f.get('reason') not in set(range(15))|{16,17}:continue
            if at is None or left is None or end is None or right is None or not left<end<=right or not left<=at<right:continue
            if not r['start']['tick']<=shot['tick']<=winner['tick'] or f['tick']>winner['tick']:continue
            if any(t.get('objectId')==shot['projectileObjectId'] for t in terminals):continue
            winner_admitted.append(i)
    admitted=sorted(set(admitted)|set(winner_admitted))
    def parent_proof(i):
        start=lives[i][0];r=policy_records[i];f=r.get('finish') if r else None
        return dict(startTick=start['tick'],startOrder=command_order(start),
            finishTick=f['tick'] if f else lives[i][1],finishOrder=command_order(f) if f else None,
            finishReason=f.get('reason') if f else None,emittedProjectileIds=[x['projectileObjectId'] for x in by_cast[i]],
            emittedProjectiles=[{k:x[k] for k in ('projectileObjectId','projectileCode','ownerObjectId','tick','wireOrder','wireCategory') if k in x} for x in by_cast[i]])
    policy_misses=[dict(parent_proof(i),policyKind='actual-winner-before-projectile-arrival' if i in winner_admitted else 'actual-cancel-before-projectile-emission',
        winnerEnd=winner if i in winner_admitted else None,projectileFinishInvented=False) for i in admitted]
    policy_blocked=[dict(parent_proof(i),reasons=sorted(set(blocked.get(i,[]))|unknown[i])) for i in unknown if unknown[i] and i not in admitted]
    for i in admitted:unknown[i].clear()
    combat=[i for i,(s,end) in enumerate(lives) if any(l<=s['tick']<h for l,h in intervals)];valid=[i for i in combat if not unknown[i]]
    # Diagnostics mirror the evaluated gates; they never supply an outcome.
    unresolved_evidence=[]
    for i in combat:
        if not unknown[i]:continue
        start=lives[i][0];stages=[];arrival_evidence=[]
        if len(by_cast[i])!=1:stages.append('projectile-emission-count-not-one')
        for shot in by_cast[i]:
            oid=shot['projectileObjectId']
            arrival_ticks=sorted(tick for tick,oids in arrivals.items() if oid in oids)
            if not arrival_ticks:stages.append('projectile-arrival-missing')
            for tick in arrival_ticks:
                oids=arrivals[tick];candidates=children[tick];child_evidence=[]
                if len(oids)!=1:stages.append('arrival-projectile-count-not-one')
                if len(candidates)!=1:stages.append('owned-explosion-child-count-not-one')
                for child in candidates:
                    cid=child['objectId']
                    ss=[s for s in child_starts if s.get('sourceObjectId')==cid and s.get('skillIdCode')==356]
                    fs=[s for s in finishes if s.get('playerObjectId')==cid and s.get('skillIdCode')==356]
                    if len(oids)==len(candidates)==1:
                        if len(ss)!=1:stages.append('explosion-start-count-not-one')
                        if len(fs)!=1:stages.append('explosion-finish-count-not-one')
                        if len(ss)==len(fs)==1:
                            if ss[0]['tick']!=tick:stages.append('explosion-start-not-arrival-frame')
                            if fs[0]['tick']!=tick:stages.append('explosion-finish-not-arrival-frame')
                            if fs[0].get('reason')!=0:stages.append('explosion-finish-not-normal')
                    child_evidence.append(dict(objectId=cid,starts=ss,finishes=fs))
                arrival_evidence.append(dict(projectileObjectId=oid,tick=tick,
                    sameFrameProjectileIds=sorted(oids),ownedExplosionChildren=child_evidence))
        unresolved_evidence.append(dict(startTick=start['tick'],startOrder=command_order(start),
            emittedProjectileIds=[s['projectileObjectId'] for s in by_cast[i]],
            reasons=sorted(unknown[i]),failedStages=sorted(set(stages)),
            explosionLinked=i in linked,arrivals=arrival_evidence))
    method='static-Barbara-arrival-owned-explosion-and-central-Stun' if central else 'static-Barbara-parent-arrival-owned-explosion-damage'
    r=_result(spec,[contacts[i] for i in valid],method,cast_ticks=[lives[i][0]['tick'] for i in valid]) if valid or not combat else fail('no complete parent E explosion uses')
    r.update(userPolicyMissEvidence=policy_misses,userPolicyBlockedEvidence=policy_blocked,
      userPolicyMissCastCount=sum(i in combat for i in admitted),userPolicyMissCastTicks=[lives[i][0]['tick'] for i in admitted if i in combat],
      userPolicyMissAuthority='explicit-user-rule',
      observedCombatCastCount=len(combat),verifiedCombatCastCount=sum(i not in admitted for i in valid),unresolvedCombatCastCount=len(combat)-len(valid),unresolvedCastReasons=dict(Counter(x for i in combat for x in unknown[i])),
      unresolvedCastTicks=[lives[i][0]['tick'] for i in combat if unknown[i]],
      barbaraParentLinkDiagnostics=unresolved_evidence,
      perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,geometryInferred=False,missingStunUsedAsOuter=False,
      measuredOutcome='actual central Stun application, not inferred inner damage or stun duration' if central else 'actual base E enemy damage from its owned explosion; no region inferred',
      evidenceReview='deliverables/barbara-central-stun-static-proof-v1.json')
    if admitted:r.update(verifiedCompletionCredit=False,fullRequestedMetricComplete=False)
    return r
