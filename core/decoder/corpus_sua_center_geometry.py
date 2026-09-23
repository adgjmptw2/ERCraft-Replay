"""Extract Sua center geometry from retained packets, without per-match literals.

Stationary anchors and exact bookmark-stun stops are supported. The latter
require an ordinary-movement sweep bound; other moving cases remain unknown.
"""
from bisect import bisect_right
from collections import defaultdict
from contextlib import closing
from pathlib import Path
import hashlib,json,math,time
STRAIGHT_MOTION_RULE_SHA256='1b653d46619046c7e7f3aeaa2088dc68b5ee01290ed8ebfbf90b9001a601c042'
try:
    from .skill_game_data_contract import revision_identity
    from .corpus_runtime_source import restore
    from .delta_payloads import SchemaDecoder
    from .skill_pose_events import pose_event_facts,pose_packet_channels,StationaryPoseIndex,f32
    from .skill_native_center_rotation import sua_center_rotation
    from .skill_box_circle_rule import STATIONARY_SUB_RULE_SHA256,BOOKMARK_STOP_SWEEP_RULE_SHA256
    from .skill_wire_position_precision import position_bounds
    from .skill_box_circle_rule import SOURCE_CELL_SWEEP_RULE_SHA256
except ImportError:
    from skill_game_data_contract import revision_identity
    from corpus_runtime_source import restore
    from delta_payloads import SchemaDecoder
    from skill_pose_events import pose_event_facts,pose_packet_channels,StationaryPoseIndex,f32
    from skill_native_center_rotation import sua_center_rotation
    from skill_box_circle_rule import STATIONARY_SUB_RULE_SHA256,BOOKMARK_STOP_SWEEP_RULE_SHA256
    from skill_wire_position_precision import position_bounds
    from skill_box_circle_rule import SOURCE_CELL_SWEEP_RULE_SHA256

def retained_sua_center_geometry(source,cache,uses):
    begun=time.perf_counter()
    if source is None or not uses:return [],dict(status='no-selected-uses',contacts=0)
    revision_identity(cache['clientVersion'],cache['gameDataSha256'])
    teams={int(k):v for k,v in cache['teams'].items()}
    starts={tuple(u['start']['wireOrder']):u['start'] for u in uses}
    damages={tuple(d['wireOrder']):d for u in uses for d in u['damages']}
    spawns={tuple(u['projectile']['wireOrder']):u['projectile'] for u in uses}
    anchors=set(starts)|set(damages)|set(spawns)
    max_rid=max(at[0] for at in anchors)
    mapping={};reverse={};raw_spawns={};seen=set()
    def bind(raw,local):
        if type(raw)is not int or type(local)is not int:raise ValueError('missing geometry identity')
        if raw in mapping and mapping[raw]!=local or local in reverse and reverse[local]!=raw:
            raise ValueError('conflicting geometry identity')
        mapping[raw]=local;reverse[local]=raw
    schema=json.loads((Path(__file__).resolve().parent.parent/'schema/schema.json').read_text())
    decoder=SchemaDecoder({n:v['base'] for n,v in schema['classes'].items()},client_version=cache['clientVersion'])
    poses=[];radius_changes=defaultdict(list);speed_events=defaultdict(list);callback_commands={}
    with closing(source.connect()) as db:
        frames=list(db.execute('SELECT tick,id FROM records WHERE kind=1 AND version=1 AND id<=? ORDER BY tick,id',(max_rid,)))
        frame_ticks=[tick for tick,rid in frames]
        # Use the existing packet-name index; never a per-record full-table join.
        for rid,n,name,blob in db.execute("SELECT record_id,ordinal,packet_name,decoded_json_zlib FROM packets WHERE packet_name IN ('CmdStartSkill','CmdDamage','CmdSpawn','CmdSpawnBatch') AND category='commands' AND record_id<=? ORDER BY record_id,id",(max_rid,)):
            at=(rid,n)
            if at not in anchors:continue
            d=restore(blob)
            if name=='CmdStartSkill' and at in starts:
                s=starts[at]
                if (d['skillId'],d['skillCode'])!=(s['skillIdCode'],s['skillCode']):raise ValueError('geometry start anchor mismatch')
                bind(d['objectId'],s['playerObjectId']);seen.add(at)
            elif name=='CmdDamage' and at in damages:
                s=damages[at]
                if d['effectCode']!=s['effectCode']:raise ValueError('geometry damage anchor mismatch')
                bind(d['objectId'],s['targetObjectId']);bind(d['attackerId'],s['attackerObjectId']);seen.add(at)
            elif name=='CmdSpawn' and at in spawns:
                raw_spawns[at]=d['snapshot'];seen.add(at)
            # Batch members need individual identities; no arbitrary first match.
        names=tuple(pose_packet_channels())
        sql='SELECT p.record_id,p.ordinal,p.category,p.packet_name,r.tick,p.decoded_json_zlib FROM packets p JOIN records r ON r.id=p.record_id WHERE p.packet_name IN ('+','.join('?' for _ in names)+') AND r.kind=1 AND r.version=1 AND p.record_id<=? ORDER BY p.record_id,p.id'
        for rid,n,cat,name,tick,blob in db.execute(sql,(*names,max_rid)):
            d=restore(blob)
            ids=d.get('objectIds',[]) if name=='CmdInSightRange' else [d.get('objectId')]
            if not any(who in mapping for who in ids):continue
            for e in pose_event_facts(name,d,tick,cat,[rid,n]):
                if e['objectId'] in mapping:
                    if name in ('CmdMoveToDestination','CmdMoveToDestinationAvoidance','CmdMoveStraight'):
                        e['encodedPositionVector2']=d.get('positionVector2')
                    if name=='CmdMoveStraight':
                        e['straightMotion']=dict(requestedDestinationXZ=d.get('destinationVector2'),
                            durationInternalValue=(d.get('duration') or {}).get('internalValue'),ease=d.get('ease'))
                    poses.append({**e,'objectId':mapping[e['objectId']]})
        for rid,n,cat,blob in db.execute("SELECT record_id,ordinal,category,decoded_json_zlib FROM packets WHERE packet_name IN ('CmdUpdateStat','CmdBroadcastUpdateStat') AND record_id<=? ORDER BY record_id,id",(max_rid,)):
            d=restore(blob);who=mapping.get(d.get('objectId'))
            if who is not None and any(s.get('statType')==1 for s in d['updates']):radius_changes[who].append((rid,n))
        for rid,n,blob in db.execute("SELECT record_id,ordinal,decoded_json_zlib FROM packets WHERE packet_name IN ('CmdUpdateMoveSpeed','CmdUpdateMoveSpeedWhenMoving') AND category='commands' AND record_id<=? ORDER BY record_id,id",(max_rid,)):
            d=restore(blob);who=mapping.get(d.get('objectId'))
            if who is not None:
                raw=d.get('moveSpeed',{}).get('internalValue')
                speed_events[who].append(((rid,n),f32(f32(raw)/100) if type(raw)is int else None))
        callback_ids=sorted({at[0] for at in damages})
        # A bounded set of frame records, using the existing packet-name index.
        names=('CmdAddState','CmdStartStateSkill')
        sql='SELECT record_id,ordinal,packet_name,decoded_json_zlib FROM packets WHERE packet_name IN (?,?) AND category=\'commands\' AND record_id IN ('+','.join('?' for _ in callback_ids)+')'
        for rid,n,name,blob in db.execute(sql,(*names,*callback_ids)):
            callback_commands[rid,n]=(name,restore(blob))
    pose_index=StationaryPoseIndex(poses)
    radii={};initial_speeds={};first=source.first_snapshot()
    if type(first.get('seq'))is not int or first['seq']>min(s['tick'] for s in starts.values()):
        raise ValueError('initial snapshot does not precede selected casts')
    for user in first['gameSnapshot']['userList']:
        wrapper=user.get('characterSnapshot')
        if not isinstance(wrapper,dict) or wrapper.get('objectId') not in mapping:continue
        character=decoder.decode_exact(wrapper['snapshot'],'PlayerCharacterSnapshot')
        values=[s['value'] for s in character['initialStat'] if s['statType']==1]
        who=mapping[wrapper['objectId']]
        if character.get('statusSnapshot') is not None:
            status=decoder.decode_exact(character['statusSnapshot'],'PlayerStatusSnapshot')
            initial_speeds[who]=status.get('moveSpeed')
        if len(values)==1 and type(values[0])is int and values[0]>0:
            radii[who]=dict(rawValue=values[0],radius=f32(f32(values[0])/f32(100)))
    def position(who,at,tick,allow_same_tick):
        if who in pose_index.bad:raise ValueError('incomplete-pose-stream')
        row=pose_index.before(who,'position',tuple(at))
        if not row or row['event']!='CmdStopMove':raise ValueError('moving-or-unanchored-position')
        if row['tick']>tick or (row['tick']==tick and not allow_same_tick):raise ValueError('pose-anchor-in-callback-frame')
        p=row.get('positionVector2')
        if not isinstance(p,list) or len(p)!=2 or any(type(v)not in (int,float) or not math.isfinite(v) or f32(v)!=v for v in p):
            raise ValueError('non-exact-stop-position')
        return p,row
    def bookmark_stop(u,damage):
        who=damage['targetObjectId'];at=tuple(damage['wireOrder']);tick=damage['tick']
        if who in pose_index.bad:raise ValueError('incomplete-pose-stream')
        events=pose_index.rows[who,'position'];orders=pose_index.orders.get((who,'position'),[])
        i=bisect_right(orders,at)
        previous=events[i-1] if i else None;stop=events[i] if i<len(events) else None
        if (not previous or previous['event'] not in ('CmdMoveToDestination','CmdMoveToDestinationAvoidance')
                or previous['tick']>=tick or not stop or stop['event']!='CmdStopMove'
                or stop['tick']!=tick or stop['wireOrder'][0]!=at[0]):
            raise ValueError('moving-or-unanchored-position')
        resets=[e for e in events[:i-1] if e['event']=='CmdStopMove' and first['seq']<=e['tick']]
        if not resets:
            raise ValueError('ordinary-strategy-has-no-retained-reset')
        completed=[(t,r) for t,r in frames if previous['tick']<=t<tick
            and previous['wireOrder'][0]<=r<at[0]]
        if not completed:raise ValueError('no-previous-completed-frame')
        frame_tick,frame_record=completed[-1]
        n=stop['wireOrder'][1];start=u['start'];raw=reverse[who];caster=reverse[start['playerObjectId']]
        adds=[(o,d) for o,(name,d) in callback_commands.items() if name=='CmdAddState'
            and at<o<(at[0],n) and d.get('objectId')==raw]
        add=adds[0][1] if len(adds)==1 else None
        state=callback_commands.get((at[0],n+1))
        from .skill_sua_odyssey_profiles import STUN_CODES,PROFILES
        stun_codes=STUN_CODES
        profiles=[p for p in PROFILES.values() if start['skillCode'] in p['states']]
        if len(profiles)!=1:raise ValueError('unknown-odyssey-bookmark-profile')
        bookmark_code=profiles[0]['bookmark']
        if (not add or add.get('casterId')!=caster or add.get('code')!=stun_codes.get(start['skillCode'])
                or not state or state[0]!='CmdStartStateSkill'
                or any(state[1].get(k)!=v for k,v in dict(objectId=raw,casterId=caster,skillId=9,
                    skillCode=start['skillCode'],stateGroup=1028200).items())):
            raise ValueError('stop-is-not-exact-bookmark-stun-bracket')
        marks=[m for m in u.get('marking',[]) if m['targetObjectId']==who and m['tick']==tick
            and m.get('effectCode')==bookmark_code and at<tuple(m['wireOrder'])<adds[0][0]]
        if len(marks)!=1:raise ValueError('stop-lacks-exact-bookmark-damage')
        reset_at=tuple(resets[-1]['wireOrder'])
        before_reset=[v for o,v in speed_events[who] if o<=reset_at]
        speed_at_reset=before_reset[-1] if before_reset else initial_speeds.get(who)
        speeds=[speed_at_reset,*(v for o,v in speed_events[who] if reset_at<o<=tuple(stop['wireOrder']))]
        if any(type(v)not in (int,float) or not math.isfinite(v) or not 0<=v<=128 for v in speeds):
            raise ValueError('ordinary-speed-upper-bound-unavailable')
        p=stop.get('positionVector2')
        if not isinstance(p,list) or len(p)!=2 or any(type(v)not in (int,float) or not math.isfinite(v) or f32(v)!=v for v in p):
            raise ValueError('non-exact-stop-position')
        witness=dict(method='same-callback-bookmark-stop-ordinary-sweep',nativeRuleSha256=BOOKMARK_STOP_SWEEP_RULE_SHA256,
            castWireOrder=start['wireOrder'],damageWireOrder=list(at),callbackTick=tick,previousFrameTick=frame_tick,
            previousFrameRecord=frame_record,moveTick=previous['tick'],maxFrames=tick-frame_tick,
            moveWireOrder=previous['wireOrder'],stopWireOrder=stop['wireOrder'],markWireOrder=marks[0]['wireOrder'],
            stunWireOrder=list(adds[0][0]),stateSkillWireOrder=[at[0],n+1],maxHistoricalMoveSpeed=max(speeds),
            speedHistorySource='status-at-reset-and-both-speed-command-types',strategyResetWireOrder=list(reset_at),
            statusSpeedAtReset=speed_at_reset,interveningPositionCommands=0)
        return p,stop,witness
    def source_cell(u,damage):
        who=damage['targetObjectId'];at=tuple(damage['wireOrder']);tick=damage['tick']
        if who in pose_index.bad:raise ValueError('incomplete-pose-stream')
        anchor=pose_index.before(who,'position',at)
        if anchor and anchor['event']=='CmdMoveStraight':
            raise ValueError('straight-motion-needs-adjusted-destination-and-elapsed-state')
        if not anchor or anchor['event'] not in ('CmdMoveToDestination','CmdMoveToDestinationAvoidance'):
            raise ValueError('moving-or-unanchored-position')
        bounds=position_bounds(anchor.get('encodedPositionVector2'))
        i=bisect_right(frame_ticks,anchor['tick'])
        continuity=None
        if i>=len(frames) or not frames[i][0]<tick or not anchor['wireOrder'][0]<frames[i][1]<at[0]:
            prior=pose_index.before(who,'position',tuple(anchor['wireOrder']))
            same=[(t,r) for t,r in frames if t==anchor['tick'] and r==anchor['wireOrder'][0] and r<at[0]]
            if (not same or not prior or prior['event'] not in ('CmdMoveToDestination','CmdMoveToDestinationAvoidance')
                    or not prior['tick']<anchor['tick']<tick):
                if anchor['tick']==tick and tuple(anchor['wireOrder'])<at and anchor['wireOrder'][0]==at[0]:
                    frame_tick,frame_record=tick,at[0]
                    continuity=dict(method='same-frame-source-before-damage-positive-only',sourceMoveWireOrder=anchor['wireOrder'])
                else:
                    from .skill_sua_stop_source_bridge import stop_source_bridge
                    continuity=stop_source_bridge(prior,anchor,frames,damage)
                    if continuity is None:raise ValueError('source-cell-has-no-completed-intermediate-frame')
                    frame_tick,frame_record=anchor['tick'],anchor['wireOrder'][0]
            else:
                frame_tick,frame_record=same[-1]
                continuity=dict(method='ordinary-movement-precedes-source-frame',previousMoveTick=prior['tick'],
                    previousMoveWireOrder=prior['wireOrder'],sourceMoveWireOrder=anchor['wireOrder'])
        else:frame_tick,frame_record=frames[i]
        resets=[e for e in pose_index.rows[who,'position'] if e['event']=='CmdStopMove'
            and first['seq']<=e['tick'] and tuple(e['wireOrder'])<tuple(anchor['wireOrder'])]
        if not resets:raise ValueError('ordinary-strategy-has-no-retained-reset')
        reset_at=tuple(resets[-1]['wireOrder'])
        earlier=[v for o,v in speed_events[who] if o<=reset_at]
        initial=earlier[-1] if earlier else initial_speeds.get(who)
        speeds=[initial,*(v for o,v in speed_events[who] if reset_at<o<=at)]
        if any(type(v)not in (int,float) or not math.isfinite(v) or not 0<=v<=128 for v in speeds):
            raise ValueError('ordinary-speed-upper-bound-unavailable')
        witness=dict(method='ordinary-source-cell-sweep-exclusion',nativeRuleSha256=SOURCE_CELL_SWEEP_RULE_SHA256,
            castWireOrder=u['start']['wireOrder'],damageWireOrder=list(at),callbackTick=tick,anchorTick=anchor['tick'],
            anchorWireOrder=anchor['wireOrder'],encodedPositionVector2=anchor['encodedPositionVector2'],
            recordedPath=anchor.get('recordedPath'),
            sourcePositionBounds=bounds,maxFrames=tick-anchor['tick']+1,maxStrategyMoveSpeed=max(speeds),
            strategyResetWireOrder=list(reset_at),completedFrameTick=frame_tick,completedFrameRecord=frame_record,
            ordinaryContinuity=continuity,
            callbackPositionKnown=False,positiveHitProof=False)
        return None,anchor,witness
    rows=[];unknown=[];proofs=[];moving_inputs=[]
    for u in uses:
        start=u['start'];actor=start['playerObjectId'];projectile=u['projectile']
        at=tuple(start['wireOrder']);spawn_at=tuple(projectile['wireOrder'])
        for damage in u['damages']:
            target=damage['targetObjectId'];damage_at=tuple(damage['wireOrder'])
            if target not in teams or teams[target]==teams[actor]:continue
            position_failures={}
            position_stage=None
            try:
                if not {at,spawn_at,damage_at}<=seen:raise ValueError('missing-exact-source-anchor')
                wrapper=raw_spawns[spawn_at]
                outer=decoder.decode_exact(wrapper['snapshot'],'ProjectileSnapshot')
                if outer['ownerId']!=reverse[actor] or outer['code']!=projectile['projectileCode']:
                    raise ValueError('projectile-owner-code-mismatch')
                if wrapper['objectType']!=14:raise ValueError('nonprojectile-wrapper')
                point=wrapper['positionXZ']
                position_stage='casterStationary'
                caster,actor_stop=position(actor,at,start['tick'],True)
                sweep=None;cell=None;estimate=None
                position_stage='targetStationary'
                try:target_pos,target_stop=position(target,damage_at,damage['tick'],False)
                except ValueError as stationary_error:
                    position_failures[position_stage]=str(stationary_error)
                    position_stage='bookmarkStop'
                    try:target_pos,target_stop,sweep=bookmark_stop(u,damage)
                    except ValueError as bookmark_error:
                        position_failures[position_stage]=str(bookmark_error)
                        position_stage='sourceCell'
                        try:target_pos,target_stop,cell=source_cell(u,damage)
                        except ValueError as source_cell_error:
                            position_failures[position_stage]=str(source_cell_error)
                            from .skill_development_effect_metrics import development_policy
                            policy=development_policy()
                            if not (policy['enabled'] and policy.get('recordedSourceCellRegionEstimateEnabled') is True):raise
                            from .skill_recorded_pose_estimate import recorded_pose_estimate
                            position_stage='recordedPoseEstimate'
                            target_pos,target_stop,estimate=recorded_pose_estimate(pose_index,target,damage_at,damage['tick'])
                position_stage=None
                if target not in radii:raise ValueError('missing-initial-collision-radius')
                if any(o<=damage_at for o in radius_changes[target]):raise ValueError('collision-radius-stat-updated')
                rotation=sua_center_rotation(caster,point)
                sub_absent=None;i=bisect_right(frame_ticks,target_stop['tick'])
                if sweep is None and cell is None and estimate is None and i<len(frames) and frames[i][0]<damage['tick']:
                    sub_absent=dict(method='stationary-through-completed-server-post-frame',
                        targetStopTick=target_stop['tick'],targetStopWireOrder=target_stop['wireOrder'],
                        completedFrameTick=frames[i][0],completedFrameRecord=frames[i][1],
                        callbackTick=damage['tick'],nativeRuleSha256=STATIONARY_SUB_RULE_SHA256)
                proof=dict(matchKey=cache['matchKey'],sourceParserSha256=source.parser_sha256,
                    castWireOrder=list(at),spawnWireOrder=list(spawn_at),damageWireOrder=list(damage_at),
                    actorStopWireOrder=actor_stop['wireOrder'],targetStopWireOrder=target_stop['wireOrder'] if cell is None else None,
                    casterPositionXZ=caster,boxPivotXZ=point,targetPivotXZ=target_pos,
                    subCollisionAbsent=sub_absent,
                    ordinarySweepBound=sweep,
                    sourceCellSweepBound=cell,
                    recordedPoseEstimate=estimate,
                    radiusStatType=1,radiusRawValue=radii[target]['rawValue'],radiusStatUnchanged=True,
                    rotation=rotation,automaticInputExtraction=True)
                digest=hashlib.sha256(json.dumps(proof,sort_keys=True,separators=(',',':')).encode()).hexdigest()
                rows.append(dict(playerObjectId=actor,projectileObjectId=projectile['projectileObjectId'],
                    targetObjectId=target,tick=damage['tick'],castWireOrder=list(at),damageWireOrder=list(damage_at),
                    boxPivotXZ=point,targetPivotXZ=target_pos,targetRadius=radii[target]['radius'],
                    nativeRotationPairs=rotation['nativeRotationPairs'],sourceProofSha256=digest,
                    subCollisionAbsent=sub_absent,ordinarySweepBound=sweep,sourceCellSweepBound=cell,recordedPoseEstimate=estimate))
                proofs.append(dict(sha256=digest,evidence=proof))
            except (KeyError,TypeError,ValueError,OverflowError,ZeroDivisionError) as error:
                if position_stage is not None:
                    position_failures[position_stage]=str(error)
                unknown.append(dict(castWireOrder=list(at),damageWireOrder=list(damage_at),reason=str(error),
                    positionStageFailureReasons=position_failures))
                latest=pose_index.before(target,'position',damage_at)
                if latest and 'encodedPositionVector2' in latest:
                    try:bounds=position_bounds(latest['encodedPositionVector2'])
                    except ValueError:bounds=None
                    moving_inputs.append(dict(targetObjectId=target,castWireOrder=list(at),damageWireOrder=list(damage_at),
                        event=latest['event'],tick=latest['tick'],wireOrder=latest['wireOrder'],
                        encodedPositionVector2=latest['encodedPositionVector2'],
                        precisionStatus='source-cell-known' if bounds is not None else 'source-cell-unavailable',sourcePositionBounds=bounds))
                    if latest.get('recordedPath') is not None:
                        moving_inputs[-1]['recordedPath']=latest['recordedPath']
                    if latest.get('straightMotion') is not None:
                        moving_inputs[-1]['straightMotion']={**latest['straightMotion'],
                            'nativeRuleSha256':STRAIGHT_MOTION_RULE_SHA256,
                            'adjustedDestinationStatus':'not-observed',
                            'elapsedStateStatus':'not-observed',
                            'tickDifferenceIsExactElapsedTime':False,
                            'laterStopIsExactPastPosition':False}
    return rows,dict(status='extracted',automaticInputExtraction=True,contacts=len(rows),unknown=unknown,proofs=proofs,movingInputs=moving_inputs,
        retainedSourceSha256=cache['matchKey'],sourceParserSha256=source.parser_sha256,
        poseEventsRead=len(poses),elapsedSeconds=round(time.perf_counter()-begun,3),rawReplayDecodedAgain=False,networkCalls=0)
