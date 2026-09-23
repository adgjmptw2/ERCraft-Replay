"""Arda parent uses linked by exact recorded aim and owned spawn positions.

Creation survives the ordinary parent finish. A spawn consumes a unique prior
unconsumed aim slot of the correct owner and summon kind. E additionally records
the created object's ID in action 43/44. No time window, distance tolerance or
nearest cast is used. Coincident pending aims remain unknown.
"""
from collections import defaultdict,Counter
import math
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order
    from .skill_action_stage_evidence import load_exact_skill_ids,ORDINARY_ACTIONS
    from .skill_owned_child_damage_metrics import owned_child_damage_metric
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order
    from skill_action_stage_evidence import load_exact_skill_ids,ORDINARY_ACTIONS
    from skill_owned_child_damage_metrics import owned_child_damage_metric

PARENTS={1066300:('ArdaActive2',1066600,1454,(31,),()),
         1066310:('ArdaActive2Reinforce',1066610,1455,(31,),()),
         1066400:('ArdaActive3',1066700,1457,(41,),(43,)),
         1066410:('ArdaActive3Reinforce_1',1066710,1458,(41,42),(43,44)),
         1066420:('ArdaActive3Reinforce_2',1066710,1458,(41,42),(43,44)),
         1066430:('ArdaActive3Reinforce_3',1066710,1458,(41,42),(43,44))}

def arda_parent_metric(spec,starts,finishes,child_starts,summons,objects,terminals,actions,damages,
                       player,teams,intervals,catalog,skill_rows,summon_rows,effect_rows,gaps,
                       *,skill_ids=None,child_use_cache=None):
    def fail(reason):return _unavailable(spec,reason)
    group=spec['skillGroup'];cfg=PARENTS.get(group)
    if not cfg or (spec['characterCode'],spec['mode'],spec['unit'])!=(66,'any','skill-cast'):return fail('unsupported Arda parent metric')
    name,child_group,summon_code,aim_actions,creation_actions=cfg
    required={'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdDestroy','CmdPlaySkillAction','CmdPlaySkillActionWithTargets','CmdDamage','SummonSnapshot:11'}
    if objects is None or child_starts is None or gaps is None or any(g.get('count',0) and g.get('packetName') in required for g in gaps):return fail('complete ordered Arda parent/spawn/child streams required')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    family={g for g,c in PARENTS.items() if c[1]==child_group}
    for g in family:
        if catalog['skillGroups'].get(str(g),{}).get('skillId')!=PARENTS[g][0] or type(ids.get(PARENTS[g][0])) is not int:return fail('exact Arda parent skill definition mismatch')
    code_groups={r['code']:r['group'] for r in skill_rows}
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup'] in family]
    if not selected:return fail('no recorded Arda parent starts')
    if any(s['skillIdCode']!=ids[PARENTS[s['skillGroup']][0]] or code_groups.get(s['skillCode'])!=s['skillGroup'] for s in selected):return fail('parent wire and skill code disagree')
    records,reason=ordered_cast_records(selected,finishes,player,allow_same_tick_finishes=True)
    if reason:return fail(reason)
    def within(r,a):
        at=command_order(a)
        return at is not None and a['tick']>=r['start']['tick'] and command_order(r['start'])<=at and (r['finish'] is None or at<=command_order(r['finish']) and a['tick']<=r['finish']['tick'])
    def point(v,size):
        if not isinstance(v,list) or len(v)!=size or any(type(x) not in (int,float) or not math.isfinite(x) for x in v):return None
        return (v[0],v[-1])
    for r in records:
        r.update(points={},children={},createActions=set(),unknown=set(),contacts=set())
        if not r['complete'] or not r['finish'] or r['finish']['reason']!=0:r['unknown'].add('parent-not-normally-complete')
    creation=defaultdict(list)
    for a in actions:
        if a.get('sourceObjectId')!=player or a.get('wireStatus') not in ORDINARY_ACTIONS:continue
        if a['skillIdCode'] not in {ids[PARENTS[g][0]] for g in family}:continue
        if a['actionNo'] in creation_actions:
            for target in a.get('targets',[]):creation[a['tick'],target.get('targetObjectId')].append(a)
        if a['actionNo'] not in aim_actions:continue
        candidates=[r for r in records if r['start']['skillIdCode']==a['skillIdCode'] and within(r,a)]
        if len(candidates)!=1:return fail('aim action has no single exact recorded parent use')
        r=candidates[0];targets=a.get('targets',[])
        pos=point(targets[0].get('targetPosition'),3) if len(targets)==1 and targets[0].get('hasTargetPosition') is True else None
        if pos is None or a['actionNo'] in r['points']:r['unknown'].add('missing-or-duplicate-parent-aim');continue
        r['points'][a['actionNo']]=(pos,command_order(a))
    for r in records:
        if set(r['points'])!=set(aim_actions):r['unknown'].add('incomplete-parent-aim-slots')
    object_rows=defaultdict(list)
    for o in objects:object_rows[o['tick'],o['objectId']].append(o)
    owned=[s for s in summons if s.get('ownerObjectId')==player and s.get('summonCode')==summon_code]
    spawn_rows=[]
    for s in owned:
        oo=object_rows[s['tick'],s['objectId']]
        if s.get('identityVerifiedAgainstGameDb') is not True or len(oo)!=1 or oo[0].get('objectType')!=11:return fail('owned summon has no unique exact spawn wrapper')
        o=oo[0];pos=point(o.get('positionXZ'),2);at=command_order(o)
        if pos is None or at is None:return fail('exact horizontal spawn position or command order unavailable')
        spawn_rows.append((at,s,pos))
    assigned=set()
    for at,s,pos in sorted(spawn_rows,key=lambda x:x[0]):
        if s['objectId'] in assigned:return fail('reused summon object identity')
        ca=None
        if creation_actions:
            aa=creation[s['tick'],s['objectId']]
            if len(aa)!=1 or command_order(aa[0]) is None or command_order(aa[0])<=at:return fail('E creation action does not explicitly confirm the new object')
            ca=aa[0]
        candidates=[(r,k) for r in records for k,(p,order) in r['points'].items()
                    if p==pos and order<at and k not in r['children'] and (ca is None or ca['skillIdCode']==r['start']['skillIdCode'])]
        if len(candidates)!=1:
            if not candidates:return fail('owned spawn does not match a recorded pending exact aim position')
            for r,k in candidates:r['unknown'].add('coincident-pending-parent-aims')
            continue
        r,k=candidates[0];r['children'][k]=s['objectId'];assigned.add(s['objectId'])
        if ca:
            if ca['actionNo'] in r['createActions']:r['unknown'].add('duplicate-E-creation-action')
            r['createActions'].add(ca['actionNo'])
    cache={} if child_use_cache is None else child_use_cache
    if child_group not in cache:
        sink=[];child_spec={**spec,'skillGroup':child_group,'metricId':'internal-owned-child-use-evidence'}
        child_row=owned_child_damage_metric(child_spec,child_starts,finishes,summons,terminals,actions,damages,player,teams,
            [],catalog,skill_rows,summon_rows,effect_rows,gaps,skill_ids=ids,use_evidence=sink)
        cache[child_group]=(sink,child_row.get('reason'))
    sink,child_error=cache[child_group]
    if not sink:return fail('child use evidence unavailable: '+str(child_error))
    child_by_actor=defaultdict(list)
    for child in sink:child_by_actor[child['start']['sourceObjectId']].append(child)
    for r in records:
        r['incompleteChildren']=[];r['hardChildUnknown']=False
        if set(r['children'])!=set(aim_actions) or r['createActions']!=set(creation_actions):r['unknown'].add('missing-declared-owned-child')
        for actor in r['children'].values():
            cc=child_by_actor[actor]
            if len(cc)!=1:r['unknown'].add('missing-or-repeated-owned-child-use');continue
            c=cc[0]
            if c['unknown']:
                r['unknown'].add('incomplete-or-ambiguous-child-outcome')
                if c.get('softLifetimeIncomplete') and c['unknown']=={'incomplete-child-lifetime'}:
                    r['incompleteChildren'].append(dict(sourceObjectId=actor,reasons=sorted(c['unknown'])))
                else:r['hardChildUnknown']=True
            if c.get('incompletePositiveReasons'):r['incompleteChildren'].append(dict(sourceObjectId=actor,reasons=c['incompletePositiveReasons']))
            r['contacts'].update(c['contacts'])
        if r['contacts'] and not r['hardChildUnknown'] and r['unknown']=={'incomplete-or-ambiguous-child-outcome'}:
            r['unknown'].clear()
    # Inspect the raw parent production channel through the next same-skill
    # start, including creation actions after an ordinary parent finish.
    # A missing linked child alone must never prove that nothing was emitted.
    def recorded_producer_action(r):
        start_order=command_order(r['start'])
        following=[command_order(other['start']) for other in records
                   if other['start']['skillIdCode']==r['start']['skillIdCode']
                   and command_order(other['start'])>start_order]
        next_order=min(following) if following else None
        relevant=[a for a in actions if a.get('sourceObjectId')==player
                  and a.get('skillIdCode')==r['start']['skillIdCode']
                  and a.get('actionNo') in set(aim_actions)|set(creation_actions)]
        if any(command_order(a) is None or a.get('wireStatus') not in ORDINARY_ACTIONS for a in relevant):
            return None
        return any(start_order<=command_order(a) and (next_order is None or command_order(a)<next_order)
                   for a in relevant)
    chosen=[r for r in records if r['start']['skillGroup']==group and any(l<=r['start']['tick']<h for l,h in intervals)]
    valid=[r for r in chosen if not r['unknown']]
    result=_result(spec,[r['contacts'] for r in valid],'static-Arda-parent-exact-aim-spawn-key-and-owned-child-outcomes',cast_ticks=[r['start']['tick'] for r in valid]) if valid or not chosen else fail('no complete unambiguous Arda parent uses')
    result.update(observedCombatCastCount=len(chosen),verifiedCombatCastCount=len(valid),unresolvedCombatCastCount=len(chosen)-len(valid),
        unresolvedCastReasons=dict(Counter(x for r in chosen for x in r['unknown'])),
        unresolvedUseEvidence=[dict(startTick=r['start']['tick'],startOrder=command_order(r['start']),
            reasons=sorted(r['unknown']),hasRecordedEmission=bool(r['children']),
            emissionKind='owned-child',emissionEvidenceComplete=True,
            hasRecordedProducerAction=recorded_producer_action(r)) for r in chosen if r['unknown']],
        exactOwnedChildrenPerUse=len(aim_actions),linkedOwnedChildCount=sum(len(r['children']) for r in valid),
        perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,childCastsCountedAsIndependentUses=False,
        timeWindowUsed=False,positionToleranceUsed=False,declaredCreationSurvivesParentFinish=True,
        denominatorMeaning='one recorded parent use; contacts across its exact owned children are deduplicated',
        evidenceReview='deliverables/arda-static-parent-spawn-proof-v1.json')
    partial=[r for r in valid if r['incompleteChildren']]
    if partial:
        try:from .skill_development_cancellation import annotate_provisional
        except ImportError:from decoder.skill_development_cancellation import annotate_provisional
        result=annotate_provisional(result,'Uniquely linked child contacts prove the parent positive while child finish and complete target totals remain unknown.')
        result.update(targetCountsAreLowerBounds=True,completeTargetCountsAvailable=False,
            incompleteChildPositiveEvidence=[dict(startTick=r['start']['tick'],children=r['incompleteChildren']) for r in partial])
    return result
