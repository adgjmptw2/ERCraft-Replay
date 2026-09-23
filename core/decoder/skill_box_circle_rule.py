"""Recovered CollisionBox2D/Circle2D branch used by Sua's center query.

This is a geometry kernel, not an event attribution or hit-rate calculator.
The caller supplies the native unrotation cosine/sine, actual collision pivots,
and actual circle radius. Approximate replay positions are not valid inputs.
"""
import math

try:
    from .skill_barbara_region_rule import _f32
except ImportError:
    from skill_barbara_region_rule import _f32


def box_circle_contact(box_pivot, width, depth, circle_pivot, radius,
                       native_cos_sin):
    """Native float32 broad phase and strict circle-to-box distance test.

    Tangency is excluded. The supplied rotation pair must be the results of
    GameAssembly's calls at 0x144b4a4/0x144b4b0 for the box direction; it is
    deliberately not reconstructed with Python trigonometry or a yaw guess.
    """
    if any(len(v) != 2 for v in (box_pivot, circle_pivot, native_cos_sin)):
        raise ValueError('two-component pivots and native rotation required')
    bx, bz = map(_f32, box_pivot)
    tx, tz = map(_f32, circle_pivot)
    c, s = map(_f32, native_cos_sin)
    w, d, r = map(_f32, (width, depth, radius))
    if min(w, d, r) < 0 or max(abs(c), abs(s)) > 1:
        raise ValueError('invalid collision dimensions or rotation')
    dx, dz = _f32(tx - bx), _f32(tz - bz)
    diagonal = _f32(math.sqrt(_f32(_f32(w*w) + _f32(d*d))))
    reach = _f32(r + _f32(diagonal * .5))
    distance2 = _f32(_f32(dx*dx) + _f32(dz*dz))
    if distance2 > _f32(reach*reach):
        return False
    # Keep the native rotate -> add pivot -> clamp -> subtract sequence.
    # Replacing this with an origin-relative clamp can change float32 edges.
    x = _f32(_f32(_f32(c*dx) - _f32(s*dz)) + bx)
    z = _f32(_f32(_f32(s*dx) + _f32(c*dz)) + bz)
    hw, hd = _f32(w*.5), _f32(d*.5)
    closest_x = max(_f32(bx-hw), min(_f32(bx+hw), x))
    closest_z = max(_f32(bz-hd), min(_f32(bz+hd), z))
    ex, ez = _f32(closest_x-x), _f32(closest_z-z)
    squared = _f32(_f32(ez*ez) + _f32(ex*ex))
    # f725e0 uses double sqrt of the float32 sum, then rounds back to float32.
    distance = _f32(math.sqrt(squared))
    return r > distance
STATIONARY_SUB_RULE_SHA256='9387986af8a2025579bf8f02fbf3b1bbf38ae439aae226a7cd067cdb162be840'
BOOKMARK_STOP_SWEEP_RULE_SHA256='43bc163f8bcc9290bf8d1bc8bcc8fa7d3d04d49cf93e1749aa5bf1d21f6c2e05'
SOURCE_CELL_SWEEP_RULE_SHA256='8ecf93996492025b3c904477d9ddfe59ee6d470f831f79631a41bd7f6038b0a8'
NORMALIZED_STEP_BOUND=1.001


def ordinary_sweep_excludes_center(box_pivot, width, depth, target_pivot, radius, speed, rotation_pairs, frames=1):
    """Strict exclusion of both main and swept shapes using the reviewed bound.

    This proves no intersection; it never estimates the previous position.
    The caller must prove the ordinary-strategy and same-callback stop witness.
    """
    values=[*box_pivot,*target_pivot,width,depth,radius,speed]
    if (len(box_pivot)!=2 or len(target_pivot)!=2
            or any(type(v)not in (int,float) or not math.isfinite(v) for v in values)
            or any(abs(v)>512 for v in (*box_pivot,*target_pivot))
            or type(frames)is not int or not 1<=frames<=3600
            or not 0<=speed<=128 or not 0<radius<=16 or not 0<width<=32 or not 0<depth<=32):
        raise ValueError('outside reviewed ordinary sweep domain')
    step=_f32(_f32(speed)*_f32(1/60))
    movement_bound=frames*(NORMALIZED_STEP_BOUND*step+1/128)
    # The 64-ulp coordinate budget also dominates this broad-phase rounding.
    reach=radius+movement_bound+math.hypot(width,depth)/2
    dx=target_pivot[0]-box_pivot[0];dz=target_pivot[1]-box_pivot[1]
    if dx*dx+dz*dz>reach*reach:return True
    if not isinstance(rotation_pairs,list) or len(rotation_pairs)!=2:
        raise ValueError('both native CPU rotations required')
    return not any(box_circle_contact(box_pivot,width,depth,target_pivot,
        radius+movement_bound,pair) for pair in rotation_pairs)


def source_cell_sweep_excludes_center(box_pivot,width,depth,encoded_position,radius,speed,frames,rotation_pairs):
    """An enclosing region is evidence only of exclusion, never of a hit."""
    try:
        from .skill_wire_position_precision import position_bounds
    except ImportError:
        from skill_wire_position_precision import position_bounds
    bounds=position_bounds(encoded_position)
    if (type(frames)is not int or not 1<=frames<=3600
            or len(box_pivot)!=2 or any(type(v)not in (int,float) or not math.isfinite(v) for v in box_pivot)
            or any(type(v)not in (int,float) or not math.isfinite(v) for v in (radius,speed,width,depth))
            or not 0<radius<=16 or not 0<=speed<=128 or not 0<width<=32 or not 0<depth<=32
            or not isinstance(rotation_pairs,list) or len(rotation_pairs)!=2):
        raise ValueError('outside reviewed source-cell sweep domain')
    if any(not isinstance(pair,(list,tuple)) or len(pair)!=2
            or any(type(v)not in (int,float) or not math.isfinite(v) or abs(v)>1 for v in pair) for pair in rotation_pairs):
        raise ValueError('invalid native source-cell rotations')
    center=[_f32(sum(bounds[k])/2) for k in ('x','z')]
    if any(abs(v)>512 for v in (*center,*box_pivot)):
        raise ValueError('outside reviewed source-cell coordinate domain')
    cell_radius=max(math.hypot(x-center[0],z-center[1]) for x in bounds['x'] for z in bounds['z'])
    reach=radius+cell_radius+frames*(NORMALIZED_STEP_BOUND*_f32(_f32(speed)*_f32(1/60))+1/128)
    if reach>512:raise ValueError('source-cell envelope exceeds reviewed domain')
    return not any(box_circle_contact(box_pivot,width,depth,center,reach,pair) for pair in rotation_pairs)


def source_cell_sweep_guarantees_center(box_pivot,width,depth,encoded_position,radius,speed,frames,rotation_pairs):
    """Prove contact for every point of the existing movement envelope.

    Distance to a convex box is 1-Lipschitz. Shrinking the target radius by
    the enclosing movement/cell radius proves contact for every possible
    target position, rather than treating a cell midpoint as the position.
    The same per-frame float rounding margin as exclusion is retained.
    """
    source_cell_sweep_excludes_center(box_pivot,width,depth,encoded_position,radius,speed,frames,rotation_pairs)
    try:
        from .skill_wire_position_precision import position_bounds
    except ImportError:
        from skill_wire_position_precision import position_bounds
    bounds=position_bounds(encoded_position)
    center=[_f32(sum(bounds[k])/2) for k in ('x','z')]
    cell_radius=max(math.hypot(x-center[0],z-center[1]) for x in bounds['x'] for z in bounds['z'])
    margin=cell_radius+frames*(NORMALIZED_STEP_BOUND*_f32(_f32(speed)*_f32(1/60))+1/128)
    inner_radius=radius-margin
    if inner_radius<=0:return False
    return all(box_circle_contact(box_pivot,width,depth,center,inner_radius,pair) for pair in rotation_pairs)
