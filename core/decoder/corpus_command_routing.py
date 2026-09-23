"""Recover private recipient masks from selected original command envelopes.

Existing corpus decodes omit routing. Seek their recorded source offsets and
bind each recovered wrapper to the retained command bytes; do not re-decode a
whole replay or expose client account identities. Masks alone prove no order.
"""
from contextlib import closing
import hashlib

import brotli

try:
    from .full_replay_corpus import RECORD, sha_file
    from .inspect_deltas import (Cursor, parse_delta_payload, read_byte_array,
                                 read_collection_count)
except ImportError:
    from full_replay_corpus import RECORD, sha_file
    from inspect_deltas import (Cursor, parse_delta_payload, read_byte_array,
                                read_collection_count)


def _command_wrappers(compressed, tick):
    # Keep the existing full envelope validation, including all trailing fields.
    expected = parse_delta_payload(compressed, tick)['commands']
    cursor = Cursor(brotli.decompress(compressed))
    if cursor.u8() != 5 or cursor.i32() != tick:
        raise ValueError('routing envelope header mismatch')
    commands = []
    for category in ('ignoreOrderPackets', 'commands'):
        count = read_collection_count(cursor, category)
        for _ in range(count or 0):
            if cursor.u8() != 3:
                raise ValueError('routing wrapper member mismatch')
            packet_type = cursor.i32()
            payload = read_byte_array(cursor)
            target = cursor.u32()
            if category == 'commands':
                commands.append(dict(packetType=packet_type, payload=payload,
                                     targetMask=target))
    if [{k: v for k, v in r.items() if k != 'targetMask'}
            for r in commands] != expected:
        raise ValueError('routing parser disagrees with retained envelope parser')
    return commands


def retained_command_routing(source, packet_ids):
    """Return private command masks for at most 1,000 explicitly selected IDs."""
    ids = sorted(set(packet_ids))
    if not ids or len(ids) > 1000 or any(type(i) is not int or i <= 0 for i in ids):
        raise ValueError('select 1..1000 positive command packet IDs')
    with closing(source.connect()) as db:
        source_sha = db.execute(
            "SELECT value FROM metadata WHERE key='sourceSha256'").fetchone()[0]
        rows = db.execute('''SELECT p.id,p.record_id,p.ordinal,p.category,
            p.packet_type,p.raw_payload,p.status,r.source_offset,r.kind,
            r.version,r.tick,r.payload_bytes,r.aux,r.status
            FROM packets p JOIN records r ON r.id=p.record_id
            WHERE p.id IN (''' + ','.join('?' for _ in ids) + ') ORDER BY p.id', ids).fetchall()
    if len(rows) != len(ids):
        raise ValueError('selected command packet missing')
    replay = source.path.parent.parent / 'source.er'
    if sha_file(replay) != source_sha:
        raise ValueError('retained original hash mismatch')
    envelopes, facts = {}, []
    with replay.open('rb') as stream:
        for pid, rid, ordinal, category, kind, payload, status, offset, rk, rv, tick, size, aux, rs in rows:
            if category != 'commands' or status != 'decoded' or (rk, rv, rs) != (1, 1, 'envelope-decoded'):
                raise ValueError('routing source is not an exact command envelope')
            if rid not in envelopes:
                stream.seek(offset)
                header = stream.read(RECORD.size)
                if len(header) != RECORD.size or RECORD.unpack(header) != (rk, rv, tick, size, aux):
                    raise ValueError('record index disagrees with original header')
                compressed = stream.read(size)
                if len(compressed) != size:
                    raise ValueError('truncated original command envelope')
                envelopes[rid] = _command_wrappers(compressed, tick)
            wrappers = envelopes[rid]
            if not 0 <= ordinal < len(wrappers):
                raise ValueError('command ordinal outside original envelope')
            wrapper = wrappers[ordinal]
            if wrapper['packetType'] != kind or wrapper['payload'] != payload:
                raise ValueError('retained command differs from original wrapper')
            facts.append(dict(packetId=pid, wireOrder=[rid, ordinal], tick=tick,
                              targetMask=wrapper['targetMask'], packetType=kind,
                              payloadSha256=hashlib.sha256(payload).hexdigest()))
    return dict(matchKey=source_sha, privateRouting=True, facts=facts,
                originalRecordsRead=len(envelopes), commandPayloadsDecoded=0,
                wholeReplayDecodedAgain=False, orderingProven=False)
