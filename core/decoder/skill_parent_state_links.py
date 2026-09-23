"""Adela's recorded parent casts and summoned state-handler links.

This module exposes attribution evidence, not a hit-rate calculator. A unique
active parent state plus inherited skillCode is a candidate link for a pushed
piece until the producer callback is reviewed. No time window or fabricated
state end is used. Primary links require the actual newly spawned object.
"""
from collections import defaultdict
try:
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_summon_ownership import live_summon_owner_resolver
    from .skill_wire_order import command_order
except ImportError:
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_summon_ownership import live_summon_owner_resolver
    from skill_wire_order import command_order


PRIMARY = {
    1024300: ('AdelaActive2',1083,1024310,'AdelaActive2KnightAttack'),
    1024400: ('AdelaActive3_1',1084,1024420,'AdelaActive3RookAttack'),
}


def parent_state_links(starts,finishes,scripts,summons,terminals,player,player_ids,
                       skill_rows,state_groups,skill_ids,execution_actions=None,match_end=None):
    code_groups={s['code']:s['group'] for s in skill_rows}
    definitions={s['group']:s.get('skillId') for s in state_groups}
    handler_names={g:n for g,n in definitions.items() if isinstance(n,str) and n.startswith('Adela') and 'Attack' in n}
    resolve=live_summon_owner_resolver(summons,terminals,set(player_ids))
    selected=[s for s in starts if s.get('playerObjectId')==player and s.get('skillGroup') in PRIMARY]
    for s in selected:
        name,_,sg,handler=PRIMARY[s['skillGroup']]
        if s.get('skillIdCode')!=skill_ids.get(name) or code_groups.get(s.get('skillCode'))!=s['skillGroup'] or definitions.get(sg)!=handler:
            return dict(status='unresolved-evidence',reason='parent or handler gameDb/wire identity mismatch',parents=[],states=[])
    parents,why=ordered_cast_records(selected,finishes,player,allow_same_tick_finishes=True)
    if why:return dict(status='unresolved-evidence',reason=why,parents=[],states=[])
    if match_end is not None:
        for p in parents:
            if not p['complete'] and command_order(p['start'])<command_order(match_end):
                p.update(finish=match_end,complete=True,closureKind='actual-winner-match-end')
    by_actor=defaultdict(list);issues=[]
    for s in scripts:
        if s.get('stateGroup') not in handler_names:continue
        owner,path,why=resolve(s.get('sourceObjectId'),s['tick'])
        if why:
            # Retain rejected observations without assigning an unknown owner.
            issues.append(dict(event=s,reason=why));continue
        if owner!=player:continue
        if len(path)!=1 or s.get('skillIdCode')!=skill_ids.get(handler_names[s['stateGroup']]):
            issues.append(dict(event=s,reason='state handler or direct summon identity mismatch'));continue
        key=(s['sourceObjectId'],s['stateGroup'],s.get('casterObjectId'))
        by_actor[key].append(s)
    states=[]
    for (actor,sg,caster),events in by_actor.items():
        ss=[dict(s,playerObjectId=actor) for s in events if s['event']=='CmdStartStateSkill']
        ee=[dict(s,playerObjectId=actor) for s in events if s['event']=='CmdFinishStateSkill']
        local,why=ordered_cast_records(ss,ee,actor,allow_same_tick_finishes=True)
        if why:
            # Do not infer ends at later starts. An ambiguous source stream
            # remains visible for attribution audits.
            local=[dict(start=s,finish=None,complete=False,reason=why) for s in ss]
        for r in local:
            states.append(dict(r,stateGroup=sg,handler=handler_names[sg],sourceObjectId=actor,casterObjectId=caster,
                               parentIndices=[],linkStatus='unlinked'))
    def within(s,f,e):
        at,left,right=command_order(e),command_order(s),command_order(f) if f else None
        return at is not None and left is not None and right is not None and s['tick']<=e['tick']<=f['tick'] and left<=at<=right
    spawn_by_actor=defaultdict(list)
    for s in summons:spawn_by_actor[s['objectId']].append(s)
    roots=[]
    for i,p in enumerate(parents):
        s,f=p['start'],p['finish'];_,summon_code,sg,_=PRIMARY[s['skillGroup']]
        matched=[]
        # A cancelled parent may already have emitted its summon. Ownership
        # still exists; denominator eligibility is a separate decision.
        if p['complete'] and f:
            for j,state in enumerate(states):
                e=state['start'];spawns=spawn_by_actor[state['sourceObjectId']]
                if len(spawns)!=1:continue
                spawn=spawns[0]
                if (state['stateGroup']==sg and e.get('skillCode')==s['skillCode'] and
                    spawn['summonCode']==summon_code and spawn['ownerObjectId']==player and
                    spawn.get('identityVerifiedAgainstGameDb') is True and spawn['tick']==e['tick'] and within(s,f,e)):
                    matched.append(j)
        p['primaryStateIndices']=matched
        p['linkStatus']='linked-primary' if len(matched)==1 else 'unknown-primary'
        if len(matched)==1:
            state=states[matched[0]];state['parentIndices'].append(i);state['linkStatus']='primary-new-summon'
            roots.append((i,matched[0]))
    if execution_actions is not None:
        actions_by_state=defaultdict(list)
        for action in execution_actions:
            if action.get('wireStatus')=='decoded-exact-CmdPlayStateSkillAction':
                actions_by_state[(action.get('sourceObjectId'),action.get('stateGroup'),
                    action.get('skillIdCode'),action.get('casterObjectId'))].append(action)
        activations_by_piece=defaultdict(list)
        for state in states:
            activations_by_piece[(state['sourceObjectId'],state['stateGroup'],state['casterObjectId'])].append(state)
        for state in states:
            sg=state['stateGroup'];s=state['start'];f=state['finish']
            terminal_numbers={2,3} if sg==1024420 else {1} if sg in {1024220,1024250} else set()
            aa=[a for a in actions_by_state[(state['sourceObjectId'],sg,s['skillIdCode'],state['casterObjectId'])] if
                command_order(a) is not None and command_order(s) is not None and command_order(s)<command_order(a) and
                (f is None or command_order(f) is not None and command_order(a)<command_order(f))]
            next_starts=[r['start'] for r in activations_by_piece[(state['sourceObjectId'],sg,state['casterObjectId'])] if r is not state and
                         command_order(r['start']) is not None and command_order(s) is not None and command_order(r['start'])>command_order(s)]
            if next_starts:
                state['nextRecordedActivation']=min(next_starts,key=command_order)
                aa=[a for a in aa if command_order(a)<command_order(state['nextRecordedActivation'])]
            end=[a for a in aa if a.get('actionNo') in terminal_numbers]
            if len(end)==1:state['executionEnd']=end[0]
            elif not end and next_starts:
                # PlayStateSkill puts the old IEnumerator in removeRoutines
                # before starting its replacement. The runner checks that
                # list before Process; no synthetic CmdFinishStateSkill is made.
                state['executionEnd']=state['nextRecordedActivation']
                state['executionClosure']='same-handler-coroutine-replaced'
            state['executionActions']=aa
    producers={1024220:{1024310},1024250:{1024310},1024420:{1024310},
               1024230:{1024420},1024260:{1024420},1024320:{1024420}}
    producer_states={group:[r for r in states if r['stateGroup'] in groups]
                     for group,groups in producers.items()}
    start_orders={id(r):command_order(r['start']) for r in states}
    for state in sorted(states,key=lambda r:start_orders[id(r)] or (-1,-1,-1)):
        if state['parentIndices']:
            if len(state['parentIndices'])!=1:state['linkStatus']='ambiguous-primary'
            continue
        e=state['start']
        def possible(root):
            if execution_actions is not None:
                if root['stateGroup'] not in producers.get(state['stateGroup'],set()):return False
                if root.get('nextRecordedActivation') is not None and command_order(e)>=command_order(root['nextRecordedActivation']):return False
                if root.get('executionEnd') is not None and command_order(e)>=command_order(root['executionEnd']):return False
                if root['stateGroup']==1024310:
                    # Both Knight damage/push callbacks are synchronous after
                    # their own action1; idle handler time cannot push a piece.
                    return any(a.get('actionNo')==1 and a['tick']==e['tick'] and command_order(a)<command_order(e)
                               for a in root.get('executionActions',[]))
            if root['finish'] is not None:return within(root['start'],root['finish'],e)
            left,at=command_order(root['start']),command_order(e)
            return left is not None and at is not None and left<=at and root['start']['tick']<=e['tick']
        # Followup activations may occur after the initial knight handler has
        # ended (e.g. its pushed rook later touches another piece). Preserve
        # inherited root alternatives through the actual child-state lifetime.
        at=start_orders[id(state)]
        pool=producer_states.get(state['stateGroup'],()) if execution_actions is not None else states
        active_producers=[r for r in pool if r is not state and
                   start_orders[id(r)] is not None and at is not None and
                   start_orders[id(r)]<at and possible(r)]
        preceding=[r for r in active_producers if r['parentIndices']]
        candidates=sorted({i for r in preceding for i in r['parentIndices']
                           if e.get('skillCode')==r['start'].get('skillCode')})
        if execution_actions is not None:
            reactivated=any(r['stateGroup']==state['stateGroup'] and r['sourceObjectId']==state['sourceObjectId'] and
                            r.get('nextRecordedActivation') is e for r in states)
            retained_context=any(r.get('nextRecordedActivation') is e and
                r['start'].get('skillCode')==e.get('skillCode') for r in states)
            if reactivated and retained_context and state['stateGroup'] in {1024230,1024260,1024320}:
                # AddState also retains SkillUseInfo when refreshing a piece
                # hit by the rook. Match its live producer, not its old code.
                candidates=sorted({i for r in preceding for i in r['parentIndices']})
                state['retainedContextRefresh']=True
                if any(not r['parentIndices'] for r in active_producers):
                    state['reactivationProducerUnconfirmed']=True
            if reactivated and state['stateGroup']==1024420:
                # Knight's synchronous rook AddState refreshes extraData but
                # retains the old SkillUseInfo. Its skillCode is not the new
                # causal owner. An earlier execution cannot retrigger itself.
                knight_actions=[(command_order(a),r,a) for r in preceding if r['stateGroup']==1024310
                    for a in r.get('executionActions',[]) if a.get('actionNo')==1 and a['tick']==e['tick']
                    and command_order(a) is not None and command_order(a)<command_order(e)]
                if state['stateGroup']==1024420 and knight_actions:
                    latest=max(a[0] for a in knight_actions)
                    triggers=[(r,a) for order,r,a in knight_actions if order==latest]
                    candidates=sorted({i for r,a in triggers for i in r['parentIndices']})
                    if len(triggers)==1 and len(candidates)==1:
                        state['reactivationProducer']=dict(kind='synchronous-knight-rook-refresh',
                            action=triggers[0][1],originalSkillCodeRetained=True)
                    else:state['reactivationProducerUnconfirmed']=True
                else:
                    state['reactivationProducerUnconfirmed']=True
                if state.get('reactivationProducerUnconfirmed'):
                    candidates=sorted(set(candidates)|{i for r in states
                        if r.get('nextRecordedActivation') is e for i in r['parentIndices']})
        state['parentIndices']=candidates
        state['linkStatus']='unique-active-root-candidate' if len(candidates)==1 else 'ambiguous-active-roots' if candidates else 'unlinked'
        if len(candidates)==1 and any(candidates[0] in r['parentIndices'] and not r['complete'] for r in preceding):
            state['linkStatus']='open-root-candidate'
    return dict(status='attribution-evidence-only',parents=parents,states=states,issues=issues,
                candidateFollowupsAreVerified=False,hitRateCalculable=False,fixedWindowUsed=False,inventedStateEnds=False)
