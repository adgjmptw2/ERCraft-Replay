"""Martina's state handler executions close at the synchronous damage action.

Process.MoveNext sends state action 1, runs its complete damage loop without
another yield, then returns false. CmdFinishStateSkill closes the persistent
state, not each invocation. No fixed tick window or invented finish is used.
"""
from collections import defaultdict,Counter
from bisect import bisect_right
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids
    from .skill_summon_ownership import live_summon_owner_resolver
    from .skill_wire_order import command_order
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_action_stage_evidence import load_exact_skill_ids
    from skill_summon_ownership import live_summon_owner_resolver
    from skill_wire_order import command_order


def state_execution_metric(spec,state_scripts,summons,terminals,actions,damages,player,teams,
                           intervals,catalog,summon_rows,effect_rows,state_groups,gaps,skill_ids=None,*,use_evidence=None):
    if use_evidence is not None and (type(use_evidence) is not list or use_evidence):
        raise ValueError('execution evidence sink must be a new empty list')
    diag=dict(observedOwnedChildCastCount=0,observedCombatChildCastCount=0,
              childCastsCountedAsIndependentUses=True,parentUsesInferred=False)
    def fail(reason):return {**_unavailable(spec,reason),**diag}
    if (spec.get('characterCode'),spec.get('skillGroup'),spec.get('mode'),spec.get('unit'))!=(57,1057330,'any','skill-cast'):
        return fail('unsupported state execution metric')
    name='MartinaActive2ReinforceSummonAttack'
    sr=[r for r in summon_rows if r.get('code')==1351]
    fx=[r for r in effect_rows if r.get('code')==1057302]
    sg=[r for r in state_groups if r.get('group')==1057310]
    if (catalog['skillGroups'].get('1057330',{}).get('skillId')!=name or len(sr)!=1 or
        sr[0].get('prefabPath')!='Martina_Skill02_SignTransmitter' or sr[0].get('useAttackerType')!='Owner' or
        len(fx)!=1 or fx[0].get('effectPrefabName')!='FX_BI_Martina_Skill02_Hit_Reinforce' or
        len(sg)!=1 or sg[0].get('skillId')!=name):return fail('static state/summon/effect identity mismatch')
    required={'CmdStartStateSkill','CmdFinishStateSkill','CmdPlayStateSkillAction','CmdDamage','CmdSpawn','CmdDestroy','SummonSnapshot:11'}
    if state_scripts is None or gaps is None or any(g.get('count',0) and g.get('packetName') in required for g in gaps):
        return fail('complete execution/action/damage streams required')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    wire=ids.get(name);normal=ids.get('MartinaActive2SummonAttack')
    if type(wire) is not int:return fail('exact handler wire identity missing')
    resolve=live_summon_owner_resolver(summons,terminals,set(teams))
    starts=[];events=[];boundaries=defaultdict(list);unknown=defaultdict(set)
    def own(actor,tick):
        owner,path,reason=resolve(actor,tick)
        return not reason and owner==player and path==[1351]
    for event in state_scripts:
        if event.get('stateGroup')!=1057310:continue
        if not own(event.get('sourceObjectId'),event['tick']):continue
        if event.get('skillIdCode')!=wire:return fail('state group and handler wire disagree')
        if command_order(event) is None:return fail('execution commands require exact wire order')
        if event['event']=='CmdStartStateSkill':
            i=len(starts);starts.append(event);events.append((command_order(event),'start',i,event))
        elif event['event']=='CmdFinishStateSkill':events.append((command_order(event),'finish',None,event))
    diag.update(observedOwnedChildCastCount=len(starts),observedCombatChildCastCount=sum(any(l<=s['tick']<r for l,r in intervals) for s in starts))
    if not starts:return fail('no recorded owned state executions')
    for action in actions:
        if (action.get('wireStatus')!='decoded-exact-CmdPlayStateSkillAction' or action.get('actionNo')!=1 or
            action.get('skillIdCode') not in {wire,normal} or not own(action.get('sourceObjectId'),action['tick'])):continue
        if command_order(action) is None:return fail('synchronous damage boundaries require exact action order')
        if action.get('skillIdCode')==wire and action.get('stateGroup')!=1057310:return fail('reinforced action state group mismatch')
        boundary=dict(action=action,use=None)
        boundaries[action['tick']].append(boundary)
        if action.get('skillIdCode')==wire:events.append((command_order(action),'action',boundary,action))
    if len({e[0] for e in events})!=len(events):return fail('duplicate execution command identity')
    pending=defaultdict(list);seen_action=set()
    for order,kind,value,event in sorted(events,key=lambda x:x[0]):
        key=(event['sourceObjectId'],event.get('casterObjectId'))
        if kind=='start':pending[key].append(value)
        elif kind=='finish':
            for i in pending[key]:unknown[i].add('state-ended-without-complete-damage-action')
            pending[key]=[]
        else:
            candidates=pending[key]
            if len(candidates)==1:
                i=candidates[0]
                if starts[i]['tick']>event['tick']:return fail('execution tick/order disagreement')
                value['use']=i;seen_action.add(i);pending[key]=[]
            elif not candidates:return fail('owned damage action without a unique recorded invocation')
            else:
                for i in candidates:unknown[i].add('overlapping-pending-state-executions')
    for i in range(len(starts)):
        if i not in seen_action:unknown[i].add('execution-without-complete-damage-action')
    for tick,bs in boundaries.items():
        bs.sort(key=lambda b:command_order(b['action']))
        if len({command_order(b['action']) for b in bs})!=len(bs):return fail('duplicate damage boundary identity')
    contacts=[set() for _ in starts]
    for d in damages:
        if d.get('effectCode')!=1057302 or d.get('targetObjectId') not in teams or teams[d['targetObjectId']]==teams[player]:continue
        actor=d.get('attackerObjectId')
        if actor!=player:
            if own(actor,d['tick']):return fail('dedicated damage disagrees with Owner actor policy')
            continue
        bs=boundaries.get(d['tick'],[]);at=command_order(d)
        if at is None:
            if not bs:return fail('unordered damage outside any owned emission frame')
            for b in bs:
                if b['use'] is not None:unknown[b['use']].add('missing-damage-wire-order')
            continue
        j=bisect_right([command_order(b['action']) for b in bs],at)-1
        if j<0:return fail('dedicated damage has no preceding synchronous emission')
        b=bs[j];i=b['use']
        if b['action']['skillIdCode']!=wire:return fail('reinforced damage follows a normal emission')
        if i is None:continue  # All candidate invocations were already marked unknown.
        if type(d.get('damageIsNull')) is not bool:unknown[i].add('missing-damage-discriminator')
        else:contacts[i].add((d['tick'],d['targetObjectId']))
    combat=[i for i,s in enumerate(starts) if any(l<=s['tick']<r for l,r in intervals)]
    valid=[i for i in combat if not unknown[i]]
    diag.update(perUseCompletenessTracked=True,observedCombatCastCount=len(combat),verifiedCombatCastCount=len(valid),
        unresolvedCombatCastCount=len(combat)-len(valid),unresolvedCastReasons=dict(Counter(k for i in combat for k in unknown[i])),
        incompleteUsesCountedAsMisses=False,damageAmountRequiredForContact=False,damageAmountInferred=False,
        persistentStateFinishUsedAsInvocationEnd=False,completedDamageActionCount=len(seen_action),
        exactSummonCode=1351,candidateEffectCodes=[1057302])
    if use_evidence is not None:
        action_by_use={b['use']:b['action'] for bs in boundaries.values() for b in bs if b['use'] is not None}
        use_evidence.extend(dict(start=s,action=action_by_use.get(i),contacts=contacts[i],unknown=sorted(unknown[i])) for i,s in enumerate(starts))
    if combat and not valid:return fail('no complete unambiguous state execution damage phases')
    row=_result(spec,[contacts[i] for i in valid],'static-Martina-state-execution-synchronous-damage-action',
                cast_ticks=[starts[i]['tick'] for i in valid])
    row.update(diag,denominatorMeaning='explicit state handler invocations with completed synchronous damage phase',
        evidenceReview='deliverables/martina-state-execution-static-proof-v1.json')
    return row
