"""Source-proved target pivot envelopes, shared by native region routes."""
import math
from bisect import bisect_left,bisect_right
try:
    from .skill_wire_order import command_order as order
    from .skill_pose_events import f32
    from .skill_wire_position_precision import position_bounds
except ImportError:
    from skill_wire_order import command_order as order
    from skill_pose_events import f32
    from skill_wire_position_precision import position_bounds

def _frame_index(inputs,index):
    # One pose index serves every contact in this source. Reuse its frame
    # index instead of scanning the complete match again for every bound.
    cached=getattr(index,'_evaluation_frame_index',None)
    if cached is None or cached[0] is not inputs['frames']:
        pairs=sorted((t,r) for t,r in inputs['frames'])
        cached=(inputs['frames'],pairs,[t for t,r in pairs],set(pairs))
        index._evaluation_frame_index=cached
    return cached[1:]


def _curve_fields_valid(anchor):
    fields=[anchor.get(k) for k in ('moveSpeed','angularSpeed',
            'startRotationAngleFromForward','targetDirectionAngleFromForward')]
    return (type(anchor.get('isStatSpeed')) is bool
        and all(type(v) in(int,float) and math.isfinite(v) and f32(v)==v for v in fields)
        and 1<=fields[0]<=128 and 60<=abs(fields[1])<=3600
        and all(abs(v)<=360 for v in fields[2:]))


def evaluation_bounds(inputs,index,target,damage,finish,*,_depth=0,current_position_only=False):
    at=order(damage)
    if target in index.bad:raise ValueError('incomplete-target-pose-stream')
    # Damage precedes this branch. Any position command in the remainder of
    # the synchronous child makes the before-damage pivot insufficient.
    if any(at<order(e)<order(finish) for e in index.rows.get((target,'position'),[])):
        raise ValueError('target-position-command-during-explosion')
    anchor=index.before(target,'position',at)
    ordinary={'CmdMoveToDestination','CmdMoveToDestinationAvoidance'}
    if anchor and anchor['event']=='CmdStopMove' and anchor['tick']==damage['tick']:
        previous=index.before(target,'position',order(anchor))
        p=anchor.get('positionVector2')
        prior_supported=previous and (previous['event'] in ordinary or
            previous['event']=='CmdMoveInCurve' and _curve_fields_valid(previous))
        if (prior_supported and previous['tick']<anchor['tick']
                and anchor['wireOrder'][0]==at[0]
                and (anchor['tick'],anchor['wireOrder'][0]) in _frame_index(inputs,index)[2]
                and isinstance(p,list) and len(p)==2
                and all(type(v) in(int,float) and math.isfinite(v) and abs(v)<=512 and f32(v)==v for v in p)):
            if _depth>=64:raise ValueError('same-frame-stop-chain-too-deep')
            prior,prior_evidence=evaluation_bounds(inputs,index,target,anchor,anchor,_depth=_depth+1)
            bounds={k:[min(prior[k][0],p[i]),max(prior[k][1],p[i])] for i,k in enumerate(('x','z'))}
            return bounds,dict(method='same-frame-stop-and-prior-motion-envelope',
                anchorWireOrder=anchor['wireOrder'],stopPositionVector2=p,priorBounds=prior,
                priorEvidence=prior_evidence,nativeMovementRule='schema/same-frame-stop-prior-union-v1.json')
    same_frame_ordinary=False
    if anchor and anchor['tick']==damage['tick'] and anchor['event'] in ordinary:
        previous=index.before(target,'position',order(anchor))
        same_frame_ordinary=bool(previous and previous['event'] in ordinary
            and previous['tick']<anchor['tick'] and anchor['wireOrder'][0]==at[0]
            and (anchor['tick'],anchor['wireOrder'][0]) in _frame_index(inputs,index)[2])
    if not anchor or anchor['tick']>damage['tick'] or anchor['tick']==damage['tick'] and not same_frame_ordinary:
        raise ValueError('missing-prior-frame-position-anchor')
    if anchor['event']=='CmdMoveStraight':
        if _depth>=64:raise ValueError('straight-motion-chain-too-deep')
        end=anchor.get('destinationVector2');duration=anchor.get('durationInternalValue');ease=anchor.get('ease')
        frames=damage['tick']-anchor['tick']+1
        if (type(duration)is not int or not 0<duration<=360000 or not 1<=frames<=3600
                or type(ease)is not int or ease not in (1,4,21)
                or not isinstance(end,list) or len(end)!=2
                or any(type(v)not in(int,float) or not math.isfinite(v) or abs(v)>512 or f32(v)!=v for v in end)):
            raise ValueError('straight-motion-fields-unavailable')
        prior,prior_evidence=evaluation_bounds(inputs,index,target,anchor,anchor,_depth=_depth+1)
        if any(abs(v)>512 for k in ('x','z') for v in prior[k]):
            raise ValueError('outside-reviewed-movement-domain')
        seconds=f32(f32(duration)/100)
        phase=min(1.0,frames*f32(1/60)*(1+2**-23)**(frames+4)/seconds)
        if ease==21:factor=phase
        elif ease==1:factor=min(1.0,phase*(2-phase)+2**-20)
        else:factor=min(1.0,1-(1-phase)**3+2**-20)
        # Height correction preserves XZ. The following NavMesh query moves the
        # endpoint at most five units; failed queries retain the requested XZ.
        # Keep every such endpoint and every phase, including paused movement.
        radius=5+1/128;bounds={};padding=(frames+1)/128
        for i,k in enumerate(('x','z')):
            lo,hi=prior[k]
            bounds[k]=[min(lo,(1-factor)*lo+factor*(end[i]-radius))-padding,
                       max(hi,(1-factor)*hi+factor*(end[i]+radius))+padding]
        return bounds,dict(method='straight-nav-radius-prefix-and-prior-pivot-envelope',
            anchorWireOrder=anchor['wireOrder'],requestedDestinationVector2=end,
            durationInternalValue=duration,ease=ease,maxFrames=frames,maxPhase=phase,maxEasedFactor=factor,
            endpointCorrectionRadius=radius,roundoffPadding=padding,priorBounds=prior,
            priorEvidence=prior_evidence,nativeMovementRule='schema/straight-nav-radius-prefix-bound-v1.json')
    if anchor['event']=='CmdMoveStraightWithoutNav':
        if _depth>=64:raise ValueError('non-nav-motion-chain-too-deep')
        start=anchor.get('startPosVector2');end=anchor.get('endPosVector2')
        duration=anchor.get('durationInternalValue');frames=damage['tick']-anchor['tick']+1
        if (type(duration)is not int or not 0<duration<=360000 or not 1<=frames<=3600
                or any(not isinstance(p,list) or len(p)!=2 or any(type(v) not in (int,float)
                    or not math.isfinite(v) or abs(v)>512 or f32(v)!=v for v in p) for p in (start,end))):
            raise ValueError('non-nav-motion-fields-unavailable')
        # Prior motion can still supply the pivot before the first update, or
        # while movement is paused. Keep its entire bound, not just startPos.
        prior,prior_evidence=evaluation_bounds(inputs,index,target,anchor,anchor,_depth=_depth+1)
        seconds=f32(f32(duration)/100)
        # At most age+1 server updates; include float32 accumulation and division
        # error. Native clamping is preserved, and no precise phase is assumed.
        phase=min(1.0,frames*f32(1/60)*(1+2**-23)**(frames+4)/seconds)
        bounds={};padding=(frames+1)/128
        for i,k in enumerate(('x','z')):
            limit=start[i]+(end[i]-start[i])*phase
            bounds[k]=[min(prior[k][0],start[i],limit)-padding,
                       max(prior[k][1],start[i],limit)+padding]
        return bounds,dict(method='non-nav-linear-prefix-and-prior-pivot-envelope',
            anchorWireOrder=anchor['wireOrder'],startPosVector2=start,endPosVector2=end,
            durationInternalValue=duration,maxFrames=frames,maxPhase=phase,roundoffPadding=padding,
            priorBounds=prior,priorEvidence=prior_evidence,
            nativeMovementRule='schema/non-nav-linear-prefix-bound-v1.json')
    if anchor['event']=='CmdStopMove':
        p=anchor.get('positionVector2')
        if not isinstance(p,list) or len(p)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) or f32(v)!=v for v in p):
            raise ValueError('non-exact-stop-position')
        return dict(x=[p[0],p[0]],z=[p[1],p[1]]),dict(method='unchanged-stop-pivot',anchorWireOrder=anchor['wireOrder'])
    curve=anchor['event']=='CmdMoveInCurve'
    if not curve and anchor['event'] not in ordinary:raise ValueError('unsupported-moving-strategy')
    if curve and not _curve_fields_valid(anchor):
        raise ValueError('curve-speed-or-turn-domain-unavailable')
    cell=position_bounds(anchor.get('encodedPositionVector2'))
    if any(abs(v)>512 for k in ('x','z') for v in cell[k]):raise ValueError('outside-reviewed-movement-domain')
    frame_pairs,frame_ticks,frame_set=_frame_index(inputs,index)
    lo=bisect_right(frame_ticks,anchor['tick']);hi=bisect_left(frame_ticks,damage['tick'])
    completed=any(anchor['wireOrder'][0]<r<at[0] for t,r in frame_pairs[lo:hi])
    curve_predecessor=None
    post_stop_current=False
    if not completed and not same_frame_ordinary:
        previous=index.before(target,'position',order(anchor))
        completed=(anchor['tick'],anchor['wireOrder'][0]) in frame_set and anchor['wireOrder'][0]<at[0]
        continuous=previous and (previous['event']=='CmdMoveInCurve' and _curve_fields_valid(previous)
                                if curve else previous['event'] in ordinary)
        # A current-position getter cannot read the pre-stop collision pivot.
        # Only a source-bound single-recipient FIFO witness may establish that
        # this ordinary source cell was captured after the stop correction.
        proof=inputs.get('postStopCurrentPositionOrder') or {}
        older=index.before(target,'position',order(previous)) if previous else None
        post_stop_current=bool(current_position_only and not curve and completed
            and previous and previous['event']=='CmdStopMove'
            and previous['tick']==anchor['tick']
            and previous['wireOrder'][0]==anchor['wireOrder'][0]
            and older and older['event'] in ordinary and older['tick']<anchor['tick']
            and proof.get('matchKey')==inputs.get('matchKey') and bool(inputs.get('matchKey'))
            and proof.get('rule')=='schema/post-stop-current-position-bound-v1.json'
            and any(p==dict(objectId=target,tick=anchor['tick'],stopWireOrder=previous['wireOrder'],
                           anchorWireOrder=anchor['wireOrder']) for p in proof.get('pairs',[]))
            and not any(e['wireOrder'][0]==at[0] for e in index.rows.get((target,'position'),[])))
        if not post_stop_current and (not completed or not continuous or previous['tick']>=anchor['tick']):
            raise ValueError('ordinary-source-frame-not-closed')
        if curve:curve_predecessor=previous
    if curve:
        # Setup records the current speed. Unlike ordinary movement, this
        # strategy does not require an older Stop to initialize that value.
        reset=order(anchor);initial=anchor['moveSpeed']
        speeds=inputs['speedEvents'].get(str(target),[]) if anchor['isStatSpeed'] else []
    else:
        resets=[e for e in index.rows[target,'position'] if e['event']=='CmdStopMove'
                and inputs['initialSnapshotTick']<=e['tick'] and order(e)<order(anchor)]
        if not resets:raise ValueError('ordinary-strategy-has-no-retained-reset')
        reset=order(resets[-1]);speeds=inputs['speedEvents'].get(str(target),[])
        earlier=[e['speed'] for e in speeds if tuple(e['wireOrder'])<=reset]
        initial=earlier[-1] if earlier else inputs['initialSpeeds'].get(str(target))
    values=[initial,*[e['speed'] for e in speeds if reset<tuple(e['wireOrder'])<order(finish)]]
    if curve_predecessor:
        # The completed source frame may include movement before the latest
        # curve command. Enclose its preceding strategy speed as well.
        values.append(curve_predecessor['moveSpeed'])
        if curve_predecessor['isStatSpeed']:
            values.extend(e['speed'] for e in inputs['speedEvents'].get(str(target),[])
                          if order(curve_predecessor)<tuple(e['wireOrder'])<order(anchor))
    frames=damage['tick']-anchor['tick']+1
    if not 1<=frames<=3600 or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=128 for v in values):
        raise ValueError('ordinary-speed-or-frame-bound-unavailable')
    padding=1/16 if curve else 1/128
    speed=max(values);reach=frames*(1.001*f32(f32(speed)*f32(1/60))+padding)
    if reach>512:raise ValueError('ordinary-envelope-too-wide')
    bounds={k:[cell[k][0]-reach,cell[k][1]+reach] for k in ('x','z')}
    evidence=dict(method='ordinary-source-cell-pivot-envelope',anchorWireOrder=anchor['wireOrder'],
        sourceCell=cell,maxFrames=frames,maxStrategyMoveSpeed=speed,displacementBound=reach,
        resetWireOrder=list(reset),nativeMovementRule='schema/sua-source-cell-sweep-bound-v3.json')
    if post_stop_current:
        evidence.update(method='post-stop-current-position-source-cell-envelope',
                        stopWireOrder=previous['wireOrder'],currentPositionOnly=True,
                        nativeMovementRule='schema/post-stop-current-position-bound-v1.json')
    if same_frame_ordinary:
        evidence.update(method='ordinary-same-frame-source-cell-pivot-envelope',
            previousOrdinaryWireOrder=previous['wireOrder'],
            nativeMovementRule='schema/ordinary-same-frame-source-cell-bound-v1.json')
    if curve:
        evidence.update(method='curve-source-cell-travel-budget-envelope',roundoffPaddingPerFrame=padding,
                        nativeMovementRule='schema/curve-travel-budget-bound-v1.json')
        if curve_predecessor:evidence['sourceFramePreviousCurveWireOrder']=curve_predecessor['wireOrder']
    # First subsequent position event only: never jump across a warp, stop,
    # visibility reset, or unsupported strategy to obtain a tighter witness.
    # Recursive callers evaluate immediately before a movement command. That
    # command AT the boundary is a barrier too; do not skip across its strategy.
    key=(target,'position');n=bisect_left(index.orders.get(key,[]),at)
    following=index.rows[key][n] if n<len(index.rows[key]) else None
    following_kinds={'CmdMoveInCurve'} if curve else ordinary
    if following and following['event'] in following_kinds and order(following)>order(finish):
        later_frames=following['tick']-damage['tick']+1
        future_values=[*values,*[e['speed'] for e in speeds if order(finish)<=tuple(e['wireOrder'])<=order(following)]]
        try:later_cell=position_bounds(following.get('encodedPositionVector2'))
        except (ValueError,TypeError):later_cell=None
        if (later_cell is not None and 1<=later_frames<=3600
                and all(abs(v)<=512 for k in ('x','z') for v in later_cell[k])
                and all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=128 for v in future_values)):
            later_speed=max(future_values)
            later_reach=later_frames*(1.001*f32(f32(later_speed)*f32(1/60))+padding)
            if later_reach<=512:
                intersection={k:[max(bounds[k][0],later_cell[k][0]-later_reach),
                                 min(bounds[k][1],later_cell[k][1]+later_reach)] for k in ('x','z')}
                if any(lo>hi for lo,hi in intersection.values()):
                    raise ValueError('ordinary-position-envelope-conflict')
                if intersection!=bounds:
                    bounds=intersection
                    evidence.update(method='ordinary-two-source-cell-pivot-envelope',
                        followingWireOrder=following['wireOrder'],followingSourceCell=later_cell,
                        followingMaxFrames=later_frames,followingMaxStrategyMoveSpeed=later_speed,
                        followingDisplacementBound=later_reach,
                        followingNativeRule='schema/ordinary-next-source-cell-bound-v1.json')
                    if curve:
                        evidence.update(method='curve-two-source-cell-travel-budget-envelope',
                                        followingNativeRule='schema/curve-travel-budget-bound-v1.json')
    return bounds,evidence

