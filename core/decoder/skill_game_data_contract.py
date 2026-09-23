"""Explicit exact-archive admission for byte-identical skill rule inputs.

Source identities are never rewritten to the rule reference archive. New
revisions require offline review and a separately compiled execution plan.
"""
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import zipfile

ROOT=Path(__file__).resolve().parents[1]
CONTRACT_PATH=ROOT/'schema/skill-game-data-revisions-v1.json'

def contract_path(client_version):
    if client_version == '12.4.0':
        return ROOT/'schema/skill-game-data-revisions-12.4-v1.json'
    return CONTRACT_PATH


@lru_cache(maxsize=4)
def _contract(raw):
    data=json.loads(raw)
    if (data.get('format')!='er-skill-game-data-revisions.v1'
            or data.get('automaticFutureRevisionAcceptance') is not False
            or data.get('sourceHashReplacement') is not False):
        raise ValueError('invalid exact gameDb rule contract')
    return data


def revision_identity(client_version, game_db_sha256):
    raw=contract_path(client_version).read_bytes();data=_contract(raw)
    entry=data['archives'].get(game_db_sha256)
    if client_version!=data['clientVersion'] or entry is None:
        raise ValueError('unreviewed exact gameDb revision; no fallback')
    return dict(clientVersion=client_version,gameDataSha256=game_db_sha256,
        ruleGameDataSha256=data['ruleGameDataSha256'],filename=entry['filename'],
        planPath=entry['planPath'],tableContractSha256=hashlib.sha256(raw).hexdigest())


def validate_archive(path, client_version):
    """Offline/file-ingestion boundary; not a repeated per-metric ZIP scan."""
    path=Path(path);digest=hashlib.sha256(path.read_bytes()).hexdigest()
    identity=revision_identity(client_version,digest)
    if path.name!=identity['filename']:
        raise ValueError('exact gameDb filename/hash mismatch')
    data=_contract(contract_path(client_version).read_bytes())
    if client_version == '12.4.0':
        tables=data['archives'][digest]['tableSha256']
        excluded=[]
    else:
        tables=data['sharedTableSha256']
        excluded=data['excludedFromSkillRuleEquivalence']
    with zipfile.ZipFile(path) as archive:
        names=archive.namelist()
        expected=set(tables)|set(excluded)
        if len(names)!=len(set(names)) or set(names)!=expected:
            raise ValueError('gameDb member inventory mismatch')
        for name,digest in tables.items():
            if hashlib.sha256(archive.read(name)).hexdigest()!=digest:
                raise ValueError('gameDb skill-rule table changed: '+name)
    return identity


def archive_for_revision(client_version, game_db_sha256):
    identity=revision_identity(client_version,game_db_sha256)
    path=ROOT/'acquire'/identity['filename']
    validate_archive(path,client_version)
    return path
