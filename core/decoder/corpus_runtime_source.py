"""Feed retained packet decodes into the existing runtime collector."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import base64
import zlib

try:
    from .full_replay_corpus import PARSER_FILES, json_bytes
except ImportError:
    from full_replay_corpus import PARSER_FILES, json_bytes


ROOT=Path(__file__).resolve().parent.parent
CATEGORIES=('ignoreOrderPackets','commands','itemBoxPackets','clientPackets')


def _restore_bytes(value):
    return base64.b64decode(value['__bytesBase64__'],validate=True) if len(value)==1 and '__bytesBase64__' in value else value


_JSON_DECODER=json.JSONDecoder()
_BYTES_JSON_DECODER=json.JSONDecoder(object_hook=_restore_bytes)


def select_retained_decode(archive_dir,source_sha256,client_version,*,require_complete=True):
    """Select a reviewed cache before calculation. An invalid chosen cache raises.

    No error causes a switch to an older cache or an alternate parser. Unknown
    files are preserved but never treated as a compatible decoded source.
    """
    try:
        from .corpus_decode_equivalence import candidate_parser_ids
    except ImportError:
        from decoder.corpus_decode_equivalence import candidate_parser_ids
    for digest in candidate_parser_ids(client_version):
        path=Path(archive_dir)/'decodes'/(digest+'.sqlite3')
        if path.exists():return CorpusRuntimeSource(path,source_sha256,client_version,require_complete=require_complete)
    return None


def restore(payload):
    raw=zlib.decompress(payload)
    # Most command JSON contains no embedded byte wrapper. Escaped keys must
    # still take the hook path because JSON can spell the marker with \uXXXX.
    if b'__bytesBase64__' not in raw and b'\\' not in raw:
        return _JSON_DECODER.decode(raw.decode('utf-8'))
    return _BYTES_JSON_DECODER.decode(raw.decode('utf-8'))


class CorpusRuntimeSource:
    @staticmethod
    def path_from_acquisition(replay):
        archive=replay.get('replayArchive') or {}
        summary=archive.get('decodeSummary') or {}
        if not archive.get('archivePath') or not summary.get('parserSha256'):
            raise ValueError('acquisition has no retained full decode')
        path=Path(archive['archivePath'])/'decodes'/(summary['parserSha256']+'.sqlite3')
        if not path.is_file():raise ValueError('retained full decode file missing')
        return path

    def __init__(self,path,source_sha256,client_version,*,require_complete=True):
        self.path=Path(path).resolve()
        with closing(self.connect()) as db:
            meta=dict(db.execute('SELECT key,value FROM metadata'))
        if meta['sourceSha256']!=source_sha256 or meta['clientVersion']!=client_version:
            raise ValueError('full decode source/version mismatch')
        hashes=json.loads(meta['parserFiles']);summary=json.loads(meta['summary'])
        if set(hashes)!=set(PARSER_FILES) or hashlib.sha256(json_bytes(hashes)).hexdigest()!=summary['parserSha256']:
            raise ValueError('full decode parser manifest incomplete or inconsistent')
        try:
            from .corpus_decode_equivalence import compatibility,current_manifest
        except ImportError:
            from decoder.corpus_decode_equivalence import compatibility,current_manifest
        self.compatibility=compatibility(hashes,current_manifest(),client_version)
        if require_complete and (summary.get('setupError') or summary.get('framingError') or any(k!='decoded' and v for k,v in summary['decodeStatusCounts'].items())):
            raise ValueError('full decode has gaps; cannot replace the runtime input')
        self.parser_sha256=summary['parserSha256']
        self.summary=summary

    def connect(self):
        return sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)

    def first_snapshot(self):
        with closing(self.connect()) as db:
            row=db.execute("SELECT decoded_json_zlib FROM packets WHERE category='fullSnapshot' ORDER BY id LIMIT 1").fetchone()
            if row is None:raise ValueError('full decode has no snapshot')
            return restore(row[0])

    def snapshots(self):
        with closing(self.connect()) as db:
            for tick, decoded in db.execute("SELECT tick,decoded_json_zlib FROM packets WHERE category='fullSnapshot' ORDER BY id"):
                yield tick, restore(decoded)

    def deltas(self,names,*,runtime_only=False,retain_item_box_envelopes=False):
        """Include empty delta records so runtime time boundaries remain identical."""
        names=tuple(sorted(names))
        placeholders=','.join('?' for _ in names)
        with closing(self.connect()) as db:
            # The corpus has a packet-name index, not a record-id index. A
            # LEFT JOIN driven by every record repeatedly scans the same named
            # packets. Read the selected packets once and merge the two ordered
            # streams, preserving empty deltas and the original wire order.
            wrapper_fields='NULL,p.raw_payload IS NOT NULL' if runtime_only else 'p.wrapper_json,p.raw_payload'
            if runtime_only and retain_item_box_envelopes:
                # Combat consumes only item-box envelope identity. Preserve it
                # exactly; all packets, including empty/unused positions, stay.
                wrapper_fields="CASE WHEN p.category='itemBoxPackets' THEN p.wrapper_json ELSE NULL END,p.raw_payload IS NOT NULL"
            packet_query=f'''SELECT p.record_id,p.packet_type,p.category,{wrapper_fields},p.decoded_json_zlib,p.id,p.ordinal
                FROM packets p JOIN records r ON r.id=p.record_id
                WHERE p.packet_name IN ({placeholders}) AND r.kind=1 AND r.version=1
                ORDER BY p.record_id,p.id'''
            packets=iter(db.execute(packet_query,names))
            packet=next(packets,None)
            for rid,at,status in db.execute('SELECT id,tick,status FROM records WHERE kind=1 AND version=1 ORDER BY id'):
                if status!='envelope-decoded':raise ValueError('source delta envelope unavailable')
                delta={category:[] for category in CATEGORIES}
                while packet is not None and packet[0]==rid:
                    _,ptype,category,wrapper,raw,decoded,pid,ordinal=packet
                    if category not in CATEGORIES:raise ValueError('unexpected delta packet category')
                    value=json.loads(wrapper) if (not runtime_only or retain_item_box_envelopes and category=='itemBoxPackets') else {}
                    if runtime_only:value['corpusPayloadPresent']=bool(raw)
                    else:value['payload']=raw
                    value.update(packetType=ptype,corpusDecoded=decoded,corpusPacketId=pid,
                                 corpusWireOrder=[rid,ordinal])
                    delta[category].append(value)
                    packet=next(packets,None)
                yield {'tick':at,'kind':1,'version':1,'corpusDelta':delta}
            if packet is not None:raise ValueError('selected packet has no source delta record')
