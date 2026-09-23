import re, json
txt=open(r"C:/Users/kjg/Downloads/IL2CppDumper/Dump0/dump.cs",encoding='utf-8',errors='replace').read()
classes={}; enums={}; subclasses={}
for m in re.finditer(r'\n\s*(?:public |internal )?enum (\w+)(?:\s*:\s*(\w+))?', txt):
    enums[m.group(1)]={'byte':1,'sbyte':1,'short':2,'ushort':2,'int':4,'uint':4,'long':8,'ulong':8}.get(m.group(2) or 'int',4)
for m in re.finditer(r'\n(?:\s*\[[^\n]*\]\n)*\s*(?:public |internal |protected )?(?:abstract |sealed |static )?(class|struct) (\w+)(?:<[^>]*>)?\s*(?::\s*([^\n{]+))?\s*//', txt):
    name=m.group(2); bases=m.group(3) or ''
    i=txt.find('{',m.end()-3)
    if i<0: continue
    depth=0;j=i
    while j<len(txt):
        if txt[j]=='{':depth+=1
        elif txt[j]=='}':
            depth-=1
            if depth==0:break
        j+=1
    body=txt[i:j+1]
    base=None
    for b in [x.strip() for x in bases.split(',')]:
        b=b.split('<')[0].strip()
        if b and not (b.startswith('I') and b[1:2].isupper()) and 'MemoryPack' not in b and b not in ('IEquatable','IFormattable','ValueType'):
            base=b; break
    if base in ('object',): base=None
    members=[]
    fields=body.split('// Methods')[0].split('// Properties')[0]
    # 프로퍼티도 MemoryPackOrder 가질 수 있음 → 본문 전체에서
    for fm in re.finditer(r'MemoryPackOrder\((\d+)\)\]\s*\n(?:\s*\[[^\]]*\]\s*\n)*\s*(?:public |private |protected |internal )+(?:readonly |static )?([\w\.\?\[\]<>][\w\.\?\[\]<>,\s]*?)\s+(\w+)\s*[;{]', body):
        typ=re.sub(r'\s+','',fm.group(2))
        members.append([int(fm.group(1)),typ,fm.group(3)])
    members.sort()
    is_mp='IMemoryPackable' in bases or 'MemoryPackable' in txt[max(0,m.start()-400):m.start()] or bool(members)
    if is_mp:
        classes[name]={'base':base,'members':members}
    if base:
        subclasses.setdefault(base,[]).append(name)
json.dump({'classes':classes,'enums':enums,'subclasses':subclasses},open('schema.json','w'))
print("classes",len(classes),"enums",len(enums))
for c in ['GameSnapshot','SnapshotWrapper','UserSnapshot','CharacterSnapshot','MoveAgentSnapshot']:
    v=classes.get(c);print(c,"members",len(v['members']) if v else "MISS",[x[0] for x in v['members']] if v else '')
print("SnapshotWrapper subclasses:",subclasses.get('SnapshotWrapper'))
