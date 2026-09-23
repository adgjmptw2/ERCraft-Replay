"""Bound R launch time using its ordered, capped Start-state update."""
from copy import deepcopy
try:
    from .skill_server_frame_time import _f32,FRAME_STEP
    from .skill_wire_position_precision import position_cell
except ImportError:
    from skill_server_frame_time import _f32,FRAME_STEP
    from skill_wire_position_precision import position_cell

RULE='schema/echion-start-capped-update-clock-v2.json'
START_INCREMENTS={1044520:_f32(_f32(_f32(.07)+_f32(.16))+_f32(.45)),
                  1044530:_f32(.45),1044540:_f32(_f32(_f32(.07)+_f32(.16))+_f32(.45))}

def start_update_candidates(inputs):
    if inputs.get('congestionContractVerified') is not True:return []
    result=[]
    for action in inputs['actions']:
        e=action['event']
        if action['status']!='bounded' or not 0<=action['lower']<=action['upper']<=432000:continue
        starts=[s for s in inputs['starts'] if s['objectId']==e['objectId']
            and s['skillGroup'] in (1044520,1044530,1044540) and s['skillIdCode']==e['skillId']
            and s['wireOrder'][0]==e['wireOrder'][0] and s['wireOrder']<e['wireOrder']]
        if len(starts)!=1:continue
        start=starts[0]
        events=[v for v in inputs['selfStateEvents'] if start['wireOrder']<v['wireOrder']<e['wireOrder']
            and (v['name']=='barrier' or v.get('value',{}).get('objectId')==e['objectId'] and v.get('stateGroup')==1044500)]
        if len(events)!=1 or events[0]['name']!='CmdUpdateState':continue
        update=events[0];v=update['value']
        if v.get('stackCount')!=1 or v.get('casterId') not in (0,e['objectId']):continue
        if any(x.get('wireCategory')!='commands' for x in (start,update,e)):continue
        try:
            created=position_cell(v['createdTime']['internalValue'])
            duration=position_cell(v['duration']['internalValue'])
        except (KeyError,TypeError,ValueError,OverflowError):continue
        if not 0<=created[0]<=created[1]<3600 or not 0<duration[0]<=duration[1]<3600:continue
        # Native capped remaining time <=9, plus a conservative float32 error
        # envelope. This is a lower bound, never an estimated emission time.
        threshold=created[0]+duration[0]-9.0625
        lo,hi=0,432001
        while lo<hi:
            mid=(lo+hi)//2
            if _f32(_f32(mid)*FRAME_STEP)<threshold:lo=mid+1
            else:hi=mid
        upper=None;prior=None
        prior_events=[v for v in inputs['selfStateEvents'] if v['wireOrder']<start['wireOrder']
            and (v['name']=='barrier' or v.get('value',{}).get('objectId')==e['objectId'] and v.get('stateGroup')==1044500)]
        if prior_events:
            prior=prior_events[-1];pv=prior.get('value',{})
            compatible=(prior['name'] in ('CmdAddState','CmdAddStateExtended') and pv.get('stackCount',1)==1
                or prior['name']=='CmdUpdateState' and pv.get('stackCount')==1
                and pv.get('createdTime')==v.get('createdTime'))
            try:
                raw=pv['duration'];previous=position_cell(raw['internalValue'] if isinstance(raw,dict) else raw)
            except (KeyError,TypeError,ValueError,OverflowError):compatible=False
            if compatible and pv.get('casterId') in (0,e['objectId']) and 0<previous[0]<=previous[1]<3600:
                uncapped_low=_f32(previous[0]+START_INCREMENTS[start['skillGroup']])
                if duration[1]<uncapped_low-.0625:
                    # A strictly shortened positive Start increment proves the
                    # cap branch. castingTime1=0 for all three pinned groups.
                    threshold_hi=created[1]+duration[1]-9+.0625
                    left,right=0,432001
                    while left<right:
                        mid=(left+right)//2
                        if _f32(_f32(mid)*FRAME_STEP)<=threshold_hi:left=mid+1
                        else:right=mid
                    upper=left-1
        lower=max(lo,action['lower']);ceiling=min(upper,action['upper']) if upper is not None else action['upper']
        if lower<=ceiling and (lower>action['lower'] or ceiling<action['upper']):
            result.append(dict(start=start,update=update,action=action,lower=lower,upper=ceiling,
                               prior=prior if upper is not None else None))
    return result

def refine_start_clocks(inputs,routing):
    if routing.get('matchKey')!=inputs['matchKey'] or routing.get('privateRouting') is not True:
        raise ValueError('capped-start clock routing source mismatch')
    masks={(r['packetId'],tuple(r['wireOrder'])):r for r in routing['facts']}
    out=deepcopy(inputs);actions={a['event']['packetId']:a for a in out['actions']};proofs=[]
    for c in start_update_candidates(inputs):
        records=(c['start'],c['update'],c['action']['event']);facts=[]
        for e in records:
            r=masks.get((e['packetId'],tuple(e['wireOrder'])))
            # Input payload hashes cover decoded JSON; routing hashes cover
            # original MemoryPack bytes. Bind by source+packet+wire identity.
            if r is None:break
            facts.append(r)
        if len(facts)!=3:continue
        recipient=facts[0]['targetMask']
        if type(recipient)is not int or not 0<recipient<=0xffffffff or recipient&(recipient-1):continue
        if any(r['targetMask']!=recipient for r in facts):continue
        action=actions[c['action']['event']['packetId']]
        upper=c['upper'];prior_proven=False
        if c['prior'] is not None:
            p=c['prior'];p_route=masks.get((p['packetId'],tuple(p['wireOrder'])))
            prior_proven=(p.get('wireCategory')=='commands' and p_route is not None and p_route['targetMask']==recipient)
            if not prior_proven:upper=action['upper']
        if c['lower']==action['lower'] and upper==action['upper']:continue
        proof=dict(rule=RULE,matchKey=inputs['matchKey'],startPacketId=c['start']['packetId'],
            updatePacketId=c['update']['packetId'],launchPacketId=action['event']['packetId'],
            startWireOrder=c['start']['wireOrder'],updateWireOrder=c['update']['wireOrder'],
            launchWireOrder=action['event']['wireOrder'],previousLower=action['lower'],lower=c['lower'],
            previousUpper=action['upper'],upper=upper,
            sameSingleRecipient=True,remainingTimeCap=9.0,float32ErrorEnvelope=0.0625)
        proof['rawPayloadSha256']=[r['payloadSha256'] for r in facts]
        if prior_proven:proof['shortenedStartIncrementPriorPacketId']=c['prior']['packetId']
        action['lower']=c['lower'];action['upper']=upper;action['cappedStartClockEvidence']=proof;proofs.append(proof)
    out['cappedStartClockEvidence']=proofs
    return out

def retained_start_clock_refinement(source,inputs):
    try:
        from .corpus_command_routing import retained_command_routing
    except ImportError:
        from corpus_command_routing import retained_command_routing
    candidates=start_update_candidates(inputs)
    ids=sorted({e['packetId'] for c in candidates for e in
        (c['start'],c['update'],c['action']['event'])+((c['prior'],) if c['prior'] is not None else ())})
    if not ids:return inputs
    routing=dict(matchKey=inputs['matchKey'],privateRouting=True,facts=[])
    for i in range(0,len(ids),1000):
        part=retained_command_routing(source,ids[i:i+1000])
        if part['matchKey']!=inputs['matchKey']:raise ValueError('original source mismatch')
        routing['facts'].extend(part['facts'])
    return refine_start_clocks(inputs,routing)
