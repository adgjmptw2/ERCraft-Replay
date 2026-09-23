"""Copied Blue Bird: actual blindness per executed enemy-player target cast."""
from collections import Counter,defaultdict
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order as order
    from .skill_attempt_timing import exact_outcome
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order as order
    from skill_attempt_timing import exact_outcome


def sua_rw_execution(spec,starts,finishes,player,teams,intervals,catalog,inputs):
    starts=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1028520]
    combat=lambda e:any(a<=e['tick']<b for a,b in intervals)
    enemy=lambda t:t in teams and teams[t]!=teams.get(player)
    diag=dict(observedCastCount=len(starts),observedCombatCastCount=sum(map(combat,starts)))
    fail=lambda why:{**_unavailable(spec,why),**diag}
    if (spec['characterCode'],spec['skillGroup'],spec['mode'],spec['unit'])!=(28,1028520,'blind','skill-cast'):
        return fail('unsupported Sua R-W scope')
    if player not in teams or any(inputs.get(k) is None for k in ('allProjectileSpawns','terminals','states','gaps')):
        return fail('missing Sua R-W commands')
    if catalog.get('skillGroups',{}).get('1028520',{}).get('skillId')!='SuaActive4_3' or any(s['skillIdCode']!=406 or s['skillCode'] not in (1028521,1028522,1028523) for s in starts):
        return fail('pinned Sua R-W identity mismatch')
    needed={'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdSpawnBatch','CmdDestroyDelayStart','CmdAddState','CmdAddStateExtended','CmdUpdateState','CmdResetCreateTimeState'}
    if any(g.get('count',0) and (g.get('packetName') in needed or str(g.get('packetName','')).startswith('ProjectileSnapshot:')) for g in inputs['gaps']):
        return fail('incomplete Sua R-W required commands')
    records,why=ordered_cast_records(starts,finishes,player,allow_same_tick_finishes=True)
    if why:return fail(why)
    shots=[p for p in inputs['allProjectileSpawns'] if p['ownerObjectId']==player and p['projectileCode']==1028302]
    if len({p['projectileObjectId'] for p in shots})!=len(shots):return fail('repeated R-W projectile identity')
    ends=defaultdict(dict)
    for t in inputs['terminals']:
        if t['event']!='CmdDestroyDelayStart':continue
        old=ends[t['objectId']].get(t['tick'])
        if old and any(k in old and old[k]!=v for k,v in t.items()):return fail('conflicting R-W terminal views')
        ends[t['objectId']][t['tick']]={**(old or {}),**t}
    unknown={};uses=[];excluded=[];cancelled=[]
    for i,r in enumerate(records):
        s,f=r['start'],r['finish'];target=s.get('targetObjectId')
        if type(target) is not int:unknown[i]='missing target identity';continue
        # Scope is enemy experiment subjects. Self, allies and wildlife are
        # separately reported, never fabricated failed blindness attempts.
        if not enemy(target):excluded.append(i);continue
        pp=[p for p in shots if order(p) is not None and order(s)<order(p) and (f is None or order(p)<order(f))]
        if not pp and r['complete'] and f['reason']==3:cancelled.append(i);continue
        if len(pp)!=1 or pp[0]['tick']!=s['tick']:
            unknown[i]='missing unique synchronous R-W projectile';continue
        p=pp[0];ee=list(ends[p['projectileObjectId']].values())
        if len(ee)!=1 or ee[0]['tick']<s['tick']:
            unknown[i]='R-W projectile lifetime not closed';continue
        uses.append(dict(index=i,start=s,projectile=p,end=ee[0],targetObjectId=target,
            blindCode={1028521:1028351,1028522:1028352,1028523:1028353}[s['skillCode']],states=[]))
    for u in uses:
        if any(records[i]['start'].get('targetObjectId')==u['targetObjectId'] and order(records[i]['start'])<=order(u['start']) for i in unknown):
            unknown[u['index']]='earlier unresolved R-W producer for this target'
        for v in uses:
            if u is not v and u['targetObjectId']==v['targetObjectId'] and u['end']['tick']==v['end']['tick']:
                unknown[u['index']]='multiple R-W callbacks for one target/frame'
    for s in inputs['states']:
        if s.get('casterObjectId')!=player or not enemy(s.get('targetObjectId')):continue
        if s.get('event')=='remove':continue
        if s.get('stateCode') not in (1028351,1028352,1028353) and s.get('stateGroup')!=1028350:continue
        uu=[u for u in uses if u['targetObjectId']==s['targetObjectId'] and u['end']['tick']==s['tick']]
        if len(uu)!=1:
            if not unknown:return fail('dedicated R-W blind event outside unique callback')
            continue
        u=uu[0]
        if s.get('event')!='add' or s.get('stateCode')!=u['blindCode']:
            unknown[u['index']]='blind refresh or level discriminator requires review';continue
        u['states'].append(s)
    for u in uses:
        if len(u['states'])>1:unknown[u['index']]='repeated blindness application in one callback'
    chosen=[u for u in uses if u['index'] not in unknown and combat(u['start'])]
    cc=[{(s['tick'],s['targetObjectId']) for s in u['states']} for u in chosen]
    result=_result(spec,cc,'static-Sua-RW-target-projectile-and-dedicated-blind-state',cast_ticks=[u['start']['tick'] for u in chosen])
    result['outcomes']=[exact_outcome(u['start']['tick'],u['projectile']['tick'],c) for u,c in zip(chosen,cc)]
    result.update(diag,executionEvidenceByAttempt=chosen,
        unresolvedCombatCastCount=sum(combat(records[i]['start']) for i in unknown),
        unresolvedNonCombatCastCount=sum(not combat(records[i]['start']) for i in unknown),
        unresolvedCastReasons=dict(Counter(v for i,v in unknown.items() if combat(records[i]['start']))),
        unknownUseEvidence=[dict(cast=records[i],reason=v) for i,v in unknown.items()],
        excludedNonEnemyTargetCastCount=sum(combat(records[i]['start']) for i in excluded),excludedNonEnemyTargetAllCastCount=len(excluded),
        nonExecutedCastCount=sum(combat(records[i]['start']) for i in cancelled),nonExecutedAllCastCount=len(cancelled),
        perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,blindAbsenceMeansProjectileMiss=False,
        meaning='Actual blind application / executed casts aimed at enemy experiment subjects. Closed projectile without blind is failed application, not a claim of projectile miss.',
        castFinishUsedAsProjectileEnd=False,evidenceReview='deliverables/sua-r-static-routes-v1.json')
    return result
