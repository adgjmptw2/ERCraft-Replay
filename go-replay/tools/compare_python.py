"""Read-only whole-file differential check against the original Python decoder.

No acquisition, analysis service, or original-file cleanup code is invoked.
"""
import argparse
import base64
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'core'))
from decoder.delta_payloads import SchemaDecoder
from decoder.full_replay_corpus import records
from decoder.inspect_deltas import load_definitions, parse_delta_payload
import brotli

CATEGORIES = ('ignoreOrderPackets', 'commands', 'itemBoxPackets', 'clientPackets')


def normalize(v):
    if isinstance(v, bytes):
        return {'__bytesBase64__': base64.b64encode(v).decode('ascii')}
    if isinstance(v, float) and not math.isfinite(v):
        return {'__float__': 'NaN' if math.isnan(v) else 'Infinity' if v > 0 else '-Infinity'}
    if isinstance(v, dict):
        return {k: normalize(x) for k, x in v.items()}
    if isinstance(v, list):
        return [normalize(x) for x in v]
    return v


def run(path, comparison=None):
    started = datetime.now().astimezone().isoformat()
    start = time.perf_counter()
    with path.open('rb') as f:
        header = f.read(0x410)
    if len(header) != 0x410 or header[:16] != b'EternalReturnV1\0':
        raise ValueError('invalid replay header')
    version = header[16:32].split(b'\0', 1)[0].decode('ascii')
    definitions, names = load_definitions([r for r in records(path) if r['kind'] == 3 and r['version'] == 2])
    decoder = SchemaDecoder({d['name']: d.get('baseType') for d in definitions}, client_version=version)
    with path.open('rb') as source:
        sha = hashlib.file_digest(source, 'sha256').hexdigest()
    if comparison:
        row = json.loads(next(comparison))
        assert row['type'] == 'header'
        assert row['header']['clientVersion'] == version
        assert row['header']['raw'] == normalize(header)
    statuses, types = Counter(), Counter()
    count = 0
    for count, record in enumerate(records(path), 1):
        packets = []
        status = 'source-preserved'
        try:
            if record['kind'] == 1 and record['version'] == 1:
                delta = parse_delta_payload(record['payload'], record['tick'])
                for category in CATEGORIES:
                    for ordinal, wrapper in enumerate(delta[category]):
                        packets.append((category, ordinal, wrapper['packetType'], names.get(wrapper['packetType'], ''), wrapper.get('payload'), wrapper.get('itemBoxObjectId')))
                status = 'envelope-decoded'
            elif record['kind'] == 2 and record['version'] == 1:
                packets.append(('fullSnapshot', 0, None, 'ReplaySnapshot', brotli.decompress(record['payload']), None))
                status = 'snapshot-preserved'
            elif record['kind'] == 3 and record['version'] == 2:
                status = 'definitions-preserved'
        except (ValueError, brotli.error):
            status = 'envelope-failed'
            statuses[status] += 1
        decoded = []
        for category, ordinal, ptype, name, payload, item_box in packets:
            value = None
            if not name:
                ps = 'unknown-packet-type'
            elif not decoder.supports_object_type(name):
                ps = 'unknown-schema-type'
            elif payload is None:
                ps = 'decode-failed'
            else:
                try:
                    value = decoder.decode_exact(payload, name)
                    ps = 'decoded'
                except (ValueError, UnicodeError):
                    ps = 'decode-failed'
            statuses[ps] += 1
            types[name or '<unknown>'] += 1
            # Retain the current record's outputs, matching Go's lifetime.
            decoded.append((category, ordinal, ptype, name, payload, item_box, ps, value))
        if comparison:
            row = json.loads(next(comparison))
            assert row['type'] == 'record', f'missing record {count}'
            actual = row['record']
            assert actual['id'] == count
            for key in ('kind', 'version', 'tick', 'length', 'aux', 'offset'):
                expected = record['offset'] if key == 'offset' else record[key]
                assert actual[key] == expected, f'record {count} {key}'
            assert actual['status'] == status, f'record {count} status'
            if 'rawPayload' in actual:
                assert actual['rawPayload'] == normalize(record['payload'])
            assert len(actual['packets']) == len(decoded), f'record {count} packet count'
            for packet, expected in zip(actual['packets'], decoded):
                category, ordinal, ptype, name, payload, item_box, ps, value = expected
                identity = f'record {count} {category}[{ordinal}] {name}'
                assert (packet['category'], packet['ordinal'], packet['packetType'], packet['name'], packet.get('itemBoxObjectId')) == (category, ordinal, ptype, name, item_box), identity
                assert packet['raw'] == normalize(payload), identity + ' raw bytes'
                assert packet['status'] == ps, identity + ' status'
                assert packet['value'] == normalize(value), identity + ' decoded value'
    summary = dict(recordCount=count, packetCount=sum(types.values()), decodeStatusCounts=dict(statuses), packetTypeCounts=dict(types), sourceSha256=sha)
    if comparison:
        row = json.loads(next(comparison))
        assert row['type'] == 'summary'
        for key, value in summary.items():
            assert row['summary'][key] == value, f'summary {key}'
        assert not comparison.read().strip(), 'unexpected trailing output'
    summary.update(started=started, finished=datetime.now().astimezone().isoformat(), seconds=time.perf_counter() - start, differential=bool(comparison))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('replay', type=Path)
    parser.add_argument('--compare', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.compare:
        with args.compare.open(encoding='utf8') as comparison:
            result = run(args.replay, comparison)
    else:
        result = run(args.replay)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2), encoding='utf8')
    print(json.dumps({k: v for k, v in result.items() if k != 'packetTypeCounts'}))
