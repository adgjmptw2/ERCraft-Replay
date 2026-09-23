"""Invert the native frame-to-fixed-point timestamp without guessing a frame.

This recovers the frame represented by a timestamp. A state's createdTime is
NOT necessarily the frame at which its update packet was sent. Callers must
separately prove which state creation/reset the timestamp belongs to.
"""
import math
import struct
from functools import lru_cache


def _f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


FRAME_STEP = _f32(1 / 60)
MAX_UPDATE_SEQ = (1 << 31) - 1


def _encoded_frame_time(frame):
    # CharacterState.Start/ResetCreateTime -> CompleteChangedState.
    # cvtdq2ps, mulss(1/60), mulss(100), floor, cvttsd2si.
    return math.floor(_f32(_f32(_f32(frame) * FRAME_STEP) * 100))


def frame_time_preimage(internal_value):
    """Inclusive nonnegative int32 UpdateSeq interval, or None if impossible.

    All preimages are retained, including float32 plateaus in very long games.
    Invalid conversions (int32 minimum sentinel) and malformed values fail.
    The bounds are binary-searched over the complete supported integer domain.
    """
    if type(internal_value) is not int or not 0 <= internal_value <= MAX_UPDATE_SEQ:
        return None
    return _frame_time_preimage(internal_value)


@lru_cache(maxsize=32768)
def _frame_time_preimage(internal_value):
    # A timestamp's complete float32 preimage is immutable. Validate before
    # entering the cache, including malformed/unhashable caller inputs.

    def lower_bound(value):
        lo, hi = 0, MAX_UPDATE_SEQ + 1
        while lo < hi:
            mid = (lo + hi) // 2
            if _encoded_frame_time(mid) < value:
                lo = mid + 1
            else:
                hi = mid
        return lo

    first = lower_bound(internal_value)
    stop = lower_bound(internal_value + 1)
    return (first, stop - 1) if first < stop else None
