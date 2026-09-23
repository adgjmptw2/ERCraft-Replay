"""Native parent/owned-state execution spans, closed by their own commands."""
from collections import Counter,defaultdict
try:
    from .skill_partial_cast_lifetimes import ordered_cast_records,user_cancelled_no_recorded_contact_indices
    from .skill_wire_order import command_order
    from .skill_action_stage_evidence import load_exact_skill_ids
    from .requested_skill_hit_rates import _result,_unavailable
except ImportError:
    from skill_partial_cast_lifetimes import ordered_cast_records,user_cancelled_no_recorded_contact_indices
    from skill_wire_order import command_order
    from skill_action_stage_evidence import load_exact_skill_ids
    from requested_skill_hit_rates import _result,_unavailable

CONTRACTS={
    1080200:dict(character=80,parent='IstvanActive1',child='IstvanClone1',mainEffects={1080201},childEffects={1080203,1080204},summonCode=1571),
    1080300:dict(character=80,parent='IstvanActive2',child='IstvanClone2',mainEffects={1080301},childEffects={1080302},summonCode=1571),
    1080400:dict(character=80,parent='IstvanActive3',child='IstvanClone3',mainEffects={1080401},childEffects={1080402},summonCode=1571),
}


def contains(record,event):
    at=command_order(event);left=command_order(record['start'])
    if at is None or left is None:return False
    if at<left or event['tick']<record['start']['tick']:return False
    if not record['complete']:return True
    return at<=command_order(record['finish']) and event['tick']<=record['finish']['tick']


def owned_state_execution_metric(spec,starts,finishes,damages,state_scripts,summons,player,teams,intervals,
                                 catalog,skill_rows,state_groups,effect_rows,gaps,skill_ids=None,development=False):
    cfg=CONTRACTS.get(spec['skillGroup']);fail=lambda reason:_unavailable(spec,reason)
    if not cfg or (spec['characterCode'],spec['mode'],spec['unit'])!=(cfg['character'],'any','skill-cast'):
        return fail('native owned-state execution contract not registered')
    required={'CmdStartSkill','CmdFinishSkill','CmdStartStateSkill','CmdFinishStateSkill','CmdDamage','CmdSpawn','CmdSpawnBatch'}
    if any(v is None for v in (starts,finishes,damages,state_scripts,summons,gaps)) or any(g.get('count',0) and g.get('packetName') in required for g in gaps):
        return fail('owned-state execution command inventory incomplete')
    group=spec['skillGroup'];ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    code_groups={r['code']:r['group'] for r in skill_rows}
    definition=catalog['skillGroups'].get(str(group),{})
    links=[r for r in state_groups if r['group']==group]
    if (definition.get('characterCode')!=cfg['character'] or definition.get('skillId')!=cfg['parent']
            or len(links)!=1 or links[0].get('skillId')!=cfg['child']
            or not cfg['mainEffects']|cfg['childEffects']<={r['code'] for r in effect_rows}):
        return fail('native parent/state/effect game data identity mismatch')
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==group]
    if not selected or any(s['skillIdCode']!=ids.get(cfg['parent']) or code_groups.get(s['skillCode'])!=group for s in selected):
        return fail('native parent cast wire identity mismatch')
    parents,reason=ordered_cast_records(selected,finishes,player)
    if reason:return fail(reason)
    unknown={i:r['reason'] if not r['complete'] else 'parent-cast-not-normally-complete'
             for i,r in enumerate(parents) if not r['complete'] or r['finish'].get('reason')!=0}
    objects=defaultdict(list)
    for obj in summons:objects[obj['objectId']].append(obj)
    children=[];by_actor=defaultdict(list)
    for event in state_scripts:
        if event.get('skillIdCode')!=ids.get(cfg['child']):continue
        owned=any(o.get('ownerObjectId')==player for o in objects.get(event['sourceObjectId'],[]))
        if event.get('casterObjectId')!=player and not owned:continue
        if (event.get('casterObjectId')!=player or event.get('stateGroup')!=group or command_order(event) is None):
            return fail('child state caster/group/command identity mismatch')
        by_actor[event['sourceObjectId']].append(event)
    used_parents=set()
    for actor,events in by_actor.items():
        inventory=objects.get(actor,[])
        if not inventory or any(o.get('ownerObjectId')!=player or o.get('summonCode')!=cfg['summonCode']
                or o.get('identityVerifiedAgainstGameDb') is not True for o in inventory):
            return fail('child state summon ownership is not verified')
        child_starts=[dict(e,playerObjectId=actor) for e in events if e['event']=='CmdStartStateSkill']
        child_ends=[dict(e,playerObjectId=actor) for e in events if e['event']=='CmdFinishStateSkill']
        records,reason=ordered_cast_records(child_starts,child_ends,actor)
        if reason:return fail('child state '+reason)
        for child in records:
            start=child['start']
            if (code_groups.get(start['skillCode'])!=group or min(o['tick'] for o in inventory)>start['tick']):
                return fail('child state skill/summon creation identity mismatch')
            possible=[i for i,p in enumerate(parents) if p['start']['skillCode']==start['skillCode'] and contains(p,start)]
            if len(possible)!=1:return fail('child state has no exclusive recorded parent cast')
            parent=possible[0]
            # These native Start methods create at most one clone per use.
            if parent in used_parents:return fail('multiple child executions for a single-clone native cast')
            used_parents.add(parent)
            if not child['complete'] or child['finish'].get('reason')!=0:unknown[parent]='child-state-execution-incomplete'
            children.append((parent,child))
    contacts=[set() for _ in parents];main_count=child_count=0
    for damage in damages:
        target=damage['targetObjectId'];effect=damage.get('effectCode')
        if damage['attackerObjectId']!=player or target not in teams or teams[target]==teams[player]:continue
        if effect in cfg['mainEffects']:
            possible=[i for i,p in enumerate(parents) if contains(p,damage)]
            if len(possible)!=1:return fail('native primary damage outside exclusive parent execution')
            i=possible[0];main_count+=1
        elif effect in cfg['childEffects']:
            possible=[i for i,c in children if contains(c,damage)]
            if len(possible)!=1:return fail('native child damage outside exclusive state execution')
            i=possible[0];child_count+=1
        else:continue
        contacts[i].add((damage['tick'],target))
    combat=[i for i,p in enumerate(parents) if any(a<=p['start']['tick']<b for a,b in intervals)]
    recovered=[i for i in combat if i in unknown and contacts[i]]
    for i in recovered:del unknown[i]
    pending=set();policy_blocked=[]
    for i,why in unknown.items():
        p=parents[i];left=command_order(p['start']);reasons=[]
        related=[c for parent,c in children if parent==i]
        if not related:reasons.append('no-related-clone-existing-policy-unchanged')
        if any(not c['complete'] or not c.get('finish') or c['finish'].get('reason')!=0 or command_order(c['start']) is None or command_order(c['finish']) is None or command_order(c['start'])>=command_order(c['finish']) or c['start']['tick']>c['finish']['tick'] for c in related):reasons.append('clone-not-exactly-normally-closed')
        def after(e):
            if e.get('tick') is not None and e['tick']<p['start']['tick']:return False
            return command_order(e) is None or left is None or e.get('tick',p['start']['tick'])>p['start']['tick'] or command_order(e)>=left
        for e in state_scripts:
            if e.get('skillIdCode')!=ids.get(cfg['child']) and e.get('stateGroup')!=group:continue
            if not after(e):continue
            owners={o.get('ownerObjectId') for o in objects.get(e.get('sourceObjectId'),[])}
            if e.get('casterObjectId') in teams and e.get('casterObjectId')!=player and owners and player not in owners:continue
            if (e.get('casterObjectId')!=player or owners!={player} or e.get('skillIdCode')!=ids.get(cfg['child']) or e.get('stateGroup')!=group or command_order(e) is None):reasons.append('unresolved-owned-clone-event')
        represented={e.get('sourceObjectId') for e in state_scripts}
        if any(after(o) and o.get('summonCode')==cfg['summonCode'] and o.get('ownerObjectId') not in set(teams)-{player} and o.get('objectId') not in represented for o in summons):reasons.append('unresolved-clone-summon')
        if any(after(d) and d.get('effectCode') in cfg['mainEffects']|cfg['childEffects'] and d.get('attackerObjectId') not in teams for d in damages):reasons.append('unresolved-damage-owner')
        if reasons:pending.add(i)
        policy_blocked.append(dict(startTick=p['start']['tick'],startOrder=left,reasons=sorted(set(reasons+[why]))))
    admitted=user_cancelled_no_recorded_contact_indices(parents,contacts,unknown,
        cancellation_reason='parent-cast-not-normally-complete',pending_continuations=pending,gaps=gaps)
    miss_evidence=[dict(startTick=parents[i]['start']['tick'],startOrder=command_order(parents[i]['start']),
        finishTick=parents[i]['finish']['tick'],finishOrder=command_order(parents[i]['finish']),finishReason=parents[i]['finish']['reason'],
        clones=[dict(sourceObjectId=c['start']['sourceObjectId'],startTick=c['start']['tick'],startOrder=command_order(c['start']),
            finishTick=c['finish']['tick'],finishOrder=command_order(c['finish']),finishReason=c['finish']['reason']) for parent,c in children if parent==i]) for i in admitted if i in combat]
    for i in admitted:unknown.pop(i)
    excluded=[i for i in combat if development and unknown.get(i)=='parent-cast-not-normally-complete'
        and parents[i]['complete'] and parents[i]['finish'].get('reason') in set(range(1,15))|{16,17}
        and not contacts[i] and i not in used_parents]
    for i in excluded:del unknown[i]
    valid=[i for i in combat if i not in unknown and i not in excluded]
    if combat and not valid and any(i in unknown for i in combat):
        result=fail('no complete parent/child combat execution')
        result.update(userPolicyBlockedEvidence=policy_blocked)
        return result
    result=_result(spec,[contacts[i] for i in valid],'native-parent-and-owned-state-execution-spans',
        cast_ticks=[parents[i]['start']['tick'] for i in valid])
    result.update(userPolicyMissEvidence=miss_evidence,userPolicyBlockedEvidence=[e for e in policy_blocked if e['startTick'] not in {p['startTick'] for p in miss_evidence}],
        userPolicyMissCastCount=len(miss_evidence),userPolicyMissAuthority='explicit-user-rule',
        observedCombatCastCount=len(combat),verifiedCombatCastCount=sum(i not in admitted for i in valid),
        unresolvedCombatCastCount=sum(i in unknown for i in combat),unresolvedCastReasons=dict(Counter(unknown[i] for i in combat if i in unknown)),
        perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,positiveSampleRequired=False,
        parentFinishDoesNotCloseChild=True,childStateExecutionCount=len(children),
        nativePrimaryDamagePackets=main_count,nativeChildDamagePackets=child_count,
        exactMainEffectCodes=sorted(cfg['mainEffects']),exactChildEffectCodes=sorted(cfg['childEffects']),
        evidenceReview='deliverables/native-istvan-parent-state-execution-v1.json')
    if miss_evidence:
        from .skill_development_cancellation import annotate_provisional
        annotate_provisional(result,'Explicit user attempt policy: recorded cancelled parent with normally closed owned clone and no recorded enemy contact counts as a miss; this is not native completion or geometric absence proof.')
        result.update(verifiedCompletionCredit=False,fullRequestedMetricComplete=False)
    if recovered:
        result.update(recordedHitDespiteIncompleteExecutionCount=len(recovered),
            recordedHitDespiteIncompleteExecutionTicks=[parents[i]['start']['tick'] for i in recovered],
            targetCountsAreLowerBounds=True,completeTargetCountsAvailable=False,fullRequestedMetricComplete=False)
    if excluded:
        from .skill_development_cancellation import annotate_provisional
        result.update(provisionallyExcludedCancelledCastCount=len(excluded),
            provisionallyExcludedCancelledCastTicks=[parents[i]['start']['tick'] for i in excluded])
        annotate_provisional(result,'Recorded gameplay cancellation with neither enemy contact nor an owned clone execution is provisionally excluded. An unobserved direct attack miss may be excluded incorrectly.')
    return result
