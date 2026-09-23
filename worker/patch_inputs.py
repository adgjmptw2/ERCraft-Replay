"""Exact sidecar input selection. A manifest is review evidence, not patch inference.

No acquisition, network, core writes, or cross-version fallback. 12.4 remains
unavailable until independently reviewed inputs and core admission are installed.
"""
import hashlib
import json
import math
from pathlib import Path

ADAPTER = Path(__file__).resolve().parent


def _json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _verified(root, record, version):
    if record.get('clientVersion') != version:
        raise ValueError('PATCH_INPUT_VERSION_MISMATCH')
    relative = record.get('file')
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError('PATCH_INPUT_PATH_INVALID')
    root = root.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError('PATCH_INPUT_PATH_ESCAPE')
    digest = record.get('sha256')
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('PATCH_INPUT_HASH_INVALID')
    if not target.is_file():
        raise ValueError('PATCH_INPUT_FILE_MISSING')
    with target.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != digest:
        raise ValueError('PATCH_INPUT_HASH_MISMATCH')
    return target


def resolve_sidecar_inputs(core, version, adapter=ADAPTER):
    core, adapter = Path(core), Path(adapter)
    if version == '12.3.0':
        # Preserve existing supported inputs; do not relabel them for a newer patch.
        return core / 'schema/schema.json', adapter / 'transport-area-calibration-12.3.json'
    if version != '12.4.0':
        raise ValueError('PATCH_INPUT_VERSION_UNSUPPORTED')
    manifest_path = adapter / 'replay-patches/12.4.0.json'
    if not manifest_path.is_file():
        raise ValueError('PATCH_12_4_REVIEWED_INPUT_MANIFEST_MISSING')
    manifest = _json(manifest_path)
    if manifest.get('format') != 'ercraft-reviewed-sidecar-inputs.v1' or manifest.get('clientVersion') != version:
        raise ValueError('PATCH_INPUT_MANIFEST_INVALID')
    # An adapter-local claim must not override the core gameDb review authority.
    contract_path = core / 'schema/skill-game-data-revisions-12.4-v1.json'
    contract = _json(contract_path)
    revision = manifest.get('gameDataSha256')
    if contract.get('clientVersion') != version or revision not in contract.get('archives', {}):
        raise ValueError('PATCH_GAME_DB_NOT_ADMITTED_BY_CORE')
    archive = contract['archives'][revision]
    _verified(core / 'acquire', {'file': archive['filename'], 'sha256': revision, 'clientVersion': version}, version)
    schema_path = _verified(core, manifest['schema'], version)
    calibration_path = _verified(adapter, manifest['transportCalibration'], version)
    # Review evidence is separately hash-bound and must describe the exact inputs.
    evidence = _json(_verified(adapter, manifest['reviewEvidence'], version))
    if (evidence.get('clientVersion') != version or
            evidence.get('gameDataSha256') != revision or
            evidence.get('schemaSha256') != manifest['schema']['sha256'] or
            evidence.get('calibrationSha256') != manifest['transportCalibration']['sha256'] or
            evidence.get('status') != 'reviewed-exact-inputs'):
        raise ValueError('PATCH_INPUT_REVIEW_BINDING_MISMATCH')
    schema = _json(schema_path)
    if not isinstance(schema.get('classes'), dict) or not schema['classes']:
        raise ValueError('PATCH_SCHEMA_INVALID')
    rows = _json(calibration_path)
    if not isinstance(rows, list) or not rows:
        raise ValueError('PATCH_CALIBRATION_INVALID')
    positions = set()
    for row in rows:
        position = row.get('position')
        areas = row.get('areaCodes')
        if (not isinstance(position, list) or len(position) != 2 or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in position) or
                not isinstance(areas, list) or len(areas) != 1 or type(areas[0]) is not int):
            raise ValueError('PATCH_CALIBRATION_INVALID')
        key = tuple(position)
        if key in positions:
            raise ValueError('PATCH_CALIBRATION_DUPLICATE_POSITION')
        positions.add(key)
    return schema_path, calibration_path
