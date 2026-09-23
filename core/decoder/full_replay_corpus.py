"""Private, replayable source archive and unfiltered packet decode attempts.

The original .er is authoritative. SQLite is a versioned, rebuildable view;
unknown packets, decode failures and full snapshots never disappear with it.
No authentication session or download response is accepted by this module.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter, deque
from contextlib import closing
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import uuid
import zlib
from urllib.parse import urlparse

import brotli

try:
    from .delta_payloads import SchemaDecoder
    from .inspect_deltas import load_definitions,parse_delta_payload
except ImportError:
    from delta_payloads import SchemaDecoder
    from inspect_deltas import load_definitions,parse_delta_payload

ROOT=Path(__file__).resolve().parent.parent
DEFAULT_ARCHIVE_ROOT=ROOT/'local-corpus'/'replays'
HEADER_SIZE=0x410
RECORD=struct.Struct('<HHIII')
SUPPORTED_DECODE_VERSIONS={'12.3.0','12.4.0'}
PARSER_FILES=['decoder/full_replay_corpus.py','decoder/delta_payloads.py',
              'decoder/inspect_deltas.py','schema/schema.json',
              'decoder/replay_schema_inputs.py','schema/schema-12.4.json']
CATEGORIES=('ignoreOrderPackets','commands','itemBoxPackets','clientPackets')


def sha_file(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def copy_exact_once(source,destination):
    source=Path(source);destination=Path(destination)
    digest=sha_file(source)
    if destination.exists():
        if sha_file(destination)!=digest:raise ValueError('existing archive object differs from source')
        return digest
    destination.parent.mkdir(parents=True,exist_ok=True)
    partial=destination.with_name(destination.name+'.partial-'+uuid.uuid4().hex)
    try:
        with source.open('rb') as src,partial.open('xb') as out:
            shutil.copyfileobj(src,out,1024*1024);out.flush();os.fsync(out.fileno())
        if sha_file(partial)!=digest:raise ValueError('archive copy hash mismatch')
        # Link publication is atomic and refuses an existing destination.
        try:os.link(partial,destination)
        except FileExistsError:
            if sha_file(destination)!=digest:raise ValueError('concurrent archive object differs')
    finally:
        if partial.exists():partial.unlink()
    return digest


def records(path):
    with Path(path).open('rb') as stream:
        stream.seek(HEADER_SIZE)
        while True:
            offset=stream.tell();header=stream.read(RECORD.size)
            if not header:return
            if len(header)!=RECORD.size:raise ValueError('unframed trailing record bytes')
            kind,version,tick,length,aux=RECORD.unpack(header)
            payload=stream.read(length)
            if len(payload)!=length:raise ValueError('record extends past EOF')
            yield dict(offset=offset,kind=kind,version=version,tick=tick,length=length,aux=aux,payload=payload)


def json_bytes(value):
    def encode(item):
        if isinstance(item,(bytes,bytearray)):
            return {'__bytesBase64__':base64.b64encode(item).decode('ascii')}
        raise TypeError(type(item).__name__)
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),default=encode).encode('utf-8')


PACKET_INSERT = ('INSERT INTO packets(record_id,ordinal,category,packet_type,packet_name,tick,'
                 'wrapper_json,raw_payload,decoded_json_zlib,status,error_type) VALUES(?,?,?,?,?,?,?,?,?,?,?)')
_WORKER_DECODER = None
_WORKER_VERSION = None


def _initialize_decode_worker(base_types, client_version):
    global _WORKER_DECODER, _WORKER_VERSION
    _WORKER_DECODER = SchemaDecoder(base_types, client_version=client_version)
    _WORKER_VERSION = client_version


def _decode_packet_row(row, decoder, client_version):
    name, payload = row[4], row[7]
    status = 'unknown-packet-type' if not name else 'decoder-unavailable' if decoder is None else 'decoded'
    if (status == 'decoded' and name not in decoder.classes
            and not (client_version == '12.3.0' and name == 'CmdPlayStateSkillAction')):
        status = 'unknown-schema-type'
    decoded = None; error_type = None
    if status == 'decoded':
        try:
            if payload is None:raise ValueError('null packet payload')
            decoded = zlib.compress(json_bytes(decoder.decode_exact(payload, name)))
        except Exception as error:
            status = 'decode-failed'; error_type = type(error).__name__
    return (*row, decoded, status, error_type)


def _decode_packet_batch(rows):
    return [_decode_packet_row(row, _WORKER_DECODER, _WORKER_VERSION) for row in rows]


def decode_source(source,archive_dir,client_version,*,decode_workers=None):
    """Decode every packet, with bounded workers and deterministic stored order.

    Worker failures abort publication. Small sources use one decoder selected
    before processing; an error never switches to a second decoding path.
    """
    if decode_workers is not None and (type(decode_workers) is not int or not 1 <= decode_workers <= 8):
        raise ValueError('decode_workers must be between one and eight')
    source=Path(source);archive_dir=Path(archive_dir)
    hashes={name:sha_file(ROOT/name) for name in PARSER_FILES}
    parser_hash=hashlib.sha256(json_bytes(hashes)).hexdigest()
    resources=archive_dir/'parsers'/parser_hash
    for name in PARSER_FILES:copy_exact_once(ROOT/name,resources/name)
    destination=archive_dir/'decodes'/(parser_hash+'.sqlite3')
    if destination.exists():
        with closing(sqlite3.connect(destination)) as db:
            return json.loads(db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0])
    destination.parent.mkdir(parents=True,exist_ok=True)
    partial=destination.with_name(destination.name+'.partial-'+uuid.uuid4().hex)
    definitions=[];names={};decoder=None;setup_error=None
    try:
        if client_version not in SUPPORTED_DECODE_VERSIONS:raise ValueError('unsupported client version')
        defs=[r for r in records(source) if r['kind']==3 and r['version']==2]
        definitions,names=load_definitions(defs)
        base_types={d['name']:d.get('baseType') for d in definitions if isinstance(d.get('name'),str)}
        decoder=SchemaDecoder(base_types,client_version=client_version)
    except (ValueError,KeyError,TypeError,OSError) as error:setup_error=type(error).__name__
    workers=decode_workers if decode_workers is not None else min(8,max(1,(os.cpu_count() or 1)//2)) if source.stat().st_size>=1024*1024 else 1
    if decoder is None:workers=1
    counts=Counter();packet_counts=Counter();record_count=0;framing_error=None
    db=sqlite3.connect(partial);pool=None
    try:
        db.executescript("""
          CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE records(id INTEGER PRIMARY KEY,source_offset INTEGER,kind INTEGER,version INTEGER,tick INTEGER,
            payload_bytes INTEGER,aux INTEGER,status TEXT,error_type TEXT);
          CREATE TABLE packets(id INTEGER PRIMARY KEY,record_id INTEGER,ordinal INTEGER,category TEXT,packet_type INTEGER,
            packet_name TEXT,tick INTEGER,wrapper_json TEXT,raw_payload BLOB,decoded_json_zlib BLOB,status TEXT,error_type TEXT);
        """)
        db.execute('INSERT INTO metadata VALUES(?,?)',('sourceSha256',sha_file(source)))
        db.execute('INSERT INTO metadata VALUES(?,?)',('parserFiles',json_bytes(hashes).decode()))
        db.execute('INSERT INTO metadata VALUES(?,?)',('clientVersion',client_version))
        if workers>1:
            pool=ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('spawn'),
                                     initializer=_initialize_decode_worker,initargs=(base_types,client_version))
        batch=[];batch_bytes=0;pending=deque();record_rows=[]
        def persist(rows):
            db.executemany(PACKET_INSERT,rows)
            for row in rows:
                counts[row[-2]]+=1;packet_counts[row[4] or '<unknown>']+=1
        def submit_batch():
            nonlocal batch,batch_bytes
            if not batch:return
            if pool is None:
                persist([_decode_packet_row(row,decoder,client_version) for row in batch])
            else:
                pending.append(pool.submit(_decode_packet_batch,batch))
                # Ordered consumption preserves packet ids and wire order even
                # when a later batch finishes first. Memory is bounded.
                if len(pending)>=workers*2:persist(pending.popleft().result())
            batch=[];batch_bytes=0
        def packet(record_id,ordinal,category,packet_type,name,tick,payload,wrapper):
            nonlocal batch_bytes
            batch.append((record_id,ordinal,category,packet_type,name,tick,json_bytes(wrapper).decode(),payload))
            batch_bytes+=len(payload) if payload is not None else 0
        source_records=iter(records(source))
        while True:
            try:r=next(source_records)
            except StopIteration:break
            except ValueError as error:
                framing_error=type(error).__name__;break
            record_count+=1
            status='source-preserved';error_type=None
            try:
                if r['kind']==1 and r['version']==1:
                    delta=parse_delta_payload(r['payload'],r['tick'])
                    for category in CATEGORIES:
                        for ordinal,w in enumerate(delta[category]):
                            packet(record_count,ordinal,category,w['packetType'],names.get(w['packetType']),r['tick'],w.get('payload'),
                                {k:v for k,v in w.items() if k!='payload'})
                    status='envelope-decoded'
                elif r['kind']==2 and r['version']==1:
                    packet(record_count,0,'fullSnapshot',None,'ReplaySnapshot',r['tick'],brotli.decompress(r['payload']),{})
                    status='snapshot-preserved'
                elif r['kind']==3 and r['version']==2:status='definitions-preserved'
            except Exception as error:
                status='envelope-failed';error_type=type(error).__name__;counts[status]+=1
            record_rows.append((record_count,r['offset'],r['kind'],r['version'],r['tick'],r['length'],r['aux'],status,error_type))
            if len(batch)>=512 or batch_bytes>=2*1024*1024:submit_batch()
            if len(record_rows)>=1000:
                db.executemany('INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?)',record_rows);record_rows=[]
            if record_count%5000==0:db.commit()
        submit_batch()
        while pending:persist(pending.popleft().result())
        if record_rows:db.executemany('INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?)',record_rows)
        db.execute('CREATE INDEX packet_tick ON packets(tick)')
        db.execute('CREATE INDEX packet_type_tick ON packets(packet_name,tick)')
        summary=dict(format='er-full-decode.v1',parserSha256=parser_hash,sourceSha256=sha_file(source),
            clientVersion=client_version,recordCount=record_count,packetCount=sum(packet_counts.values()),
            decodeStatusCounts=dict(counts),packetTypeCounts=dict(packet_counts),setupError=setup_error,framingError=framing_error,
            rawSourceRetained=True,packetWhitelistUsed=False,decodedFieldProjectionUsed=False,
            semanticCompletenessProven=False,wrapperRoutingDecoded=False,
            limitations=['Wrapper routing remains available in the original envelope.',
                        'Nested opaque bytes are retained, not claimed decoded.',
                        'Byte-exact decoding is separate from verified combat meaning.'])
        db.execute('INSERT INTO metadata VALUES(?,?)',('summary',json_bytes(summary).decode()))
        db.commit();db.close();db=None
        if pool is not None:pool.shutdown(wait=True);pool=None
        os.link(partial,destination)
        return summary
    finally:
        if pool is not None:pool.shutdown(wait=True,cancel_futures=True)
        if db is not None:db.close()
        if partial.exists():partial.unlink()


def archive_replay(source,archive_root=DEFAULT_ARCHIVE_ROOT,decode=True):
    source=Path(source)
    with source.open('rb') as stream:header=stream.read(HEADER_SIZE)
    if len(header)!=HEADER_SIZE or header[:16]!=b'EternalReturnV1\0':raise ValueError('invalid replay header')
    client_version=header[16:32].split(b'\0',1)[0].decode('ascii')
    digest=sha_file(source);archive_dir=Path(archive_root).resolve()/digest
    archive_dir.mkdir(parents=True,exist_ok=True)
    manifest=dict(format='er-source-archive.v1',sourceSha256=digest,sourceBytes=source.stat().st_size,
        clientVersion=client_version,rawSourceFile=None,retention='derived-only; raw-source-not-retained',
        visibility='private-local-not-public-export',authenticationMaterialCopied=False)
    # Preserve a matching local gameDb by its exact header reference; no network fallback.
    reference=header[32:].split(b'\0',1)[0].decode('utf-8')
    name=Path(urlparse(reference).path).name
    game_db=ROOT/'acquire'/name
    if name.endswith('.zip') and game_db.is_file():
        game_hash=sha_file(game_db);copy_exact_once(game_db,archive_dir/'game-data'/(game_hash+'.zip'))
        manifest['gameDataSha256']=game_hash
    manifest_path=archive_dir/'manifest.json'
    if not manifest_path.exists():
        with manifest_path.open('x',encoding='utf-8') as stream:json.dump(manifest,stream,ensure_ascii=False,indent=2)
    else:
        saved=json.loads(manifest_path.read_text(encoding='utf-8'))
        if saved['sourceSha256']!=digest or saved['sourceBytes']!=source.stat().st_size:raise ValueError('archive manifest mismatch')
        saved.update(rawSourceFile=None,retention=manifest['retention'])
        manifest_path.write_text(json.dumps(saved,ensure_ascii=False,indent=2),encoding='utf-8')
    summary=None
    if decode:
        try:
            from .corpus_runtime_source import select_retained_decode
        except ImportError:
            import sys
            if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
            from decoder.corpus_runtime_source import select_retained_decode
        # Archival keeps incomplete decodes too. Runtime consumers independently
        # require complete streams and cannot silently filter stored failures.
        retained=select_retained_decode(archive_dir,digest,client_version,require_complete=False)
        summary=retained.summary if retained is not None else decode_source(source,archive_dir,client_version)
    return {'sourceSha256':digest,'archivePath':str(archive_dir),'rawReplayRetained':False,
            'decodeSummary':summary}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--archive-root',type=Path,default=DEFAULT_ARCHIVE_ROOT)
    args=parser.parse_args()
    result=archive_replay(args.source,args.archive_root)
    summary=result.pop('decodeSummary')
    print(json.dumps({**result,'decodeStatusCounts':summary['decodeStatusCounts'],'semanticCompletenessProven':False}))
