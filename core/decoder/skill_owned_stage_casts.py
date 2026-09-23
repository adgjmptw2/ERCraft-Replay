"""Count explicit owned child starts separately from parent and action counts."""
from collections import Counter,defaultdict
try:
    from .skill_summon_ownership import live_summon_owner_resolver
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order
except ImportError:
    from skill_summon_ownership import live_summon_owner_resolver
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order


def owned_stage_cast_evidence(catalog, skill_ids, requested_groups, players,
                              nonplayer_starts, finishes, scripts, summons,
                              terminals, intervals, skill_rows, state_groups):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    characters={p['objectId']:p['characterCode'] for p in players}
    resolve=live_summon_owner_resolver(summons,terminals,set(characters))
    by_wire=defaultdict(set)
    for group in requested_groups:
        wire=skill_ids.get(catalog['skillGroups'].get(str(group),{}).get('skillId'))
        if type(wire) is int:by_wire[wire].add(group)
    code_groups={r['code']:r['group'] for r in skill_rows}
    state_wires={r['group']:skill_ids.get(r.get('skillId')) for r in state_groups}
    streams=defaultdict(list);diagnostics=Counter()
    events=[('CmdStartSkill',s) for s in nonplayer_starts or []]
    events += [('CmdStartStateSkill',s) for s in scripts or [] if s.get('event')=='CmdStartStateSkill']
    for kind,event in events:
        source=event.get('sourceObjectId');wire=event.get('skillIdCode')
        if source in characters or wire not in by_wire:continue
        owner,path,reason=resolve(source,event['tick'])
        if reason or not path:
            diagnostics[reason or 'not-a-summon-source']+=1;continue
        groups={g for g in by_wire[wire] if catalog['skillGroups'][str(g)]['characterCode']==characters[owner]}
        if kind=='CmdStartSkill':groups={g for g in groups if code_groups.get(event.get('skillCode'))==g}
        elif state_wires.get(event.get('stateGroup'))!=wire:
            diagnostics['state-script-does-not-match-explicit-state-group-skill-id']+=1;continue
        if len(groups)!=1:
            diagnostics['ambiguous-or-foreign-stage-identity']+=1;continue
        group=next(iter(groups))
        # State skillCode can name the triggering ability. The state-group
        # handler and actual wire SkillId, not that origin code, identify it.
        channel=(kind,event.get('stateGroup'),event.get('casterObjectId')) if kind=='CmdStartStateSkill' else (kind,None,None)
        streams[owner,group,source,wire,channel].append({**event,'playerObjectId':source,'skillGroup':group})
    ordinary_ends=defaultdict(list);state_ends=defaultdict(list)
    for event in finishes:ordinary_ends[event['playerObjectId'],event['skillIdCode']].append(event)
    for event in scripts or []:
        if event.get('event')=='CmdFinishStateSkill':
            state_ends[event.get('sourceObjectId'),event.get('skillIdCode'),event.get('stateGroup'),event.get('casterObjectId')].append(event)
    output={}
    for (owner,group,source,wire,channel),starts in streams.items():
        row=output.setdefault(owner,{}).setdefault(group,dict(
            observedOwnedChildCastCount=0,observedCombatChildCastCount=0,
            completeChildLifetimeCount=0,unresolvedChildStartCount=0,
            childStartEventCounts=Counter(),childLifetimeReasons=Counter(),
            childCastsCountedAsIndependentUses=True,parentUsesInferred=False,
            actionCountUsedAsAttemptCount=False,hitRateInferred=False))
        row['observedOwnedChildCastCount']+=len(starts)
        row['observedCombatChildCastCount']+=sum(any(l<=s['tick']<r for l,r in intervals.get(owner,[])) for s in starts)
        row['childStartEventCounts'][channel[0]]+=len(starts)
        if channel[0]=='CmdStartSkill':
            ends=ordinary_ends[source,wire]
        else:
            ends=[{**s,'playerObjectId':source} for s in state_ends[source,wire,channel[1],channel[2]]]
        if all(command_order(e) is not None for e in [*starts,*ends]):
            records,reason=ordered_cast_records(starts,ends,source,allow_same_tick_finishes=True)
            lives=[]
            if records is not None:
                for record in records:
                    if record['complete']:lives.append((record['start'],record['finish']['tick']))
                    else:
                        row['unresolvedChildStartCount']+=1
                        row['childLifetimeReasons'][record['reason']]+=1
        else:
            lives,reason=exact_cast_lifetimes(starts,ends,source)
        if reason:
            row['unresolvedChildStartCount']+=len(starts);row['childLifetimeReasons'][reason]+=len(starts)
            continue
        for start,end in lives:
            final_owner,_,reason=resolve(source,end)
            if reason or final_owner!=owner:
                row['unresolvedChildStartCount']+=1;row['childLifetimeReasons']['child-owner-unavailable-at-finish']+=1
            else:row['completeChildLifetimeCount']+=1
    for groups in output.values():
        for row in groups.values():
            row['childStartEventCounts']=dict(row['childStartEventCounts'])
            row['childLifetimeReasons']=dict(row['childLifetimeReasons'])
    return output,dict(diagnostics)
