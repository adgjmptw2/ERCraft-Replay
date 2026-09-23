"""Explicit developmental positions; never an exact callback pose witness."""
from .skill_pose_events import f32
from .skill_wire_position_precision import position_bounds


def recorded_pose_estimate(index, who, at, tick):
    if who in index.bad:
        raise ValueError('incomplete-pose-stream')
    anchor = index.before(who, 'position', tuple(at))
    if not anchor or anchor['tick'] >= tick:
        raise ValueError('missing-prior-recorded-pose')
    event = anchor['event']
    if event == 'CmdMoveStraight':
        bounds = position_bounds(anchor.get('encodedPositionVector2'))
        point = [f32(sum(bounds[k]) / 2) for k in ('x', 'z')]
        method = 'recorded-straight-source-cell'
    elif event == 'CmdWarpTo':
        point = anchor.get('destinationVector2')
        previous = index.before(who, 'position', tuple(anchor['wireOrder']))
        while previous and previous['event'] == 'CmdWarpTo' and previous.get('destinationVector2') == point:
            previous = index.before(who, 'position', tuple(previous['wireOrder']))
        if not previous or previous['event'] != 'CmdStopMove' or previous.get('positionVector2') != point:
            raise ValueError('warp-destination-not-retained-stop')
        method = 'recorded-same-position-warp'
    else:
        raise ValueError('unsupported-recorded-pose-estimate')
    if not isinstance(point, list) or len(point) != 2 or any(type(v) not in (int, float) or f32(v) != v for v in point):
        raise ValueError('invalid-recorded-pose-estimate')
    return point, anchor, dict(method=method, anchorTick=anchor['tick'],
        anchorWireOrder=anchor['wireOrder'], damageWireOrder=list(at), callbackTick=tick,
        callbackPositionKnown=False, regionEstimated=True)
