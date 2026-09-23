"""Ordered pose commands. Unsupported strategies invalidate, never interpolate."""
from bisect import bisect_left
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
import hashlib,json,math,struct

ROOT=Path(__file__).resolve().parent.parent
TABLE_SHA='34be83350dc6c0b862495fc229630bf2a9f713576f9b30442fc61f3c497fac96'
FULL_POSE_EVENT_SHAPE='full-ordered-pose.v2'
f32=lambda x:struct.unpack('<f',struct.pack('<f',x))[0]

@lru_cache(maxsize=1)
def pose_packet_channels():
    classes=json.loads((ROOT/'schema/schema.json').read_text(encoding='utf-8'))['classes']
    def inherits(name,base):
        while name:
            if name==base:return True
            if name not in classes:return False
            name=classes[name]['base']
        return False
    channels={}
    for name in classes:
        if not name.startswith('Cmd'):continue
        if inherits(name,'MoveCommandPacket'):channels[name]='both'
        elif inherits(name,'RotateCommandPacket'):channels[name]='rotation'
    channels.update({n:'both' for n in ['CmdWarpTo','CmdMoveStraightWithoutNav','CmdInSightRange','CmdResurrection','CmdMovementSkillInterrupt']})
    channels.update({n:'rotation' for n in ['CmdLookAtInstance','CmdLookAtSmoothly','CmdLockRotation']})
    return channels

def pose_event_facts(name,decoded,tick,category,wire_order):
    channel=pose_packet_channels().get(name)
    if channel is None:return []
    ids=decoded.get('objectIds',[]) if name=='CmdInSightRange' else [decoded.get('objectId')]
    rows=[]
    for who in ids:
        if type(who) is not int:raise ValueError('pose command lacks object identity')
        row=dict(event=name,objectId=who,tick=tick,wireCategory=category,wireOrder=wire_order,poseChannel=channel)
        if name=='CmdStopMove':
            row['positionVector2']=decoded.get('positionVector2')
        elif name in ('CmdMoveToDestination','CmdMoveToDestinationAvoidance'):
            row['encodedPositionVector2']=decoded.get('positionVector2')
            # Preserve the encoded path; do not invent a decoded server path.
            row['recordedPath']={key:decoded[key] for key in
                ('relativeDestinationVector2','cornersVector2') if key in decoded}
        elif name=='CmdWarpTo':
            # This is the requested warp destination emitted before the server
            # warp/callback sequence, not an exact post-warp position witness.
            row['destinationVector2']=decoded.get('destinationVector2')
        elif name=='CmdMoveInCurve':
            row['encodedPositionVector2']=decoded.get('positionVector2')
            for field in ('moveSpeed','isStatSpeed','angularSpeed',
                          'startRotationAngleFromForward','targetDirectionAngleFromForward'):
                row[field]=decoded.get(field)
        elif name=='CmdMoveStraightWithoutNav':
            row['startPosVector2']=decoded.get('startPosVector2')
            row['endPosVector2']=decoded.get('endPosVector2')
            duration=decoded.get('duration')
            row['durationInternalValue']=duration.get('internalValue') if isinstance(duration,dict) else None
        elif name=='CmdMoveStraight':
            row['encodedPositionVector2']=decoded.get('positionVector2')
            row['destinationVector2']=decoded.get('destinationVector2')
            row['ease']=decoded.get('ease')
            duration=decoded.get('duration')
            row['durationInternalValue']=duration.get('internalValue') if isinstance(duration,dict) else None
        elif name=='CmdLookAtInstance':row['lookAtToY']=decoded.get('lookAtToY')
        elif name=='CmdLockRotation':row['isLock']=decoded.get('isLock')
        rows.append(row)
    return rows

@lru_cache(maxsize=1)
def native_yaw_table():
    data=(ROOT/'schema/unity-native-yaw-forward-v1.bin').read_bytes()
    if len(data)!=36001*16 or hashlib.sha256(data).hexdigest()!=TABLE_SHA:
        raise ValueError('native yaw table identity mismatch')
    return data

def native_yaw_forward(angle):
    if type(angle) is not int or not 0<=angle<=36000:raise ValueError('unreviewed wire yaw')
    x,z,ax,az=struct.unpack_from('<4f',native_yaw_table(),angle*16)
    return ((x,0.0,z),(ax,0.0,az))

class StationaryPoseIndex:
    def __init__(self,events,rotation_baselines=None):
        self.rotation_baselines=rotation_baselines
        self.rows=defaultdict(list);self.orders={};self.bad=set()
        channels=pose_packet_channels()
        for event in events:
            who=event.get('objectId');name=event.get('event');channel=channels.get(name)
            at=event.get('wireOrder')
            if channel is None or event.get('poseChannel')!=channel or event.get('wireCategory')!='commands' or not isinstance(at,list) or len(at)!=2 or any(type(v)is not int for v in at):
                self.bad.add(who);continue
            for axis in (('position','rotation') if channel=='both' else (channel,)):
                self.rows[who,axis].append(event)
        for key,rows in self.rows.items():
            rows.sort(key=lambda e:tuple(e['wireOrder']))
            ats=[tuple(e['wireOrder']) for e in rows];self.orders[key]=ats
            if len(set(ats))!=len(ats):self.bad.add(key[0])

    def before(self,who,axis,at):
        key=(who,axis);idx=bisect_left(self.orders.get(key,[]),at)
        return self.rows[key][idx-1] if idx else None

    def contact(self,start,damage):
        actor=start['playerObjectId'];target=damage['targetObjectId'];at=tuple(damage['wireOrder'])
        if actor in self.bad or target in self.bad:return None,'unordered-or-incomplete-pose-stream'
        positions=[];sources=[]
        for who in (actor,target):
            event=self.before(who,'position',at)
            if not event or event['event']!='CmdStopMove':return None,'moving-or-unanchored-'+('caster' if who==actor else 'target')
            # Region is evaluated before its damage command. A stop first
            # emitted in that frame is not a proved pre-evaluation anchor.
            if event['tick']>=damage['tick']:return None,'pose-anchor-in-damage-frame'
            p=event.get('positionVector2')
            if not isinstance(p,list) or len(p)!=2 or any(type(v)not in (int,float) or not math.isfinite(v) or f32(v)!=v for v in p):
                return None,'non-exact-stop-position'
            positions.append(p);sources.append(event['wireOrder'])
        direction,why=self.direction(start,damage)
        if why:return None,why
        return dict(casterPosition=positions[0],targetPosition=positions[1],
                    positionSourceOrders=sources,**direction),None

    def direction(self,start,damage):
        actor=start['playerObjectId'];at=tuple(damage['wireOrder'])
        if actor in self.bad:return None,'unordered-or-incomplete-pose-stream'
        if self.rotation_baselines is not None:return self.counted_direction(start,damage)
        lock=self.before(actor,'rotation',at)
        if not lock or lock['event']!='CmdLockRotation' or lock.get('isLock')is not True:return None,'Q-direction-not-locked'
        look=self.before(actor,'rotation',tuple(lock['wireOrder']))
        if not look or look['event']!='CmdLookAtInstance' or tuple(look['wireOrder'])<=tuple(start['wireOrder']):
            return None,'missing-Q-instant-look-at'
        try:vectors=native_yaw_forward(look.get('lookAtToY'))
        except (ValueError,OSError):return None,'unavailable-native-yaw'
        return dict(forwardByCpuBranch=vectors,lookAtOrder=look['wireOrder'],lockOrder=lock['wireOrder']),None

    def counted_direction(self,start,damage):
        actor=start['playerObjectId'];at=tuple(damage['wireOrder']);start_at=tuple(start['wireOrder'])
        baselines=[b for b in self.rotation_baselines.get(str(actor),[]) if (b['recordId'],2**31)<start_at]
        if not baselines:return None,'missing-rotation-counter-baseline'
        baseline=max(baselines,key=lambda b:b['recordId']);count=baseline.get('lockRotation');strategy=baseline.get('rotateStrategyType')
        if type(count)is not int or type(strategy)is not int:return None,'malformed-rotation-counter-baseline'
        look=None;locks=[];all_locks=[];last_lock=None;pending_unlocked_look=False
        ordinary={'CmdStopMove','CmdMoveToDestination','CmdMoveToDestinationAvoidance'}
        resets={'CmdInSightRange','CmdResurrection'}
        for event in self.rows.get((actor,'rotation'),[]):
            where=tuple(event['wireOrder']);name=event['event']
            if where<=(baseline['recordId'],2**31):continue
            if where>=at:break
            if name in resets:return None,'rotation-baseline-invalidated-by-lifecycle'
            if name=='CmdLockRotation':
                value=event.get('isLock')
                if type(value)is not bool:return None,'malformed-lock-rotation-delta'
                count+=1 if value else -1
                if not -(2**31)<=count<2**31:return None,'rotation-counter-overflow'
                delta=dict(wireOrder=event['wireOrder'],delta=1 if value else -1,count=count)
                all_locks.append(delta)
                if where>start_at:locks.append(delta)
                if value:
                    last_lock=event
                    if pending_unlocked_look and look is not None and look['tick']!=event['tick']:look=None
                    pending_unlocked_look=False
                if count<=0:look=None
            elif name=='CmdLookAtInstance':
                # The live orientation has no cast boundary. A retained look
                # before StartSkill is usable while the native lock preserves
                # it. A separate rotate strategy can change even a locked look;
                # stopping that strategy later does not restore this angle.
                look=event if strategy==0 else None
                pending_unlocked_look=count<=0
            elif name=='CmdStopRotate':
                strategy=0
            elif name in ordinary:
                if count<=0:look=None
            else:
                # All other rotation commands or unreviewed movement remain
                # barriers. A later explicit look can re-anchor direction;
                # active rotate strategies require an explicit StopRotate.
                look=None
                if name.startswith('CmdRotate'):strategy=-1
        if count<=0:return None,'Q-rotation-counter-not-positive'
        if strategy!=0:return None,'active-rotation-strategy-not-excluded'
        if not look:return None,'missing-Q-instant-direction-anchor'
        try:vectors=native_yaw_forward(look.get('lookAtToY'))
        except (ValueError,OSError):return None,'unavailable-native-yaw'
        return dict(forwardByCpuBranch=vectors,lookAtOrder=look['wireOrder'],lockOrder=last_lock['wireOrder'] if last_lock else None,
            rotationCounterProof=dict(snapshotRecordId=baseline['recordId'],snapshotPayloadSha256=baseline['payloadSha256'],
                initialCount=baseline['lockRotation'],finalCount=count,lockDeltasAfterStart=locks,
                lockDeltasSinceBaseline=all_locks,lookPrecedesCast=tuple(look['wireOrder'])<start_at,
                rotateStrategyAbsent=True,nativeRulePath='schema/native-rotation-lock-counter-v1.json')),None

def projected_region(caster,target,forward,skill_range,end_point_range):
    dx=f32(target[0]-caster[0]);dz=f32(target[1]-caster[1]);fx,fy,fz=forward
    dot=f32(f32(f32(dx*fx)+f32(fy*0.0))+f32(dz*fz))
    norm=f32(f32(f32(fy*fy)+f32(fx*fx))+f32(fz*fz))
    scale=f32(dot/norm);px=f32(scale*fx);pz=f32(scale*fz)
    squared=f32(f32(px*px)+f32(pz*pz))
    threshold=f32(f32(skill_range)-f32(end_point_range));threshold=f32(threshold*threshold)
    return ('tip' if squared>threshold else 'non-tip'),squared,threshold


def projected_region_bounds(caster,target,forward,skill_range,end_point_range):
    """Native projection interval; crossing the threshold stays unknown."""
    def multiplied(interval,value):return sorted(f32(x*value) for x in interval)
    delta=[[f32(target[k][0]-caster[k][1]),f32(target[k][1]-caster[k][0])] for k in ('x','z')]
    fx,fy,fz=forward
    x=multiplied(delta[0],fx);z=multiplied(delta[1],fz)
    dot=[f32(f32(x[i]+f32(fy*0.0))+z[i]) for i in (0,1)]
    norm=f32(f32(f32(fy*fy)+f32(fx*fx))+f32(fz*fz))
    if not math.isfinite(norm) or norm<=0:raise ValueError('invalid native forward norm')
    scale=[f32(v/norm) for v in dot]
    def squared(v):
        px,pz=f32(v*fx),f32(v*fz)
        return f32(f32(px*px)+f32(pz*pz))
    values=[squared(v) for v in scale]
    bounds=[0.0 if scale[0]<=0<=scale[1] else min(values),max(values)]
    threshold=f32(f32(skill_range)-f32(end_point_range));threshold=f32(threshold*threshold)
    region='tip' if bounds[0]>threshold else 'non-tip' if bounds[1]<=threshold else None
    return region,bounds,threshold
