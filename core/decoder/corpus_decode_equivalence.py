"""Explicit reviewed decode equivalence; unknown implementations never qualify."""
import hashlib,json
from pathlib import Path
from . import full_replay_corpus as corpus
from .full_replay_corpus import json_bytes

ROOT=Path(__file__).resolve().parents[1]
CONTRACT_PATH=ROOT/'schema/full-corpus-equivalence-v1.json'


def current_manifest():
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in corpus.PARSER_FILES}


def parser_digest(manifest):
    return hashlib.sha256(json_bytes(manifest)).hexdigest()


def reviewed_contract():
    raw=CONTRACT_PATH.read_bytes();value=json.loads(raw)
    if value.get('format')!='er-reviewed-corpus-equivalence.v1' or value.get('automaticFutureAcceptance') is not False:
        raise ValueError('invalid reviewed corpus equivalence contract')
    for entry in value['implementations']:
        required={'decoder/full_replay_corpus.py','decoder/delta_payloads.py','decoder/inspect_deltas.py','schema/schema.json'}
        if not required<=set(entry['files']) or parser_digest(entry['files'])!=entry['parserSha256']:
            raise ValueError('invalid reviewed parser manifest')
    for proof in value['proofs']:
        path=(ROOT/proof['path']).resolve()
        if not path.is_relative_to(ROOT) or hashlib.sha256(path.read_bytes()).hexdigest()!=proof['sha256']:
            raise ValueError('reviewed corpus equivalence evidence changed')
    return value,hashlib.sha256(raw).hexdigest()


def compatibility(stored,current,version):
    if stored==current:return dict(mode='exact-implementation',storedParserSha256=parser_digest(stored))
    contract,digest=reviewed_contract()
    manifests={row['parserSha256']:row['files'] for row in contract['implementations']}
    if (version not in contract['clientVersions'] or manifests.get(parser_digest(stored))!=stored
            or manifests.get(parser_digest(current))!=current):
        raise ValueError('unreviewed full decode implementation; no alternate parser')
    return dict(mode='reviewed-identical-wire-data',storedParserSha256=parser_digest(stored),
                currentImplementationSha256=parser_digest(current),contractSha256=digest,
                dataContract=contract['dataContract'],sourceProvenanceRewritten=False)


def candidate_parser_ids(version):
    current=current_manifest();digest=parser_digest(current)
    contract,_=reviewed_contract()
    known={row['parserSha256']:row for row in contract['implementations']}
    if version not in contract['clientVersions'] or known.get(digest,{}).get('files')!=current:
        return [digest]
    return [digest,*[row['parserSha256'] for row in reversed(contract['implementations']) if row['parserSha256']!=digest]]
