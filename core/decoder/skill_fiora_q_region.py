"""Q regions from a positive outer-state witness or the native pose formula."""
from collections import Counter
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order as order
    from .skill_pose_events import StationaryPoseIndex,projected_region,projected_region_bounds,pose_packet_channels
    from .skill_evaluation_position_bounds import evaluation_bounds
    from .skill_fiora_state_admission import admitted_inner_contact
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order as order
    from skill_pose_events import StationaryPoseIndex,projected_region,projected_region_bounds,pose_packet_channels
    from skill_evaluation_position_bounds import evaluation_bounds
    from skill_fiora_state_admission import admitted_inner_contact

# Explicit constructor entries in FioraSkillActive1Data.SlowState by Q level;
# this equality is proved by those entries, not a numeric namespace heuristic.
SLOW_BY_SKILL={1003201:1003201,1003202:1003202,1003203:1003203,1003204:1003204,1003205:1003205}

def fiora_q_region(spec,starts,finishes,damages,pose_events,player,teams,intervals,catalog,skill_rows,gaps,states=None,motion_inputs=None,state_inventory=None):
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1003200]
    combat=lambda s:any(a<=s['tick']<b for a,b in intervals)
    diag=dict(observedCastCount=len(selected),observedCombatCastCount=sum(map(combat,selected)))
    def fail(reason):return {**_unavailable(spec,reason),**diag}
    if (spec.get('characterCode'),spec.get('skillGroup'),spec.get('unit'))!=(3,1003200,'skill-cast') or spec.get('mode')not in ('any','tip','non-tip'):
        return fail('unsupported Fiora Q region')
    rows={s['code']:s for s in skill_rows if s.get('group')==1003200}
    if (catalog.get('skillGroups',{}).get('1003200',{}).get('skillId')!='FioraActive1'
            or set(rows)!=set(range(1003201,1003206)) or any(r.get('range')!=5.3 for r in rows.values())
            or any(s['skillIdCode']!=65 or s['skillCode']not in rows for s in selected)):
        return fail('pinned Fiora Q definition mismatch')
    if player not in teams or any(x is None for x in (damages,gaps)):
        return fail('missing complete damage/pose streams')
    required={'CmdStartSkill','CmdFinishSkill','CmdDamage'}
    if any(g.get('count',0) and g.get('packetName')in required for g in gaps):return fail('incomplete Q command streams')
    pose_capable=pose_events is not None and not any(g.get('count',0) and g.get('packetName')in pose_packet_channels() for g in gaps)
    state_capable=states is not None and not any(g.get('count',0) and g.get('packetName')in {'CmdAddState','CmdAddStateExtended'} for g in gaps)
    admission_capable=state_inventory is not None and not any(g.get('count',0) and g.get('packetName') in
        {'CmdAddState','CmdAddStateExtended','CmdUpdateState','CmdRemoveState','CmdResetCreateTimeState','CmdPauseState',
         'CmdDead','CmdDyingCondition','CmdDyingBlockBeforeDead','CmdResetCharacter','CmdResurrection','CmdTeamRevival'} for g in gaps)
    if spec['mode']!='any' and not pose_capable and not state_capable and not admission_capable:return fail('missing complete Q region evidence stream')
    records,why=ordered_cast_records(selected,finishes,player,allow_same_tick_finishes=True)
    if why:return fail(why)
    rotation_baselines=motion_inputs.get('rotationBaselines') if motion_inputs is not None else None
    poses=StationaryPoseIndex(pose_events,rotation_baselines) if pose_capable else None
    motion_poses=StationaryPoseIndex(motion_inputs['poses'],rotation_baselines) if pose_capable and motion_inputs is not None else None
    outer_states=[s for s in states or [] if state_capable and s.get('event')=='add' and s.get('casterObjectId')==player and s.get('stateCode')in SLOW_BY_SKILL.values() and order(s)is not None]
    contacts=[[] for _ in records];unknown={};unknown_contacts=[[] for _ in records]
    for i,r in enumerate(records):
        if not r['complete'] or r['finish']is None or r['finish'].get('reason')!=0:unknown[i]='cast-not-normally-complete'
    overall_unknown=dict(unknown);overall_contacts=[[] for _ in records]
    for damage in damages:
        if damage.get('attackerObjectId')!=player or damage.get('effectCode')!=1003201:continue
        at=order(damage)
        if at is None:return fail('missing dedicated Q damage order')
        owners=[i for i,r in enumerate(records) if order(r['start'])<at and (r['finish']is None or at<order(r['finish']))]
        if len(owners)!=1:return fail('Q damage outside unique recorded lifetime')
        i=owners[0];r=records[i]
        if type(damage.get('damageIsNull'))is not bool:
            unknown[i]=overall_unknown[i]='missing-damage-discriminator';continue
        target=damage.get('targetObjectId')
        if target not in teams or teams[target]==teams[player]:continue
        overall_contacts[i].append(dict(hitTick=damage['tick'],targetObjectId=target,damageOrder=damage['wireOrder']))
        admission=admitted_inner_contact(r['start'],damage,player,state_inventory) if admission_capable else None
        witnesses=[s for s in outer_states if s['targetObjectId']==target and s['stateCode']==SLOW_BY_SKILL[r['start']['skillCode']]
                   and s['tick']==damage['tick'] and order(r['start'])<order(s)<at]
        pose,why=poses.contact(r['start'],damage) if poses is not None else (None,'unavailable-pose-stream')
        evaluated=[] if why else [projected_region(pose['casterPosition'],pose['targetPosition'],v,rows[r['start']['skillCode']]['range'],2.0) for v in pose['forwardByCpuBranch']]
        bounded=False
        if why and motion_poses is not None and r['finish'] is not None:
            try:
                direction,direction_reason=motion_poses.direction(r['start'],damage)
                if direction_reason:raise ValueError(direction_reason)
                # Fiora tests IsOuterRange before emitting this damage. Unlike
                # Barbara, commands after damage cannot change that decision.
                caster_bounds,caster_evidence=evaluation_bounds(motion_inputs,motion_poses,player,damage,damage)
                target_bounds,target_evidence=evaluation_bounds(motion_inputs,motion_poses,target,damage,damage)
                evaluations=[projected_region_bounds(caster_bounds,target_bounds,v,rows[r['start']['skillCode']]['range'],2.0)
                             for v in direction['forwardByCpuBranch']]
                if any(e[0] is None for e in evaluations):raise ValueError('projection-bound-crosses-Q-region-edge')
                evaluated=evaluations;bounded=True;why=None
                pose=dict(casterBounds=caster_bounds,targetBounds=target_bounds,
                    casterPositionEvidence=caster_evidence,targetPositionEvidence=target_evidence,**direction)
            except (KeyError,ValueError,TypeError,OverflowError) as error:
                why=str(error)
        if evaluated and evaluated[0][0]!=evaluated[1][0]:unknown[i]='native-cpu-region-disagreement';continue
        if admission and (witnesses or evaluated and evaluated[0][0]!='non-tip'):
            unknown[i]='state-admission-and-region-evidence-disagree';continue
        if len(witnesses)==1:
            if evaluated and evaluated[0][0]!='tip':unknown[i]='outer-state-and-geometry-disagree';continue
            region='tip';source='positive-outer-Slow-before-same-target-Q-damage'
        elif evaluated:
            region=evaluated[0][0];source='native-projection-bounded-position-envelope' if bounded else 'native-projection-exact-stationary-pose'
        elif admission:
            region='non-tip';source='native-mandatory-outer-Slow-admission'
        else:
            unknown[i]=why
            # Keep the exact cause/command identities while the inputs are
            # already indexed. A later worklist must not rescan entire cached
            # matches just to discover which unsupported strategy was active.
            pose_index=motion_poses if motion_poses is not None else poses
            unknown_contacts[i].append(dict(damageWireOrder=damage['wireOrder'],tick=damage['tick'],
                targetObjectId=target,reason=why,
                casterLatestPose=pose_index.before(player,'position',at) if pose_index else None,
                targetLatestPose=pose_index.before(target,'position',at) if pose_index else None))
            continue
        contacts[i].append(dict(region=region,hitTick=damage['tick'],targetObjectId=target,regionSource=source,
                                damageOrder=damage['wireOrder'],pose=pose,projectionByCpuBranch=evaluated,
                                stateAdmissionProof=admission,
                                outerStateOrder=witnesses[0]['wireOrder'] if len(witnesses)==1 else None))
    observed=[i for i,r in enumerate(records) if combat(r['start'])]
    if spec['mode']=='any':
        valid=[i for i in observed if i not in overall_unknown]
        diag.update(verifiedCombatCastCount=len(valid),unresolvedCombatCastCount=len(observed)-len(valid),
            unresolvedCastReasons=dict(Counter(overall_unknown[i] for i in observed if i in overall_unknown)),
            unresolvedUseEvidence=[dict(start=records[i]['start'],finish=records[i]['finish'],reason=overall_unknown[i],
                startTick=records[i]['start']['tick'],startOrder=records[i]['start'].get('wireOrder'),
                reasons=[overall_unknown[i]],emissionKind='direct-effect',
                hasRecordedEffectEvidence=bool(overall_contacts[i]))
                for i in observed if i in overall_unknown],
            perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False)
        if observed and not valid:return fail('no completely attributed Q uses')
        values=[{(d['hitTick'],d['targetObjectId']) for d in overall_contacts[i]} for i in valid]
        result=_result(spec,values,'static-Fiora-Q-owned-dedicated-damage-with-confirmed-tip-count',
                       cast_ticks=[records[i]['start']['tick'] for i in valid])
        tip_evidence=[dict(confirmedTip=any(d['region']=='tip' for d in contacts[i]),
                           regionUnresolved=i in unknown) for i in valid]
        result.update(diag,verifiedCombatCastCount=len(valid),
            unresolvedCombatCastCount=len(observed)-len(valid),
            unresolvedCastReasons=dict(Counter(overall_unknown[i] for i in observed if i in overall_unknown)),
            confirmedTipCastCount=sum(t['confirmedTip'] for t in tip_evidence),
            confirmedTipContactCount=sum(len({(d['hitTick'],d['targetObjectId']) for d in contacts[i] if d['region']=='tip'}) for i in valid),
            tipRegionUnresolvedCastCount=sum(t['regionUnresolved'] for t in tip_evidence),
            tipEvidenceByAttempt=tip_evidence,tipCountMeaning='Casts with at least one confirmed tip contact; no tip-rate denominator or inferred region',
            contactDetailsByAttempt=[overall_contacts[i] for i in valid],
            confirmedRegionContactsByAttempt=[contacts[i] for i in valid],
            unresolvedRegionEvidence=[dict(start=records[i]['start'],reason=unknown[i],contacts=unknown_contacts[i]) for i in valid if i in unknown],
            perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,
            regionAmbiguityAffectsOverallHit=False,
            evidenceReview='deliverables/fiora-q-positive-outer-state-static-proof-v1.json')
        return result
    valid=[i for i in observed if i not in unknown]
    diag.update(verifiedCombatCastCount=len(valid),unresolvedCombatCastCount=len(observed)-len(valid),
                unresolvedCastReasons=dict(Counter(unknown[i] for i in observed if i in unknown)),
                unresolvedUseEvidence=[dict(start=records[i]['start'],finish=records[i]['finish'],
                    reason=unknown[i],contacts=unknown_contacts[i]) for i in observed if i in unknown],
                perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False)
    if observed and not valid:return fail('no completely classified Q uses')
    result=_result(spec,[{(d['hitTick'],d['targetObjectId']) for d in contacts[i] if d['region']==spec['mode']} for i in valid],
                   'static-Fiora-Q-positive-outer-state-or-native-projection',cast_ticks=[records[i]['start']['tick'] for i in valid])
    result.update(diag,contactDetailsByAttempt=[contacts[i] for i in valid],
                  evidenceReview='deliverables/fiora-q-positive-outer-state-static-proof-v1.json',
                  movementSimulationImplemented=False,movementBoundsImplemented=motion_inputs is not None,
                  missingSlowUsedAsInner=any(d['stateAdmissionProof'] for i in valid for d in contacts[i]),
                  missingSlowAloneUsedAsInner=False,roundedLockYawUsed=False)
    result['conditionalStateAdmissionImplemented']=True
    return result
