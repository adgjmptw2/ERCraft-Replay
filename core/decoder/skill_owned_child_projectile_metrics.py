"""Niah block's independent Q shots outlive the child skill execution.

The synchronous launch emits owner108122 before child action202. Actual
command boundaries identify the emitting block even when child uses overlap.
"""
from collections import Counter,defaultdict
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids,ORDINARY_ACTIONS
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order
    from .skill_projectile_active_end import projectile_active_end_records
    from .skill_summon_ownership import live_summon_owner_resolver
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_action_stage_evidence import load_exact_skill_ids,ORDINARY_ACTIONS
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order
    from skill_projectile_active_end import projectile_active_end_records
    from skill_summon_ownership import live_summon_owner_resolver


def owned_child_projectile_metric(spec,nonplayer_starts,finishes,summons,spawns,collisions,terminals,
                                  actions,damages,player,teams,intervals,catalog,skill_rows,summon_rows,
                                  effect_rows,gaps,skill_ids=None):
    diag=dict(observedOwnedChildCastCount=0,observedCombatChildCastCount=0,
              parentUsesInferred=False,childCastsCountedAsIndependentUses=True)
    def fail(why):return {**_unavailable(spec,why),**diag}
    if (spec['characterCode'],spec['skillGroup'],spec['mode'],spec['unit'])!=(81,1081600,'any','skill-cast'):
        return fail('unsupported owned child projectile metric')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    codes={r['code'] for r in skill_rows if r.get('group')==1081600}
    defs=[r for r in summon_rows if r.get('code')==1581];fx=[r for r in effect_rows if r.get('code')==1081202]
    if (ids.get('NiahSummonActive1')!=1184 or not codes
        or catalog['skillGroups'].get('1081600',{}).get('skillId')!='NiahSummonActive1'
        or not catalog.get('projectileDefinitions',{}).get('108122') or len(defs)!=1
        or defs[0].get('prefabPath')!='Object_FX_BI_Niah_Skill01_Block' or defs[0].get('useAttackerType')!='None'
        or len(fx)!=1 or fx[0].get('effectPrefabName')!='FX_BI_Niah_Skill01_To_Skill02_Hit'):
        return fail('pinned Niah block/projectile/effect schema mismatch')
    required={'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdSpawnBatch','CmdDestroy','CmdDestroyDelayStart',
              'CmdProjectileCollision','CmdDamage','CmdPlaySkillAction','CmdPlaySkillActionWithTargets','SummonSnapshot:11'}
    if any(v is None for v in (nonplayer_starts,actions,gaps)) or any(g.get('count',0) and g.get('packetName') in required for g in gaps):
        return fail('missing complete child projectile stream')
    resolve=live_summon_owner_resolver(summons,terminals,set(teams));by_actor=defaultdict(list)
    for s in nonplayer_starts:
        if s['skillIdCode']!=1184 and s['skillCode'] not in codes:continue
        owner,path,why=resolve(s['sourceObjectId'],s['tick'])
        if why:return fail('child owner: '+why)
        if owner!=player:continue
        if path!=[1581] or s['skillIdCode']!=1184 or s['skillCode'] not in codes:return fail('child wire/owner mismatch')
        by_actor[s['sourceObjectId']].append({**s,'playerObjectId':s['sourceObjectId']})
    combat=lambda s:any(a<=s['tick']<b for a,b in intervals)
    records=[]
    for actor,ss in by_actor.items():
        local,why=ordered_cast_records(ss,finishes,actor,allow_same_tick_finishes=True)
        if why:return fail('child lifetime: '+why)
        records.extend(local)
    records.sort(key=lambda r:command_order(r['start']))
    diag.update(observedOwnedChildCastCount=len(records),observedCombatChildCastCount=sum(combat(r['start']) for r in records))
    own_spawns=[p for p in spawns if p.get('ownerObjectId')==player and p.get('projectileCode')==108122]
    if any(command_order(p) is None for p in own_spawns):return fail('missing ordered child projectile spawn')
    markers=[]
    for a in actions:
        if a.get('skillIdCode')!=1184 or a.get('actionNo')!=202:continue
        owner,path,why=resolve(a['sourceObjectId'],a['tick'])
        if why:return fail('launch marker owner: '+why)
        if owner!=player:continue
        if path!=[1581] or command_order(a) is None or a.get('wireStatus') not in ORDINARY_ACTIONS:
            return fail('missing exact child launch marker')
        markers.append(a)
    markers.sort(key=command_order)
    if len({command_order(a) for a in markers})!=len(markers):return fail('duplicate launch command identity')
    by_use=defaultdict(list);bound_objects=set();prior=None
    for a in markers:
        candidates=[i for i,r in enumerate(records) if r['start']['sourceObjectId']==a['sourceObjectId']
                    and command_order(r['start'])<command_order(a)
                    and (r['finish'] is None or command_order(a)<command_order(r['finish']))]
        if len(candidates)!=1:return fail('launch marker has no unique child use')
        i=candidates[0];start=records[i]['start']
        lower=max(command_order(start),prior) if prior is not None else command_order(start)
        emitted=[p for p in own_spawns if p['tick']==a['tick'] and lower<command_order(p)<command_order(a)]
        by_use[i].append((a,emitted));prior=command_order(a)
        for p in emitted:
            if p['projectileObjectId'] in bound_objects:return fail('projectile has duplicate launch parents')
            bound_objects.add(p['projectileObjectId'])
    if bound_objects!={p['projectileObjectId'] for p in own_spawns}:return fail('owned Q projectile lacks its launch marker')
    ends=projectile_active_end_records(terminals);unknown=Counter();contacts=[];ticks=[];details=[];proofs=[];cancelled=0;positive_without_end=0
    for i,r in enumerate(records):
        s=r['start']
        if not combat(s):continue
        if not r['complete']:
            unknown['open-child-use']+=1;continue
        emitted=by_use[i]
        if not emitted and r['finish']['reason']==3:
            # Actual CancelByDying before the synchronous launch. Every own
            # projectile was already accounted for above; no missing marker
            # or orphan shot is silently converted into a missed cast.
            contacts.append(set());ticks.append(s['tick']);details.append([]);cancelled+=1
            proofs.append(dict(sourceObjectId=s['sourceObjectId'],childStartOrder=s['wireOrder'],
                               childFinishOrder=r['finish']['wireOrder'],cancelReason=3,launched=False))
            continue
        if len(emitted)!=1 or len(emitted[0][1])!=1:
            unknown['missing-unique-child-shot']+=1;continue
        a,pp=emitted[0];p=pp[0];oid=p['projectileObjectId'];end=ends.get(oid,{})
        missing_end=not end and not any(t.get('objectId')==oid for t in terminals)
        if not missing_end and (not end.get('complete') or end['endTick']<p['tick']):
            unknown['missing-actual-projectile-end']+=1;continue
        cs=[c for c in collisions if c['projectileObjectId']==oid]
        if any(c['tick']<p['tick'] or (not missing_end and c['tick']>end['endTick']) for c in cs):
            unknown['collision-outside-projectile-active-lifetime']+=1;continue
        # Only absent retirement is soft. Exact wire order must independently
        # prove the already-recorded contact; no synthetic end or damage-only join.
        if missing_end and (not s['tick']<=p['tick']==a['tick']<=r['finish']['tick']
                or any(command_order(c) is None or command_order(c)<=command_order(a)
                       or c['tick']<a['tick'] for c in cs)
                or len({command_order(c) for c in cs})!=len(cs)):
            unknown['missing-actual-projectile-end']+=1;continue
        found=set();ds=[];why=None
        for c in cs:
            target=c['targetObjectId']
            if target not in teams or teams[target]==teams[player]:continue
            supporting=[d for d in damages if d.get('attackerObjectId')==player and d.get('targetObjectId')==target
                        and d['tick']==c['tick'] and d.get('damageType')==2 and d.get('effectCode')==1081202]
            if not supporting or any(type(d.get('damageIsNull')) is not bool for d in supporting):
                why='collision-without-exact-owner-projectile-damage';break
            if missing_end and (any(command_order(d) is None or command_order(d)<=command_order(c)
                                        for d in supporting)
                    or len({command_order(d) for d in supporting})!=len(supporting)):
                why='missing-actual-projectile-end';break
            # Collision itself identifies this projectile/target. Shared
            # same-frame FX only corroborates it; no damage amount assigned.
            found.add((c['tick'],target));ds.append(dict(hitTick=c['tick'],targetObjectId=target,projectileObjectId=oid,
                                                       sourceObjectId=s['sourceObjectId']))
        if why:unknown[why]+=1;continue
        if missing_end:
            if not found:
                unknown['missing-actual-projectile-end']+=1;continue
            positive_without_end+=1
        contacts.append(found);ticks.append(s['tick']);details.append(ds)
        proofs.append(dict(sourceObjectId=s['sourceObjectId'],childStartOrder=s['wireOrder'],launchActionOrder=a['wireOrder'],
                           projectileObjectId=oid,projectileSpawnOrder=p['wireOrder'],projectileEndTick=end.get('endTick'),positiveBeforeRecordedEnd=missing_end))
    diag.update(unresolvedCombatCastCount=sum(unknown.values()),unresolvedCastReasons=dict(unknown),perUseCompletenessTracked=True,
                incompleteUsesCountedAsMisses=False,cancelledBeforeAttackCount=cancelled)
    if diag['observedCombatChildCastCount'] and not contacts:return fail('no complete child projectile outcomes')
    result=_result(spec,contacts,'static-child-launch-marker-owned-projectile-active-lifetime',cast_ticks=ticks)
    result.update(diag,contactDetailsByAttempt=details,childProjectileEvidenceByAttempt=proofs,
                  evidenceReview='deliverables/niah-q-child-projectile-static-proof-v1.json',fixedDurationWindowUsed=False)
    if positive_without_end:
        from .skill_development_cancellation import annotate_provisional
        result=annotate_provisional(result,'Exact child launch, projectile collision and dedicated owner damage prove a positive before unrecorded retirement; target counts remain lower bounds.')
        result.update(positiveBeforeRecordedEndCastCount=positive_without_end,
                      targetCountsAreLowerBounds=True,completeTargetCountsAvailable=False)
    return result
