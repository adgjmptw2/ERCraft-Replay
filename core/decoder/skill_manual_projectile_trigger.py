"""Eva Q2: synchronous manual destruction of an explicitly identified Q1 orb."""
from collections import Counter
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


def manual_projectile_trigger_metric(spec,starts,finishes,spawns,terminals,actions,damages,
                                     player,teams,intervals,catalog,skill_rows,gaps,skill_ids=None):
    ss=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1036210]
    combat=lambda s:any(a<=s['tick']<b for a,b in intervals)
    diag=dict(observedCastCount=len(ss),observedCombatCastCount=sum(map(combat,ss)))
    def fail(why):return {**_unavailable(spec,why),**diag}
    if (spec['characterCode'],spec['skillGroup'],spec['mode'],spec['unit'])!=(36,1036210,'any','skill-cast'):
        return fail('unsupported manual projectile trigger')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    codes={r['code'] for r in skill_rows if r.get('group')==1036210}
    if (ids.get('EvaActive1_1')!=497 or ids.get('EvaActive1_2')!=498 or not codes
        or catalog['skillGroups'].get('1036210',{}).get('skillId')!='EvaActive1_2'
        or not catalog.get('projectileDefinitions',{}).get('103604')):return fail('pinned Eva Q2 identity mismatch')
    if any(s.get('skillCode') not in codes or s.get('skillIdCode')!=498 for s in ss):return fail('wrong Q2 wire code')
    if actions is None or gaps is None:return fail('missing complete action/decode stream')
    required={'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdDestroy','CmdDestroyDelayStart','CmdDamage',
              'CmdPlaySkillAction','CmdPlaySkillActionWithTargets'}
    if any(g.get('count',0) and g.get('packetName') in required for g in gaps):return fail('Q2 required command decode gap')
    lives,why=ordered_cast_records(ss,finishes,player,allow_same_tick_finishes=True)
    if why:return fail(why)
    own=[a for a in actions if a.get('sourceObjectId')==player and a.get('skillIdCode')==497 and a.get('actionNo') in (3001,3002)]
    ends=projectile_active_end_records(terminals);unknown=Counter();contacts=[];ticks=[];proofs=[];details=[]
    multiple_uses=0
    for life in lives:
        s,end=life['start'],life['finish']
        if not combat(s):continue
        def reject(reason):unknown.update([reason])
        if not life['complete'] or end['reason']!=0 or end['tick']!=s['tick']:
            reject('Q2-not-synchronously-complete');continue
        same=[a for a in own if a['tick']==s['tick']]
        if any(command_order(a) is None or a.get('wireStatus') not in ORDINARY_ACTIONS for a in same):
            reject('missing-exact-Q1-callback-order');continue
        aa=sorted([a for a in same if command_order(s)<command_order(a)<command_order(end)],key=command_order)
        trigger=[a for a in aa if a['actionNo']==3002]
        if len(trigger)!=1 or len(trigger[0].get('targets',[]))!=1:
            reject('missing-unique-manually-triggered-orb');continue
        tr=trigger[0];oid=tr['targets'][0]['targetObjectId']
        objects=[p for p in spawns if p.get('projectileObjectId')==oid]
        if (len(objects)!=1 or objects[0].get('projectileCode')!=103604 or objects[0].get('ownerObjectId')!=player
            or command_order(objects[0]) is None or command_order(objects[0])>=command_order(s)
            or not ends.get(oid,{}).get('complete') or ends[oid]['endTick']!=s['tick']):
            reject('manual-trigger-orb-identity-or-removal-mismatch');continue
        hits=[a for a in aa if a['actionNo']==3001]
        if any(command_order(a)<=command_order(tr) or len(a.get('targets',[]))!=1 for a in hits):
            reject('explosion-contact-before-trigger-or-without-target');continue
        found=set();hit_details=[];why=None;multiple=False
        for j,a in enumerate(hits):
            target=a['targets'][0]['targetObjectId']
            if target not in teams or teams[target]==teams[player]:continue
            boundary=command_order(hits[j+1]) if j+1<len(hits) else command_order(end)
            ds=[d for d in damages if d.get('attackerObjectId')==player and d.get('targetObjectId')==target
                and d['tick']==s['tick'] and d.get('damageType')==2]
            if any(command_order(d) is None for d in ds):why='missing-exact-explosion-damage-order';break
            ds=[d for d in ds if command_order(a)<command_order(d)<boundary]
            if not callback_damage_corroborated(ds):
                why='missing-or-ambiguous-per-target-explosion-damage';break
            multiple=multiple or len(ds)>1
            found.add((ds[0]['tick'],target))
            hit_details.append(dict(damageOrders=[d['wireOrder'] for d in ds],damageAttributionExact=len(ds)==1,hitTick=ds[0]['tick'],targetObjectId=target,projectileObjectId=oid,
                                    hitActionOrder=a['wireOrder'],damageOrder=ds[0]['wireOrder']))
        if why:reject(why);continue
        multiple_uses+=int(multiple)
        contacts.append(found);ticks.append(s['tick']);details.append(hit_details)
        proofs.append(dict(projectileObjectId=oid,triggerOrder=tr['wireOrder'],q2StartOrder=s['wireOrder'],q2FinishOrder=end['wireOrder']))
    diag.update(unresolvedCombatCastCount=sum(unknown.values()),unresolvedCastReasons=dict(unknown),
                perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False)
    if diag['observedCombatCastCount'] and not contacts:return fail('no complete manual Q2 explosion outcomes')
    row=_result(spec,contacts,'static-Q2-ordered-manual-Q1-orb-explosion',cast_ticks=ticks)
    row.update(diag,manualTriggerEvidenceByAttempt=proofs,contactDetailsByAttempt=details,fixedDurationWindowUsed=False,
               evidenceReview='deliverables/eva-q2-manual-trigger-static-proof-v1.json')
    return annotate_multiple_callback_damage(row,multiple_uses)
