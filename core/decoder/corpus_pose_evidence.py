"""Restore pose facts from retained decoded SQL with exact identity anchors."""
from contextlib import closing
try:
    from .skill_game_data_contract import revision_identity
    from .corpus_runtime_source import restore
    from .skill_pose_events import pose_event_facts,pose_packet_channels,FULL_POSE_EVENT_SHAPE
except ImportError:
    from skill_game_data_contract import revision_identity
    from corpus_runtime_source import restore
    from skill_pose_events import pose_event_facts,pose_packet_channels,FULL_POSE_EVENT_SHAPE

def retained_pose_events(source,cache,*,include_motion=False):
    facts=cache['facts'];teams={int(k) for k in cache['teams']}
    starts={tuple(s['wireOrder']):s for s in facts['starts'] if s.get('wireCategory')=='commands' and s.get('wireOrder')}
    damages={tuple(d['wireOrder']):d for d in facts['damages'] if d.get('wireCategory')=='commands' and d.get('wireOrder')}
    mapping={0:0,-1:-1};reverse={0:0,-1:-1}
    def bind(raw,local):
        if raw in mapping and mapping[raw]!=local or local in reverse and reverse[local]!=raw:
            raise ValueError('conflicting exact pose identity anchors')
        mapping[raw]=local;reverse[local]=raw
    events=[]
    with closing(source.connect()) as db:
        sql="SELECT p.record_id,p.ordinal,p.packet_name,p.decoded_json_zlib FROM packets p WHERE p.packet_name IN ('CmdStartSkill','CmdDamage') AND p.category='commands' ORDER BY p.record_id,p.id"
        for rid,n,name,blob in db.execute(sql):
            at=(rid,n)
            if name=='CmdStartSkill' and at in starts:
                d=restore(blob);s=starts[at]
                if (d['skillId'],d['skillCode'])!=(s['skillIdCode'],s['skillCode']):raise ValueError('start anchor mismatch')
                bind(d['objectId'],s['playerObjectId'])
            elif name=='CmdDamage' and at in damages:
                d=restore(blob);s=damages[at]
                if d['effectCode']!=s['effectCode']:raise ValueError('damage anchor mismatch')
                bind(d['objectId'],s['targetObjectId']);bind(d['attackerId'],s['attackerObjectId'])
        if cache.get('poseEventShape')==FULL_POSE_EVENT_SHAPE:
            # This explicit shape retains every pose field used below. Older
            # projected caches select the retained-SQL route before evaluation.
            known_players=teams & set(reverse)
            events=[dict(event) for event in facts['poseEvents'] if event['objectId'] in known_players]
        else:
            names=tuple(pose_packet_channels())
            sql='SELECT p.record_id,p.ordinal,p.category,p.packet_name,r.tick,p.decoded_json_zlib FROM packets p JOIN records r ON r.id=p.record_id WHERE p.packet_name IN ('+','.join('?' for _ in names)+') AND r.kind=1 AND r.version=1 ORDER BY p.record_id,p.id'
            for rid,n,cat,name,tick,blob in db.execute(sql,names):
                d=restore(blob)
                for event in pose_event_facts(name,d,tick,cat,[rid,n]):
                    local=mapping.get(event['objectId'])
                    if local in teams:events.append({**event,'objectId':local})
        if not include_motion:
            for event in events:
                if event['event'] in ('CmdMoveToDestination','CmdMoveToDestinationAvoidance'):
                    event.pop('encodedPositionVector2',None)
        motion=None
        if include_motion:
            from collections import defaultdict
            from pathlib import Path
            import json
            try:
                from .delta_payloads import SchemaDecoder
                from .skill_pose_events import f32
            except ImportError:
                from delta_payloads import SchemaDecoder
                from skill_pose_events import f32
            revision_identity(cache['clientVersion'],cache['gameDataSha256'])
            if db.execute("SELECT value FROM metadata WHERE key='sourceSha256'").fetchone()[0]!=cache['matchKey']:
                raise ValueError('motion input source/cache mismatch')
            frames=list(db.execute('SELECT tick,id FROM records WHERE kind=1 AND version=1 ORDER BY tick,id'))
            speeds=defaultdict(list)
            for rid,n,blob in db.execute("SELECT record_id,ordinal,decoded_json_zlib FROM packets WHERE packet_name IN ('CmdUpdateMoveSpeed','CmdUpdateMoveSpeedWhenMoving') AND category='commands' ORDER BY record_id,id"):
                d=restore(blob);local=mapping.get(d.get('objectId'))
                if local not in teams:continue
                raw=(d.get('moveSpeed') or {}).get('internalValue')
                speeds[str(local)].append(dict(wireOrder=[rid,n],speed=f32(f32(raw)/100) if type(raw)is int else None))
            initial=source.first_snapshot();initial_speeds={};initial_movement_states={}
            schema=json.loads((Path(__file__).resolve().parent.parent/'schema/schema.json').read_text())
            decoder=SchemaDecoder({n:v['base'] for n,v in schema['classes'].items()},client_version=cache['clientVersion'])
            for user in initial['gameSnapshot']['userList']:
                wrapper=user.get('characterSnapshot')
                if not isinstance(wrapper,dict):continue
                local=mapping.get(wrapper.get('objectId'))
                if local not in teams:continue
                character=decoder.decode_exact(wrapper['snapshot'],'PlayerCharacterSnapshot')
                agent=character.get('moveAgentSnapshot')
                initial_movement_states[str(local)]=dict(isAlive=character.get('isAlive'),
                    isDyingCondition=character.get('isDyingCondition'),
                    walkableNavMask=agent.get('walkableNavMask') if isinstance(agent,dict) else None)
                if character.get('statusSnapshot') is not None:
                    status=decoder.decode_exact(character['statusSnapshot'],'PlayerStatusSnapshot')
                    initial_speeds[str(local)]=status.get('moveSpeed')
            control_events=[]
            control_names=('CmdSetWalkableNavMask','CmdDyingCondition','CmdDead','CmdTeamRevival','CmdResurrection','CmdDestroy')
            sql='SELECT record_id,ordinal,tick,category,packet_name,decoded_json_zlib FROM packets WHERE packet_name IN ('+','.join('?' for _ in control_names)+') ORDER BY record_id,id'
            for rid,n,tick,cat,name,blob in db.execute(sql,control_names):
                d=restore(blob);local=mapping.get(d.get('objectId'))
                if local not in teams:continue
                control=dict(objectId=local,event=name,tick=tick,wireOrder=[rid,n],wireCategory=cat)
                if name=='CmdSetWalkableNavMask':control['walkableNavMask']=d.get('walkableNavMask')
                control_events.append(control)
            motion=dict(matchKey=cache['matchKey'],sourceParserSha256=source.parser_sha256,poses=events,
                frames=frames,speedEvents=dict(speeds),initialSpeeds=initial_speeds,initialSnapshotTick=initial['seq'])
            motion.update(initialMovementStates=initial_movement_states,movementControlEvents=control_events)
            motion['rotationBaselines']=retained_rotation_baselines(source,cache,mapping=mapping)
    proof=dict(mappedPlayerCount=len(teams & set(reverse)),sourceParserSha256=source.parser_sha256,
               rawReplayDecodedAgain=False,networkCalls=0)
    if include_motion:proof['motionInputs']=motion
    return events,proof


def retained_rotation_baselines(source,cache,*,mapping=None):
    """Read native lock counts from periodic snapshots, not guessed booleans."""
    import json,hashlib
    from pathlib import Path
    from collections import defaultdict
    try:
        from .delta_payloads import SchemaDecoder
    except ImportError:
        from delta_payloads import SchemaDecoder
    players={p['objectId'] for p in cache['players'] if p['characterCode']==3}
    baselines=defaultdict(list)
    schema=json.loads((Path(__file__).resolve().parent.parent/'schema/schema.json').read_text())
    decoder=SchemaDecoder({n:v['base'] for n,v in schema['classes'].items()},client_version=cache['clientVersion'])
    with closing(source.connect()) as db:
        if db.execute("SELECT value FROM metadata WHERE key='sourceSha256'").fetchone()[0]!=cache['matchKey']:
            raise ValueError('rotation snapshot/cache identity mismatch')
        if mapping is None:
            anchors={tuple(s['wireOrder']):s for s in cache['facts']['starts'] if s['playerObjectId'] in players and s.get('wireOrder')}
            mapping={};reverse={}
            for rid,n,blob in db.execute("SELECT record_id,ordinal,decoded_json_zlib FROM packets WHERE packet_name='CmdStartSkill' AND category='commands' ORDER BY record_id,id"):
                anchor=anchors.get((rid,n))
                if not anchor:continue
                d=restore(blob);local=anchor['playerObjectId'];raw=d['objectId']
                if (d['skillCode'],d['skillId'])!=(anchor['skillCode'],anchor['skillIdCode']):raise ValueError('rotation start identity mismatch')
                if mapping.setdefault(raw,local)!=local or reverse.setdefault(local,raw)!=raw:raise ValueError('ambiguous rotation player identity')
        for rid,blob in db.execute("SELECT record_id,decoded_json_zlib FROM packets WHERE category='fullSnapshot' ORDER BY id"):
            snapshot=restore(blob)
            for user in snapshot['gameSnapshot']['userList']:
                wrapper=user.get('characterSnapshot')
                if not isinstance(wrapper,dict):continue
                local=mapping.get(wrapper.get('objectId'))
                if local not in players:continue
                character=decoder.decode_exact(wrapper['snapshot'],'PlayerCharacterSnapshot')
                agent=character.get('moveAgentSnapshot')
                if not isinstance(agent,dict):continue
                baselines[str(local)].append(dict(recordId=rid,tick=snapshot['seq'],
                    lockRotation=agent.get('lockRotation'),rotateStrategyType=agent.get('RotateStrategyType'),
                    payloadSha256=hashlib.sha256(wrapper['snapshot']).hexdigest()))
    return dict(baselines)
