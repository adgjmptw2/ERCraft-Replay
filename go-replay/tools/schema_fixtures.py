"""Generate synthetic Python-oracle cases across all published schema classes."""
import argparse
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'core'))
from decoder.delta_payloads import Cursor, SchemaDecoder, split_generic
from compare_python import normalize


def encode(d, name, depth=0, concrete=False):
    if name in d.unions and not concrete:
        tag, child = next(iter(d.unions[name].items()))
        return bytes([int(tag)]) + encode(d, child, depth + 1, True)
    if name in d.primitive_formats:
        fmt = d.primitive_formats[name]
        return struct.pack('<' + fmt, 1.25 if fmt in 'fd' else 1)
    if name == 'byte[]':
        return struct.pack('<i', 3) + b'abc'
    if name.endswith('[]'):
        return struct.pack('<i', 0) if depth >= 3 else struct.pack('<i', 1) + encode(d, name[:-2], depth + 1)
    if name == 'string':
        value = '한😀'.encode('utf8')
        return struct.pack('<ii', ~len(value), 3) + value
    if name.startswith('Nullable<'):
        child = name[9:-1]
        size, alignment = d.unmanaged_layout(child)
        offset = (1 + alignment - 1) // alignment * alignment
        total = (offset + size + alignment - 1) // alignment * alignment
        raw = bytearray(total)
        raw[0] = 1
        raw[offset:offset + size] = encode(d, child, depth + 1)
        return bytes(raw)
    if name.startswith('Dictionary<'):
        k, v = split_generic(name[11:-1])
        if depth >= 3:
            return struct.pack('<i', 0)
        if d.client_version == '12.4.0' and (k, v) == ('long', 'bool'):
            return struct.pack('<iqB7x', 1, 1, 1)
        return struct.pack('<i', 1) + encode(d, k, depth + 1) + encode(d, v, depth + 1)
    if name.endswith('[]') or name.startswith(('List<', 'HashSet<')):
        child = name[:-2] if name.endswith('[]') else name[name.index('<') + 1:-1]
        return struct.pack('<i', 0) if depth >= 3 else struct.pack('<i', 1) + encode(d, child, depth + 1)
    size = d.enum_size(name)
    if size:
        return (1).to_bytes(size, 'little')
    if name in d.struct_formats:
        fmt = d.struct_formats[name]
        return struct.pack('<' + fmt, *[1.25 if ch in 'fd' else 1 for ch in fmt])
    if name == 'SnapshotWrapper' and not concrete:
        if d.client_version == '12.4.0':
            return encode(d, name, depth + 1, True)
        from decoder.test_delta_payloads import direct_full_wrapper
        return direct_full_wrapper(version=d.client_version)
    if not d.supports_object_type(name):
        raise ValueError('unsupported fixture type ' + name)
    if depth > 4:
        return b'\xff'
    members = d.wire_members_of(name)
    return bytes([len(members)]) + b''.join(encode(d, m.field_type, depth + 1) for m in members)


def generate():
    cases, omitted = [], []
    for version in ('12.1.0', '12.2.0', '12.3.0', '12.4.0'):
        bases = {'LocalObjectCommandPacket': 'ObjectCommandPacket'} if version == '12.3.0' else {}
        if version == '12.3.0':
            bases['CmdPlayStateSkillAction'] = 'CmdPlaySkillActionBase'
        d = SchemaDecoder(bases, client_version=version)
        names = sorted(set(d.classes) | set(bases))
        for name in names:
            try:
                raw = encode(d, name, concrete=name not in d.unions)
            except (ValueError, KeyError, struct.error) as e:
                omitted.append(dict(version=version, name=name, reason=str(e)))
                continue
            base = dict(version=version, name=name, mode='exact', bases=bases)
            for payload in (raw, raw[:-1], raw + b'\0'):
                try:
                    expected = normalize(d.decode_exact(payload, name))
                    cases.append(dict(base, hex=payload.hex(), valid=True, expected=expected))
                except (ValueError, UnicodeError, IndexError):
                    cases.append(dict(base, hex=payload.hex(), valid=False))
        # Exercise non-object public reading primitives, nullable, collections and unions.
        for name in list(d.primitive_formats) + list(d.struct_formats) + ['string', 'byte[]', 'Nullable<int>', 'Nullable<Vector3>', 'List<int>', 'HashSet<int>', 'Dictionary<long,bool>', 'SnapshotWrapper'] + list(d.unions):
            raw = encode(d, name)
            cursor = Cursor(raw)
            value = d.read(cursor, name)
            assert cursor.offset == len(raw)
            cases.append(dict(version=version, name=name, mode='value', bases=bases, hex=raw.hex(), valid=True, expected=normalize(value)))
    return cases, omitted


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    cases, omitted = generate()
    args.output.write_text(json.dumps(cases, separators=(',', ':')), encoding='utf8')
    args.output.with_suffix('.omitted.json').write_text(json.dumps(omitted, indent=2), encoding='utf8')
    print(json.dumps(dict(cases=len(cases), valid=sum(c['valid'] for c in cases), omitted=len(omitted))))
