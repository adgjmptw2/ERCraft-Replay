"""Sua copied Odyssey: static projectile/effect route, with partial region evidence."""
from collections import Counter, defaultdict
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order as order
    from .skill_attempt_timing import exact_outcome
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order as order
    from skill_attempt_timing import exact_outcome


def sua_rq_execution(spec, starts, finishes, player, teams, intervals, catalog, inputs, *, include_details=True):
    from .skill_sua_odyssey_profiles import PROFILES
    profile=PROFILES.get(spec.get('skillGroup'))
    if profile is None:return _unavailable(spec,'unsupported Sua Odyssey profile')
    starts=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==spec['skillGroup']]
    combat=lambda e:any(a<=e['tick']<b for a,b in intervals)
    diag=dict(observedCastCount=len(starts),observedCombatCastCount=sum(map(combat,starts)))
    fail=lambda why:{**_unavailable(spec,why),**diag}
    if (spec['characterCode'],spec['skillGroup'],spec['mode'],spec['unit'])!=(28,spec['skillGroup'],'any','skill-cast'):
        return fail('unsupported Sua R-Q scope')
    if player not in teams or any(inputs.get(k) is None for k in ('allProjectileSpawns','terminals','damages','states','gaps')):
        return fail('missing Sua R-Q command stream')
    if catalog.get('skillGroups',{}).get(str(spec['skillGroup']),{}).get('skillId')!=profile['skill_id'] or any(s['skillIdCode']!=profile['wire_id'] for s in starts):
        return fail('pinned Sua R-Q identity mismatch')
    needed={'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdSpawnBatch','CmdDamage','CmdDestroyDelayStart'}
    if any(g.get('count',0) and (g.get('packetName') in needed or str(g.get('packetName','')).startswith('ProjectileSnapshot:')) for g in inputs['gaps']):
        return fail('incomplete Sua R-Q required commands')
    records,why=ordered_cast_records(starts,finishes,player,allow_same_tick_finishes=True)
    if why:return fail(why)
    shots=[s for s in inputs['allProjectileSpawns'] if s['ownerObjectId']==player and s['projectileCode']==profile['projectile']]
    if len({s['projectileObjectId'] for s in shots})!=len(shots):return fail('repeated Sua R-Q projectile identity')
    # Cache views may repeat the same event with supplemental fields.
    ends=defaultdict(dict)
    for t in inputs['terminals']:
        if t['event']!='CmdDestroyDelayStart':continue
        key=t['tick'];old=ends[t['objectId']].get(key)
        if old and any(k in old and old[k]!=v for k,v in t.items()):return fail('conflicting projectile terminal views')
        ends[t['objectId']][key]={**(old or {}),**t}
    unknown={};uses=[];assigned=set();cancelled=[]
    for i,r in enumerate(records):
        s,f=r['start'],r['finish']
        ps=[p for p in shots if order(p) is not None and order(s)<order(p) and (f is None or order(p)<order(f))]
        assigned.update(p['projectileObjectId'] for p in ps)
        if not ps and r['complete'] and f['reason']==3:cancelled.append(i);continue
        if len(ps)!=1 or ps[0]['tick']!=s['tick']:
            unknown[i]='missing unique synchronous R-Q projectile';continue
        p=ps[0];ee=list(ends[p['projectileObjectId']].values())
        if len(ee)!=1 or ee[0]['tick']<p['tick']:
            unknown[i]='R-Q attack callback is not closed';continue
        uses.append(dict(index=i,start=s,projectile=p,end=ee[0],damages=[],marking=[],states=[]))
    if assigned!={p['projectileObjectId'] for p in shots}:return fail('orphan Sua R-Q projectile')
    # The static death callback runs the whole range query synchronously.
    # Dedicated R effects are attached only to one actual projectile callback
    # in that frame. Neither a flight-time estimate nor parent finish is used.
    at_tick=defaultdict(list)
    for u in uses:at_tick[u['end']['tick']].append(u)
    for us in at_tick.values():
        if len(us)!=1:
            for u in us:unknown[u['index']]='multiple R-Q callbacks in one frame'
    for u in uses:
        if any(order(r['start'])<=order(u['start']) for i,r in enumerate(records) if i in unknown):
            unknown[u['index']]='earlier unresolved R-Q producer may overlap'
    for d in inputs['damages']:
        if d['attackerObjectId']!=player or d['effectCode'] not in (profile['primary'],profile['bookmark']):continue
        us=at_tick.get(d['tick'],[])
        if len(us)!=1:
            if not unknown:return fail('dedicated R-Q effect outside one projectile callback')
            continue
        u=us[0]
        if d.get('damageType')!=2 or order(d) is None:
            unknown[u['index']]='R-Q damage discriminator/order unavailable';continue
        u['marking' if d['effectCode']==profile['bookmark'] else 'damages'].append(d)
    for u in uses:
        base=[d['targetObjectId'] for d in u['damages']]
        extra=[d['targetObjectId'] for d in u['marking']]
        if len(set(base))!=len(base) or len(set(extra))!=len(extra) or not set(extra)<=set(base):
            unknown[u['index']]='R-Q target/marking damage invariant differs'
    policy_uses=[];policy_blocked=[]
    if spec['skillGroup']==1028200:
        import json
        from pathlib import Path
        from .skill_ordered_match_end import ordered_winner_match_end
        enabled=json.loads((Path(__file__).resolve().parents[1]/'data/user-hit-rate-scope-20260912.json').read_text(encoding='utf-8'))['recordedUseMissPolicy'].get('enabled') is True
        winner=ordered_winner_match_end(inputs.get('gameTerminals'),inputs['gaps']) if enabled else None
        for i,r in enumerate(records):
            if not enabled or unknown.get(i)!='R-Q attack callback is not closed':continue
            s,f=r['start'],r['finish'];left=order(s);right=order(winner) if winner else None;reasons=[]
            if inputs.get('collisions') is None or any(g.get('count',0) for g in inputs['gaps']):reasons.append('complete-collision-stream-unavailable')
            if not r['complete'] or not f or f.get('reason') not in set(range(15))|{16,17} or order(f) is None or left is None or right is None or not left<order(f)<right or not s['tick']<=f['tick']<=winner['tick']:reasons.append('actual-parent-finish-and-winner-order-unresolved')
            def after(e):
                if e.get('tick') is not None and e['tick']<s['tick']:return False
                return order(e) is None or left is None or e.get('tick',s['tick'])>s['tick'] or order(e)>=left
            ps=[p for p in shots if order(p) is not None and left is not None and left<order(p) and f and order(f) and order(p)<order(f)]
            arrival=None;p=ps[0] if len(ps)==1 else None
            if p is None:reasons.append('unique-parent-shot-unavailable')
            else:
                parents=[j for j,z in enumerate(records) if order(z['start'])<order(p) and (z.get('finish') is None or order(p)<order(z['finish']))]
                ts=[t for t in inputs['terminals'] if t.get('objectId')==p['projectileObjectId']]
                if len(ts)!=1 or ts[0].get('event')!='CmdProjectileArrived':reasons.append('arrival-missing-or-conflicting-terminal')
                else:
                    arrival=ts[0]
                    if parents!=[i] or p['tick']!=s['tick'] or arrival.get('isCollision') is not False or order(arrival) is None or right is None or not order(p)<order(arrival)<right or not p['tick']<=arrival['tick']<=winner['tick']:reasons.append('exact-noncollision-arrival-not-proven')
                if any(c.get('projectileObjectId')==p['projectileObjectId'] for c in inputs.get('collisions') or []):reasons.append('projectile-collision-needs-classification')
            if any(after(d) for d in inputs['damages'] if d.get('effectCode') in {profile['primary'],profile['bookmark']} and (d.get('attackerObjectId')==player or d.get('attackerObjectId') not in teams)):reasons.append('dedicated-damage-unresolved')
            state_codes={code for pair in profile['states'].values() for code in pair}
            if any(after(t) for t in inputs['states'] if t.get('casterObjectId') in {None,player} and (t.get('stateCode') in state_codes or t.get('stateGroup') in state_codes|{1028200,1028230})):reasons.append('related-CC-state-unresolved')
            if any(after(t) for t in inputs['allProjectileSpawns'] if t.get('projectileCode')==profile['projectile'] and t.get('ownerObjectId') not in teams):reasons.append('projectile-owner-unresolved')
            proof=dict(index=i,start=s,parentFinish=f,projectile=p,arrival=arrival,winnerEnd=winner,callbackExecutionObserved=False,callbackEndInvented=False,damages=[],marking=[],states=[])
            if reasons:policy_blocked.append(dict(proof,reasons=reasons));continue
            unknown.pop(i);policy_uses.append(proof)
    chosen=[u for u in uses if u['index'] not in unknown and combat(u['start'])]
    enemy=lambda t:t in teams and teams[t]!=teams[player]
    contacts=lambda u,key:{(d['tick'],d['targetObjectId']) for d in u[key] if enemy(d['targetObjectId'])}
    policy_chosen=[u for u in policy_uses if combat(u['start'])]
    counted=sorted(chosen+policy_chosen,key=lambda u:order(u['start']))
    phases={}
    for label,key in [('any','damages'),('bookmarkDamage','marking')]:
        cc=[contacts(u,key) for u in counted]
        row=_result(spec,cc,'static-Sua-RQ-projectile-death-callback-dedicated-effects',cast_ticks=[u['start']['tick'] for u in counted])
        row['outcomes']=[exact_outcome(u['start']['tick'],u['projectile']['tick'],c) for u,c in zip(counted,cc)]
        phases[label]=row
    combat_unknown=[dict(cast=records[i]['start'],reason=v) for i,v in unknown.items() if combat(records[i]['start'])]
    for phase in phases.values():
        phase.update(unresolvedCombatCastCount=len(combat_unknown),
            unresolvedCastReasons=dict(Counter(e['reason'] for e in combat_unknown)),
            unresolvedCastTicks=[e['cast']['tick'] for e in combat_unknown],
            unknownUseEvidence=combat_unknown,unresolvedBaseExecutionCount=len(combat_unknown),
            perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False)
    result=phases.pop('any')
    result.update(diag,phaseMetrics=phases,executionEvidenceByAttempt=counted,
        unresolvedCombatCastCount=sum(combat(records[i]['start']) for i in unknown),
        unresolvedCastReasons=dict(Counter(v for i,v in unknown.items() if combat(records[i]['start']))),
        unresolvedNonCombatCastCount=sum(not combat(records[i]['start']) for i in unknown),
        unknownUseEvidence=[dict(cast=records[i],reason=v) for i,v in unknown.items()],
        combatUnknownUseEvidence=combat_unknown,
        unresolvedCastTicks=[e['cast']['tick'] for e in combat_unknown],
        nonExecutedCastCount=sum(combat(records[i]['start']) for i in cancelled),nonExecutedAllCastCount=len(cancelled),
        perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,
        centerHitRateStatus='unresolved-evidence',bookmarkStunStatus='unresolved-evidence',
        detailReason='Shared Q/R state codes require exact callback order; absent CC never proves outside or no bookmark.',
        castFinishUsedAsProjectileEnd=False,damageAmountInferred=False,
        evidenceReview='deliverables/sua-r-static-routes-v1.json')
    try:
        from .skill_sua_rq_details import attach_sua_rq_details
    except ImportError:
        from skill_sua_rq_details import attach_sua_rq_details
    if include_details:
        result=attach_sua_rq_details(result,spec,chosen,player,teams,inputs,policy_uses=policy_chosen)
    else:
        from .skill_sua_hit_scope import project_sua_any_hit_scope
        result=project_sua_any_hit_scope(result)
    for row in [result,*result['phaseMetrics'].values()]:
        row.update(userPolicyMissEvidence=policy_chosen,userPolicyBlockedEvidence=policy_blocked,
            userPolicyMissCastCount=len(policy_chosen),userPolicyMissCastTicks=[u['start']['tick'] for u in policy_chosen],userPolicyMissAuthority='explicit-user-rule')
        if policy_chosen:row.update(verifiedCompletionCredit=False,fullRequestedMetricComplete=False)
    return result
