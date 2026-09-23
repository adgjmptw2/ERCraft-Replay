"""Keep one byte-exact compressed copy of each validated viewer payload."""
import gzip,hashlib,json,os
from pathlib import Path

def seal_viewer(viewer:Path):
 viewer=viewer.resolve(strict=True);rows=[]
 for path in sorted(viewer.glob('*.json')):
  if path.is_symlink():raise ValueError('Refusing linked payload')
  raw=path.read_bytes();packed=gzip.compress(raw,compresslevel=6,mtime=0)
  if gzip.decompress(packed)!=raw:raise ValueError('Payload roundtrip failed')
  target=path.with_suffix('.json.gz');pending=target.with_suffix('.gz.part')
  with pending.open('wb') as f:f.write(packed);f.flush();os.fsync(f.fileno())
  if gzip.decompress(pending.read_bytes())!=raw:raise ValueError('Stored payload roundtrip failed')
  pending.replace(target)
  rows.append({'file':path.name,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'storedBytes':len(packed)})
  path.unlink()
 return rows
