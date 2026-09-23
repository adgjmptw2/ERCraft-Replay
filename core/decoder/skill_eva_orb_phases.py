"""Whole Eva Q1 orb: actual penetration contacts and its synchronous explosion."""
from collections import Counter,defaultdict
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids,ORDINARY_ACTIONS,callback_damage_corroborated,annotate_multiple_callback_damage
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order
    from .skill_projectile_active_end import projectile_active_end_records
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_action_stage_evidence import load_exact_skill_ids,ORDINARY_ACTIONS,callback_damage_corroborated,annotate_multiple_callback_damage
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order
    from skill_projectile_active_end import projectile_active_end_records


def eva_orb_phase_metric(spec,starts,finishes,spawns,collisions,terminals,actions,damages,
                        player,teams,intervals,catalog,skill_rows,gaps,skill_ids=None):
    ss=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1036200]
    combat=lambda s:any(a<=s['tick']<b for a,b in intervals)
    diag=dict(observedCastCount=len(ss),observedCombatCastCount=sum(map(combat,ss)))
    def fail(why):return {**_unavailable(spec,why),**diag}
    if (spec['characterCode'],spec['skillGroup'],spec['mode'],spec['unit'])!=(36,1036200,'any','skill-cast'):
        return fail('unsupported Eva orb phase metric')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    codes={r['code'] for r in skill_rows if r.get('group')==1036200}
    if (ids.get('EvaActive1_1')!=497 or not codes
        or catalog['skillGroups'].get('1036200',{}).get('skillId')!='EvaActive1_1'
        or not catalog.get('projectileDefinitions',{}).get('103604')):return fail('pinned Eva Q1 schema mismatch')
    if any(s.get('skillCode') not in codes or s.get('skillIdCode')!=497 for s in ss):return fail('wrong Q1 wire code')
    if actions is None or gaps is None:return fail('missing complete orb action/decode stream')
    required={'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdSpawnBatch','CmdDestroy','CmdDestroyDelayStart',
              'CmdDamage','CmdProjectileCollision','CmdPlaySkillAction','CmdPlaySkillActionWithTargets'}
    if any(g.get('count',0) and g.get('packetName') in required for g in gaps):return fail('Q1 required command decode gap')
    lives,why=ordered_cast_records(ss,finishes,player,allow_same_tick_finishes=True)
    if why:return fail(why)
    orbs=[p for p in spawns if p.get('ownerObjectId')==player and p.get('projectileCode')==103604]
    by_cast=defaultdict(list)
    for p in orbs:
        if command_order(p) is None:return fail('missing exact orb spawn order')
        parents=[i for i,r in enumerate(lives) if command_order(r['start'])<command_order(p)
                 and (not r['complete'] or command_order(p)<command_order(r['finish']))]
        if not parents:return fail('own orb outside every recorded Q1 use')
        for i in parents:by_cast[i].append((p,len(parents)))
    own=[a for a in actions if a.get('sourceObjectId')==player and a.get('skillIdCode')==497 and a.get('actionNo') in (3001,3002)]
    ends=projectile_active_end_records(terminals)
    unknown=Counter();contacts=[];ticks=[];details=[];phases=[];cancelled_before_attack=0
    enemy=lambda target:target in teams and teams[target]!=teams[player]
    multiple_uses=0
    for i,r in enumerate(lives):
        s=r['start']
        if not combat(s):continue
        if not r['complete']:
            unknown['open-Q1-use']+=1;continue
        children=by_cast[i]
        if not children and r['finish']['reason']==3:
            # Explicit CancelByDying, a closed use, and no own orb anywhere
            # outside recorded uses. Q1 emits attacks only through its orb.
            # The requested skill-cast denominator includes cancelled casts.
            contacts.append(set());ticks.append(s['tick']);details.append([])
            phases.append({name:dict(executed=False,hitCount=0,distinctEnemyCount=0,firstHitTick=None)
                           for name in ('penetration','explosion')})
            cancelled_before_attack+=1;continue
        if len(children)!=1 or children[0][1]!=1:
            unknown['missing-unique-own-Q1-orb']+=1;continue
        p=children[0][0];oid=p['projectileObjectId'];end=ends.get(oid,{})
        removal=[t for t in terminals if t.get('objectId')==oid and t.get('event') in {'CmdDestroyDelayStart','CmdDestroy'}
                 and t['tick']==end.get('endTick')]
        if not end.get('complete') or end['endTick']<p['tick'] or not removal or any(command_order(t) is None for t in removal):
            unknown['missing-exact-orb-removal-boundary']+=1;continue
        boundary=min(removal,key=command_order)
        if command_order(boundary)<=command_order(p):
            unknown['orb-removal-before-spawn']+=1;continue
        cs=[c for c in collisions if c['projectileObjectId']==oid]
        if any(not p['tick']<=c['tick']<=end['endTick'] for c in cs):
            unknown['orb-contact-outside-active-lifetime']+=1;continue
        same=[a for a in own if a['tick']==boundary['tick']]
        if any(command_order(a) is None or a.get('wireStatus') not in ORDINARY_ACTIONS for a in same):
            unknown['missing-exact-orb-explosion-action-order']+=1;continue
        triggers=[a for a in own if a['actionNo']==3002 and len(a.get('targets',[]))==1
                  and a['targets'][0].get('targetObjectId')==oid]
        if len(triggers)>1 or any(a['tick']!=boundary['tick'] or command_order(a) is None for a in triggers):
            unknown['explosion-not-unique-synchronous-removal']+=1;continue
        ds=[dict(hitTick=c['tick'],targetObjectId=c['targetObjectId'],projectileObjectId=oid,phase='penetration')
            for c in cs if enemy(c['targetObjectId'])]
        why=None;multiple=False
        if triggers:
            trigger=triggers[0]
            if not command_order(p)<command_order(trigger)<command_order(boundary):
                unknown['explosion-outside-orb-lifetime']+=1;continue
            aa=sorted([a for a in same if command_order(trigger)<command_order(a)<command_order(boundary)],key=command_order)
            if any(a['actionNo']!=3001 or len(a.get('targets',[]))!=1 for a in aa):
                unknown['nested-or-malformed-explosion-callback']+=1;continue
            for j,a in enumerate(aa):
                target=a['targets'][0]['targetObjectId']
                if not enemy(target):continue
                stop=aa[j+1] if j+1<len(aa) else boundary
                candidates=[d for d in damages if d.get('attackerObjectId')==player and d.get('targetObjectId')==target
                            and d['tick']==a['tick'] and d.get('damageType')==2]
                if any(command_order(d) is None for d in candidates):why='missing-exact-explosion-damage-order';break
                candidates=[d for d in candidates if command_order(a)<command_order(d)<command_order(stop)]
                if not callback_damage_corroborated(candidates):
                    why='missing-or-ambiguous-explosion-target-damage';break
                multiple=multiple or len(candidates)>1
                ds.append(dict(damageOrders=[d['wireOrder'] for d in candidates],damageAttributionExact=len(candidates)==1,hitTick=a['tick'],targetObjectId=target,projectileObjectId=oid,phase='explosion',
                               hitActionOrder=a['wireOrder'],damageOrder=candidates[0]['wireOrder']))
        if why:unknown[why]+=1;continue
        multiple_uses+=int(multiple)
        # Both center/subcolliders and repeated collision contacts may contain
        # one enemy. Preserve first contact independently for each phase.
        first={}
        for d in sorted(ds,key=lambda d:d['hitTick']):first.setdefault((d['phase'],d['targetObjectId']),d)
        ds=list(first.values());phase={}
        for name,executed in [('penetration',True),('explosion',bool(triggers))]:
            part=[d for d in ds if d['phase']==name]
            phase[name]=dict(executed=executed,hitCount=int(bool(part)),distinctEnemyCount=len(part),
                             firstHitTick=min((d['hitTick'] for d in part),default=None))
        contacts.append({(d['hitTick'],d['targetObjectId']) for d in ds});ticks.append(s['tick']);details.append(ds);phases.append(phase)
    diag.update(unresolvedCombatCastCount=sum(unknown.values()),unresolvedCastReasons=dict(unknown),
                perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,cancelledBeforeAttackCount=cancelled_before_attack)
    if diag['observedCombatCastCount'] and not contacts:return fail('no complete whole Q1 orb outcomes')
    result=_result(spec,contacts,'static-own-Q1-orb-penetration-and-ordered-explosion',cast_ticks=ticks)
    result.update(diag,contactDetailsByAttempt=details,phaseOutcomesByAttempt=phases,
                  evidenceReview='deliverables/eva-q-orb-phases-static-proof-v1.json',fixedDurationWindowUsed=False)
    return annotate_multiple_callback_damage(result,multiple_uses)
