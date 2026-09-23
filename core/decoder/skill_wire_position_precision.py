"""Exact float32 preimages of the server's floor(float32(position*100)).

These are source-coordinate bounds, not positions at a later callback. Moving
coordinates still require a motion rule; callers must never choose a midpoint.
"""
from functools import lru_cache
import math,struct

def f32(value):return struct.unpack('<f',struct.pack('<f',value))[0]

def ordered_float(key):
    bits=(key^0x80000000) if key&0x80000000 else (~key)&0xffffffff
    return struct.unpack('<f',struct.pack('<I',bits))[0]

def encode_position(value):
    try:scaled=f32(f32(value)*100.0)
    except OverflowError:return math.copysign(math.inf,value)
    return math.floor(scaled) if math.isfinite(scaled) else scaled

@lru_cache(maxsize=4096)
def position_cell(encoded):
    if type(encoded)is not int or not -(2**31)<encoded<2**31:raise ValueError('position requires an int32 value excluding the conversion-overflow sentinel')
    def bound(strict):
        lo,hi=0x00800000,0xff800000
        while lo<hi:
            mid=(lo+hi)//2;v=encode_position(ordered_float(mid))
            if v>encoded if strict else v>=encoded:hi=mid
            else:lo=mid+1
        return lo
    first,last=bound(False),bound(True)-1
    if first>last or encode_position(ordered_float(first))!=encoded:raise ValueError('wire value has no finite float32 position preimage')
    return ordered_float(first),ordered_float(last)

def position_bounds(wire_xz):
    if not isinstance(wire_xz,list) or len(wire_xz)!=2:raise ValueError('position requires two wire components')
    return dict(encoding='floor-float32-times-100',x=list(position_cell(wire_xz[0])),z=list(position_cell(wire_xz[1])),
                endpointsInclusive=True,exactPosition=False,callbackPosition=False)


def requested_destination_bounds(encoded_position, relative_destination):
    """CmdMoveToDestination.GetVector3Destination fbe240, divisor 10000.

    Bound the requested destination over every possible encoded source float.
    This is not a navigation-adjusted destination or a callback pose.
    """
    bounds=position_bounds(encoded_position)
    if (not isinstance(relative_destination,list) or len(relative_destination)!=2
            or any(type(v)is not int or not -(2**31)<=v<2**31 for v in relative_destination)):
        raise ValueError('relative destination requires two int32 values')
    result={}
    for axis,wire in zip(('x','z'),relative_destination):
        offset=f32(f32(wire)/10000.0)
        result[axis]=[f32(v-offset) for v in bounds[axis]]
    return dict(**result,method='native-source-minus-relative-div10000',
                exactPosition=False,callbackPosition=False,navigationAdjusted=False)
