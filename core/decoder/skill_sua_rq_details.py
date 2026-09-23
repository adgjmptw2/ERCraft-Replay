"""Shared Q/R-Q CC attribution using the native callback's execution order."""
from collections import Counter
import re

SLOW_ADMISSION_RULE_SHA256='03825e56894bc7dda781eeec2e353c6551d03ff3cbc7f25925102ba881affa9d'
try:
    from .skill_adela_state_admission import SLOW_BLOCKED_TYPES as _ADMISSION_BLOCKERS,ENUM_CORRECTION_EVIDENCE,SLOW_IMMUNITY_EVIDENCE
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_wire_order import command_order as order
    from .skill_attempt_timing import exact_outcome
    from .skill_box_circle_rule import SOURCE_CELL_SWEEP_RULE_SHA256,source_cell_sweep_excludes_center,source_cell_sweep_guarantees_center
    from .skill_box_circle_rule import box_circle_contact,STATIONARY_SUB_RULE_SHA256,BOOKMARK_STOP_SWEEP_RULE_SHA256,ordinary_sweep_excludes_center
except ImportError:
    from skill_adela_state_admission import SLOW_BLOCKED_TYPES as _ADMISSION_BLOCKERS,ENUM_CORRECTION_EVIDENCE,SLOW_IMMUNITY_EVIDENCE
    from requested_skill_hit_rates import _result,_unavailable
    from skill_wire_order import command_order as order
    from skill_attempt_timing import exact_outcome
    from skill_box_circle_rule import SOURCE_CELL_SWEEP_RULE_SHA256,source_cell_sweep_excludes_center,source_cell_sweep_guarantees_center
    from skill_box_circle_rule import box_circle_contact,STATIONARY_SUB_RULE_SHA256,BOOKMARK_STOP_SWEEP_RULE_SHA256,ordinary_sweep_excludes_center


def _center_geometry(u, damage, player, inputs):
    """A negative main-circle result needs a proved absence of the swept shape."""
    matches=[g for g in inputs.get('suaCenterGeometry') or []
             if g.get('playerObjectId')==player and g.get('projectileObjectId')==u['projectile']['projectileObjectId']
             and g.get('targetObjectId')==damage['targetObjectId'] and g.get('tick')==damage['tick']
             and g.get('castWireOrder')==u['start'].get('wireOrder')
             and g.get('damageWireOrder')==damage.get('wireOrder')]
    rows=[s for s in inputs.get('skill_rows') or [] if s.get('code')==u['start']['skillCode']]
    if len(matches)!=1 or len(rows)!=1 or order(damage) is None or order(u['start']) is None:return None
    g=matches[0]
    if not re.fullmatch('[0-9a-f]{64}',str(g.get('sourceProofSha256',''))):return None
    pairs=g.get('nativeRotationPairs')
    if not isinstance(pairs,list) or len(pairs)!=2:return None
    try:
        estimate=g.get('recordedPoseEstimate')
        if estimate is not None:
            from .skill_development_effect_metrics import development_policy
            policy=development_policy()
            if not (policy['enabled'] and policy.get('recordedSourceCellRegionEstimateEnabled') is True
                    and estimate.get('method') in ('recorded-straight-source-cell','recorded-same-position-warp')
                    and estimate.get('regionEstimated') is True and estimate.get('callbackPositionKnown') is False
                    and estimate.get('damageWireOrder')==damage['wireOrder']
                    and estimate.get('callbackTick')==damage['tick']
                    and type(estimate.get('anchorTick')) is int and estimate['anchorTick']<damage['tick']
                    and estimate['anchorWireOrder']<damage['wireOrder']):return None
            hits=[box_circle_contact(g['boxPivotXZ'],.8,rows[0]['length'],g['targetPivotXZ'],g['targetRadius'],p) for p in pairs]
            if len(set(hits))!=1:return None
            return ('center' if hits[0] else 'outside'),dict(g,regionEstimated=True,
                estimateMethod=estimate['method'],callbackPositionKnown=False,
                sourcePositionAgeTicks=damage['tick']-estimate['anchorTick'])
        cell=g.get('sourceCellSweepBound')
        if cell is not None:
            continuous=cell.get('ordinaryContinuity') if isinstance(cell,dict) else None
            source_frame=(isinstance(continuous,dict)
                and continuous.get('method')=='ordinary-movement-precedes-source-frame'
                and type(continuous.get('previousMoveTick'))is int
                and continuous['previousMoveTick']<cell['anchorTick']==cell['completedFrameTick']<damage['tick']
                and continuous.get('sourceMoveWireOrder')==cell.get('anchorWireOrder')
                and continuous['previousMoveWireOrder']<continuous['sourceMoveWireOrder']
                and cell['completedFrameRecord']==cell['anchorWireOrder'][0]<damage['wireOrder'][0])
            from .skill_sua_stop_source_bridge import valid_stop_bridge, METHOD as STOP_BRIDGE_METHOD
            stop_bridge=valid_stop_bridge(continuous,cell,damage)
            if isinstance(continuous,dict) and continuous.get('method')==STOP_BRIDGE_METHOD and not stop_bridge:return None
            same_frame_positive=(isinstance(continuous,dict)
                and continuous.get('method')=='same-frame-source-before-damage-positive-only'
                and cell.get('anchorTick')==cell.get('completedFrameTick')==damage['tick']
                and cell.get('completedFrameRecord')==damage['wireOrder'][0]
                and isinstance(cell.get('anchorWireOrder'),list)
                and cell['anchorWireOrder'][0]==damage['wireOrder'][0]
                and cell['anchorWireOrder']<damage['wireOrder']
                and continuous.get('sourceMoveWireOrder')==cell['anchorWireOrder'])
            if (isinstance(cell,dict) and cell.get('nativeRuleSha256')==SOURCE_CELL_SWEEP_RULE_SHA256
                    and cell.get('method')=='ordinary-source-cell-sweep-exclusion'
                    and cell.get('callbackTick')==damage['tick']
                    and cell.get('damageWireOrder')==damage['wireOrder']
                    and cell.get('castWireOrder')==u['start']['wireOrder']
                    and type(cell.get('anchorTick'))is int
                    and type(cell.get('completedFrameTick'))is int
                    and (cell['anchorTick']<cell['completedFrameTick']<damage['tick'] or source_frame or same_frame_positive or stop_bridge)
                    and cell.get('maxFrames')==damage['tick']-cell['anchorTick']+1
):
                args=(g['boxPivotXZ'],.8,rows[0]['length'],cell['encodedPositionVector2'],
                      g['targetRadius'],cell['maxStrategyMoveSpeed'],cell['maxFrames'],pairs)
                if stop_bridge:g=dict(g,stopSourceBridgeUsed=True,nativeReplayBuildIdentityProven=False,verifiedCompletionCredit=False)
                if not same_frame_positive and source_cell_sweep_excludes_center(*args):return 'outside',g
                if source_cell_sweep_guarantees_center(*args):
                    return 'center',dict(g,regionProof='entire-source-cell-movement-envelope-contacts-center')
                if stop_bridge:return None
                from .skill_development_effect_metrics import development_policy
                policy=development_policy()
                if policy['enabled'] and policy.get('recordedSourceCellRegionEstimateEnabled') is True:
                    from .skill_wire_position_precision import position_bounds,f32
                    bounds=position_bounds(cell['encodedPositionVector2'])
                    representative=[f32(sum(bounds[k])/2) for k in ('x','z')]
                    estimates=[box_circle_contact(g['boxPivotXZ'],.8,rows[0]['length'],
                                representative,g['targetRadius'],pair) for pair in pairs]
                    if len(set(estimates))==1:
                        return ('center' if estimates[0] else 'outside'),dict(g,
                            regionEstimated=True,estimateMethod='recorded-source-cell-representative',
                            callbackPositionKnown=False,sourcePositionAgeTicks=damage['tick']-cell['anchorTick'])

            return None
        hits=[box_circle_contact(g['boxPivotXZ'],.8,rows[0]['length'],g['targetPivotXZ'],g['targetRadius'],p) for p in pairs]
        if all(hits):return 'center',g
        sweep=g.get('ordinarySweepBound')
        if (isinstance(sweep,dict) and sweep.get('nativeRuleSha256')==BOOKMARK_STOP_SWEEP_RULE_SHA256
                and sweep.get('method')=='same-callback-bookmark-stop-ordinary-sweep'
                and sweep.get('callbackTick')==damage['tick']
                and type(sweep.get('previousFrameTick'))is int
                and type(sweep.get('moveTick'))is int
                and sweep['moveTick']<=sweep['previousFrameTick']<damage['tick']
                and type(sweep.get('previousFrameRecord'))is int
                and sweep['moveWireOrder'][0]<=sweep['previousFrameRecord']<damage['wireOrder'][0]
                and sweep.get('maxFrames')==damage['tick']-sweep['previousFrameTick']
                and sweep.get('damageWireOrder')==damage['wireOrder']
                and sweep.get('castWireOrder')==u['start']['wireOrder']
                and ordinary_sweep_excludes_center(g['boxPivotXZ'],.8,rows[0]['length'],
                    g['targetPivotXZ'],g['targetRadius'],sweep['maxHistoricalMoveSpeed'],pairs,sweep['maxFrames'])):
            return 'outside',g
        sub=g.get('subCollisionAbsent')
        if not any(hits) and isinstance(sub,dict) and sub.get('method')=='stationary-through-completed-server-post-frame' and sub.get('nativeRuleSha256')==STATIONARY_SUB_RULE_SHA256:
            stop,frame,callback=(sub.get(k) for k in ('targetStopTick','completedFrameTick','callbackTick'))
            stop_order=sub.get('targetStopWireOrder');record=sub.get('completedFrameRecord')
            if (all(type(t)is int for t in (stop,frame,callback,record)) and stop<frame<callback==damage['tick']
                and isinstance(stop_order,list) and len(stop_order)==2 and all(type(n)is int for n in stop_order)
                and stop_order[0]<record<damage['wireOrder'][0]):return 'outside',g
    except (KeyError,TypeError,ValueError,OverflowError):
        pass
    return None


def _outside_by_state_admission(u,damage,player,inputs,stun_code):
    """Absence only closes a contact when the native slow must be observable."""
    matches=[s for s in inputs.get('suaStateInventory') or []
        if s.get('playerObjectId')==player and s.get('localTargetObjectId')==damage['targetObjectId']
        and s.get('castWireOrder')==u['start']['wireOrder'] and s.get('wireOrder')==damage['wireOrder']]
    if len(matches)!=1:return None
    s=matches[0]
    from .skill_development_effect_metrics import development_policy
    policy=development_policy()
    issues=s.get('issues')
    resurrection_estimate=(policy['enabled'] and policy.get('historicalResurrectionAdmissionEstimateEnabled') is True
        and s.get('status')=='incomplete-state-inventory' and isinstance(issues,list) and bool(issues)
        and all(isinstance(e,dict) and e.get('reason')=='state-baseline-replacement-not-reconstructed'
            and e.get('event')=='CmdResurrection' and isinstance(e.get('wireOrder'),list)
            and len(e['wireOrder'])==2 and all(type(v)is int for v in e['wireOrder'])
            and e['wireOrder'][0]<damage['wireOrder'][0] for e in issues))
    if ((s.get('status')!='recorded-state-inventory' and not resurrection_estimate) or s.get('inventoryScope')!='whole-command-record'
            or s.get('callbackFrameUnique') is not True or s.get('boundaryTick')!=damage['tick']
            or (s.get('issues')!=[] and not resurrection_estimate) or s.get('callbackLifeEvents')!=[]
            or not re.fullmatch('[0-9a-f]{64}',str(s.get('sourceProofSha256','')))
            or not isinstance(s.get('possibleStates'),list)):return None
    if any(not r.get('stateType') or r['stateType'] in _ADMISSION_BLOCKERS or r.get('group')==1028230
           for r in s['possibleStates']):return None
    accepted=[e for e in s.get('acceptedBookmarkStates',[])
        if e.get('stateCode') in (1028121,stun_code) and e.get('event') in ('CmdAddState','CmdAddStateExtended')
        and isinstance(e.get('wireOrder'),list) and len(e['wireOrder'])==2
        and e['wireOrder'][0]==damage['wireOrder'][0] and e['wireOrder']>damage['wireOrder']]
    if not accepted:return None
    return 'outside',dict(method='recorded-callback-resurrection-admission-estimate' if resurrection_estimate else 'source-pinned-native-slow-admission-exclusion',
        regionEstimated=resurrection_estimate, unresolvedHistoricalResetIssues=issues if resurrection_estimate else [],
        nativeRuleSha256=SLOW_ADMISSION_RULE_SHA256,sourceProofSha256=s['sourceProofSha256'],
        admissionEnumCorrection=ENUM_CORRECTION_EVIDENCE,
        incomingSlowImmunityEvidence=SLOW_IMMUNITY_EVIDENCE,
        castWireOrder=s['castWireOrder'],damageWireOrder=s['wireOrder'],targetObjectId=damage['targetObjectId'],
        baselineRecord=s['baselineRecord'],baselinePayloadSha256=s['baselinePayloadSha256'],
        inventoryScope=s['inventoryScope'],acceptedBookmarkStates=accepted,
        completedRemovalAnchors=s.get('completedRemovalAnchors',[]))


def attach_sua_rq_details(result,spec,uses,player,teams,inputs,policy_uses=None):
    from .skill_sua_odyssey_profiles import PROFILES
    profile=PROFILES[spec['skillGroup']]
    enemy=lambda t:t in teams and teams[t]!=teams[player]
    normal={p['projectileObjectId'] for p in inputs['allProjectileSpawns'] if p['ownerObjectId']==player and p['projectileCode']==profile['competitor']}
    normal_frames={e['tick'] for e in inputs['terminals'] if e['objectId'] in normal and e['event']=='CmdDestroyDelayStart'}
    required={'CmdAddState','CmdAddStateExtended','CmdUpdateState','CmdResetCreateTimeState'}
    gap=any(g.get('count',0) and g.get('packetName') in required for g in inputs['gaps'])
    selected={'center':[],'bookmarkStun':[]};unknown={'center':[],'bookmarkStun':[]}
    # These are explicit level rows in the pinned game DB plus the restored
    # getters' dictionary key 2*R-level-1, not a numeric code-prefix join.
    codes=profile['states']
    for u in uses:
        bad={};cc={'center':set(),'bookmarkStun':set()};t=u['end']['tick']
        if gap:bad=dict(center='incomplete shared CC stream',bookmarkStun='incomplete shared CC stream')
        elif t in normal_frames:bad=dict(center='normal Q shares this callback frame',bookmarkStun='normal Q shares this callback frame')
        elif u['start']['skillCode'] not in codes:bad=dict(center='unknown copied Q level',bookmarkStun='unknown copied Q level')
        else:
            slow_code,stun_code=codes[u['start']['skillCode']]
            base={d['targetObjectId']:d for d in u['damages']};mark={d['targetObjectId']:d for d in u['marking']}
            ss=[s for s in inputs['states'] if s.get('casterObjectId')==player and s['tick']==t and s.get('event')!='remove'
                and (s.get('stateCode') in (*range(1028201,1028206),*range(1028231,1028236)) or s.get('stateGroup') in (1028200,1028230))]
            slow=[s for s in ss if s.get('stateCode') in range(1028231,1028236) or s.get('stateGroup')==1028230]
            stun=[s for s in ss if s.get('stateCode') in range(1028201,1028206) or s.get('stateGroup')==1028200]
            # A shared CC without the reviewed damage producer invalidates the
            # route; it must not leave the base metric reporting a fabricated miss.
            if any(enemy(s['targetObjectId']) and s['targetObjectId'] not in base for s in ss):
                return {**_unavailable(spec,'shared Q CC has no copied-Q primary damage witness'),
                    'observedCastCount':result['observedCastCount'],'observedCombatCastCount':result['observedCombatCastCount']}
            if any(order(s) is None or s['targetObjectId'] not in base or order(s)<=order(base[s['targetObjectId']])
                    or not ((s.get('event')=='add' and s.get('stateCode')==slow_code)
                            or (s.get('event')=='add' and s.get('stateCode') in {pair[0] for pair in codes.values()})
                            or (s.get('event')=='CmdResetCreateTimeState' and s.get('stateGroup')==1028230)) for s in slow):
                bad['center']='slow application/order differs from the center loop'
            elif slow:
                first=min(order(s) for s in slow);last=max(order(s) for s in slow);regions=[]
                for target,d in base.items():
                    if not enemy(target):continue
                    if order(d)<first:cc['center'].add((d['tick'],target));regions.append(dict(targetObjectId=target,region='center'))
                    elif order(d)>last:regions.append(dict(targetObjectId=target,region='outside'))
                    else:bad['center']='primary damage interleaves the slow loop'
                u['regionEvidence']=dict(boundaryStates=slow,enemyRegions=regions)
            elif any(enemy(target) for target in base):
                geometry=[]
                for target,d in base.items():
                    if not enemy(target):continue
                    g=_center_geometry(u,d,player,inputs)
                    admission=_outside_by_state_admission(u,d,player,inputs,stun_code)
                    if g and g[1].get('regionEstimated') and admission and not admission[1].get('regionEstimated'):
                        g=admission  # Existing native admission proof outranks position estimates.
                    if g and admission and g[0]!=admission[0]:
                        bad['center']='native geometry conflicts with state-admission evidence'
                    geometry.append((d,g or admission))
                known=[(d,g) for d,g in geometry if g is not None]
                proven_center={(d['tick'],d['targetObjectId']) for d,g in known if g[0]=='center'}
                if 'center' not in bad and geometry and (proven_center or len(known)==len(geometry)):
                    cc['center']=proven_center
                    u['regionEvidence']=dict(method='source-pinned-native-center-contact',
                        geometry=[g[1] for d,g in known],
                        enemyRegions=[dict(targetObjectId=d['targetObjectId'],region=g[0]) for d,g in known],
                        unresolvedTargetObjectIds=[d['targetObjectId'] for d,g in geometry if g is None])
                else:bad.setdefault('center','no accepted center slow exposes the region boundary')
            # Native getters read the current skill level at the callback;
            # a level-up during projectile flight does not change phase identity.
            if any(s.get('event')!='add' or s.get('stateCode') not in {pair[1] for pair in codes.values()} or order(s) is None
                    or s['targetObjectId'] not in mark or order(s)<=order(mark[s['targetObjectId']]) for s in stun):
                bad['bookmarkStun']='stun lacks exact preceding copied bookmark damage'
            else:
                cc['bookmarkStun']={(s['tick'],s['targetObjectId']) for s in stun if enemy(s['targetObjectId'])}
                u['bookmarkStunEvidence']=stun
        for name in selected:
            if name in bad:unknown[name].append(dict(cast=u['start'],reason=bad[name]))
            else:selected[name].append((u,cc[name]))
    parent_unknown=result.get('combatUnknownUseEvidence',[])
    for name,rows in selected.items():
        rows=sorted(rows+[(u,set()) for u in policy_uses or []],key=lambda item:order(item[0]['start']))
        contacts=[c for u,c in rows]
        p=_result(spec,contacts,'static-Sua-RQ-ordered-center-loop-and-bookmark-stun',cast_ticks=[u['start']['tick'] for u,c in rows])
        p['outcomes']=[exact_outcome(u['start']['tick'],u['projectile']['tick'],c) for u,c in rows]
        p.update(unresolvedCombatCastCount=len(unknown[name])+result.get('unresolvedCombatCastCount',0),unknownUseEvidence=[*unknown[name],*parent_unknown],
            unresolvedCastTicks=[e['cast']['tick'] for e in unknown[name]]+list(result.get('unresolvedCastTicks',[])),
            unresolvedBaseExecutionCount=result.get('unresolvedCombatCastCount',0),
            unresolvedCastReasons=dict(Counter(e['reason'] for e in unknown[name])+Counter(result.get('unresolvedCastReasons',{}))),
            perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,
            detailKnownForEverySelectedBaseUse=not unknown[name],stateAbsenceMeansOutside=False)
        partial_targets=[u for u,c in rows if name=='center' and u.get('regionEvidence',{}).get('unresolvedTargetObjectIds')]
        if partial_targets:
            p.update(targetCountsAreLowerBounds=True,completeTargetCountsAvailable=False,
                detailKnownForEverySelectedBaseUse=False,
                binarySuccessRetainedWithUnresolvedTargets=True,
                castsWithUnresolvedTargetRegions=len(partial_targets))
        estimates=[u for u,c in rows if name=='center' and any(
            g.get('regionEstimated') for g in u.get('regionEvidence',{}).get('geometry',[]))]
        if estimates:
            from .skill_development_effect_metrics import annotate_provisional_rate,development_policy
            p=annotate_provisional_rate(p,development_policy())
            p.update(developmentLabel='개발 중 · 중앙 위치 추정 포함',regionEstimateMayMisclassify=True,
                estimatedRegionCastCount=len(estimates),
                estimatedRegionCastTicks=[u['start']['tick'] for u in estimates],
                regionEstimateMeaning='기록된 이동 시작 위치·동일 위치 순간이동 또는 부활 이후 상태 기록을 이용한 지역 추정 포함. 실제 충돌 위치·면역 상태는 일부 미확정.')
        bridges=[u for u,c in rows if name=='center' and any(
            g.get('stopSourceBridgeUsed') for g in u.get('regionEvidence',{}).get('geometry',[]))]
        if bridges:
            from .skill_development_effect_metrics import annotate_provisional_rate,development_policy
            p=annotate_provisional_rate(p,development_policy())
            p.update(developmentLabel=('개발 중 · 중앙 위치 추정·정지 후 이동 범위 판정' if estimates else '개발 중 · 정지 후 이동 범위 판정'),
                stopSourceBridgeCastTicks=[u['start']['tick'] for u in bridges],
                nativeReplayBuildIdentityProven=False,verifiedCompletionCredit=False)
        result['phaseMetrics'][name]=p
    result.update(centerHitRateStatus=result['phaseMetrics']['center']['status'],
        bookmarkStunStatus=result['phaseMetrics']['bookmarkStun']['status'],
        detailedRequirementsComplete=not any(unknown.values()) and not result.get('unresolvedCombatCastCount',0) and not result['phaseMetrics']['center'].get('castsWithUnresolvedTargetRegions'),
        detailReason='Center uses an ordered slow boundary or source-pinned native geometry. Outside requires geometric exclusion or a complete native slow-admission witness. Missing CC alone stays unknown.')
    return result
