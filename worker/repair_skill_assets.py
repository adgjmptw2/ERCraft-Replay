"""Replace verified skill artwork without recalculating match statistics."""
import argparse
import copy
import gzip
import hashlib
import json
import shutil
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[3] / 'secret_replay-safe'
sys.path.insert(0, str(CORE))


def repair(folder, manifest, apply=False):
    from decoder.build_public_combat_analysis import load_skill_assets
    folder = folder.resolve(strict=True)
    viewer = folder / 'viewer-data'
    files = sorted(viewer.glob('*.json.gz'))
    original = {p.name: gzip.decompress(p.read_bytes()) for p in files}
    name = 'combat-analysis-personal-pvp-v1.json.gz'
    public = json.loads(original[name])
    old_hash = hashlib.sha256(original[name]).hexdigest()
    before = copy.deepcopy(public)
    public['skillAssets'] = load_skill_assets(
        {p['characterCode'] for p in public['players']}, manifest)
    changed = [code for code, icons in public['skillAssets']['icons'].items()
               if icons != before['skillAssets']['icons'].get(code)]
    if not changed:
        return {'game': folder.name, 'changedCharacters': [], 'applied': False}
    check = copy.deepcopy(public)
    check['skillAssets'] = before['skillAssets']
    assert check == before, 'non-artwork public data changed'
    encoded = json.dumps(public, ensure_ascii=False, separators=(',', ':')).encode()
    new_hash = hashlib.sha256(encoded).hexdigest()
    updated = {name: encoded}
    for filename, raw in original.items():
        if filename == name:
            continue
        data = json.loads(raw)
        assert data['sourceFixtureSha256'] == old_hash, filename
        data['sourceFixtureSha256'] = new_hash
        updated[filename] = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()
        check = copy.deepcopy(data)
        check['sourceFixtureSha256'] = old_hash
        assert check == json.loads(raw), 'non-binding data changed'
    result = {'game': folder.name, 'changedCharacters': changed,
              'oldPublicSha256': old_hash, 'newPublicSha256': new_hash,
              'statisticsUnchanged': True, 'applied': apply}
    if apply:
        backup = folder / 'skill-artwork-rollback'
        backup.mkdir(exist_ok=False)
        for path in files:
            shutil.copy2(path, backup / path.name)
        retention_path = folder / 'retention.json'
        retention = json.loads(retention_path.read_text(encoding='utf8'))
        shutil.copy2(retention_path, backup / retention_path.name)
        payloads = []
        for filename, raw in updated.items():
            packed = gzip.compress(raw, compresslevel=6, mtime=0)
            assert gzip.decompress(packed) == raw
            pending = viewer / (filename + '.repair')
            pending.write_bytes(packed)
            pending.replace(viewer / filename)
            payloads.append({'file': filename.removesuffix('.gz'),
                             'sha256': hashlib.sha256(raw).hexdigest(),
                             'bytes': len(raw), 'storedBytes': len(packed)})
        retention['payloads'] = payloads
        retention_path.write_text(json.dumps(retention, ensure_ascii=False), encoding='utf8')
        (backup / 'repair.json').write_text(json.dumps(result), encoding='utf8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('folder', type=Path)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(repair(args.folder, args.manifest, args.apply)))
