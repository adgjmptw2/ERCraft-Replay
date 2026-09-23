"""Resolve exact patch/code icons before public export; never substitute icons."""
import hashlib
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def prepare(core, catalog):
    version=catalog['meta']['clientVersion']
    if version not in ('12.3.0','12.4.0'):raise ValueError('Unsupported item asset version')
    path=core/'data/items.provenance.json'
    manifest=json.loads(path.read_text(encoding='utf8'))
    known={int(r['code']) for r in manifest['assets']} | {int(r['code']) for r in manifest.get('unavailable',[])}
    missing=sorted({int(c) for c in catalog['itemCatalog']}-known)
    if version=='12.4.0':
        if manifest.get('clientVersion')!=version:raise ValueError('Exact item asset manifest version mismatch')
        if missing:raise ValueError(f'Unreviewed 12.4 item assets: {missing}')
        return 0
    def fetch(code):
        url=f'https://cdn.dak.gg/assets/er/game-assets/{version}/ItemIcon_{code}.png'
        with urllib.request.urlopen(url,timeout=30) as r:
            payload=r.read(2*1024*1024+1)
        if len(payload)>2*1024*1024 or not payload.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError(f'Invalid exact item icon {code}')
        target=core/f'data/items/{code}.png'
        if target.exists() and target.read_bytes()!=payload:
            raise ValueError(f'Conflicting exact item icon {code}')
        if not target.exists():target.write_bytes(payload)
        return dict(code=code,file=f'data/items/{code}.png',mimeType='image/png',
                    sha256=hashlib.sha256(payload).hexdigest(),sourceUrl=url,clientVersion=version,
                    authority='exact-code-versioned-client-cdn',status='exact-code-hash-verified-asset')
    if missing:
        with ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(fetch,missing))
        manifest['assets'].extend(rows);manifest['assets'].sort(key=lambda r:r['code'])
        temp=path.with_suffix('.json.tmp');temp.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf8');temp.replace(path)
    return len(missing)
