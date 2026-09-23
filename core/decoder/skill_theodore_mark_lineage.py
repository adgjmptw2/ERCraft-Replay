"""Explicit E projectile/mark/consumption graph; no nearest-cast assignment."""
from collections import defaultdict
try:
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order
except ImportError:
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order


def theodore_mark_lineage(starts,finishes,spawns,collisions,movement,scripts,states,player,skill_ids,fetter_codes,*,actions=None,spread_delay_frames=None):
    def fail(why):return dict(status='unresolved-evidence',reason=why)
    selected=[s for s in starts if s.get('playerObjectId')==player and s.get('skillGroup')==1062400]
    if any(s.get('skillIdCode')!=skill_ids.get('TheodoreActive3') for s in selected):return fail('wrong-E-wire-identity')
    parents,why=ordered_cast_records(selected,finishes,player,allow_same_tick_finishes=True)
    if why:return fail(why)
    own={}
    for s in spawns:
        if s.get('ownerObjectId')!=player or s.get('projectileCode') not in (106241,106242):continue
        oid=s['projectileObjectId']
        if oid in own:return fail('reused-projectile-object-identity')
        own[oid]=s
    contacts=defaultdict(list)
    for c in collisions:
        if c['projectileObjectId'] in own:contacts[c['projectileObjectId']].append(c)
    root_parents={}
    for oid,s in own.items():
        if s['projectileCode']!=106241:continue
        at=command_order(s)
        root_parents[oid]=[i for i,p in enumerate(parents) if at is not None and
            command_order(p['start'])<=at and (p['finish'] is None or at<=command_order(p['finish']))]
    links={};issues=[]
    for oid,s in own.items():
        if s['projectileCode']==106241:
            links[oid]=dict(projectile=s,parentIndices=root_parents[oid],kind='initial');continue
        mm=[m for m in movement or [] if m.get('projectileObjectId')==oid]
        if len(mm)!=1 or mm[0].get('wireStatus')!='decoded-exact-projectile-movement' or mm[0].get('ownerObjectId')!=player:
            links[oid]=dict(projectile=s,parentIndices=[],kind='spread',reason='missing-exact-child-pivot');continue
        pivot=mm[0].get('spawnPositionObjectId')
        # Include EVERY preceding compatible root. An expired/consumed mark
        # must not hide an older delayed callback, and latest-cast is forbidden.
        roots=[rid for rid in root_parents if any(c['targetObjectId']==pivot and c['tick']<s['tick'] for c in contacts[rid])]
        if spread_delay_frames is not None:
            # This is the statically recovered server waitSeq, not a fitted
            # lookback window. The caller must validate the pinned schema.
            roots=[rid for rid in roots if any(c['targetObjectId']==pivot and c['tick']+spread_delay_frames==s['tick']
                and len([a for a in actions or [] if a.get('sourceObjectId')==player and a.get('skillIdCode')==skill_ids.get('TheodoreActive3')
                         and a.get('actionNo')==52 and a['tick']==c['tick'] and len(a.get('targets',[]))==1
                         and a['targets'][0].get('targetObjectId')==pivot])==1 for c in contacts[rid])]
        indices=sorted({i for rid in roots for i in root_parents[rid]})
        links[oid]=dict(projectile=s,parentIndices=indices,kind='spread',pivotObjectId=pivot,
                       movementTargetObjectId=mm[0].get('movementTargetObjectId'),candidateRootObjectIds=roots)
    mark_events=defaultdict(list)
    for s in scripts or []:
        if s.get('casterObjectId')==player and s.get('stateGroup')==1062400:
            if s.get('skillIdCode')!=skill_ids.get('TheodoreActive3MarkState'):return fail('wrong-mark-wire-identity')
            mark_events[s['sourceObjectId']].append(s)
    marks=[]
    for target,events in mark_events.items():
        ss=[dict(s,playerObjectId=target) for s in events if s['event']=='CmdStartStateSkill']
        ee=[dict(s,playerObjectId=target) for s in events if s['event']=='CmdFinishStateSkill']
        lives,why=ordered_cast_records(ss,ee,target,allow_same_tick_finishes=True)
        if why:
            issues.append(dict(targetObjectId=target,reason=why));continue
        for life in lives:
            start,end=life['start'],life['finish']
            origins=[oid for oid in own if any(c['tick']==start['tick'] and c['targetObjectId']==target for c in contacts[oid])
                     and (own[oid]['projectileCode']==106241 or links[oid].get('movementTargetObjectId')==target)]
            parents_for_mark=sorted({i for oid in origins for i in links[oid]['parentIndices']})
            # Mark script removal happens synchronously before its fetter add.
            fetters=[s for s in states if end and s.get('event')=='add' and s.get('casterObjectId')==player
                     and s.get('targetObjectId')==target and s.get('stateCode') in fetter_codes
                     and s['tick']==end['tick'] and command_order(s) is not None and command_order(end)<command_order(s)]
            refreshes=[dict(projectileObjectId=oid,collision=c) for oid in own for c in contacts[oid]
                       if c['targetObjectId']==target and start['tick']<c['tick'] and (not end or c['tick']<=end['tick'])]
            marks.append(dict(start=start,finish=end,complete=life['complete'],targetObjectId=target,possibleRefreshContacts=refreshes,
                              sourceProjectileIds=origins,parentIndices=parents_for_mark,fetterAdditions=fetters,
                              sourceStatus='unique' if len(origins)==1 and len(parents_for_mark)==1 and not refreshes else 'ambiguous-or-unlinked'))
    # A synchronous state consumption must not be credited to two removals
    # from the same target in one frame.
    cc_owners=defaultdict(list)
    for i,m in enumerate(marks):
        for s in m['fetterAdditions']:cc_owners[command_order(s)].append(i)
    for owners in cc_owners.values():
        if len(owners)>1:
            for i in owners:marks[i]['sourceStatus']='ambiguous-or-unlinked'
    return dict(status='lineage-evidence',parents=parents,projectiles=list(links.values()),marks=marks,issues=issues,
                parentAssignmentUsesAllCandidates=True,nearestCastUsed=False,fixedTimeWindowUsed=False,
                missingFetterIsNotYetACastMiss=True,staticSpreadDelayFrames=spread_delay_frames)
