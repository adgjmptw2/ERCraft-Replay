"""Bounded x86 floating-point interpreter over explicit static instruction/data inputs.
No native library is loaded or executed. Unsupported control flow is an error.
Extracted from the existing offline evaluator without arithmetic changes.
"""
import math,struct,re
from fractions import Fraction
from capstone import CS_OP_REG,CS_OP_IMM,CS_OP_MEM
f32=lambda x:struct.unpack('<f',struct.pack('<f',x))[0]
bits=lambda v,n:int.from_bytes(struct.pack('<f' if n==32 else '<d',v),'little')
flt=lambda v,n:struct.unpack('<f'if n==32 else '<d',(v&((1<<n)-1)).to_bytes(n//8,'little'))[0]

def evaluate(program,read,start,value,fma_enabled=False,*,input_bits=32,output_bits=32,memory_overrides=None,domain=(0,f32(math.pi))):
 if not math.isfinite(value) or not domain[0]<=value<=domain[1]:raise ValueError('Unreviewed trig input domain')
 regs={'rsp':0x100000000};vectors={'xmm0':bits(f32(value) if input_bits==32 else value,input_bits)};memory={};cf=zf=sf=of=False;pc=start;trace=[]
 def reginfo(name):
  if name.startswith('xmm'):return name,128
  alias={'eax':'rax','ebx':'rbx','ecx':'rcx','edx':'rdx','esi':'rsi','edi':'rdi','esp':'rsp','ebp':'rbp','ax':'rax','al':'rax','cl':'rcx'}
  if name in alias:return alias[name],8 if name in ('al','cl')else 16 if name=='ax'else 32
  if re.fullmatch(r'r\d+d',name):return name[:-1],32
  return name,64
 def getreg(name):
  r,n=reginfo(name);return (vectors if n==128 else regs).get(r,0)&((1<<n)-1)
 def setreg(name,v):
  r,n=reginfo(name);v&=(1<<n)-1
  if n==128:vectors[r]=v
  elif n>=32:regs[r]=v
  else:regs[r]=(regs.get(r,0)&~((1<<n)-1))|v
 def addr(op,ins):
  m=op.mem;return m.disp+(ins.address+ins.size if m.base and ins.reg_name(m.base)=='rip'else getreg(ins.reg_name(m.base))if m.base else 0)+(getreg(ins.reg_name(m.index))*m.scale if m.index else 0)
 def get(op,ins):
  if op.type==CS_OP_IMM:return op.imm
  if op.type==CS_OP_REG:return getreg(ins.reg_name(op.reg))
  a=addr(op,ins)
  if memory_overrides and a in memory_overrides:return memory_overrides[a]
  if a==0x219ebe0:return int(fma_enabled)
  if a>=0x100000000-0x1000:return int.from_bytes(bytes(memory.get(a+i,0)for i in range(op.size)),'little')
  return int.from_bytes(read(a,op.size),'little')
 def put(op,v,ins,scalar=None):
  if op.type==CS_OP_REG:
   name=ins.reg_name(op.reg)
   if scalar and name.startswith('xmm'):v=(getreg(name)&~((1<<scalar)-1))|(v&((1<<scalar)-1))
   setreg(name,v)
  else:
   a=addr(op,ins)
   if a<0x100000000-0x1000:raise ValueError('Static evaluator may not write binary memory')
   for i,b in enumerate((v&((1<<(op.size*8))-1)).to_bytes(op.size,'little')):memory[a+i]=b
 def signed(v,n):v&=(1<<n)-1;return v-(1<<n)if v>>(n-1)else v
 for step in range(2000):
  ins=program.get(pc)
  if ins is None:raise ValueError(f'Unreviewed control flow {pc:x}')
  ops=ins.operands;mn=ins.mnemonic;trace.append(hex(pc));nextpc=pc+ins.size
  vals=lambda:[get(o,ins)for o in ops]
  if mn=='ret':return flt(vectors.get('xmm0',0),output_bits),trace
  if mn in ('nop','prefetchw'):pass
  elif mn=='lea':put(ops[0],addr(ops[1],ins),ins)
  elif mn in ('mov','movabs','movd','movq','movapd','movaps','movsd','movss','movdqa','movdqu','vmovd','vmovq','vmovapd','vmovaps','vmovsd','vmovdqa','vmovdqu'):
   v=get(ops[1],ins)
   if mn=='vmovsd' and len(ops)==3:v=(v&~((1<<64)-1))|(get(ops[2],ins)&((1<<64)-1))
   scalar=64 if mn=='movsd'and ops[1].type==CS_OP_REG else 32 if mn=='movss'and ops[1].type==CS_OP_REG else None
   if mn in ('movd','vmovd'):v&=(1<<32)-1
   if mn in ('movq','vmovq'):v&=(1<<64)-1
   put(ops[0],v,ins,scalar)
  elif mn in ('cmp','test'):
   a,b=vals();n=ops[0].size*8;mask=(1<<n)-1;a&=mask;b&=mask;r=(a-b)&mask if mn=='cmp'else a&b
   zf=r==0;sf=bool(r>>(n-1));cf=a<b if mn=='cmp'else False;of=bool(((a^b)&(a^r))>>(n-1))if mn=='cmp'else False
  elif mn in ('comiss','ucomiss','comisd','ucomisd','vcomisd','vucomisd'):
   n=32 if mn.endswith('ss')else 64;a,b=[flt(v,n) for v in vals()]
   if not all(map(math.isfinite,(a,b))):raise ValueError('nonfinite comparison unsupported')
   cf=a<b;zf=a==b;sf=of=False
  elif mn in ('sqrtsd','sqrtss','vsqrtsd'):
   n=32 if mn.endswith('ss')else 64;v=flt(get(ops[-1],ins),n)
   put(ops[0],bits(math.sqrt(v),n),ins,n)
  elif mn.startswith('j'):
   yes={'jmp':True,'je':zf,'jz':zf,'jne':not zf,'jnz':not zf,'jg':not zf and sf==of,'jge':sf==of,'jl':sf!=of,'jle':zf or sf!=of,'ja':not cf and not zf,'jae':not cf,'jb':cf,'jbe':cf or zf,'js':sf,'jns':not sf,'jp':False,'jnp':True}.get(mn)
   if yes is None:raise ValueError(mn)
   if yes:nextpc=get(ops[0],ins)
  elif mn in ('not','neg'):
   v=get(ops[0],ins);put(ops[0],~v if mn=='not'else -v,ins)
  elif mn in ('xor','and','or','add','sub','shl','shr','btr','bts','bt'):
   a,b=vals();n=ops[0].size*8
   if mn in ('bt','btr','bts'):
    cf=bool(a&(1<<b));r=a&~(1<<b)if mn=='btr'else a|(1<<b)
    if mn!='bt':put(ops[0],r,ins)
   else:
    r={'xor':lambda:a^b,'and':lambda:a&b,'or':lambda:a|b,'add':lambda:a+b,'sub':lambda:a-b,'shl':lambda:a<<(b&(n-1)),'shr':lambda:a>>(b&(n-1))}[mn]()
    rr=r&((1<<n)-1);zf=rr==0;sf=bool(rr>>(n-1))
    if mn in ('xor','and','or'):cf=of=False
    elif mn in ('add','sub'):
     cf=(r<0 or r>=(1<<n));of=bool(((~(a^b) if mn=='add'else a^b)&(a^rr))>>(n-1)&1)
    if mn=='shr'and b:cf=bool(a&(1<<((b&(n-1))-1)))
    put(ops[0],r,ins)
  elif mn in ('xorps','xorpd','andpd','orpd','vxorpd','vandpd','vorpd','vandnpd'):
   vv=vals();a,b=vv[-2:];r=(~a)&b if mn=='vandnpd'else a^b if 'xor'in mn else a&b if 'and'in mn else a|b;put(ops[0],r,ins)
  elif mn in ('addss','subss','mulss','divss','addsd','subsd','mulsd','divsd','vaddss','vsubss','vmulss','vaddsd','vsubsd','vmulsd','vdivsd'):
   n=32 if mn.endswith('ss')else 64;vv=vals();a,b=[flt(x,n)for x in vv[-2:]];op=mn.lstrip('v')[:3];r={'add':lambda:a+b,'sub':lambda:a-b,'mul':lambda:a*b,'div':lambda:a/b}[op]();put(ops[0],bits(r if n==64 else f32(r),n),ins,n)
  elif mn.startswith(('vfmadd','vfnmadd','vfmsub','vfnmsub')):
   a,b,c=[flt(v,64)for v in vals()];order=mn[-5:-2]
   x,y,z={'132':(a,c,b),'213':(b,a,c),'231':(b,c,a)}[order];prod=Fraction.from_float(x)*Fraction.from_float(y)
   r=float((-prod if mn.startswith('vfnm')else prod)+(-1 if 'sub'in mn else 1)*Fraction.from_float(z));put(ops[0],bits(r,64),ins,64)
  elif mn in ('cvtss2sd','vcvtss2sd','cvtsd2ss','vcvtsd2ss'):
   n=64 if mn.endswith('2sd')else 32;v=flt(get(ops[-1],ins),96-n);put(ops[0],bits(v if n==64 else f32(v),n),ins,n)
  elif mn in ('cvttpd2dq','vcvttpd2dq','cvtdq2pd','vcvtdq2pd','vpmovsxdq'):
   v=get(ops[-1],ins)
   if 'ttpd2dq'in mn:out=sum((int(flt(v>>(64*i),64))&0xffffffff)<<(32*i)for i in range(2))
   elif mn=='vpmovsxdq':out=sum((signed(v>>(32*i),32)&((1<<64)-1))<<(64*i)for i in range(2))
   else:out=sum(bits(float(signed(v>>(32*i),32)),64)<<(64*i)for i in range(2))
   put(ops[0],out,ins)
  elif mn=='vshufps':
   _,a,b,k=vals();out=0
   for i in range(4):out|=(((a if i<2 else b)>>(32*((k>>(2*i))&3)))&0xffffffff)<<(32*i)
   put(ops[0],out,ins)
  elif mn=='vpcmpeqq':
   _,a,b=vals();put(ops[0],sum((0xffffffffffffffff if ((a>>(i*64))&0xffffffffffffffff)==((b>>(i*64))&0xffffffffffffffff)else 0)<<(i*64)for i in range(2)),ins)
  else:raise ValueError(f'Unsupported {pc:x}: {mn} {ins.op_str}')
  pc=nextpc
 raise ValueError('Instruction budget exceeded')
