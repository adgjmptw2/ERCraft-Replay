"""Source-bound Jan rope action absence; never infer absence from partial facts."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .corpus_runtime_source import restore
from .corpus_state_inventory import WRAPPER_EVENTS
from .delta_payloads import SchemaDecoder, DecodeError
from .skill_summon_ownership import SUMMON_TYPES, decode_summon_fact

ROOT = Path(__file__).resolve().parent.parent
NATIVE_PROOF = ROOT / 'deliverables/native-jan-rope-action-completeness-v1.json'
RULE = 'live-four-owned-dummies-require-same-frame-rope-action'


def _order(e):
    value = e.get('wireOrder')
    if (not isinstance(value, list) or len(value) != 2
            or any(type(x) is not int or x < 0 for x in value)):
        raise ValueError('invalid Jan anchor order')
    return tuple(value)


def _native_contract(game_hash):
    raw = NATIVE_PROOF.read_bytes()
    proof = json.loads(raw)
    supported = proof.get('gameDataSha256')
    if (proof.get('schema') != 'jan-rope-action-completeness.v1'
            or proof.get('approvedRule') != RULE
            or not isinstance(supported, list) or game_hash not in supported
            or not re.fullmatch('[0-9a-f]{64}', str(game_hash))
            or not proof.get('nativeMethods')):
        return None
    for item in proof['nativeMethods']:
        path = (ROOT / item['path']).resolve()
        if not path.is_relative_to(ROOT) or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            return None
    return hashlib.sha256(raw).hexdigest()


def _wrappers(value):
    if isinstance(value, dict):
        if 'objectId' in value and 'snapshot' in value:
            yield value
        else:
            for child in value.values():
                yield from _wrappers(child)
    elif isinstance(value, list):
        for child in value:
            yield from _wrappers(child)


def _retired_previous_ring_objects(db, decoder, prior_rings, raw_player, latest_ring_at, summon_definitions):
    """Old callbacks may survive a dictionary reset: require actual destruction."""
    if not prior_rings:
        return []
    frames = sorted({r[0] for r in prior_rings})
    sql = ('SELECT record_id,ordinal,category,packet_name,status,decoded_json_zlib FROM packets '
           'WHERE record_id IN (' + ','.join('?' for _ in frames) + ') AND packet_name IN ('
           + ','.join('?' for _ in WRAPPER_EVENTS) + ') ORDER BY record_id,ordinal')
    old = []
    for rid, order, category, name, status, blob in db.execute(sql, (*frames, *WRAPPER_EVENTS)):
        if category != 'commands' or status != 'decoded':
            return None
        value = restore(blob)
        wrappers = list(_wrappers(value))
        if not wrappers and name != 'CmdSpawns':
            return None
        for wrapper in wrappers:
            kind = wrapper.get('objectType')
            if kind not in SUMMON_TYPES:
                continue
            payload = wrapper.get('snapshot')
            if not isinstance(payload, bytes):
                return None
            snap = decode_summon_fact(decoder, wrapper, 0, summon_definitions)
            if snap['ownerObjectId'] == raw_player and snap['summonCode'] in (1171, 1172):
                oid = wrapper.get('objectId')
                if type(oid) is not int:
                    return None
                old.append(dict(objectId=oid, summonCode=snap['summonCode'], spawnWireOrder=[rid, order]))
    if len({r['objectId'] for r in old}) != len(old):
        return None
    if not old:
        return []
    destroyed = {}
    for rid, order, status, blob in db.execute(
            "SELECT record_id,ordinal,status,decoded_json_zlib FROM packets WHERE category='commands' AND packet_name='CmdDestroy' AND record_id>=? AND record_id<=? ORDER BY record_id,ordinal",
            (frames[0], latest_ring_at[0])):
        if status != 'decoded':
            return None
        oid = restore(blob).get('objectId')
        if oid in {r['objectId'] for r in old}:
            destroyed.setdefault(oid, []).append((rid, order))
    for item in old:
        ends = destroyed.get(item['objectId'], [])
        if len(ends) != 1 or not tuple(item['spawnWireOrder']) < ends[0] < latest_ring_at:
            return None
        item['destroyWireOrder'] = list(ends[0])
    return old


def admitted_rope_absence(start, event, player, inventory, identity):
    """Accept only a receipt for this exact source, cast and damage command."""
    try:
        if not identity or identity.get('clientVersion') != '12.3.0':
            return None
        matches = [r for r in inventory or [] if r.get('playerObjectId') == player
                   and r.get('castWireOrder') == start.get('wireOrder')
                   and r.get('wireOrder') == event.get('wireOrder')]
        if len(matches) != 1:
            return None
        row = matches[0]
        native_hash = _native_contract(identity.get('gameDataSha256'))
        if (not native_hash or row.get('nativeProofSha256') != native_hash
                or row.get('sourceProofSha256') != identity.get('sourceSha256')
                or not re.fullmatch('[0-9a-f]{64}', str(row.get('sourceProofSha256', '')))
                or row.get('clientVersion') != identity.get('clientVersion')
                or row.get('gameDataSha256') != identity.get('gameDataSha256')
                or row.get('status') != 'recorded-live-dummy-rope-action-absence'
                or row.get('sourceAnchorsValidated') is not True
                or row.get('wholeSourceDecodeComplete') is not True
                or row.get('issues') != [] or row.get('sameFrameRopeActionCount') != 0
                or row.get('boundaryTick') != event.get('tick')
                or row.get('castTick') != start.get('tick')
                or row.get('localTargetObjectId') != event.get('targetObjectId')
                or event.get('attackerObjectId') != player or event.get('effectCode') != 1035300
                or event.get('isCritical') is not False or row.get('rawIsCritical') is not False
                or type(row.get('rawTargetObjectId')) is not int
                or start.get('playerObjectId') != player or start.get('skillIdCode') != 486
                or row.get('dummyCount') != 4 or len(set(row.get('dummyRawIds', []))) != 4
                or row.get('interveningDummyTransitions') != []
                or row.get('previousRingObjectsFullyDestroyed') is not True
                or row.get('interveningRStarts') != [] or _order(start) >= _order(event)):
            return None
        return row
    except (KeyError, TypeError, ValueError, OSError):
        return None


def unresolved_jan_rope_inventory(source, observations, tables, *, client_version,
                                  source_sha256, game_db_sha256):
    """Read only retained source commands needed by explicit pending requests."""
    pending = {}
    try:
        for row in observations:
            for request in row.get('janRopeAbsenceRequests', []):
                key = (request['playerObjectId'], _order(request['start']), _order(request['damage']))
                if key in pending and pending[key] != request:
                    return []
                pending[key] = request
        if not pending or client_version != '12.3.0':
            return []
        native_hash = _native_contract(game_db_sha256)
        if not native_hash or not re.fullmatch('[0-9a-f]{64}', str(source_sha256)):
            return []
        defs = [r for r in tables.get('SummonObject', []) if r.get('code') == 1173]
        summon_definitions = {r['code']: r for r in tables.get('SummonObject', [])}
        if len(defs) != 1 or (defs[0].get('objectType'), defs[0].get('prefabPath')) != ('SummonServant', 'Jan_Skill04_RingLine'):
            return []
        skill_rows = tables.get('Skill', [])
        w_codes = {r['code'] for r in skill_rows if r.get('group') == 1035300}
        r_codes = {r['code'] for r in skill_rows if r.get('group') == 1035500}
        if not w_codes or not r_codes:
            return []
        schema_raw = (ROOT / 'schema/schema.json').read_bytes()
        schema = json.loads(schema_raw)
        decoder = SchemaDecoder({n: v['base'] for n, v in schema['classes'].items()}, client_version=client_version)
        results = []
        with closing(source.connect()) as db:
            meta = dict(db.execute('SELECT key,value FROM metadata'))
            summary = json.loads(meta.get('summary', '{}'))
            counts = summary.get('decodeStatusCounts')
            if (meta.get('sourceSha256') != source_sha256 or meta.get('clientVersion') != client_version
                    or not isinstance(counts, dict) or not counts.get('decoded')
                    or summary.get('setupError') or summary.get('framingError')
                    or any(k != 'decoded' and v for k, v in counts.items())
                    or not re.fullmatch('[0-9a-f]{64}', str(summary.get('parserSha256', '')))
                    or summary.get('parserSha256') != getattr(source, 'parser_sha256', None)):
                return []

            def anchor(e, name):
                rows = list(db.execute('SELECT category,packet_name,status,decoded_json_zlib FROM packets WHERE record_id=? AND ordinal=?', _order(e)))
                tick = db.execute('SELECT tick FROM records WHERE id=?', (_order(e)[0],)).fetchone()
                if len(rows) != 1 or rows[0][:3] != ('commands', name, 'decoded') or tick != (e['tick'],):
                    raise ValueError('Jan exact source anchor mismatch')
                return restore(rows[0][3])

            max_record = max(key[2][0] for key in pending)
            starts = [(rid, ordinal, tick, restore(blob)) for rid, ordinal, tick, blob in db.execute(
                "SELECT p.record_id,p.ordinal,r.tick,p.decoded_json_zlib FROM packets p JOIN records r ON r.id=p.record_id WHERE p.packet_name='CmdStartSkill' AND p.category='commands' AND p.record_id<=? ORDER BY p.record_id,p.ordinal", (max_record,))]
            for (player, cast_at, damage_at), request in pending.items():
                start, event = request['start'], request['damage']
                raw_s, raw_d = anchor(start, 'CmdStartSkill'), anchor(event, 'CmdDamage')
                raw_player = raw_s.get('objectId')
                if (start.get('playerObjectId') != player or event.get('attackerObjectId') != player
                        or type(player) is not int or type(event.get('targetObjectId')) is not int
                        or start.get('skillIdCode') != 486 or raw_s.get('skillId') != 486
                        or raw_s.get('skillCode') != start.get('skillCode') or raw_s.get('skillCode') not in w_codes
                        or raw_d.get('attackerId') != raw_player or type(raw_player) is not int
                        or event.get('effectCode') != 1035300 or raw_d.get('effectCode') != 1035300
                        or event.get('isCritical') is not False or raw_d.get('isCritical') is not False
                        or type(raw_d.get('objectId')) is not int
                        or cast_at >= damage_at or start['tick'] > event['tick']):
                    continue
                ring_starts = [(rid, order, tick, d) for rid, order, tick, d in starts
                               if (rid, order) <= damage_at and d.get('objectId') == raw_player and d.get('skillId') == 490]
                if not ring_starts:
                    continue
                ring = ring_starts[-1]
                ring_at = ring[:2]
                if ring_at >= cast_at or ring[3].get('skillCode') not in r_codes:
                    continue
                retired = _retired_previous_ring_objects(db, decoder, ring_starts[:-1], raw_player, ring_at, summon_definitions)
                if retired is None:
                    continue
                names = tuple(set(WRAPPER_EVENTS) | {'CmdDestroy', 'CmdDestroyDelayStart', 'CmdPlaySkillAction', 'CmdPlaySkillActionWithTargets'})
                sql = ('SELECT p.record_id,p.ordinal,r.tick,p.category,p.packet_name,p.status,p.decoded_json_zlib '
                       'FROM packets p JOIN records r ON r.id=p.record_id WHERE p.packet_name IN ('
                       + ','.join('?' for _ in names) + ') AND p.record_id>=? AND p.record_id<=? ORDER BY p.record_id,p.ordinal')
                commands = [(rid, order, tick, cat, name, status, restore(blob))
                            for rid, order, tick, cat, name, status, blob in db.execute(sql, (*names, ring[0], damage_at[0]))]
                if any(cat != 'commands' or status != 'decoded' for _, _, _, cat, _, status, _ in commands):
                    continue
                if any(rid == damage_at[0] and name in {'CmdPlaySkillAction', 'CmdPlaySkillActionWithTargets'}
                       and d.get('skillId') == 492 for rid, _, _, _, name, _, d in commands):
                    continue
                dummies = []
                wrappers = []
                for rid, order, tick, _, name, _, d in commands:
                    if name not in WRAPPER_EVENTS:
                        continue
                    found = list(_wrappers(d))
                    if not found and name != 'CmdSpawns':
                        raise ValueError('unknown spawn wrapper')
                    for wrapper in found:
                        wrappers.append(((rid, order), wrapper))
                        if rid != ring[0] or (rid, order) <= ring_at or tick != ring[2] or wrapper.get('objectType') != 11:
                            continue
                        payload = wrapper.get('snapshot')
                        if not isinstance(payload, bytes):
                            raise ValueError('summon snapshot bytes missing')
                        snap = decode_summon_fact(decoder, wrapper, tick, summon_definitions)
                        if snap['ownerObjectId'] == raw_player and snap['summonCode'] == 1173:
                            dummies.append((wrapper, payload, (rid, order)))
                dummy_ids = [w['objectId'] for w, _, _ in dummies]
                if (len(dummy_ids) != 4 or len(set(dummy_ids)) != 4
                        or any(type(x) is not int for x in dummy_ids)
                        or any(w.get('inWorldType') != 1 for w, _, _ in dummies)):
                    continue
                expected_spawns = {at for _, _, at in dummies}
                if any(w.get('objectId') in dummy_ids and at not in expected_spawns for at, w in wrappers):
                    continue
                if any(name in {'CmdDestroy', 'CmdDestroyDelayStart'} and d.get('objectId') in dummy_ids
                       for _, _, _, _, name, _, d in commands):
                    continue
                results.append(dict(status='recorded-live-dummy-rope-action-absence', playerObjectId=player,
                    localTargetObjectId=event.get('targetObjectId'), rawPlayerObjectId=raw_player,
                    rawTargetObjectId=raw_d['objectId'], rawIsCritical=False,
                    castWireOrder=list(cast_at), wireOrder=list(damage_at),
                    castTick=start['tick'], boundaryTick=event['tick'], sourceProofSha256=source_sha256,
                    parserSha256=summary['parserSha256'], clientVersion=client_version, gameDataSha256=game_db_sha256,
                    sourceAnchorsValidated=True, wholeSourceDecodeComplete=True, issues=[],
                    ringStartWireOrder=list(ring_at), ringStartTick=ring[2], dummyCount=4, dummyRawIds=dummy_ids,
                    dummySnapshotSha256=[hashlib.sha256(b).hexdigest() for _, b, _ in dummies],
                    schemaSha256=hashlib.sha256(schema_raw).hexdigest(), nativeProofSha256=native_hash,
                    sameFrameRopeActionCount=0, interveningDummyTransitions=[], interveningRStarts=[],
                    previousRingObjectsFullyDestroyed=True, previousRingObjectDestructions=retired,
                    verifiedCompletionCredit=False, nativeReplayBuildIdentityProven=False))
        return results
    except (KeyError, TypeError, ValueError, OSError, sqlite3.Error, DecodeError):
        return []
