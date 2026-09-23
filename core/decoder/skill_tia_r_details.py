"""Per-contact color before damage and independently observed R stun addition."""
try:
    from .skill_ordered_state_presence import ordered_state_presence,bracketed_raw_state_presence
    from .skill_wire_order import command_order
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from skill_ordered_state_presence import ordered_state_presence,bracketed_raw_state_presence
    from skill_wire_order import command_order
    from skill_action_stage_evidence import load_exact_skill_ids

COLORS={1048530:('yellow','TiaActive2_TargetYellow'),1048540:('red','TiaActive2_TargetRed'),1048550:('blue','TiaActive2_TargetBlue')}


def tia_r_contact_details(contacts,damages,states,scripts,player,state_groups,gaps,skill_ids=None,state_rows=None):
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    definitions={r['group']:r for r in state_groups or []}
    valid=all(type(ids.get(name)) is int and definitions.get(g,{}).get('skillId')==name
              and definitions[g].get('notCheckCasterId') is False for g,(_,name) in COLORS.items())
    valid=valid and definitions.get(1048700,{}).get('stateType')=='Stun'
    required={'CmdAddState','CmdAddStateExtended','CmdStartStateSkill','CmdFinishStateSkill'}
    valid=valid and gaps is not None and not any(g.get('count',0) and g.get('packetName') in required for g in gaps)
    result=[]
    for tick,target in sorted(contacts):
        row=dict(hitTick=tick,targetObjectId=target,colorPresence='unknown',colors=None,
                 stunApplication='unknown',stunAddedDurationRaw=None,stunDurationUnit='wire units; not actual CC duration')
        ds=[d for d in damages if d.get('attackerObjectId')==player and d.get('targetObjectId')==target and d.get('effectCode')==1048107 and d['tick']==tick]
        if not valid or states is None or len(ds)!=1 or command_order(ds[0]) is None:
            row['reason']='missing-pinned-state-schema-or-unique-damage-order';result.append(row);continue
        d=ds[0]
        presence=ordered_state_presence(scripts,target=target,caster=player,group_skill_ids={g:ids[name] for g,(_,name) in COLORS.items()},before=d)
        if presence['status']=='known':
            row['colors']=[COLORS[g][0] for g in presence['presentGroups']]
            row['colorPresence']='present' if row['colors'] else 'absent'
        else:row['colorReason']=presence['reason']
        adds=[s for s in states if s.get('event')=='add' and s.get('stateCode')==1048701
              and s.get('targetObjectId')==target and s.get('casterObjectId')==player and s['tick']==tick]
        if len(adds)==1 and command_order(adds[0]) is not None and command_order(adds[0])>command_order(d):
            row.update(stunApplication='applied',stunAddedDurationRaw=adds[0].get('duration'),stunCommandOrder=adds[0]['wireOrder'])
        elif not adds:
            row.update(stunApplication='not-observed',stunReason='no matching R stun addition; immunity or refresh not inferred')
            immunity=definitions.get(3016000,{})
            codes={s['code'] for s in state_rows or [] if s.get('group')==3016000}
            schema=(immunity.get('stateType')=='CCImmunity' and immunity.get('skillId')=='None'
                    and immunity.get('stateBehaviourType')=='Common' and immunity.get('notCheckCasterId') is False
                    and definitions[1048700].get('canImmuned') is True and 3016003 in codes)
            raw_gaps=any(g.get('count',0) and ('State' in g.get('packetName','')) for g in gaps)
            if schema and not raw_gaps:
                bracket=bracketed_raw_state_presence(states,target=target,state_code=3016003,
                    state_group=3016000,group_codes=codes,before=d)
                if bracket['status']=='present':
                    row.update(stunApplication='blocked-by-cc-immunity',stunImmunityEvidence=bracket,
                               stunReason='closed two-hand-sword immunity state; static CCImmunity policy blocks Stun')
        else:row['stunReason']='ambiguous-or-unordered-stun-addition'
        result.append(row)
    return result
