import struct, json, brotli
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / 'schema' / 'schema.json'
S=json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
CLASSES=S['classes']; ENUMS=S['enums']; SUBS=S['subclasses']
STRUCTS={'Vector2Int':('2i',8),'Vector2':('2f',8),'Vector3':('3f',12),'Vector3Int':('3i',12),
         'Quaternion':('4f',16),'Color':('4f',16),'Color32':('4B',4)}
UNIONS={'SnapshotWrapper':['SnapshotWrapperBasic','SnapshotWrapperFull']}

class R:
    def __init__(s,b): s.b=b; s.o=0
    def u8(s): v=s.b[s.o]; s.o+=1; return v
    def i16(s): v=struct.unpack_from('<h',s.b,s.o)[0]; s.o+=2; return v
    def u16(s): v=struct.unpack_from('<H',s.b,s.o)[0]; s.o+=2; return v
    def i32(s): v=struct.unpack_from('<i',s.b,s.o)[0]; s.o+=4; return v
    def u32(s): v=struct.unpack_from('<I',s.b,s.o)[0]; s.o+=4; return v
    def i64(s): v=struct.unpack_from('<q',s.b,s.o)[0]; s.o+=8; return v
    def f32(s): v=struct.unpack_from('<f',s.b,s.o)[0]; s.o+=4; return v
    def raw(s,n): v=s.b[s.o:s.o+n]; s.o+=n; return v

PRIM={'int':('i32',),'uint':('u32',),'long':('i64',),'ulong':('i64',),'short':('i16',),
      'ushort':('u16',),'byte':('u8',),'sbyte':('u8',),'bool':('u8',),'float':('f32',),
      'char':('u16',),'double':('raw',8)}

def read(r, typ, depth=0):
    t=typ
    if t=='byte[]':
        n=r.i32()
        if n<0: return None
        return r.raw(n)
    # 배열
    if t.endswith('[]'):
        el=t[:-2]
        n=r.i32()
        if n<0: return None
        return [read(r,el,depth+1) for _ in range(n)]
    if t.startswith('List<'):
        el=t[5:-1]; n=r.i32()
        if n<0: return None
        return [read(r,el,depth+1) for _ in range(n)]
    if t.startswith('Dictionary<'):
        inner=t[11:-1]; k,v=split2(inner); n=r.i32()
        if n<0: return None
        return {json.dumps(read(r,k,depth+1)):read(r,v,depth+1) for _ in range(n)}
    if t.startswith('HashSet<'):
        el=t[8:-1]; n=r.i32()
        if n<0: return None
        return [read(r,el,depth+1) for _ in range(n)]
    if t=='Nullable<int>':
        # MemoryPack writes the native unmanaged Nullable<int> layout as 8 bytes:
        # bool hasValue, three padding bytes, then int32 value.  The padding is
        # part of the format (DangerousWriteUnmanaged), so this is platform-layout
        # dependent and must not be replaced with a one-byte presence prefix.
        raw=r.raw(8)
        return struct.unpack_from('<i',raw,4)[0] if raw[0] else None
    if t.startswith('Nullable<'):
        el=t[9:-1]; has=r.u8()
        return read(r,el,depth+1) if has else None
    if t in PRIM:
        m=PRIM[t]
        return getattr(r,m[0])() if m[0]!='raw' else r.raw(m[1])
    if t in STRUCTS:
        fmt,sz=STRUCTS[t]; vals=struct.unpack_from('<'+fmt,r.b,r.o); r.o+=sz
        return list(vals)
    if t in ENUMS:
        sz=ENUMS[t]; v=int.from_bytes(r.raw(sz),'little'); return v
    if t=='string':
        return read_string(r)
    if t in UNIONS:
        return read_union(r,t,depth)
    if t in CLASSES:
        return read_object(r,t,depth)
    raise ValueError(f"UNKNOWN TYPE {t} @off {r.o}")

def split2(s):
    d=0;out=[]
    cur=''
    for ch in s:
        if ch=='<':d+=1
        if ch=='>':d-=1
        if ch==',' and d==0: out.append(cur);cur=''
        else: cur+=ch
    out.append(cur)
    return out[0],out[1]

def read_string(r):
    n=r.i32()
    if n==0: return ""
    if n==-1: return None
    if n<0:
        utf8_len=~n
        r.i32()  # UTF-16 character count; byte count is authoritative here.
        return r.raw(utf8_len).decode('utf-8','replace')
    return r.raw(n*2).decode('utf-16-le','replace')

def members_of(cls):
    out=[]
    c=cls
    chain=[]
    while c:
        chain.append(c)
        c=CLASSES.get(c,{}).get('base')
    for c in reversed(chain):
        for o,t,n in CLASSES.get(c,{}).get('members',[]):
            out.append((o,t,n))
    out.sort()
    return out

def read_object(r, cls, depth):
    h=r.u8()
    if h==0xFF: return None
    mem=members_of(cls)
    cnt=h
    res={'__type':cls}
    for i in range(cnt):
        if i<len(mem):
            o,t,n=mem[i]
            res[n]=read(r,t,depth+1)
        else:
            break
    return res

def read_union(r, base, depth):
    h=r.u8()
    if h==0xFF: return None
    if h==0xFA or h==250:
        tag=r.u16()
        sub=UNIONS[base][tag] if tag<len(UNIONS[base]) else None
        if sub is None: raise ValueError(f"union tag {tag} unknown for {base}")
        return read_object(r,sub,depth)
    # 혹시 union이 직접 object로? (h=member count)
    r.o-=1
    return read_object(r,base,depth)

if __name__=='__main__':
    import struct as _s
    BASE=r"C:/Users/kjg/AppData/Local/Temp/claude/C--Users-kjg/1fbeafe4-ec45-4593-88d9-9e6a70e7517b/scratchpad"
    erd=open(BASE+'/replay_work/63953953.er','rb').read()
    REC=_s.Struct('<HHIII'); pos=0x410
    while True:
        kind,ver,tick,length,aux=REC.unpack_from(erd,pos); off=pos+16
        if kind==2 and ver==1: sp=erd[off:off+length]; break
        pos=off+length
    raw=brotli.decompress(sp)
    r=R(raw)
    try:
        rs=read_object(r,'ReplaySnapshot',0)
        print("PARSED ReplaySnapshot OK, offset",r.o,"/",len(raw))
        print("targetFrameRate",rs.get('targetFrameRate'),"gameId",rs.get('gameId'),"seq",rs.get('seq'))
        gs=rs.get('gameSnapshot',{})
        print("gameSnapshot keys sample:",list(gs.keys())[:6] if gs else None)
        ul=gs.get('userList') if gs else None
        print("userList count:",len(ul) if ul else None)
    except Exception as e:
        import traceback; traceback.print_exc()
        print("FAILED at offset",r.o,"/",len(raw)," nearby bytes:",raw[max(0,r.o-4):r.o+8].hex())
