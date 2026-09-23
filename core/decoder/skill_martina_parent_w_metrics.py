"""Explicit W installation target -> camera lifetime -> reinforced executions."""
from collections import Counter,defaultdict
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order as order
    from .skill_action_stage_evidence import load_exact_skill_ids
    from .skill_martina_state_execution_metrics import state_execution_metric
    from .skill_ordered_match_end import ordered_winner_match_end
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order as order
    from skill_action_stage_evidence import load_exact_skill_ids
    from skill_martina_state_execution_metrics import state_execution_metric
    from skill_ordered_match_end import ordered_winner_match_end

def martina_parent_w_metric(spec,starts,finishes,actions,damages,player,teams,intervals,
                            catalog,skill_rows,effect_rows,state_groups,inputs,skill_ids=None):
    roots=[s for s in starts if s['playerObjectId']==player and s['skillGroup'] in (1057300,1057310)]
    chosen_roots=[s for s in roots if s['skillGroup']==1057310]
    combat=lambda s:any(a<=s['tick']<b for a,b in intervals)
    diag=dict(observedCastCount=len(chosen_roots),observedCombatCastCount=sum(map(combat,chosen_roots)))
    def fail(why):return {**_unavailable(spec,why),**diag}
    if (spec['characterCode'],spec['skillGroup'],spec['mode'],spec['unit'])!=(57,1057310,'any','skill-cast'):return fail('unsupported Martina W parent')
    if any(inputs.get(k) is None for k in ('summons','objects','state_scripts','deaths','gaps','summon_rows')) or actions is None or damages is None or state_groups is None:return fail('missing Martina installation/execution stream')
    required={'CmdStartSkill','CmdFinishSkill','CmdPlaySkillAction','CmdPlaySkillActionWithTargets','CmdSpawn','CmdSpawnBatch','CmdDestroy','CmdDead'}
    if any(g.get('count',0) and g.get('packetName') in required for g in inputs['gaps']):return fail('incomplete Martina installation lifecycle')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    groups={1057300:('MartinaActive2',835),1057310:('MartinaActive2Reinforce',836)}
    if any(ids.get(n)!=w or catalog['skillGroups'].get(str(g),{}).get('skillId')!=n for g,(n,w) in groups.items()):return fail('pinned Martina W form mismatch')
    code_groups={s['code']:s['group'] for s in skill_rows}
    if any(code_groups.get(s['skillCode'])!=s['skillGroup'] or s['skillIdCode']!=groups[s['skillGroup']][1] for s in roots):return fail('Martina W skill/wire mismatch')
    records,why=ordered_cast_records(roots,finishes,player,allow_same_tick_finishes=True)
    if why:return fail(why)
    cams={};objects=defaultdict(list)
    for o in inputs['objects']:objects[o['objectId']].append(o)
    for cam in inputs['summons']:
        if cam['ownerObjectId']!=player or cam['summonCode']!=1351:continue
        bid=cam['objectId'];oo=objects[bid]
        if bid in cams or cam.get('identityVerifiedAgainstGameDb') is not True or len(oo)!=1 or order(oo[0]) is None or oo[0]['tick']!=cam['tick']:return fail('missing exact Martina camera spawn identity')
        cams[bid]={**cam,'wireOrder':oo[0]['wireOrder'],'wireCategory':'commands'}
    parent_actions=defaultdict(list)
    for a in actions:
        if a['sourceObjectId']!=player or a['skillIdCode'] not in (835,836) or a['actionNo']!=1:continue
        if order(a) is None or a.get('wireStatus')!='decoded-exact-CmdPlaySkillActionWithTargets':return fail('missing exact W installation target action')
        rr=[i for i,r in enumerate(records) if a['skillIdCode']==r['start']['skillIdCode'] and order(r['start'])<order(a) and (r['finish'] is None or order(a)<order(r['finish']))]
        if len(rr)!=1:return fail('installation action has no unique W cast')
        parent_actions[rr[0]].append(a)
    linked={};used=set();unknown={};cancelled=set()
    for i,r in enumerate(records):
        if not r['complete']:unknown[i]='open-W-cast';continue
        aa=parent_actions[i]
        if not aa and r['finish']['reason']==3:cancelled.add(i);continue
        if len(aa)!=1:unknown[i]='missing-or-multiple-W-installations';continue
        a=aa[0];targets=a.get('targets',[])
        if len(targets)!=1:unknown[i]='ambiguous-installed-camera-target';continue
        bid=targets[0]['targetObjectId'];cam=cams.get(bid)
        if cam is None or bid in used or cam['tick']!=a['tick'] or not order(r['start'])<order(cam)<order(a):return fail('installation target is not its newly spawned owned camera')
        used.add(bid);linked[i]=dict(camera=cam,installation=a)
    # Cancellation is only before installation when the complete source has
    # neither the mandatory target action nor any unassigned owned camera.
    if used!=set(cams):return fail('unassigned camera prevents closed installation/cancellation proof')
    sink=[];last=max((e['tick'] for rows in (inputs['state_scripts'],actions,damages,inputs['deaths']) for e in rows),default=0)+1
    child=state_execution_metric({**spec,'skillGroup':1057330},inputs['state_scripts'],inputs['summons'],inputs.get('terminals',[]),actions,damages,
        player,teams,[(0,last)],catalog,inputs['summon_rows'],effect_rows,state_groups,inputs['gaps'],ids,use_evidence=sink)
    if child['status'] not in ('calculable-observed','no-combat-sample') and not (child.get('reason')=='no recorded owned state executions' and child.get('observedOwnedChildCastCount')==0):
        return fail('camera execution evidence: '+str(child.get('reason')))
    executions=defaultdict(list)
    for use in sink:
        bid=use['start']['sourceObjectId']
        if bid not in used:return fail('reinforced execution has no installed camera parent')
        executions[bid].append(use)
    match_end=ordered_winner_match_end(inputs.get('gameTerminals'),inputs['gaps'])
    contacts=[];ticks=[];details=[];proofs=[]
    for i,r in enumerate(records):
        s=r['start']
        if s['skillGroup']!=1057310 or not combat(s) or i in cancelled or i in unknown:continue
        v=linked[i];cam=v['camera'];bid=cam['objectId'];end=None;method=None
        destroys=[t for t in inputs.get('terminals',[]) if t['objectId']==bid and t['event']=='CmdDestroy']
        deaths=[d for d in inputs['deaths'] if d['deadObjectId']==bid and d['event']=='CmdDead' and d.get('isDyingBlockDead') is False]
        if len(destroys)==1:end=destroys[0];method='final-camera-destruction'
        elif not destroys and len(deaths)==1:end=deaths[0];method='actual-camera-death'
        elif not destroys and not deaths and match_end is not None:end=match_end;method='recorded-match-completion-with-live-camera'
        if end is None or end['tick']<cam['tick']:unknown[i]='missing-actual-camera-end';continue
        uses=executions[bid]
        if any(u['unknown'] or u['action'] is None for u in uses):unknown[i]='incomplete-camera-attack';continue
        if any(order(u['start'])<order(cam) or u['action']['tick']>end['tick'] or
               (u['action']['tick']==end['tick'] and (order(end) is None or order(u['action'])>=order(end))) for u in uses):
            unknown[i]='camera-attack-outside-closed-lifetime';continue
        ds=[dict(hitTick=t,targetObjectId=target,cameraObjectId=bid,phase='reinforced-camera-attack',
            invocationStartOrder=u['start']['wireOrder'],attackActionOrder=u['action']['wireOrder']) for u in uses for t,target in sorted(u['contacts'])]
        contacts.append({(d['hitTick'],d['targetObjectId']) for d in ds});ticks.append(s['tick']);details.append(ds)
        proofs.append(dict(cameraObjectId=bid,cameraSpawnOrder=cam['wireOrder'],installationActionOrder=v['installation']['wireOrder'],
            cameraEndTick=end['tick'],cameraEndMethod=method,reinforcedAttackInvocationCount=len(uses)))
    reasons=Counter(reason for i,reason in unknown.items() if records[i]['start']['skillGroup']==1057310 and combat(records[i]['start']))
    cancelled_ticks=[records[i]['start']['tick'] for i in cancelled if records[i]['start']['skillGroup']==1057310 and combat(records[i]['start'])]
    diag.update(unresolvedCombatCastCount=sum(reasons.values()),unresolvedCastReasons=dict(reasons),perUseCompletenessTracked=True,
        incompleteUsesCountedAsMisses=False,cancelledBeforeAttackCount=len(cancelled_ticks),cancelledBeforeInstallationCastTicks=sorted(cancelled_ticks))
    if diag['observedCombatCastCount'] and not contacts and not cancelled_ticks:return fail('no closed enhanced-W camera outcomes')
    row=_result(spec,contacts,'static-W-installation-target-camera-and-reinforced-execution-lineage',cast_ticks=ticks)
    row.update(diag,contactDetailsByAttempt=details,parentCameraEvidenceByAttempt=proofs,
        evidenceReview='deliverables/martina-parent-w-static-proof-v1.json',fixedDurationWindowUsed=False,normalPlacedCamerasCountedAsEnhancedW=False)
    return row
