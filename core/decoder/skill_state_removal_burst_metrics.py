"""Manual shield burst linked through the actual synchronous state-removal body."""
from collections import Counter
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_wire_order import command_order
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_wire_order import command_order
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_action_stage_evidence import load_exact_skill_ids


def state_removal_burst_metric(spec,starts,finishes,state_scripts,actions,damages,player,teams,
                               intervals,catalog,skill_rows,state_groups,effect_rows,gaps,skill_ids=None):
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==spec['skillGroup']]
    diag=dict(observedCastCount=len(selected),observedCombatCastCount=sum(any(l<=s['tick']<r for l,r in intervals) for s in selected))
    def fail(reason):return {**_unavailable(spec,reason),**diag}
    if (spec.get('characterCode'),spec.get('skillGroup'),spec.get('mode'),spec.get('unit'))!=(14,1014310,'any','skill-cast'):
        return fail('unsupported manual state-removal burst')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    wire=ids.get('ChiaraActive2ShieldBreak');state_wire=ids.get('ChiaraActive2ShieldState')
    sg=[g for g in state_groups if g.get('group')==1014400]
    fx=[r for r in effect_rows if r.get('code')==1014401]
    codes={r['code'] for r in skill_rows if r.get('group')==1014310}
    if (type(wire) is not int or type(state_wire) is not int or not codes or
        catalog['skillGroups'].get('1014310',{}).get('skillId')!='ChiaraActive2ShieldBreak' or
        len(sg)!=1 or sg[0].get('skillId')!='ChiaraActive2ShieldState' or sg[0].get('stateType')!='Shield' or
        len(fx)!=1 or fx[0].get('effectPrefabName')!='FX_BI_Chiara_Skill03_Hit'):
        return fail('static shield/burst/effect identities differ from exact gameDb')
    required={'CmdStartSkill','CmdFinishSkill','CmdFinishStateSkill','CmdPlayStateSkillAction','CmdDamage'}
    if state_scripts is None or gaps is None or any(g.get('count',0) and g.get('packetName') in required for g in gaps):
        return fail('complete manual/state/damage command streams required')
    if any(s['skillIdCode']!=wire or s['skillCode'] not in codes for s in selected):return fail('manual shield-break wire identity mismatch')
    if not selected:return fail('no observed manual shield-break casts')
    records,reason=ordered_cast_records(selected,finishes,player,allow_same_tick_finishes=True)
    if reason:return fail(reason)
    state_ends=[s for s in state_scripts if s.get('event')=='CmdFinishStateSkill' and s.get('sourceObjectId')==player and s.get('stateGroup')==1014400 and s.get('skillIdCode')==state_wire]
    markers=[a for a in actions if a.get('wireStatus')=='decoded-exact-CmdPlayStateSkillAction' and a.get('sourceObjectId')==player and a.get('stateGroup')==1014400 and a.get('skillIdCode')==state_wire]
    enemy=[d for d in damages if d.get('attackerObjectId')==player and d.get('effectCode')==1014401 and d.get('targetObjectId') in teams and teams[d['targetObjectId']]!=teams[player]]
    contacts=[set() for _ in records];unknown={};packets=0
    for i,r in enumerate(records):
        start,end=r['start'],r['finish']
        if not r['complete'] or end is None or end.get('reason')!=0:
            unknown[i]='manual-cast-not-normally-complete';continue
        if start['tick']!=end['tick']:
            unknown[i]='manual-state-removal-did-not-complete-synchronously';continue
        tick=start['tick'];left,right=command_order(start),command_order(end)
        def within(e):return e['tick']==tick and command_order(e) is not None and left<command_order(e)<right
        es=[e for e in state_ends if within(e)];aa=[a for a in markers if within(a)]
        if any(command_order(e) is None and e['tick']==tick for e in [*state_ends,*markers]):
            unknown[i]='missing-state-removal-command-order';continue
        if len(es)!=1 or len(aa)!=1 or aa[0].get('actionNo') not in {1,2}:
            unknown[i]='missing-or-ambiguous-state-removal-burst';continue
        e,a=es[0],aa[0]
        if e.get('casterObjectId')!=a.get('casterObjectId') or command_order(e)>=command_order(a):
            unknown[i]='state-removal-and-completed-burst-do-not-match';continue
        for d in enemy:
            if d['tick']!=tick:continue
            at=command_order(d)
            if at is None:unknown[i]='missing-damage-command-order';continue
            if not command_order(e)<at<command_order(a):continue
            if a['actionNo']!=2:unknown[i]='damage-in-static-empty-shield-branch';continue
            if type(d.get('damageIsNull')) is not bool:unknown[i]='missing-damage-discriminator';continue
            contacts[i].add((tick,d['targetObjectId']));packets+=1
    combat=[i for i,r in enumerate(records) if any(l<=r['start']['tick']<h for l,h in intervals)]
    valid=[i for i in combat if i not in unknown]
    diag.update(unresolvedCombatCastCount=len(combat)-len(valid),unresolvedCastReasons=dict(Counter(unknown[i] for i in combat if i in unknown)),perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False)
    if combat and not valid:return fail('no complete ordered manual shield bursts')
    row=_result(spec,[contacts[i] for i in valid],'static-manual-cast-state-removal-synchronous-burst',cast_ticks=[records[i]['start']['tick'] for i in valid])
    row.update(diag,exactDamagePacketCount=packets,candidateEffectCodes=[1014401],effectSelectedByPrefabName=False,
        automaticShieldRemovalsCountedAsManualCasts=False,damageAmountInferred=False,
        evidenceReview='deliverables/chiara-manual-shield-burst-static-proof-v1.json')
    return row
