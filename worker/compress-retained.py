import ctypes,hashlib,json,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];CORE=(ROOT.parent/'secret_replay-safe').resolve()
k=ctypes.WinDLL('kernel32',use_last_error=True);k.GetCompressedFileSizeW.argtypes=[ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_ulong)];k.GetCompressedFileSizeW.restype=ctypes.c_ulong
def allocated(p):
 high=ctypes.c_ulong();low=k.GetCompressedFileSizeW(str(p),ctypes.byref(high));return (high.value<<32)|low
def digest(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
rows=[];started=time.monotonic()
for directory in ['tmp','derived','deliverables']:
 for p in (CORE/directory).rglob('*.json'):
  if p.is_symlink() or not p.resolve().is_relative_to(CORE/directory) or p.stat().st_size<1024**2:continue
  if time.time()-p.stat().st_mtime<3600:continue
  before=allocated(p)
  if before<p.stat().st_size*.65:continue
  h=digest(p);r=subprocess.run(['compact.exe','/C','/EXE:LZX',str(p)],capture_output=True)
  if r.returncode:continue
  if digest(p)!=h:raise RuntimeError('Hash changed: '+str(p))
  rows.append({'file':str(p.relative_to(CORE)),'sha256':h,'before':before,'after':allocated(p)})
  if len(rows)%50==0:print(json.dumps({'files':len(rows),'savedBytes':sum(x['before']-x['after'] for x in rows)}),flush=True)
result={'files':rows,'savedBytes':sum(x['before']-x['after'] for x in rows),'seconds':time.monotonic()-started,'deletedFiles':0}
(ROOT/'work/storage-compression-result.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='files'}),flush=True)
