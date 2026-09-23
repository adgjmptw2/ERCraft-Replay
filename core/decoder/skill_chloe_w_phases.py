"""Separate Chloe spin, thrown projectile explosion, and Nina landing."""
from collections import defaultdict, Counter
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order as order
    from .skill_summon_ownership import live_summon_owner_resolver
    from .skill_action_stage_evidence import load_exact_skill_ids
    from .skill_attempt_timing import exact_outcome
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order as order
    from skill_summon_ownership import live_summon_owner_resolver
    from skill_action_stage_evidence import load_exact_skill_ids
    from skill_attempt_timing import exact_outcome


def chloe_w_phase_metric(spec, starts, finishes, player, teams, intervals, catalog, inputs,development=False):
    group = spec['skillGroup']
    names = {1040300:'ChloeActive2_1', 1040310:'ChloeActive2_2', 1040350:'NinaActive2'}
    ids = load_exact_skill_ids()
    roots = [s for s in starts if s['playerObjectId'] == player and s['skillGroup'] == group]
    combat = lambda s: any(a <= s['tick'] < b for a, b in intervals)
    diag = dict(observedCastCount=len(roots), observedCombatCastCount=sum(map(combat, roots)))
    fail = lambda why: {**_unavailable(spec, why), **diag}
    if group not in names or spec['characterCode'] != 40 or spec['mode'] != 'any' or spec['unit'] != 'skill-cast':
        return fail('unsupported Chloe W phase')
    if any(inputs.get(k) is None for k in ('damages', 'gaps', 'terminals', 'allProjectileSpawns')):
        return fail('missing W phase evidence')
    if any(catalog['skillGroups'].get(str(g), {}).get('skillId') != name for g, name in names.items()):
        return fail('pinned W phase identities differ')
    required = {'CmdStartSkill','CmdFinishSkill','CmdDamage','CmdSpawn','CmdSpawnBatch','CmdProjectileArrived','CmdProjectileExplosion','CmdDestroyDelayStart'}
    if any(g.get('count', 0) and (g.get('packetName') in required or str(g.get('packetName', '')).startswith('SummonSnapshot:')) for g in inputs['gaps']):
        return fail('incomplete W phase stream')
    damages = inputs['damages']
    contacts, attempts, details, evidence, cast_ticks = [], [], [], [], []
    unknown = {};estimated=[]

    def add(s, attempt, ds, proof):
        if not combat(s):
            return
        hits = {(d['tick'], d['targetObjectId']) for d in ds if d['targetObjectId'] in teams and teams[d['targetObjectId']] != teams[player]}
        contacts.append(hits); attempts.append(attempt); cast_ticks.append(s['tick'])
        details.append([dict(hitTick=t, targetObjectId=who) for t, who in sorted(hits)])
        evidence.append({**proof, 'damageCommands':ds})

    def result(records, phase):
        row = _result(spec, contacts, 'static-Chloe-W-' + phase, cast_ticks=cast_ticks)
        reasons = Counter(why for i, why in unknown.items() if combat(records[i]['start']))
        row.update(diag, outcomes=[exact_outcome(t, a, h) for t, a, h in zip(cast_ticks, attempts, contacts)],
                   unresolvedCombatCastCount=sum(reasons.values()), unresolvedCastReasons=dict(reasons),
                   unresolvedNonCombatCastCount=sum(not combat(records[i]['start']) for i in unknown),
                   contactDetailsByAttempt=details, executionEvidenceByAttempt=evidence,
                   enemyDamageCommandCount=sum(sum(d['targetObjectId'] in teams and teams[d['targetObjectId']] != teams[player] for d in p['damageCommands']) for p in evidence),
                   phase=phase, perUseCompletenessTracked=True, incompleteUsesCountedAsMisses=False,
                   evidenceReview='deliverables/chloe-w-phases-static-proof-v1.json', fixedDurationWindowUsed=False,
                   damageAmountInferred=False)
        if estimated:
            from .skill_development_cancellation import annotate_provisional
            row['estimatedLandingCastTicks']=estimated
            annotate_provisional(row,'Owned Nina W uses actual spell damage during its closed execution. Jump/unlock/return markers are not mandatory. Basic attacks are excluded; shared spell effects make attribution provisional.')
        return row

    if group == 1040300:
        if any(s['skillIdCode'] != ids[names[group]] or s['skillCode'] not in range(1040301,1040306) for s in roots):
            return fail('spin wire/code mismatch')
        records, why = ordered_cast_records(roots, finishes, player, allow_same_tick_finishes=True)
        if why:return fail(why)
        assigned = defaultdict(list)
        for d in damages:
            if d['attackerObjectId'] != player or d.get('effectCode') != 1040022:continue
            candidates = [i for i,r in enumerate(records) if order(d) is not None and order(r['start']) < order(d) and
                          (r['finish'] is None or order(d) < order(r['finish']))]
            if len(candidates) != 1 or type(d.get('damageIsNull')) is not bool:return fail('spin damage has no exact execution')
            assigned[candidates[0]].append(d)
        for i,r in enumerate(records):
            if not r['complete'] or r['finish']['tick'] == r['start']['tick'] and r['finish']['reason'] != 0:
                unknown[i] = 'spin execution not closed'
            else:add(r['start'],r['start']['tick'],assigned[i],dict(cast=r))
        return result(records, 'spin')

    throw_starts = [s for s in starts if s['playerObjectId'] == player and s['skillGroup'] == 1040310]
    if any(s['skillIdCode'] != ids['ChloeActive2_2'] or s['skillCode'] not in range(1040311,1040316) for s in throw_starts):
        return fail('throw wire/code mismatch')
    throws, why = ordered_cast_records(throw_starts,finishes,player,allow_same_tick_finishes=True)
    if why:return fail(why)
    throws_by_projectile, projectiles_by_cast = {}, defaultdict(list)
    for p in inputs['allProjectileSpawns']:
        if p['ownerObjectId'] != player or p['projectileCode'] != 104031:continue
        candidates = [i for i,r in enumerate(throws) if order(p) is not None and order(r['start']) < order(p) and
                      r['finish'] is not None and order(p) < order(r['finish'])]
        if len(candidates) != 1:return fail('thrown projectile lacks unique parent execution')
        pid=p['projectileObjectId']
        if pid in throws_by_projectile:return fail('duplicate thrown projectile identity')
        throws_by_projectile[pid]=candidates[0];projectiles_by_cast[candidates[0]].append(p)
    explosions = {}
    for i,ps in projectiles_by_cast.items():
        if len(ps)!=1:continue
        p=ps[0]
        es=[e for e in inputs['terminals'] if e['objectId']==p['projectileObjectId'] and e['event']=='CmdProjectileExplosion']
        ds=[e for e in inputs['terminals'] if e['objectId']==p['projectileObjectId'] and e['event']=='CmdDestroyDelayStart']
        arrivals=[e for e in inputs['terminals'] if e['objectId']==p['projectileObjectId'] and e['event']=='CmdProjectileArrived']
        if len(es)==len(ds)==len(arrivals)==1 and all(order(e) is not None for e in (arrivals[0],es[0],ds[0])) and order(p)<order(arrivals[0])<order(es[0])<order(ds[0]) and arrivals[0]['tick']==es[0]['tick']==ds[0]['tick']:
            explosions[i]=dict(projectile=p,callbackStart=arrivals[0],explosion=es[0],callbackEnd=es[0],destructionScheduled=ds[0])
    throw_damage=defaultdict(list)
    for d in damages:
        if d['attackerObjectId']!=player or d.get('effectCode')!=1040021:continue
        matches=[i for i,e in explosions.items() if order(d) is not None and order(e['callbackStart'])<order(d)<order(e['callbackEnd']) and d['tick']==e['explosion']['tick']]
        if len(matches)!=1 or type(d.get('damageIsNull')) is not bool:return fail('throw effect outside one exact explosion callback')
        throw_damage[matches[0]].append(d)
    if group==1040310:
        for i,r in enumerate(throws):
            if not r['complete'] or i not in explosions:unknown[i]='throw explosion not exactly closed'
            else:add(r['start'],explosions[i]['projectile']['tick'],throw_damage[i],dict(cast=r,**explosions[i]))
        return result(throws,'throw-explosion')

    if any(inputs.get(k) is None for k in (('nonPlayerSkillStarts','summons','actions') if development else ('nonPlayerSkillStarts','summons','actions','rotationEvents'))):
        return fail('missing Nina landing phase evidence')
    if any(g.get('count',0) and g.get('packetName') in ('CmdLockRotation','CmdPlaySkillAction','CmdPlaySkillActionWithTargets') for g in inputs['gaps']):
        return fail('missing Nina landing markers')
    resolve=live_summon_owner_resolver(inputs['summons'],inputs['terminals'],set(teams))
    by_actor=defaultdict(list)
    for s in inputs['nonPlayerSkillStarts']:
        if s['skillIdCode']!=ids['NinaActive2'] and s['skillCode']!=1040351:continue
        owner,path,why=resolve(s['sourceObjectId'],s['tick'])
        if why:return fail(why)
        if owner!=player:continue
        if path!=[1191] or s['skillIdCode']!=ids['NinaActive2'] or s['skillCode']!=1040351:return fail('Nina W actor/code mismatch')
        by_actor[s['sourceObjectId']].append(dict(s,playerObjectId=s['sourceObjectId']))
    records=[]
    for actor,ss in by_actor.items():
        rr,why=ordered_cast_records(ss,finishes,actor,allow_same_tick_finishes=True)
        if why:return fail(why)
        records.extend(rr)
    records.sort(key=lambda r:order(r['start']))
    diag=dict(observedCastCount=len(records),observedCombatCastCount=sum(combat(r['start']) for r in records))
    for i,r in enumerate(records):
        s,end=r['start'],r['finish'];actor=s['playerObjectId']
        if not r['complete']:unknown[i]='Nina W lifetime not closed';continue
        parents=[j for j,e in explosions.items() if order(e['callbackStart'])<order(s)<order(e['callbackEnd']) and s['tick']==e['explosion']['tick']]
        if len(parents)!=1:unknown[i]='Nina W has no exact throw callback';continue
        inside=lambda e: order(e) is not None and order(s)<order(e)<order(end)
        acts=[a for a in inputs['actions'] if a['sourceObjectId']==actor and a.get('skillIdCode')==ids['NinaActive2'] and a.get('actionNo')==23 and inside(a)]
        if development:
            ds=[d for d in damages if d['attackerObjectId']==actor and d.get('effectCode')==0 and d.get('damageType')==2 and inside(d)]
            competing=[x for x in inputs['nonPlayerSkillStarts'] if x['sourceObjectId']==actor and x['skillIdCode']!=ids['NinaActive2'] and inside(x)]
            if competing:unknown[i]='Nina spell execution overlaps another child skill';continue
            add(s,acts[0]['tick'] if acts else s['tick'],ds,dict(cast=r,parentThrow=throws[parents[0]],throwCallback=explosions[parents[0]],phaseEvidence='provisional-closed-owned-Nina-W-spell-damage'))
            if combat(s):estimated.append(s['tick'])
            continue
        if len(acts)!=1:unknown[i]='Nina W actual jump marker missing';continue
        own=[d for d in damages if d['attackerObjectId']==actor and d.get('effectCode')==0 and d.get('damageType')==2 and inside(d)]
        unlocks=[e for e in inputs['rotationEvents'] if e['objectId']==actor and e.get('isLock') is False and inside(e) and order(acts[0])<order(e)]
        returns=[p for p in inputs['allProjectileSpawns'] if p['ownerObjectId']==player and p['projectileCode']==104032 and inside(p)]
        phases=[]
        for u in unlocks:
            anchors=[p for p in returns if p['tick']==u['tick'] and order(u)<order(p)]
            phase_damage=[d for d in own if d['tick']==u['tick'] and order(u)<order(d)]
            if len(anchors)==1 or not returns and phase_damage:
                phases.append((u,anchors,phase_damage))
        if len(phases)!=1:unknown[i]='Nina landing phase ambiguous';continue
        u,anchors,ds=phases[0]
        competing=[x for x in inputs['nonPlayerSkillStarts'] if x['sourceObjectId']==actor and x['tick']==u['tick'] and x['skillIdCode']!=ids['NinaActive2']]
        basic=[d for d in damages if d['attackerObjectId']==actor and d['tick']==u['tick'] and d.get('damageType')==1]
        if len(ds)!=len(own) or competing or basic or any(type(d.get('damageIsNull')) is not bool for d in ds):
            unknown[i]='Nina landing damage conflicts with another execution';continue
        add(s,acts[0]['tick'],ds,dict(cast=r,parentThrow=throws[parents[0]],throwCallback=explosions[parents[0]],
            jump=acts[0],landingUnlock=u,returnProjectiles=anchors,phaseEvidence='return-projectile-and-unlock' if anchors else 'displaced-landing-unlock-and-synchronous-damage'))
    return result(records,'nina-landing')
