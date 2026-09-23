"""Retained stationary-to-ordinary source-frame bridge; no point estimates."""
import math
from .skill_wire_order import command_order
from .skill_wire_position_precision import position_bounds
from .skill_pose_events import f32

RULE_SHA256='bc43306e469140ea41f64f2647ee6500ae52acf95087ab102af960f8ed0283a8'
METHOD='retained-stop-precedes-ordinary-source-frame'

def valid_stop_bridge(witness, cell, damage):
    try:
        if not isinstance(witness,dict) or witness.get('method')!=METHOD or witness.get('nativeRuleSha256')!=RULE_SHA256:return False
        keys=('stopTick','stationaryCompletedFrameTick','stationaryCompletedFrameRecord')
        if any(type(witness.get(k)) is not int for k in keys):return False
        stop=witness['stopWireOrder'];anchor=cell['anchorWireOrder'];at=damage['wireOrder']
        if any(not isinstance(o,list) or len(o)!=2 or any(type(n)is not int or n<0 for n in o) for o in (stop,anchor,at)):return False
        if not (witness['stopTick']<witness['stationaryCompletedFrameTick']<cell['anchorTick']==cell['completedFrameTick']<damage['tick']):return False
        if not (stop[0]<witness['stationaryCompletedFrameRecord']<anchor[0]==cell['completedFrameRecord']<at[0]):return False
        if witness.get('sourceMoveWireOrder')!=anchor or witness.get('interveningPositionEvents')!=0:return False
        if witness.get('stopEvent')!='CmdStopMove' or witness.get('sourceMoveEvent') not in ('CmdMoveToDestination','CmdMoveToDestinationAvoidance'):return False
        if witness.get('encodedPositionVector2')!=cell['encodedPositionVector2']:return False
        point=witness['stopPositionVector2'];bounds=position_bounds(cell['encodedPositionVector2'])
        if not isinstance(point,list) or len(point)!=2:return False
        return all(type(v)in(int,float) and math.isfinite(v) and f32(v)==v and bounds[k][0]<=v<=bounds[k][1] for v,k in zip(point,('x','z')))
    except (KeyError,TypeError,ValueError,OverflowError):return False

def stop_source_bridge(previous, anchor, frames, damage):
    if not previous or previous.get('event')!='CmdStopMove' or command_order(previous) is None or command_order(anchor) is None:return None
    stationary=[(t,r) for t,r in frames if previous['tick']<t<anchor['tick'] and previous['wireOrder'][0]<r<anchor['wireOrder'][0]]
    if not stationary or (anchor['tick'],anchor['wireOrder'][0]) not in frames:return None
    t,r=stationary[-1]
    witness=dict(method=METHOD,nativeRuleSha256=RULE_SHA256,nativeReplayBuildIdentityProven=False,verifiedCompletionCredit=False,stopEvent=previous['event'],
        stopTick=previous['tick'],stopWireOrder=previous['wireOrder'],stopPositionVector2=previous.get('positionVector2'),
        sourceMoveEvent=anchor['event'],sourceMoveWireOrder=anchor['wireOrder'],encodedPositionVector2=anchor.get('encodedPositionVector2'),
        stationaryCompletedFrameTick=t,stationaryCompletedFrameRecord=r,interveningPositionEvents=0)
    cell=dict(anchorTick=anchor['tick'],anchorWireOrder=anchor['wireOrder'],completedFrameTick=anchor['tick'],
        completedFrameRecord=anchor['wireOrder'][0],encodedPositionVector2=anchor.get('encodedPositionVector2'))
    return witness if valid_stop_bridge(witness,cell,damage) else None
