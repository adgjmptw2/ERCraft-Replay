"""First lightning contact and actual central-stun application, independently."""
from collections import Counter,defaultdict
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_wire_order import command_order as order
    from .skill_attempt_timing import exact_outcome
    from .skill_ordered_match_end import ordered_winner_match_end
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_wire_order import command_order as order
    from skill_attempt_timing import exact_outcome
    from skill_ordered_match_end import ordered_winner_match_end


def aiden_first_thunderbolt(spec,player,teams,intervals,catalog,inputs):
    spawns=[p for p in inputs.get('allProjectileSpawns',[]) if p['ownerObjectId']==player and p['projectileCode']==104651]
    combat=lambda e:any(a<=e['tick']<b for a,b in intervals)
    diag=dict(observedCastCount=len(spawns),observedCombatCastCount=sum(map(combat,spawns)))
    fail=lambda why:{**_unavailable(spec,why),**diag}
    if (spec['characterCode'],spec['skillGroup'],spec['mode'],spec['unit'])!=(46,1046500,'any','skill-cast'):
        return fail('unsupported first-lightning scope')
    if player not in teams or any(inputs.get(k) is None for k in ('allProjectileSpawns','collisions','terminals','damages','states','gaps')):
        return fail('missing first-lightning or central-state stream')
    if catalog.get('skillGroups',{}).get('1046500',{}).get('skillId')!='AidenActive4':
        return fail('pinned Aiden R identity mismatch')
    if len({p['projectileObjectId'] for p in spawns})!=len(spawns):return fail('repeated first projectile identity')
    required={'CmdSpawn','CmdSpawnBatch','CmdProjectileExplosion','CmdProjectileCollision','CmdDestroy','CmdDestroyDelayStart','CmdDamage','CmdAddState','CmdAddStateExtended','CmdUpdateState','CmdResetCreateTimeState'}
    if any(g.get('count',0) and (g.get('packetName') in required or str(g.get('packetName','')).startswith('ProjectileSnapshot:')) for g in inputs['gaps']):
        return fail('incomplete first-lightning contact/state commands')
    terms=defaultdict(list);collisions=defaultdict(list)
    for e in inputs['terminals']:terms[e['objectId']].append(e)
    for e in inputs['collisions']:collisions[e['projectileObjectId']].append(e)
    secondary=[p for p in inputs['allProjectileSpawns'] if p['ownerObjectId']==player and p['projectileCode']==104652]
    stun=[s for s in inputs['states'] if s.get('event') in ('add','CmdUpdateState','CmdResetCreateTimeState') and s.get('casterObjectId')==player and
          (s.get('stateCode') in (1046511,1046512,1046513) or s.get('stateGroup')==1046510)]
    own_ids={p['projectileObjectId'] for p in inputs['allProjectileSpawns'] if p['ownerObjectId']==player}
    explosions=[e for e in inputs['terminals'] if e['objectId'] in own_ids and e['event']=='CmdProjectileExplosion']
    unknown={};uses=[];assigned_states=set()
    match_end=ordered_winner_match_end(inputs.get('gameTerminals'),inputs['gaps'])
    for i,p in enumerate(spawns):
        oid=p['projectileObjectId'];ts=terms[oid];xs=[e for e in ts if e['event']=='CmdProjectileExplosion']
        ends=[e for e in ts if e['event'] in ('CmdDestroyDelayStart','CmdDestroy')];cs=collisions[oid]
        if not ends and match_end is not None and len(xs)==1 and order(xs[0]) is not None and order(xs[0])<order(match_end):
            ends=[match_end]
        if len(xs)!=1 or not ends:
            unknown[i]='first-lightning explosion/removal not unique and closed';continue
        x=xs[0]
        if any(order(e) is None for e in [p,x,*ends,*cs]):unknown[i]='missing first-lightning lifecycle order';continue
        end=min(ends,key=order)
        if not order(p)<order(x)<order(end):unknown[i]='first-lightning lifecycle order differs';continue
        others=[e for e in explosions if e['tick']==x['tick'] and e['objectId']!=oid]
        if any(order(e) is None for e in others):unknown[i]='missing neighboring explosion order';continue
        upper=min([order(end),*[order(e) for e in others if order(e)>order(x)]])
        seconds=[s for s in secondary if s['tick']==x['tick'] and order(s) is not None and order(x)<order(s)<upper]
        # First OnExplosion creates the second projectile synchronously before
        # the per-target collision loop. Its exact spawn is a callback marker.
        if len(seconds)!=1:unknown[i]='first explosion lacks unique second-projectile creation';continue
        marker=seconds[0]
        if len({c['targetObjectId'] for c in cs})!=len(cs) or any(c['tick']!=x['tick'] or not order(marker)<order(c)<upper for c in cs):
            unknown[i]='first-lightning collision loop differs';continue
        cs=sorted(cs,key=order);contacts=set();cc=set();evidence=[];bad=None
        for j,c in enumerate(cs):
            target=c['targetObjectId'];limit=order(cs[j+1]) if j+1<len(cs) else upper
            state_rows=[s for s in stun if s['tick']==x['tick'] and s['targetObjectId']==target]
            if any(order(s) is None for s in state_rows):bad='central state order missing';break
            state_rows=[s for s in state_rows if order(c)<order(s)<limit]
            if any(s.get('event')!='add' for s in state_rows):bad='central state refresh semantics require review';break
            if len(state_rows)>1:bad='multiple central applications in one target callback';break
            assigned_states.update(order(s) for s in state_rows)
            if target not in teams or teams[target]==teams[player]:continue
            ds=[d for d in inputs['damages'] if d['tick']==x['tick'] and d['attackerObjectId']==player and d['targetObjectId']==target]
            if any(order(d) is None for d in ds):bad='first-lightning damage order missing';break
            ds=sorted([d for d in ds if order(c)<order(d)<limit and d.get('effectCode')==0 and d.get('damageType')==2],key=order)
            adjacent=bool(ds) and ds[0]['wireOrder']==[c['wireOrder'][0],c['wireOrder'][1]+1]
            if not ds or (len(ds)!=1 and not adjacent) or type(ds[0].get('damageIsNull')) is not bool:
                bad='first-lightning target damage ambiguous';break
            if any(s['playerObjectId']==player and s['tick']==x['tick'] and
                   (order(s) is None or order(c)<order(s)<order(ds[0])) for s in inputs.get('starts',[])):
                bad='competing cast interrupts first-lightning target callback';break
            if any(order(s)<=order(ds[0]) for s in state_rows):bad='central stun precedes first damage callback';break
            contacts.add((ds[0]['tick'],target));cc.update((s['tick'],target) for s in state_rows)
            evidence.append(dict(collision=c,damage=ds[0],centralStunApplications=state_rows,
                                 additionalDamageCommandsNotAttributed=ds[1:]))
        if bad:unknown[i]=bad;continue
        uses.append((i,p,contacts,cc,dict(projectile=p,explosion=x,secondProjectile=marker,removal=end,contacts=evidence)))
    # No central application may silently disappear outside the reviewed
    # projectile callback. Removed/expired states are not new applications.
    unassigned=[s for s in stun if s.get('event')=='add' and order(s) not in assigned_states]
    if unassigned and not unknown:return fail('orphan central stun application')
    selected=[u for u in uses if combat(u[1])];ticks=[u[1]['tick'] for u in selected]
    row=_result(spec,[u[2] for u in selected],'static-Aiden-first-lightning-projectile-callback',cast_ticks=ticks)
    central=_result(spec,[u[3] for u in selected],'static-Aiden-central-stun-application',cast_ticks=ticks)
    row['outcomes']=[exact_outcome(u[1]['tick'],u[1]['tick'],u[2]) for u in selected]
    central['outcomes']=[exact_outcome(u[1]['tick'],u[1]['tick'],u[3]) for u in selected]
    central.update(meaning='Actual central stun application success, not geometrical center contact or outer classification',absentStunMeansOuter=False)
    reasons=Counter(v for i,v in unknown.items() if combat(spawns[i]))
    row.update(diag,unresolvedCombatCastCount=sum(reasons.values()),unresolvedCastReasons=dict(reasons),
        unresolvedNonCombatCastCount=sum(not combat(spawns[i]) for i in unknown),
        phaseMetrics={'centralStun':central},executionEvidenceByAttempt=[u[4] for u in selected],
        perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,
        centralStunAbsentMeansOuter=False,geometryInferred=False,damageAmountInferred=False,
        evidenceReview='deliverables/aiden-r-central-static-proof-v1.json')
    return row
