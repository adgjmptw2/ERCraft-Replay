"""Small de-identified event facts for offline skill-policy iteration.

This is neither a replay nor a player history. Only explicitly allowed decoded
facts are copied; account fields, raw payloads, snapshots and original object
identities cannot enter the cache. Object numbers are local to one file.
"""
import gzip
import hashlib
import json
from pathlib import Path

FORMAT='er-skill-scope-facts.v1'
MAX_EXPANDED_BYTES=64*1024*1024
MAX_COMPRESSED_BYTES=8*1024*1024
# Retained caches are intermediate evidence, so avoid level9's CPU cost.
# Level3 preserves identical decoded bytes and the existing size caps.
CACHE_COMPRESSION_LEVEL=3
# The worker handoff is process-local. Keeping the source dictionaries in this
# registry lets consumers prove they received the exact builder outputs without
# recursively validating the million-row fact graph a second time.
_MEMORY_HANDOFFS={}
ID_FIELDS={'playerObjectId','projectileObjectId','ownerObjectId','ownerPlayerObjectId',
           'targetObjectId','sourceObjectId','casterObjectId','stateCasterObjectId','attackerObjectId','objectId',
           'killerObjectId','deadObjectId','parentObjectId','fromObjectId','toObjectId','destroyerObjectId',
           'movementTargetObjectId','spawnPositionObjectId','aroundTargetObjectId','moveToObjectId'}
FIELDS={
 'starts':{'tick','playerObjectId','skillCode','skillIdCode','skillEvolutionLevel','targetObjectId','skillGroup','wireStatus','linkAnchorTicks','linkActions','linkAnchorStatus','wireCategory','wireOrder'},
 'actions':{'tick','sourceObjectId','skillIdCode','casterObjectId','stateCasterObjectId','stateGroup','actionNo','decodedFieldNames','targets','wireStatus','wireCategory','wireOrder'},
 'spawns':{'tick','projectileObjectId','projectileCode','ownerObjectId','ownerPlayerObjectId','wireCategory','wireOrder'},
 'collisions':{'tick','projectileObjectId','targetObjectId','wireCategory','wireOrder'},
 'finishes':{'tick','playerObjectId','skillIdCode','reason','skillSlotSet','wireCategory','wireOrder'},
 'states':{'tick','targetObjectId','casterObjectId','event','stateCode','stateGroup','stackCount','reserveCount','createdTime','duration','originalDuration','power','wireCategory','wireOrder'},
 'damages':{'tick','attackerObjectId','targetObjectId','effectCode','isCritical','damageFontDisplayType','damageType','damageIsNull','curHp','wireCategory','wireOrder'},
 'terminals':{'tick','objectId','event','isCollision','arrivedPosVector2','destroyerObjectId','collisionTargetEffectAndSoundCode','resultType','wireCategory','wireOrder'},
 'stateScripts':{'tick','event','sourceObjectId','casterObjectId','skillIdCode','skillCode','skillEvolutionLevel','stateGroup','reason','wireCategory','wireOrder'},
 'heals':{'tick','targetObjectId','casterObjectId','stateCode','effectCode'},
 'objects':{'tick','objectId','objectType','positionXZ','positionY','wireCategory','wireOrder'},
 'deaths':{'tick','event','deadObjectId','killerObjectId','isDyingBlockDead','wireCategory','wireOrder'},
 'movement':{'tick','event','objectId','positionVector2','positionFieldType','destinationVector2','relativeDestinationVector2',
             'cornersVector2','startPosVector2','endPosVector2','duration','ease','wireCategory','wireOrder'},
 'summons':{'tick','objectId','objectType','ownerObjectId','summonCode','expireTimer','timeAfterCreated',
            'fromObjectId','toObjectId','fromAnchorIndex','toAnchorIndex','snapshotType','identityVerifiedAgainstGameDb'},
 'gaps':{'packetName','count','reasonCode'},
}
OPTIONAL_FIELDS={
 'suaCenterGeometry':{'playerObjectId','projectileObjectId','targetObjectId','tick','castWireOrder','damageWireOrder',
    'boxPivotXZ','targetPivotXZ','targetRadius','nativeRotationPairs','sourceProofSha256','subCollisionAbsent','ordinarySweepBound','sourceCellSweepBound','recordedPoseEstimate'},
 'poseEvents':{'event','tick','objectId','wireCategory','wireOrder','poseChannel','positionVector2','lookAtToY','isLock',
    'destinationVector2','encodedPositionVector2','moveSpeed','isStatSpeed','angularSpeed',
    'startRotationAngleFromForward','targetDirectionAngleFromForward','startPosVector2','endPosVector2','durationInternalValue','ease'},
 'evasionEvents':{'event','tick','objectId','wireCategory','wireOrder'},
 'trapEvents':{'event','tick','objectId','targets','wireCategory','wireOrder'},
 'rotationEvents':{'event','tick','objectId','isLock','rotationYInternalValue','wireCategory','wireOrder'},
 'gameTerminals':{'event','tick','wireCategory','wireOrder','finishGame','rank'},
 'directHeals':{'tick','targetObjectId','casterObjectId','effectCode','addHp','addVp','wireCategory','wireOrder'},
 'skillContexts':{'tick','event','sourceObjectId','targetObjectId','sourcePacketId','skillIdCode',
                 'wireCategory','wireOrder',
                 'skillCode','skillEvolutionLevel','isCritical','reason','actionNo','skillSlotSet',
                 'skillSlotIndex','pointType','currentPoint','selectCode','masteryType','sequence',
                 'duration','maxDuration','sequenceCooldown','targetPosition'},
 'allProjectileSpawns':{'tick','projectileObjectId','projectileCode','ownerObjectId','wireCategory','wireOrder'},
 'nonPlayerSkillStarts':{'tick','sourceObjectId','skillCode','skillIdCode','skillEvolutionLevel','targetObjectId','wireCategory','wireOrder'},
 'projectileMovement':{'tick','projectileObjectId','objectType','projectileCode','ownerObjectId','wireCategory','wireOrder',
    'definitionMovementType','fallbackUsed','wireStatus','reasonCode','outerSnapshotType','movementSnapshotType',
    'outerPayloadByteCount','movementPayloadByteCount','optionalFieldsPresent','projectileSpeed','fullByteConsumption','parentSkillInferred',
    'movementTargetObjectId','spawnPositionObjectId','accumulatedMoveAmountForTarget','timeAfterCreated','duration',
    'arriveRate','targetDirectionEndPos','useOriginalDurationForArrival','projectileDirection','createdAngle','distance',
    'aroundTargetObjectId','aroundTargetPosition','totalElapsedAngle','createdPosition','convexVertices','moveToObjectId'},
}
ALL_FIELDS={**FIELDS,**OPTIONAL_FIELDS}
TARGET_FIELDS={'targetObjectId','hasTargetPosition','targetPosition','sameTickDirectPlayerDamage','sameTickOwnedProjectileCollision'}
PRIVATE_FIELDS=frozenset({'userNum','userId','nickname','session','snapshot','payload','accountId'})
EMPTY_OBJECT_IDS=frozenset({None,0,-1})
CONTAINER_TYPES=(dict,list)


def decoder_contract_hash():
    root=Path(__file__).resolve().parent.parent
    files=['decoder/delta_payloads.py','decoder/er_mempack.py','schema/schema.json']
    return hashlib.sha256(json.dumps({p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in files},sort_keys=True).encode()).hexdigest()


def collector_contract_hash():
    """Historical provenance only; a hash does not certify packet completeness."""
    root=Path(__file__).resolve().parent
    files=['audit_projectile_runtime_replay.py','skill_scope_evidence_cache.py','corpus_runtime_source.py','projectile_movement_facts.py']
    return hashlib.sha256(json.dumps({p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in files},sort_keys=True).encode()).hexdigest()


def build_evidence_cache(*,client_version,game_db_sha256,match_key,players,teams,intervals,
                         player_identity_map_out=None,pose_event_shape=None,**event_lists):
    # Private sidecar sink; raw identities must never enter the anonymous cache.
    if player_identity_map_out is not None and (type(player_identity_map_out) is not dict or player_identity_map_out):
        raise ValueError('player identity sink must be a new empty dict')
    if not set(FIELDS)<=set(event_lists) or set(event_lists)-set(ALL_FIELDS):
        raise ValueError('every required fact stream must be supplied explicitly; optional streams are never inferred')
    mapping={}
    def object_id(value):
        if value is None or value in {0,-1}: return value
        if type(value) is not int or value<0: raise ValueError('invalid cache object identity')
        if value not in mapping: mapping[value]=len(mapping)+1
        return mapping[value]
    def project(row,allowed):
        out={}
        for k,v in row.items():
            if k not in allowed:continue
            if k in ID_FIELDS: out[k]=object_id(v)
            elif k=='targets': out[k]=[project(t,TARGET_FIELDS) for t in v]
            elif k=='linkActions': out[k]=[project(a,{'tick','wireStatus','targets','actionNo'}) for a in v]
            else: out[k]=v
        return out
    player_rows=[{'objectId':object_id(p),'characterCode':info['characterCode']} for p,info in sorted(players.items())]
    team_map={t:i+1 for i,t in enumerate(sorted(set(teams.values())))}
    cached_teams={str(object_id(p)):team_map[t] for p,t in teams.items()}
    cached_intervals={str(object_id(p)):rows for p,rows in intervals.items()}
    facts={name:[project(row,ALL_FIELDS[name]) for row in rows] for name,rows in event_lists.items()}
    out={'format':FORMAT,'clientVersion':client_version,'gameDataSha256':game_db_sha256,
         'matchKey':match_key,'decoderContractSha256':decoder_contract_hash(),
         'collectorContractSha256':collector_contract_hash(),
         'players':player_rows,'teams':cached_teams,'intervals':cached_intervals,'facts':facts,
         'objectCount':len(mapping),'originalObjectIdsRetained':False,'accountIdentifiersRetained':False,
         'rawReplayRetained':False,'capabilities':sorted(facts),
         'limitations':['no raw payloads or raw snapshots','no damage amounts; movement commands are not simulated positions',
                        'object types cover observed spawns only; initial non-player snapshot not retained']}
    if pose_event_shape is not None:out['poseEventShape']=pose_event_shape
    validate_evidence_cache(out)
    if player_identity_map_out is not None:
        player_identity_map_out.update(format='private-replay-player-map.v1',
            matchKey=match_key,clientVersion=client_version,gameDataSha256=game_db_sha256,
            players=[{'replayObjectId':p,'cacheObjectId':mapping[p],
                      'characterCode':info['characterCode']} for p,info in sorted(players.items())],
            mappingAuthority='captured-during-evidence-cache-object-remapping',
            accountIdentifiersRetained=False,fallbackUsed=False)
    return out


def validate_evidence_cache(value):
    try:
        from .skill_game_data_contract import revision_identity
    except ImportError:
        from skill_game_data_contract import revision_identity
    if value.get('format')!=FORMAT:
        raise ValueError('cache format/version/gameDb mismatch')
    revision_identity(value.get('clientVersion'),value.get('gameDataSha256'))
    if value.get('decoderContractSha256')!=decoder_contract_hash():
        raise ValueError('cache decoder contract changed; cached meanings cannot be silently reused')
    # Older caches predate collector provenance; never label them with today's hash.
    if 'collectorContractSha256' in value:
        stamp=value['collectorContractSha256']
        if not isinstance(stamp,str) or len(stamp)!=64 or any(c not in '0123456789abcdef' for c in stamp):
            raise ValueError('invalid historical collector digest')
    key=value.get('matchKey')
    if not isinstance(key,str) or len(key)!=64 or any(c not in '0123456789abcdef' for c in key):
        raise ValueError('cache requires a replay digest, never a game/account identifier')
    if any(value.get(k) is not False for k in ['originalObjectIdsRetained','accountIdentifiersRetained','rawReplayRetained']):
        raise ValueError('cache identity/retention contract mismatch')
    capabilities=set(value.get('facts',{}))
    if 'poseEventShape' in value:
        try:
            from .skill_pose_events import FULL_POSE_EVENT_SHAPE
        except ImportError:
            from skill_pose_events import FULL_POSE_EVENT_SHAPE
        if value['poseEventShape']!=FULL_POSE_EVENT_SHAPE or 'poseEvents' not in capabilities:
            raise ValueError('unsupported or missing full pose event shape')
    if not set(FIELDS)<=capabilities or capabilities-set(ALL_FIELDS):raise ValueError('cache fact capabilities incomplete or unknown')
    if sorted(capabilities)!=value.get('capabilities'):raise ValueError('cache capabilities differ from explicitly stored streams')
    def walk(row):
        if isinstance(row,dict):
            for k,v in row.items():
                if k in PRIVATE_FIELDS:
                    raise ValueError('private or raw field in derived cache')
                if k in ID_FIELDS and v not in EMPTY_OBJECT_IDS and (type(v) is not int or not 1<=v<=value['objectCount']):
                    raise ValueError('cache contains non-local object identity')
                if isinstance(v,CONTAINER_TYPES):walk(v)
        elif isinstance(row,list):
            for v in row:
                if isinstance(v,CONTAINER_TYPES):walk(v)
    walk(value)
    for name,rows in value['facts'].items():
        if any(set(row)-ALL_FIELDS[name] for row in rows):raise ValueError('unrecognized cache fact field')


def write_evidence_cache(path,value):
    path=Path(path)
    if path.exists():raise ValueError('refusing to overwrite existing evidence cache')
    validate_evidence_cache(value)
    return _write_validated_evidence_cache(path,value)


def build_and_write_evidence_cache(path,*,result_out=None,**kwargs):
    """Build, validate once, then persist and optionally hand off the result.

    Standalone writes still validate caller-owned dictionaries. This combined
    path only skips a second walk of the value just validated by the builder.
    """
    return build_evidence_handoff(path=Path(path),result_out=result_out,**kwargs)


def build_evidence_handoff(*,path=None,result_out=None,player_identity_result_out=None,**kwargs):
    """Validate new facts once and hand them to the current calculation.

    A disk destination is optional for intermediate facts; the caller retains
    the original replay and full decoded corpus independently. Memory mode
    reports no file digest and never claims an intermediate cache was saved.
    """
    if result_out is not None and (type(result_out) is not dict or result_out):
        raise ValueError('evidence result sink must be a new empty dict')
    if (player_identity_result_out is not None and
            (type(player_identity_result_out) is not dict or player_identity_result_out)):
        raise ValueError('player identity result sink must be a new empty dict')
    if path is None and result_out is None:
        raise ValueError('memory evidence requires a result sink')
    if player_identity_result_out is not None:
        kwargs['player_identity_map_out']=player_identity_result_out
    value=build_evidence_cache(**kwargs)
    storage=(_write_validated_evidence_cache(Path(path),value) if path is not None else
             dict(storageMode='in-memory',persisted=False,matchKey=value['matchKey'],
                  decoderContractSha256=value['decoderContractSha256'],
                  collectorContractSha256=value['collectorContractSha256']))
    if path is None and player_identity_result_out is not None:
        player_identity_result_out.update(
            evidenceStorageMode='in-memory',
            decoderContractSha256=value['decoderContractSha256'],
            collectorContractSha256=value['collectorContractSha256'])
    if result_out is not None:
        result_out.update(value)
        if path is None and player_identity_result_out is not None:
            _MEMORY_HANDOFFS[id(result_out)]=(result_out,player_identity_result_out)
    return storage


def memory_handoff_matches(evidence_cache, player_identity):
    # Consume the handoff so repeated jobs cannot keep large fact graphs alive.
    pair=_MEMORY_HANDOFFS.pop(id(evidence_cache),None)
    return pair is not None and pair[0] is evidence_cache and pair[1] is player_identity


def _write_validated_evidence_cache(path,value):
    if path.exists():raise ValueError('refusing to overwrite existing evidence cache')
    tables={}
    for name,rows in value['facts'].items():
        keys=set()
        for row in rows:keys.update(row)
        columns=sorted(keys)
        encoded=[];layouts={}
        for row in rows:
            shape=frozenset(row)
            layout=layouts.get(shape)
            if layout is None:
                present=[(i,k) for i,k in enumerate(columns) if k in shape]
                layout=(sum(1<<i for i,k in present),[k for i,k in present])
                layouts[shape]=layout
            mask,present=layout
            encoded.append([mask,*[row[k] for k in present]])
        tables[name]={'columns':columns,'rows':encoded}
    stored={**value,'facts':tables,'storageEncoding':'columnar-facts-v1'}
    raw=json.dumps(stored,ensure_ascii=False,separators=(',',':'),sort_keys=True).encode()
    if len(raw)>MAX_EXPANDED_BYTES:raise ValueError('derived cache exceeds expanded size cap')
    compression_level=CACHE_COMPRESSION_LEVEL
    compressed=gzip.compress(raw,compresslevel=compression_level,mtime=0)
    # Repack the same validated bytes before rejecting a large source. Never
    # repeat event extraction merely because the fast compression exceeds cap.
    if len(compressed)>MAX_COMPRESSED_BYTES and compression_level<9:
        compression_level=9
        compressed=gzip.compress(raw,compresslevel=compression_level,mtime=0)
    if len(compressed)>MAX_COMPRESSED_BYTES:raise ValueError('derived cache exceeds compressed size cap')
    # Exclusive creation also protects the evidence if another writer creates
    # the same path while validation/packing is in progress.
    try:
        with path.open('xb') as stream:stream.write(compressed)
    except FileExistsError as error:
        raise ValueError('refusing to overwrite existing evidence cache') from error
    return {'compressedBytes':len(compressed),'expandedBytes':len(raw),'sha256':hashlib.sha256(compressed).hexdigest(),
            'compressionLevel':compression_level}


def read_evidence_cache(path, *, validate=True):
    """Read a retained cache.

    Acquisition always uses the default full validation.  Offline workers may
    pass ``validate=False`` after the cache has already passed that gate; the
    columnar decoder still checks the storage encoding, table names, masks,
    and row arity while avoiding a second recursive walk of every fact.
    """
    path=Path(path)
    if path.stat().st_size>MAX_COMPRESSED_BYTES:raise ValueError('oversized evidence cache')
    with gzip.open(path,'rb') as stream: raw=stream.read(MAX_EXPANDED_BYTES+1)
    if len(raw)>MAX_EXPANDED_BYTES:raise ValueError('expanded evidence cache exceeds cap')
    value=json.loads(raw)
    if value.pop('storageEncoding',None)!='columnar-facts-v1':raise ValueError('unsupported cache storage encoding')
    facts={}
    for name,table in value['facts'].items():
        columns=table['columns']
        if name not in ALL_FIELDS or len(set(columns))!=len(columns) or set(columns)-ALL_FIELDS[name]:
            raise ValueError('invalid cache fact columns')
        rows=[];columns_by_mask={}
        for packed in table['rows']:
            mask=packed[0]
            if type(mask) is not int or not 0<=mask<(1<<len(columns)) or mask.bit_count()!=len(packed)-1:
                raise ValueError('invalid cache presence mask')
            # Most tables repeat a few shapes hundreds of thousands of times.
            # Resolve each presence-mask layout once, retaining every per-row
            # mask/arity check and the full identity/privacy validation below.
            keys=columns_by_mask.get(mask)
            if keys is None:
                keys=tuple(k for i,k in enumerate(columns) if mask&(1<<i))
                columns_by_mask[mask]=keys
            rows.append(dict(zip(keys,packed[1:])))
        facts[name]=rows
    value['facts']=facts
    if validate:
        validate_evidence_cache(value)
    return value
