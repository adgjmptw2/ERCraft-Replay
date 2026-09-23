"""State presence at an actual command boundary; no duration-based expiry."""
try:
    from .skill_wire_order import command_order
except ImportError:
    from skill_wire_order import command_order


def ordered_state_presence(scripts, *, target, caster, group_skill_ids, before):
    def unknown(reason):return dict(status='unknown',reason=reason,presentGroups=None)
    limit=command_order(before)
    if scripts is None or limit is None:return unknown('missing-state-stream-or-anchor-order')
    relevant=[s for s in scripts if s.get('sourceObjectId')==target and s.get('casterObjectId')==caster
              and s.get('stateGroup') in group_skill_ids and s['tick']<=before['tick']]
    for s in relevant:
        if command_order(s) is None:return unknown('missing-state-command-order')
        if s.get('skillIdCode')!=group_skill_ids[s['stateGroup']]:return unknown('state-wire-identity-mismatch')
    events=[s for s in relevant if command_order(s)<limit]
    if len({command_order(s) for s in events})!=len(events):return unknown('duplicate-state-command-identity')
    active=set();last_tick=None
    for s in sorted(events,key=command_order):
        if last_tick is not None and s['tick']<last_tick:return unknown('state-tick-order-mismatch')
        last_tick=s['tick'];group=s['stateGroup']
        if s['event']=='CmdStartStateSkill':
            if group in active:return unknown('overlapping-state-starts')
            active.add(group)
        elif s['event']=='CmdFinishStateSkill':
            if s.get('reason')==15 or group not in active:return unknown('missing-start-or-replay-end')
            active.remove(group)
        else:return unknown('unexpected-state-event')
    return dict(status='known',reason=None,presentGroups=sorted(active))


def bracketed_raw_state_presence(states, *, target, state_code, state_group, group_codes, before, allow_updates=False):
    """Positive evidence only: an actual add and later removal enclose the hit.

    A script-less state cannot be reconstructed from StartStateSkill. Require
    its raw group stream, retain caster identity, and never expire by duration.
    Removal on the hit frame is ambiguous relative to post-damage callbacks.
    """
    unknown=lambda reason: dict(status='unknown',reason=reason)
    limit=command_order(before)
    if states is None or limit is None or state_code not in group_codes:
        return unknown('missing-raw-state-stream-or-schema')
    rows=[s for s in states if s.get('targetObjectId')==target and
          (s.get('stateCode') in group_codes or s.get('stateGroup')==state_group)]
    candidates=[]
    for start in rows:
        if start.get('event')!='add' or start.get('stateCode')!=state_code:continue
        order=command_order(start)
        if order is None or order>=limit or start['tick']>before['tick']:continue
        caster=start.get('casterObjectId')
        if type(caster) is not int:continue
        prior=[s for s in rows if s is not start and s.get('casterObjectId') in (None,caster) and
               (s['tick']<start['tick'] or s['tick']==start['tick'] and
                command_order(s) is not None and command_order(s)<order)]
        if prior:
            latest=max(s['tick'] for s in prior)
            previous=[s for s in prior if s['tick']==latest]
            if any(s.get('event')!='remove' for s in previous):continue
        following=[s for s in rows if s is not start and
                   (s['tick']>start['tick'] or s['tick']==start['tick'] and
                    (command_order(s) is None or command_order(s)>=order))]
        ends=[s for s in following if s.get('event')=='remove' and
              s.get('casterObjectId')==caster and s['tick']>before['tick']]
        if not ends:continue
        end=min(ends,key=lambda s:s['tick'])
        # Other casters are separate group instances. Missing caster or any
        # replacement/update/removal in this instance prevents a closed proof.
        inside=[s for s in following if s['tick']<=end['tick'] and
                s.get('casterObjectId') in (None,caster)]
        boundaries=[s for s in inside if not (allow_updates and s.get('event')=='CmdUpdateState'
                    and s.get('casterObjectId')==caster and s.get('stateGroup')==state_group)]
        if len(boundaries)!=1 or boundaries[0] is not end:continue
        candidates.append(dict(stateCode=state_code,stateGroup=state_group,
                               casterObjectId=caster,startTick=start['tick'],
                               startCommandOrder=start['wireOrder'],endTick=end['tick']))
    if len(candidates)!=1:return unknown('no-unique-closed-state-bracket')
    return dict(status='present',**candidates[0])
