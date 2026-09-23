"""Elena R: actual projectile and expanding field, with separate inner/frozen evidence."""
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


def elena_r_execution(spec, starts, finishes, player, teams, intervals, catalog, inputs):
    starts=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1050500]
    combat=lambda e:any(a<=e['tick']<b for a,b in intervals)
    diag=dict(observedCastCount=len(starts),observedCombatCastCount=sum(map(combat,starts)))
    fail=lambda reason:{**_unavailable(spec,reason),**diag}
    if (spec['characterCode'],spec['skillGroup'],spec['mode'],spec['unit'])!=(50,1050500,'any','skill-cast'):
        return fail('unsupported Elena R scope')
    required=('allProjectileSpawns','summons','objects','terminals','deaths','damages','states','gaps')
    if player not in teams or any(inputs.get(k) is None for k in required):
        return fail('missing Elena R lineage/state stream')
    if (catalog.get('skillGroups',{}).get('1050500',{}).get('skillId')!='ElenaActive4'
            or any(s['skillIdCode']!=739 or s['skillCode'] not in (1050501,1050502,1050503) for s in starts)):
        return fail('pinned Elena R skill identity mismatch')
    packet_names={'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdSpawnBatch','CmdDead','CmdDamage',
                  'CmdDestroyDelayStart','CmdAddState','CmdAddStateExtended','CmdUpdateState','CmdResetCreateTimeState'}
    if any(g.get('count',0) and (g.get('packetName') in packet_names or str(g.get('packetName','')).startswith(('ProjectileSnapshot:','SummonSnapshot:'))) for g in inputs['gaps']):
        return fail('incomplete Elena R required commands')
    records,why=ordered_cast_records(starts,finishes,player,allow_same_tick_finishes=True)
    if why:return fail(why)
    shots=[s for s in inputs['allProjectileSpawns'] if s['ownerObjectId']==player and s['projectileCode']==105011]
    areas=[s for s in inputs['summons'] if s['ownerObjectId']==player and s['summonCode']==1262]
    if len({s['projectileObjectId'] for s in shots})!=len(shots) or len({s['objectId'] for s in areas})!=len(areas):
        return fail('repeated Elena R object identity')
    objects=defaultdict(list)
    for o in inputs['objects']:objects[o['objectId']].append(o)
    # Supplemental cache views of the SAME wire event are not extra removals.
    # Conflicting payload values at that identity are never silently discarded.
    terms=defaultdict(dict);related_ids={p['projectileObjectId'] for p in shots}|{a['objectId'] for a in areas}
    for e in inputs['terminals']:
        if e['event']!='CmdDestroyDelayStart' or e['objectId'] not in related_ids:continue
        key=(e['tick'],order(e))
        old=terms[e['objectId']].get(key)
        if old and any(k in old and old[k]!=v for k,v in e.items()):
            return fail('conflicting Elena terminal views')
        terms[e['objectId']][key]={**(old or {}),**e}
    unknown={};uses=[];assigned_shots=set();assigned_areas=set();cancelled=[]
    for i,r in enumerate(records):
        start,finish=r['start'],r['finish']
        within=lambda e:order(e) is not None and order(start)<order(e) and (finish is None or order(e)<order(finish))
        ps=[p for p in shots if within(p)]
        ss=[]
        for a in areas:
            os=objects[a['objectId']]
            if len(os)!=1 or order(os[0]) is None:return fail('missing exact R field spawn order')
            if within(os[0]):ss.append((a,os[0]))
        assigned_shots.update(p['projectileObjectId'] for p in ps);assigned_areas.update(a['objectId'] for a,o in ss)
        if not ps and not ss and r['complete'] and finish['reason']==3:
            cancelled.append(i);continue
        if len(ps)!=1 or len(ss)!=1:
            unknown[i]='R does not have one exact projectile/field pair';continue
        p=ps[0];a,o=ss[0]
        if not (p['tick']==o['tick']==start['tick'] and order(p)<order(o)
                and a.get('objectType')==o.get('objectType')==21 and a.get('snapshotType')=='SummonSnapshot'
                and a.get('identityVerifiedAgainstGameDb') is True and a['tick']==o['tick']):
            unknown[i]='R synchronous owned field creation differs';continue
        pe=list(terms[p['projectileObjectId']].values());ae=list(terms[a['objectId']].values())
        ds=[d for d in inputs['deaths'] if d.get('event')=='CmdDead' and d['deadObjectId']==a['objectId']]
        if len(pe)!=1 or len(ae)!=1 or len(ds)!=1 or any(order(e) is None for e in [pe[0],ae[0],ds[0]]):
            unknown[i]='R projectile/field end not exactly closed';continue
        pe,ae,death=pe[0],ae[0],ds[0]
        if not order(o)<order(pe)<order(death)<order(ae) or death['tick']!=ae['tick']:
            unknown[i]='R projectile/field lifetime order differs';continue
        uses.append(dict(index=i,start=start,projectile=p,area=a,areaSpawn=o,projectileEnd=pe,
                         areaDeath=death,areaEnd=ae,damages=[],frozen=[]))
    if assigned_shots!={p['projectileObjectId'] for p in shots} or assigned_areas!={a['objectId'] for a in areas}:
        return fail('orphan Elena R projectile/field')
    # Dedicated static producer codes plus an exclusive ACTUAL object lifetime.
    # No fixed duration or parent-finish damage cutoff; the field outlives R.
    for u in uses:
        for i,r in enumerate(records):
            if i in unknown and order(r['start'])<order(u['areaEnd']):
                # An unresolved older producer could still be alive. Its
                # parent's CmdFinishSkill is not a valid field end.
                unknown[u['index']]='potential overlapping unresolved R producer'
        for v in uses:
            if u is not v and order(u['projectile'])<order(v['areaEnd']) and order(v['projectile'])<order(u['areaEnd']):
                unknown[u['index']]='overlapping R producers require an additional cast key'
    damages=[d for d in inputs['damages'] if d['attackerObjectId']==player and d['effectCode'] in (1050501,1050502)]
    for d in damages:
        if order(d) is None:return fail('missing exact R damage order')
        owners=[u for u in uses if order(u['areaSpawn'])<order(d)<order(u['areaDeath'])]
        if len(owners)!=1:
            if not unknown:return fail('R damage outside unique field lifetime')
            for u in owners:unknown[u['index']]='ambiguous R damage producer'
            continue
        u=owners[0]
        if d.get('damageType')!=2 or type(d.get('damageIsNull')) is not bool:
            unknown[u['index']]='R damage discriminator mismatch';continue
        if d['effectCode']==1050502 and (d['tick']!=u['projectileEnd']['tick'] or order(d)>=order(u['projectileEnd'])):
            unknown[u['index']]='inner damage outside projectile death callback';continue
        if d['effectCode']==1050501 and d['tick']<u['projectileEnd']['tick']:
            unknown[u['index']]='outer damage before field activation';continue
        u['damages'].append(d)
    frozen=[s for s in inputs['states'] if s.get('casterObjectId')==player
            and s.get('event') in ('add','CmdUpdateState','CmdResetCreateTimeState')
            and (s.get('stateCode') in (1050191,1050192,1050193) or s.get('stateGroup')==1050190)]
    for s in frozen:
        candidates=[u for u in uses if s['tick']==u['projectileEnd']['tick']
                    and any(d['effectCode']==1050502 and d['targetObjectId']==s['targetObjectId'] for d in u['damages'])]
        # Passive full-stack freezing uses the SAME state. Only a recorded
        # inner-damage callback is eligible for this R detail.
        if not candidates:continue
        if order(s) is None:
            for u in candidates:unknown[u['index']]='missing exact R frozen state order'
            continue
        owners=[u for u in candidates if order(s)<order(u['projectileEnd'])
                and any(d['effectCode']==1050502 and d['targetObjectId']==s['targetObjectId'] and order(d)<order(s) for d in u['damages'])]
        if len(owners)!=1:
            if not unknown:return fail('frozen application lacks unique inner damage callback')
            continue
        u=owners[0]
        if s['event']!='add':unknown[u['index']]='frozen refresh semantics require review';continue
        u['frozen'].append(s)
    # The server's shared hitObjectIds set prevents inner targets being hit a
    # second time by the expanding outer field. Check that invariant explicitly.
    for u in uses:
        targets=[d['targetObjectId'] for d in u['damages']]
        if len(set(targets))!=len(targets):unknown[u['index']]='repeated target conflicts with R hitObjectIds'
    chosen=[u for u in uses if u['index'] not in unknown and combat(u['start'])]
    enemy=lambda t:t in teams and teams[t]!=teams[player]
    contacts=lambda u,code:{(d['tick'],d['targetObjectId']) for d in u['damages'] if enemy(d['targetObjectId']) and (code is None or d['effectCode']==code)}
    phases={}
    for label,code in [('any',None),('inner',1050502),('outer',1050501),('innerFrozen',-1)]:
        cc=[{(s['tick'],s['targetObjectId']) for s in u['frozen'] if enemy(s['targetObjectId'])} if code==-1 else contacts(u,code) for u in chosen]
        out=_result(spec,cc,'static-Elena-R-owned-field-and-explicit-inner-outer-effects',cast_ticks=[u['start']['tick'] for u in chosen])
        out['outcomes']=[exact_outcome(u['start']['tick'],u['projectile']['tick'],c) for u,c in zip(chosen,cc)]
        phases[label]=out
    result=phases.pop('any');reasons=Counter(v for i,v in unknown.items() if combat(records[i]['start']))
    result.update(diag,phaseMetrics=phases,unresolvedCombatCastCount=sum(reasons.values()),unresolvedCastReasons=dict(reasons),
        unresolvedNonCombatCastCount=sum(not combat(records[i]['start']) for i in unknown),
        unresolvedNonCombatCastReasons=dict(Counter(v for i,v in unknown.items() if not combat(records[i]['start']))),
        nonExecutedCastCount=sum(combat(records[i]['start']) for i in cancelled),nonExecutedAllCastCount=len(cancelled),
        executionEvidenceByAttempt=chosen,unknownUseEvidence=[dict(cast=records[i],reason=v) for i,v in unknown.items()],
        perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,castFinishUsedAsFieldEnd=False,
        frozenAbsenceMeansOuter=False,geometryInferred=False,damageAmountInferred=False,
        evidenceReview='deliverables/elena-r-execution-static-proof-v1.json')
    phases['innerFrozen']['meaning']='Actual Frozen1050190 application after inner damage; immunity or absent state never erases inner contact.'
    return result
