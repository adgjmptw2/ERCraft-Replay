import dnfile, json, struct, sys
P=r"C:/Users/kjg/Downloads/IL2CppDumper/Dump0/DummyDll/Assembly-CSharp.dll"
pe=dnfile.dnPE(P)
mdt=pe.net.mdtables
print("loaded. TypeDef",len(mdt.TypeDef.rows),"Field",len(mdt.Field.rows),"CustomAttribute",len(mdt.CustomAttribute.rows),flush=True)

def type_name(row):
    try:
        ns=row.TypeNamespace; nm=row.TypeName
        return (ns+"."+nm) if ns else nm
    except: return None

# --- helpers to read CustomAttribute value blob ---
def read_serstring(b,o):
    # compressed length or 0xFF=null
    if b[o]==0xFF: return None,o+1
    # compressed uint
    x=b[o]
    if x&0x80==0: ln=x; o+=1
    elif x&0xC0==0x80: ln=((x&0x3f)<<8)|b[o+1]; o+=2
    else: ln=((x&0x1f)<<24)|(b[o+1]<<16)|(b[o+2]<<8)|b[o+3]; o+=4
    return b[o:o+ln].decode('utf-8','replace'),o+ln

def attr_ctor_typename(ca):
    # ca.Type -> coded CustomAttributeType (MethodDef or MemberRef)
    t=ca.Type
    try:
        r=t.row  # resolved row
    except:
        r=None
    # dnfile: ca.Type may be an InterfaceImpl-like with .table/.row_index; try .row
    row=getattr(t,'row',None)
    if row is None: return None
    # MemberRef -> .Class (TypeRef/TypeDef) ; MethodDef -> declaring type via TypeDef ownership
    cls=getattr(row,'Class',None)
    if cls is not None:
        cr=getattr(cls,'row',None)
        if cr is not None:
            return type_name(cr) if hasattr(cr,'TypeName') else getattr(cr,'Name',None)
    return None

unions={}   # baseType -> {tag:int -> derivedType}
orders={}   # (declaringType, fieldName) -> order  (we key by field row index too)
mp_types=set()
errors=0
for i,ca in enumerate(mdt.CustomAttribute.rows):
    try:
        ctor_t=attr_ctor_typename(ca)
    except Exception as e:
        ctor_t=None; errors+=1
    if not ctor_t: continue
    short=ctor_t.split('.')[-1]
    if short not in ('MemoryPackUnionAttribute','MemoryPackOrderAttribute','MemoryPackableAttribute'): 
        continue
    # parent (what the attr is on)
    par=ca.Parent
    prow=getattr(par,'row',None)
    blob=bytes(ca.Value) if ca.Value else b''
    if short=='MemoryPackUnionAttribute':
        # blob: 01 00 | tag | Type(serstring) | 0000
        if len(blob)>=4 and blob[0]==1 and blob[1]==0:
            o=2
            tag=struct.unpack_from('<H',blob,o)[0]; 
            # try ushort first
            o2=o+2
            tn,_=read_serstring(blob,o2)
            if tn is None or not any(c.isalpha() for c in (tn or '')):
                # try int tag
                tag=struct.unpack_from('<i',blob,o)[0]; o2=o+4
                tn,_=read_serstring(blob,o2)
            base=type_name(prow) if prow is not None and hasattr(prow,'TypeName') else None
            deriv=(tn or '').split(',')[0].split('.')[-1]
            if base:
                unions.setdefault(base,{})[tag]=deriv
    elif short=='MemoryPackableAttribute':
        if prow is not None and hasattr(prow,'TypeName'):
            mp_types.add(type_name(prow))

json.dump({'unions':unions,'mp_types':sorted(mp_types)},open('mempack_meta.json','w'),ensure_ascii=False,indent=1)
print("unions found:",len(unions),"mp_types:",len(mp_types),"attr errors:",errors,flush=True)
for b,m in list(unions.items())[:20]:
    print("  UNION",b,"->",m)
