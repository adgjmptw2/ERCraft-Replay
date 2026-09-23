"""Version-bound Azure Laboratory map from the client's world-space NavMesh."""
import base64
import hashlib
import json
from pathlib import Path

DATA_SHA = '9484f373bcf3dfc0c972587ba93312b4ef2ed56f6a6bf0c282b02237acab9cbb'


def template(version='12.3.0'):
    inputs = {
        '12.3.0': ('12.3', DATA_SHA),
        '12.4.0': ('12.4', 'aac6e7828417e0a99da17bf06fd266a9b70ab1dbced15661961e8518fe46bc90'),
    }
    if version not in inputs:raise ValueError('unsupported Azure map version')
    patch,digest=inputs[version]
    raw = (Path(__file__).resolve().parent.parent / f'data/azure-laboratory-{patch}.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('Azure map geometry hash mismatch')
    data = json.loads(raw)
    if data['clientVersion'] != version:raise ValueError('Azure map version mismatch')
    points = [p for poly in data['polygons'] for p in poly]
    bounds = dict(xMin=min(p[0] for p in points)-2, xMax=max(p[0] for p in points)+2,
                  zMin=min(p[1] for p in points)-2, zMax=max(p[1] for p in points)+2)
    # Same world-axis orientation as the corrected battle laboratory viewer.
    ox, oz = -368.25, 399.675
    px = [-4, -4, 240+4*(ox+oz)]
    py = [-4, 4, 240+4*(ox-oz)]
    def project(p):
        return (px[0]*p[0]+px[1]*p[1]+px[2], py[0]*p[0]+py[1]*p[1]+py[2])
    paths = ' '.join('M'+' L'.join(f'{x:.4f},{y:.4f}' for x,y in map(project, poly))+'Z' for poly in data['polygons'])
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="480" height="480"><rect width="480" height="480" fill="#000"/><path d="{paths}" fill="#8b9090" stroke="#8b9090" stroke-width="0.4"/></svg>'
    return dict(spaceId='azure-1080', label='연구실', status=f'verified-{patch}-client-navmesh-area-1080',
                backgroundStatus='exact-client-navigation-geometry', backgroundAssetKey='azure-map-background',
                backgroundDataUrl='data:image/svg+xml;base64,'+base64.b64encode(svg.encode()).decode(),
                geometrySha256=digest, areaCode=1080, bounds=bounds, image=dict(w=480,h=480),
                projection=dict(type='affine-secondary-world-xz-to-pixel.v1',pixelX=px,pixelY=py))


def contains(bounds, x, z):
    return bounds['xMin'] <= x <= bounds['xMax'] and bounds['zMin'] <= z <= bounds['zMax']


def derive_azure_space(players, version):
    if version not in ('12.3.0','12.4.0'):
        return []
    space = template(version)
    rows = [(r[0],p['teamNumber']) for p in players for r in p.get('movementTrack',[])
            if contains(space['bounds'],r[1],r[2])]
    if not rows:
        return []
    space.update(firstTick=min(r[0] for r in rows),lastTick=max(r[0] for r in rows),
                 teamNumbers=sorted({r[1] for r in rows}),observedAnchorCount=len(rows))
    return [space]


def validate_azure_space(space, version):
    if version not in ('12.3.0','12.4.0') or any(space.get(k) != v for k,v in template(version).items()):
        raise ValueError('invalid Azure map calibration')
    if not space.get('teamNumbers') or space.get('observedAnchorCount',0)<1 or space['firstTick']>space['lastTick']:
        raise ValueError('invalid Azure map observations')
