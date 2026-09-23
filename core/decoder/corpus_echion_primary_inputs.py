"""Read existing full decodes once for Echion's delayed primary executions."""
import hashlib,json,time
from pathlib import Path
try:
    from .corpus_runtime_source import restore
    from .corpus_state_inventory import RESET_EVENTS,WRAPPER_EVENTS,LIFE_EVENTS,_wrapper_ids,retained_state_inventory
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .delta_payloads import SchemaDecoder
    from .skill_state_clock_constraints import state_clock_constraints,event_clock_bounds
    from .skill_single_recipient_clock import retained_clock_bounds
except ImportError:
    from corpus_runtime_source import restore
    from corpus_state_inventory import RESET_EVENTS,WRAPPER_EVENTS,LIFE_EVENTS,_wrapper_ids,retained_state_inventory
    from skill_partial_cast_lifetimes import ordered_cast_records
    from delta_payloads import SchemaDecoder
    from skill_state_clock_constraints import state_clock_constraints,event_clock_bounds
    from skill_single_recipient_clock import retained_clock_bounds

GROUPS={1044510:615,1044520:616,1044530:617,1044540:618}
# AttackBase.Play selects this dictionary using the LIVE weapon at each
# contact (2a8ea09..2a8ea2c), not the parent CmdStartSkill's group.
PRIMARY_EFFECTS=frozenset((1044510,1044520,1044530,1044540))
SECONDARY_STATE_CODES=frozenset((1044601,1044602,1044603,1044604))


def _launch_life_requests(starts,finishes,actions,actors):
    requests=[]
    for actor in actors:
        # ordered_cast_records filters finishes, but expects caller-selected
        # starts. Other players using the same R enum are independent streams.
        actor_starts=[s for s in starts if s['objectId']==actor]
        parents,reason=ordered_cast_records(actor_starts,finishes,actor,allow_same_tick_finishes=True)
        if reason:continue
        for p in parents:
            for a in actions:
                if (a['objectId']==actor and a['skillId']==p['start']['skillIdCode']
                        and p['start']['wireOrder']<=a['wireOrder']
                        and (p['finish'] is None or a['wireOrder']<=p['finish']['wireOrder'])):
                    requests.append(dict(targetObjectId=actor,wireOrder=a['wireOrder'],tick=a['tick']))
    return requests


def retained_echion_primary_inputs(source,cache,tables):
    started=time.perf_counter()
    groups={x['group']:x for x in tables['CharacterStateGroup']}
    state_codes={x['code']:x['group'] for x in tables['CharacterState']}
    skill_codes={x['code']:x['group'] for x in tables['Skill']}
    names=('CmdAddState','CmdAddStateExtended','CmdUpdateState','CmdRemoveState',
           'CmdResetCreateTimeState','CmdPauseState','CmdDamage','CmdStartSkill','CmdFinishSkill',
           'CmdPlaySkillAction','CmdPlaySkillActionWithTargets')+RESET_EVENTS+WRAPPER_EVENTS+LIFE_EVENTS
    state_events=[];inventory_events=[];starts=[];finishes=[];actions=[];damages=[]
    with source.connect() as db:
        if db.execute("SELECT value FROM metadata WHERE key='sourceSha256'").fetchone()[0]!=cache['matchKey']:
            raise ValueError('Echion cache/source match mismatch')
        query='''SELECT p.id,p.record_id,r.tick,p.ordinal,p.category,p.packet_name,p.decoded_json_zlib
            FROM packets p JOIN records r ON r.id=p.record_id WHERE p.packet_name IN ('''+','.join('?' for _ in names)+') ORDER BY p.id'
        for pid,rid,tick,ordinal,cat,name,blob in db.execute(query,names):
            d=restore(blob)
            identity=dict(packetId=pid,wireOrder=[rid,ordinal],tick=tick,wireCategory=cat,
                          payloadSha256=hashlib.sha256(blob).hexdigest())
            event=dict(**d,**identity)
            if name=='CmdStartSkill':
                if skill_codes.get(d['skillCode']) in GROUPS:
                    starts.append(dict(**event,playerObjectId=d['objectId'],skillIdCode=d['skillId'],skillGroup=skill_codes[d['skillCode']]))
            elif name=='CmdFinishSkill':
                if d['skillId'] in GROUPS.values():
                    finishes.append(dict(**event,playerObjectId=d['objectId'],skillIdCode=d['skillId']))
            elif name in ('CmdPlaySkillAction','CmdPlaySkillActionWithTargets'):
                if d['skillId'] in GROUPS.values() and d['actionNo']==6001:actions.append(event)
            elif name=='CmdDamage':
                if d.get('effectCode') in PRIMARY_EFFECTS:damages.append(event)
            else:
                # The admission inventory consumes these same decoded rows.
                # Preserve original names/category before clock barriers are
                # normalized, avoiding a second SQL/decompression pass.
                inventory_events.append((rid,pid,ordinal,cat,name,d))
                e=dict(**identity,name=name,value=d,stateGroup=d.get('group',state_codes.get(d.get('code'))))
                if cat!='commands':e['name']='barrier'
                elif name in RESET_EVENTS:e.update(name='barrier',objectIds=[d['objectId']] if type(d.get('objectId')) is int else None)
                elif name in WRAPPER_EVENTS:e.update(name='barrier',objectIds=list(_wrapper_ids(d)) or None)
                state_events.append(e)
    raw_starts={tuple(x['wireOrder']):x for x in starts}
    mapping={}
    for s in cache['facts']['starts']:
        if s.get('skillGroup') not in GROUPS:continue
        actual=raw_starts.get(tuple(s.get('wireOrder',[])))
        if actual is None or actual['skillCode']!=s['skillCode'] or actual['skillIdCode']!=s['skillIdCode']:
            raise ValueError('Echion cached start disagrees with retained command')
        old=mapping.setdefault(str(s['playerObjectId']),actual['objectId'])
        if old!=actual['objectId']:raise ValueError('ambiguous Echion source player identity')
    actors=set(mapping.values())
    starts=[x for x in starts if x['objectId'] in actors]
    if any(GROUPS[s['skillGroup']]!=s['skillIdCode'] for s in starts):
        raise ValueError('Echion skill group/enum identity mismatch')
    finishes=[x for x in finishes if x['objectId'] in actors]
    actions=[x for x in actions if x['objectId'] in actors]
    damages=[x for x in damages if x['attackerId'] in actors]
    schema=json.loads((Path(__file__).resolve().parents[1]/'schema/schema.json').read_text())
    decoder=SchemaDecoder({k:v['base'] for k,v in schema['classes'].items()},client_version=cache['clientVersion'])
    teams={}
    for user in source.first_snapshot()['gameSnapshot']['userList']:
        wrapper=user['characterSnapshot'];character=decoder.decode_exact(wrapper['snapshot'],'PlayerCharacterSnapshot')
        teams[str(wrapper['objectId'])]=character['teamNumber']
    constraints=state_clock_constraints(state_events,groups,state_codes)
    secondary_group=groups.get(1044600,{})
    secondary_contract=(secondary_group.get('stateType')=='Airborne'
        and secondary_group.get('effectType')=='Debuff'
        and secondary_group.get('notCheckCasterId') is False
        and all(state_codes.get(code)==1044600 for code in SECONDARY_STATE_CODES))
    secondary_states=[dict(**e['value'],packetId=e['packetId'],wireOrder=e['wireOrder'],
                           tick=e['tick'],wireCategory=e['wireCategory'])
        for e in state_events if e['name'] in ('CmdAddState','CmdAddStateExtended')
        and e['value'].get('code') in SECONDARY_STATE_CODES
        and e['value'].get('casterId') in actors and e['wireCategory']=='commands']
    congestion=groups.get(1044500,{})
    congestion_codes=[x for x in tables['CharacterState'] if x['group']==1044500]
    congestion_contract=(congestion.get('stateBehaviourType')=='Common'
        and congestion.get('effectType')=='Buff' and congestion.get('notCheckCasterId') is False
        and congestion_codes and all(x['duration']==9 for x in congestion_codes))
    self_events=[]
    for e in state_events:
        if e['name']=='barrier':
            affected=e.get('objectIds')
            if affected is None or actors.intersection(affected):self_events.append({**e,'value':{}})
        elif e['value'].get('objectId') in actors:self_events.append(e)
    life_requests=_launch_life_requests(starts,finishes,actions,actors)
    life_inventory=retained_state_inventory(source,life_requests,tables['CharacterState'],tables['CharacterStateGroup'],
                                          client_version=cache['clientVersion'],
                                          decoded_state_events=inventory_events) if life_requests else []
    life_keys=('targetObjectId','wireOrder','status','baselineRecord','baselinePayloadSha256',
               'baselineIsAlive','baselineIsDyingCondition','lifeEventsSinceBaseline','issues')
    clocks=retained_clock_bounds(source,constraints,secondary_states+actions+damages,cache['matchKey'])
    ns,na=len(secondary_states),len(actions)
    result=dict(format='er-echion-primary-inputs.v1',matchKey=cache['matchKey'],parserSha256=source.parser_sha256,
                players=mapping,teams=teams,starts=starts,finishes=finishes,
                launchLifeEvidence=[{k:r.get(k) for k in life_keys} for r in life_inventory],
                congestionContractVerified=bool(congestion_contract),
                secondaryStateContractVerified=bool(secondary_contract),
                secondaryStates=clocks[:ns],
                selfStateEvents=self_events,
                laterFrameWitnesses=constraints['lower'],
                # Keep the full upper frontier too. Retaining only witnesses
                # selected for R actions forces later state queries to reread
                # the corpus and otherwise gives unnecessarily loose bounds.
                upperFrameWitnesses=constraints['upper'],
                actions=clocks[ns:ns+na],damages=clocks[ns+na:],
                lowerConstraintCount=len(constraints['lower']),upperConstraintCount=len(constraints['upper']),
                sourceClockLowerAtEnd=max((x['lower'] for x in constraints['lower']),default=None),
                seconds=round(time.perf_counter()-started,3),fallbackUsed=False)
    try:
        from .skill_echion_start_clock import retained_start_clock_refinement
    except ImportError:
        from skill_echion_start_clock import retained_start_clock_refinement
    return retained_start_clock_refinement(source,result)
