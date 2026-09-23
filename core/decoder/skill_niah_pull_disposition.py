"""Actual W pull-state addition versus observed immune/death disposition."""
try:
    from .skill_wire_order import command_order as order
    from .skill_ordered_state_presence import bracketed_raw_state_presence
except ImportError:
    from skill_wire_order import command_order as order
    from skill_ordered_state_presence import bracketed_raw_state_presence

IMMUNITY_GROUPS={1010510:'CCImmunity',11360:'CCImmunity',1082300:'CCImmunity',
                 1022510:'DisplacementImmunity',1023500:'DisplacementImmunity',1030410:'CCImmunity',
                 1081510:'CCImmunity',1007300:'CCMovementImmunity',1068100:'CCStopImmunity'}

def pull_disposition(damage,finish,states,deaths,groups,state_rows,gaps):
    unknown=lambda why:dict(pullApplication='unknown',pullReason=why)
    defs={g['group']:g for g in groups or []};codes={s['code']:s['group'] for s in state_rows or []}
    pull=defs.get(1081310,{})
    if pull.get('stateType')!='Airborne' or pull.get('canImmuned') is not True or codes.get(1081311)!=1081310:
        return unknown('missing pinned Airborne pull schema')
    if gaps is None or any(g.get('count',0) and ('State' in g.get('packetName','') or g.get('packetName') in {'CmdDead','CmdDyingCondition'}) for g in gaps):
        return unknown('incomplete state or death stream')
    target=damage['targetObjectId'];owner=damage['attackerObjectId'];tick=damage['tick']
    aa=[s for s in states if s['event']=='add' and s.get('stateCode')==1081311 and s['targetObjectId']==target and s.get('casterObjectId')==owner and s['tick']==tick]
    exact=[s for s in aa if order(s) is not None and order(damage)<order(s)<order(finish)]
    if len(exact)==1:return dict(pullApplication='applied',pullStateCode=1081311,pullStateOrder=exact[0]['wireOrder'])
    if aa:return unknown('ambiguous or unordered pull state')
    ended=[d for d in deaths if d['deadObjectId']==target and d['event'] in {'CmdDead','CmdDyingCondition'} and d['tick']==tick
           and order(d) is not None and order(damage)<order(d)<order(finish)]
    if ended:
        return dict(pullApplication='target-entered-dead-or-dying-state',pullStateOrder=None,
                    targetEndEvents=[dict(event=d['event'],wireOrder=d['wireOrder']) for d in ended])
    evidence=[]
    for group,kind in IMMUNITY_GROUPS.items():
        definition=defs.get(group,{})
        if definition.get('stateType')!=kind or definition.get('notCheckCasterId') is not False:continue
        group_codes={c for c,g in codes.items() if g==group}
        for code in {s['stateCode'] for s in states if s['event']=='add' and s['targetObjectId']==target and s.get('stateCode') in group_codes}:
            bracket=bracketed_raw_state_presence(states,target=target,state_code=code,state_group=group,group_codes=group_codes,before=damage,allow_updates=True)
            if bracket['status']=='present':evidence.append({**bracket,'immunityType':kind})
    if evidence:return dict(pullApplication='blocked-by-airborne-immunity',pullImmunityEvidence=evidence)
    return unknown('no pull addition, exact target-end event or closed applicable immunity bracket')
