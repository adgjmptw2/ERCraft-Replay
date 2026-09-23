"""Delete only verified local beta results from an explicitly superseded patch."""
import gzip,hashlib,json,shutil,stat
from pathlib import Path
def expire_other_patches(root:Path,current_patch:str):
 root=root.resolve(strict=True);removed=[]
 for folder in root.iterdir():
  if not folder.name.isdigit() or folder.is_symlink() or not folder.is_dir():continue
  if getattr(folder.lstat(),'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT:continue
  manifest=folder/'retention.json'
  if not manifest.is_file() or not (folder/'READY').is_file():continue
  info=json.loads(manifest.read_text())
  if not info.get('verified') or not info.get('patch'):continue
  def version(v):
   parts=v.split('.')
   if not all(x.isdigit() for x in parts):raise ValueError('Invalid patch')
   return tuple(map(int,parts))
  if version(info['patch'])>=version(current_patch):continue
  # Never discard a viewer until its patch-scoped analysis aggregate is readable.
  statistics=folder/'statistics.json.gz'
  if statistics.resolve()!=statistics or not statistics.is_file():raise ValueError('Missing safe retained statistics')
  packed=statistics.read_bytes();summary=json.loads(gzip.decompress(packed))
  if (summary.get('gameId')!=int(folder.name) or summary.get('patch')!=info['patch']
      or not isinstance(summary.get('players'),list) or not summary['players']):
   raise ValueError('Retained statistics scope mismatch')
  for name in ['analysis','viewer-data']:
   target=folder/name
   if target.is_symlink():raise ValueError('Refusing symlink cleanup')
   if target.exists():
    if getattr(target.lstat(),'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT:raise ValueError('Refusing junction cleanup')
    resolved=target.resolve(strict=True)
    if not resolved.is_relative_to(root) or resolved==root:raise ValueError('Unsafe cleanup path')
    shutil.rmtree(resolved)
  # Preserve exact aggregate bytes (including unknown/null/status), not replay payloads.
  manifest.write_text(json.dumps({'patch':info['patch'],'verified':True,'viewerExpired':True,
    'statisticsSha256':hashlib.sha256(packed).hexdigest(),'policy':'analysis-aggregate-only'}),encoding='utf8')
  (folder/'READY').unlink();(folder/'EXPIRED').write_text(info['patch']);removed.append(folder.name)
 return removed
