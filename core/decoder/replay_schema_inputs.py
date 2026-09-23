"""Exact version-bound schema selection; unknown 12.4 bytes never use legacy."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_124_SHA256 = '7772e36dbeb0d0a51ad3cd147bf24dc9a91eac34c5492623e4274b1f9b182584'

def schema_path_for_version(client_version):
    if client_version == '12.4.0':
        path = ROOT / 'schema/schema-12.4.json'
        if hashlib.sha256(path.read_bytes()).hexdigest() != SCHEMA_124_SHA256:
            raise ValueError('Exact 12.4 schema hash mismatch')
        return path
    # Existing callers keep their existing explicit/default legacy schema.
    if client_version not in (None, '12.1.0', '12.2.0', '12.3.0'):
        raise ValueError('Unreviewed replay schema version')
    return ROOT / 'schema/schema.json'
