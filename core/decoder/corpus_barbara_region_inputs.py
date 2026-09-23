"""Indexed retained-command extraction for Barbara's base E regions."""
from collections import defaultdict
from contextlib import closing
from pathlib import Path
import json,time
try:
    from .skill_game_data_contract import revision_identity
    from .corpus_runtime_source import restore
    from .delta_payloads import SchemaDecoder
    from .skill_pose_events import pose_packet_channels,pose_event_facts,f32,StationaryPoseIndex
    from .corpus_command_routing import retained_command_routing
except ImportError:
    from skill_game_data_contract import revision_identity
    from corpus_runtime_source import restore
    from delta_payloads import SchemaDecoder
    from skill_pose_events import pose_packet_channels,pose_event_facts,f32,StationaryPoseIndex
    from corpus_command_routing import retained_command_routing


def retained_post_stop_current_position_order(source,inputs):
    """Recover routing only for post-stop anchors used by region contacts."""
    index=StationaryPoseIndex(inputs['poses']);pairs={};ordinary={'CmdMoveToDestination','CmdMoveToDestinationAvoidance'}
    for damage in inputs['damages']:
        target=damage['objectId'];anchor=index.before(target,'position',tuple(damage['wireOrder']))
        if not anchor or anchor['event'] not in ordinary or anchor['tick']!=damage['tick']-1:continue
        stop=index.before(target,'position',tuple(anchor['wireOrder']))
        if (not stop or stop['event']!='CmdStopMove' or stop['tick']!=anchor['tick']
                or stop['wireOrder'][0]!=anchor['wireOrder'][0]):continue
        pairs[tuple(anchor['wireOrder'])]=(stop,anchor)
    proof=dict(matchKey=inputs['matchKey'],rule='schema/post-stop-current-position-bound-v1.json',pairs=[])
    if not pairs:return proof
    selected={tuple(e['wireOrder']):e for pair in pairs.values() for e in pair};ids={}
    with closing(source.connect()) as db:
        if db.execute("SELECT value FROM metadata WHERE key='sourceSha256'").fetchone()[0]!=inputs['matchKey']:
            raise ValueError('post-stop input/source mismatch')
        for tick in sorted({e['tick'] for e in selected.values()}):
            for pid,rid,n,name,category,blob in db.execute('''SELECT id,record_id,ordinal,packet_name,category,decoded_json_zlib
                    FROM packets WHERE tick=? AND packet_name IN
                    ('CmdStopMove','CmdMoveToDestination','CmdMoveToDestinationAvoidance')''',(tick,)):
                e=selected.get((rid,n))
                if e is None:continue
                d=restore(blob)
                if category!='commands' or name!=e['event'] or d['objectId']!=e['objectId']:
                    raise ValueError('post-stop pose identity mismatch')
                field='positionVector2' if name=='CmdStopMove' else 'encodedPositionVector2'
                if d['positionVector2']!=e.get(field):raise ValueError('post-stop pose value mismatch')
                if (rid,n) in ids:raise ValueError('duplicate post-stop command')
                ids[rid,n]=pid
    if set(ids)!=set(selected):raise ValueError('missing post-stop command')
    masks={};packet_ids=list(ids.values())
    for offset in range(0,len(packet_ids),1000):
        routing=retained_command_routing(source,packet_ids[offset:offset+1000])
        masks.update({tuple(f['wireOrder']):f['targetMask'] for f in routing['facts']})
    for stop,anchor in pairs.values():
        a=masks[tuple(stop['wireOrder'])];b=masks[tuple(anchor['wireOrder'])]
        if a==b and 0<a<2**32 and a&(a-1)==0:
            proof['pairs'].append(dict(objectId=anchor['objectId'],tick=anchor['tick'],
                stopWireOrder=stop['wireOrder'],anchorWireOrder=anchor['wireOrder']))
    return proof


def retained_barbara_region_inputs(source,cache,tables):
    begun=time.perf_counter()
    if source is None:return None
    revision_identity(cache['clientVersion'],cache['gameDataSha256'])
    schema=json.loads((Path(__file__).resolve().parent.parent/'schema/schema.json').read_text())
    decoder=SchemaDecoder({n:v['base'] for n,v in schema['classes'].items()},client_version=cache['clientVersion'])
    cache_starts={tuple(s['wireOrder']):s for s in cache['facts']['starts'] if s.get('wireOrder')}
    cache_damages={tuple(s['wireOrder']):s for s in cache['facts']['damages'] if s.get('wireOrder')}
    raw_players={};local_players={};teams={int(k) for k in cache['teams']}
    def bind(raw,local):
        if local not in teams:return
        if raw_players.setdefault(str(local),raw)!=raw or local_players.setdefault(raw,local)!=local:
            raise ValueError('conflicting exact player identity')
    starts=[];finishes=[];damages=[];states=[];projectiles=[];summons=[];arrivals=[];poses=[];speed_events=defaultdict(list)
    names=tuple(set(pose_packet_channels())|{'CmdStartSkill','CmdFinishSkill','CmdDamage','CmdAddState','CmdAddStateExtended',
        'CmdSpawn','CmdSpawnBatch','CmdProjectileArrived','CmdUpdateMoveSpeed','CmdUpdateMoveSpeedWhenMoving'})
    with closing(source.connect()) as db:
        if db.execute("SELECT value FROM metadata WHERE key='sourceSha256'").fetchone()[0]!=cache['matchKey']:
            raise ValueError('Barbara cache/source identity mismatch')
        frames=list(db.execute('SELECT tick,id FROM records WHERE kind=1 AND version=1 ORDER BY tick,id'))
        sql='SELECT p.record_id,p.ordinal,p.category,p.packet_name,r.tick,p.decoded_json_zlib FROM packets p JOIN records r ON r.id=p.record_id WHERE p.packet_name IN ('+','.join('?' for _ in names)+') ORDER BY p.record_id,p.id'
        for rid,n,category,name,tick,blob in db.execute(sql,names):
            d=restore(blob);at=(rid,n);meta=dict(tick=tick,wireOrder=[rid,n],wireCategory=category)
            if category!='commands':
                if name in pose_packet_channels():poses.extend(pose_event_facts(name,d,tick,category,[rid,n]))
                continue
            if name=='CmdStartSkill':
                anchor=cache_starts.get(at)
                if anchor:
                    if (anchor['skillCode'],anchor['skillIdCode'])!=(d['skillCode'],d['skillId']):raise ValueError('Barbara start anchor mismatch')
                    bind(d['objectId'],anchor['playerObjectId'])
                if d['skillId'] in (355,356):starts.append(dict(**d,**meta,playerObjectId=d['objectId'],skillIdCode=d['skillId']))
            elif name=='CmdFinishSkill':
                if d['skillId'] in (355,356):finishes.append(dict(**d,**meta,playerObjectId=d['objectId'],skillIdCode=d['skillId']))
            elif name=='CmdDamage':
                anchor=cache_damages.get(at)
                if anchor:
                    if anchor['effectCode']!=d['effectCode']:raise ValueError('Barbara damage anchor mismatch')
                    bind(d['attackerId'],anchor['attackerObjectId']);bind(d['objectId'],anchor['targetObjectId'])
                if d['effectCode']==1026401:damages.append(dict(**meta,objectId=d['objectId'],attackerId=d['attackerId'],effectCode=d['effectCode']))
            elif name in ('CmdAddState','CmdAddStateExtended'):
                if d['code'] in range(1026401,1026406):states.append(dict(**d,**meta))
            elif name=='CmdSpawnBatch':
                raise ValueError('unreviewed batch spawn in Barbara extraction')
            elif name=='CmdSpawn':
                w=d['snapshot']
                if w['objectType']==14:
                    value=decoder.decode_exact(w['snapshot'],'ProjectileSnapshot')
                    if value['code']==102641:projectiles.append(dict(**meta,objectId=w['objectId'],ownerId=value['ownerId'],code=value['code']))
                elif w['objectType']==11:
                    value=decoder.decode_exact(w['snapshot'],'SummonServantSnapshot')
                    if value['summonId']==1102:
                        definition=[r for r in tables['SummonObject'] if r['code']==1102]
                        if len(definition)!=1 or definition[0].get('useAttackerType')!='Owner' or definition[0].get('prefabPath')!='Barbara_Skill03_Explosion':
                            raise ValueError('Barbara explosion identity mismatch')
                        summons.append(dict(**meta,objectId=w['objectId'],ownerId=value['ownerId'],code=1102,positionXZ=w['positionXZ']))
            elif name=='CmdProjectileArrived':arrivals.append(dict(**d,**meta))
            elif name in ('CmdUpdateMoveSpeed','CmdUpdateMoveSpeedWhenMoving'):
                raw=(d.get('moveSpeed') or {}).get('internalValue')
                speed_events[str(d['objectId'])].append(dict(**meta,speed=f32(f32(raw)/100) if type(raw)is int else None))
            elif name in pose_packet_channels():
                events=pose_event_facts(name,d,tick,category,[rid,n])
                for e in events:
                    if name in ('CmdMoveToDestination','CmdMoveToDestinationAvoidance'):e['encodedPositionVector2']=d.get('positionVector2')
                poses.extend(events)
    actors={raw_players[str(p['objectId'])] for p in cache['players'] if p['characterCode']==26 and str(p['objectId']) in raw_players}
    relevant_summons=[s for s in summons if s['ownerId'] in actors];children={s['objectId'] for s in relevant_summons}
    relevant_damages=[d for d in damages if d['attackerId'] in actors]
    targets={d['objectId'] for d in relevant_damages};wanted=actors|children|targets
    initial=source.first_snapshot();speeds={}
    for user in initial['gameSnapshot']['userList']:
        w=user.get('characterSnapshot')
        if not isinstance(w,dict) or w.get('objectId') not in targets:continue
        c=decoder.decode_exact(w['snapshot'],'PlayerCharacterSnapshot')
        if c.get('statusSnapshot') is not None:
            status=decoder.decode_exact(c['statusSnapshot'],'PlayerStatusSnapshot');speeds[str(w['objectId'])]=status.get('moveSpeed')
    if any(initial['seq']>s['tick'] for s in starts if s['playerObjectId'] in actors):
        raise ValueError('initial movement status follows selected cast')
    result=dict(matchKey=cache['matchKey'],sourceParserSha256=source.parser_sha256,rawPlayers=raw_players,
        starts=[s for s in starts if s['playerObjectId'] in actors|children],
        finishes=[s for s in finishes if s['playerObjectId'] in actors|children],
        projectiles=[s for s in projectiles if s['ownerId'] in actors],summons=relevant_summons,
        arrivals=[a for a in arrivals if a['objectId'] in {p['objectId'] for p in projectiles if p['ownerId'] in actors}],
        damages=relevant_damages,states=[s for s in states if s['casterId'] in actors],
        poses=[e for e in poses if e['objectId'] in wanted],frames=frames,
        speedEvents={k:v for k,v in speed_events.items() if int(k) in targets},initialSpeeds=speeds,initialSnapshotTick=initial['seq'],
        seconds=round(time.perf_counter()-begun,3),rawReplayDecodedAgain=False,networkCalls=0)
    result['postStopCurrentPositionOrder']=retained_post_stop_current_position_order(source,result)
    result['seconds']=round(time.perf_counter()-begun,3)
    return result
