"""Exact bounded native rotation from decoded positions; no game/DLL execution."""
import base64,hashlib,json,math
from functools import lru_cache
from pathlib import Path
from capstone import Cs,CS_ARCH_X86,CS_MODE_64
try:
    from .static_float_evaluator import evaluate,f32
except ImportError:
    from static_float_evaluator import evaluate,f32

MATH_SHA256='e8e58a46e8751afc7e971dd1651c6ed82022b96bf925fe5b35f6f620302d8cd6'

@lru_cache(maxsize=1)
def native_math():
    path=Path(__file__).resolve().parent.parent/'schema/game-native-center-math-v1.json'
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=MATH_SHA256:raise ValueError('native math artifact mismatch')
    doc=json.loads(data);cs=Cs(CS_ARCH_X86,CS_MODE_64);cs.detail=True
    program={int(at):next(cs.disasm(bytes.fromhex(code),int(at))) for at,code in doc['instructions'].items()}
    pages={int(at):base64.b64decode(data,validate=True) for at,data in doc['pages'].items()}
    def read(at,n):
        try:return bytes(pages[a&~4095][a&4095] for a in range(at,at+n))
        except KeyError as e:raise ValueError('unpackaged native constant address') from e
    return program,read,doc['constants']

def sua_center_rotation(caster,point):
    if any(not isinstance(p,(list,tuple)) or len(p)!=2 or
           any(type(v) not in (int,float) or not math.isfinite(v) or f32(v)!=v for v in p) for p in (caster,point)):
        raise ValueError('non-exact decoded position')
    program,read,k=native_math()
    x=f32(point[0]-caster[0]);z=f32(point[1]-caster[1])
    length=f32(math.sqrt(f32(f32(z*z)+f32(x*x))))
    if length<=k['normalizationEpsilon']:raise ValueError('unreviewed zero skill direction')
    x=f32(x/length);z=f32(z/length)
    norm=f32(f32(x*x)+f32(z*z))
    if norm<k['angleNormThreshold']:raise ValueError('unreviewed degenerate angle')
    denominator=f32(math.sqrt(norm));cos_angle=min(1.0,max(-1.0,f32(z/denominator)))
    pairs=[];traces=[]
    for branch in (0,3):
        kw=dict(memory_overrides={0xaac15d4:branch})
        angle,acos_trace=evaluate(program,read,0x88b750,float(cos_angle),input_bits=64,output_bits=64,domain=(-1,1),**kw)
        degrees=f32(f32(angle)*k['radiansToDegrees'])
        degrees=f32(degrees*(-1.0 if x<0 else 1.0))
        radians=f32(degrees*k['degreesToRadians'])
        cos,ct=evaluate(program,read,0x88d690,radians,domain=(-f32(math.pi),f32(math.pi)),**kw)
        sin,st=evaluate(program,read,0x8915a0,radians,domain=(-f32(math.pi),f32(math.pi)),**kw)
        pairs.append([cos,sin]);traces.append(dict(branch=branch,radians=radians,instructions=len(acos_trace)+len(ct)+len(st)))
    return dict(direction=[x,0.0,z],nativeRotationPairs=pairs,nativeMathSha256=MATH_SHA256,branches=traces)
