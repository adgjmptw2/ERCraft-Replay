"""Recovered base-E region branch, independent of replay pose reconstruction.

Inputs must be exact server-evaluation positions and the explosion's SkillData
innerRange from the pinned game DB. This function does not establish contact,
cast ownership, or the observation time. Call it only for an attributed contact.
Native evidence: deliverables/barbara-region-formula-v1.json.
"""
import math
import struct


def _f32(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('exact finite numeric coordinate/radius required')
    try:
        result = struct.unpack('<f', struct.pack('<f', value))[0]
    except (OverflowError, struct.error) as exc:
        raise ValueError('coordinate/radius outside float32 range') from exc
    if not math.isfinite(result):
        raise ValueError('exact finite numeric coordinate/radius required')
    return result


def barbara_contact_region(center_xz, target_xz, inner_range):
    """Classify an already confirmed base explosion contact; boundary is inner.

    Float32 at every native SUBSS/MULSS/ADDSS instruction. No target collision
    radius, Y coordinate, CC-absence inference, or distance epsilon is applied.
    """
    if len(center_xz) != 2 or len(target_xz) != 2:
        raise ValueError('two exact XZ coordinates required')
    cx, cz = map(_f32, center_xz)
    tx, tz = map(_f32, target_xz)
    radius = _f32(inner_range)
    if radius < 0:
        raise ValueError('nonnegative pinned innerRange required')
    dz = _f32(tz - cz)
    dx = _f32(tx - cx)
    z2, x2 = _f32(dz * dz), _f32(dx * dx)
    distance2 = _f32(x2 + z2)
    radius2 = _f32(radius * radius)
    return 'inner' if distance2 <= radius2 else 'outer'
