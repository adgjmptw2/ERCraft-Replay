"""Theodore E root/spread marks and their actual later fetter applications."""
from collections import Counter,defaultdict
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids,ORDINARY_ACTIONS
    from .skill_wire_order import command_order
    from .skill_theodore_mark_lineage import theodore_mark_lineage
    from .skill_projectile_active_end import projectile_active_end_records
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_action_stage_evidence import load_exact_skill_ids,ORDINARY_ACTIONS
    from skill_wire_order import command_order
    from skill_theodore_mark_lineage import theodore_mark_lineage
    from skill_projectile_active_end import projectile_active_end_records

# TheodoreSkillActive3Data.SpreadDelayTime float32(0.15), divided by the
# server's float32(1/60), rounded up in WaitForFrameUpdate.Seconds: exactly 9.
# This controls callback eligibility, never damage-nearness attribution.
SPREAD_WAIT_FRAMES=9


def theodore_fetter_metric(spec,starts,finishes,spawns,collisions,terminals,states,scripts,movement,
                          actions,player,teams,intervals,catalog,skill_rows,state_rows,state_groups,gaps,skill_ids=None):
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1062400]
    combat=lambda s:any(l<=s['tick']<r for l,r in intervals)
    diag=dict(observedCastCount=len(selected),observedCombatCastCount=sum(map(combat,selected)))
    def fail(reason):return {**_unavailable(spec,reason),**diag}
    if (spec['characterCode'],spec['skillGroup'],spec['unit'])!=(62,1062400,'skill-cast') or spec['mode'] not in {'any','fetter'}:
        return fail('unsupported Theodore mark metric')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    codes={r['code'] for r in skill_rows if r.get('group')==1062400}
    fetters={r['code'] for r in state_rows if r.get('group')==1062410}
    groups={r['group']:r for r in state_groups}
    if (not codes or not fetters or ids.get('TheodoreActive3')!=925 or ids.get('TheodoreActive3MarkState')!=927
        or catalog['skillGroups'].get('1062400',{}).get('skillId')!='TheodoreActive3'
        or groups.get(1062400,{}).get('skillId')!='TheodoreActive3MarkState'
        or groups.get(1062410,{}).get('stateType')!='Fetter'
        or groups.get(1062410,{}).get('startEffectAndSound')!=1062405
        or any(not catalog.get('projectileDefinitions',{}).get(str(code)) for code in (106241,106242))):
        return fail('pinned Theodore state/projectile schema mismatch')
    if any(s.get('skillCode') not in codes for s in selected):return fail('wrong E skill code')
    if any(x is None for x in (scripts,movement,actions,gaps)):return fail('missing complete mark or projectile stream')
    required={'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdSpawnBatch','CmdDestroy','CmdDestroyDelayStart','CmdProjectileCollision',
              'CmdAddState','CmdAddStateExtended','CmdStartStateSkill','CmdFinishStateSkill',
              'CmdPlaySkillAction','CmdPlaySkillActionWithTargets'}
    if any(g.get('count',0) and g.get('packetName') in required for g in gaps):return fail('Theodore required command decode gap')
    cc=[s for s in states if s.get('event')=='add' and s.get('casterObjectId')==player and s.get('stateCode') in fetters]
    if any(command_order(s) is None for s in cc):return fail('missing exact fetter addition order')
    own_actions=[a for a in actions if a.get('sourceObjectId')==player and a.get('skillIdCode')==925]
    if any(command_order(a) is None or a.get('wireStatus') not in ORDINARY_ACTIONS for a in own_actions):
        return fail('missing exact E callback action')
    graph=theodore_mark_lineage(starts,finishes,spawns,collisions,movement,scripts,states,player,ids,fetters,
                                actions=own_actions,spread_delay_frames=SPREAD_WAIT_FRAMES)
    if graph['status']!='lineage-evidence':return fail(graph['reason'])
    if graph['issues']:return fail('unpaired mark state stream')
    by_parent=defaultdict(list);bad={};marks=graph['marks'];contacts=defaultdict(list)
    for c in collisions:contacts[c['projectileObjectId']].append(c)
    ends=projectile_active_end_records(terminals)
    for p in graph['projectiles']:
        indices=p['parentIndices']
        if not indices:return fail('owned projectile lacks an explicit root/callback parent')
        if len(indices)!=1:
            for i in indices:bad[i]='ambiguous-spread-parent'
        else:by_parent[indices[0]].append(p)
    # Seeing subsequent recorded gameplay proves the server passed the exact
    # scheduled frame; replay-end alone is not such evidence.
    last_tick=max([s['tick'] for rows in (starts,spawns,collisions,states,actions) for s in rows],default=-1)
    unknown=Counter();out_contacts=[];ticks=[];proofs=[];contact_details=[];phase_details=[]
    for i,r in enumerate(graph['parents']):
        if not combat(r['start']):continue
        reason=bad.get(i)
        if not r['complete'] or r['finish'].get('reason')!=0:reason='cancelled-or-open-E-use'
        projectiles=by_parent[i];roots=[p for p in projectiles if p['kind']=='initial']
        if len(roots)!=1:reason='missing-unique-root-projectile'
        hits=set();state_proofs=[];details={};spread_triggered=False
        for p in projectiles:
            s=p['projectile'];oid=s['projectileObjectId'];cs=contacts[oid]
            end=ends.get(oid,{})
            if not end.get('complete') or end['endTick']<s['tick']:
                reason='missing-unambiguous-projectile-active-end';continue
            if any(not s['tick']<=c['tick']<=end['endTick'] for c in cs):
                reason='collision-outside-projectile-lifetime';continue
            for c in cs:
                target=c['targetObjectId']
                if p['kind']=='initial':
                    branches=[a for a in own_actions if a.get('actionNo') in (51,52) and a['tick']==c['tick']
                              and len(a.get('targets',[]))==1 and a['targets'][0].get('targetObjectId')==target]
                    if len(branches)!=1:reason='missing-unique-root-collision-branch';continue
                    spread_triggered=spread_triggered or branches[0]['actionNo']==52
                    if branches[0]['actionNo']==52 and last_tick<=c['tick']+SPREAD_WAIT_FRAMES:
                        reason='spread-callback-frame-not-observed';continue
                elif p.get('movementTargetObjectId')!=target:
                    reason='spread-collision-target-mismatch';continue
                # Non-player roots can still produce player-targeted spreads;
                # their branch above must be checked before filtering teams.
                if target not in teams or teams[target]==teams[player]:continue
                if spec['mode']=='any':
                    hits.add((c['tick'],target))
                    details[c['tick'],target,oid]=dict(hitTick=c['tick'],targetObjectId=target,projectileObjectId=oid,
                        phase='first-projectile' if p['kind']=='initial' else 'screen-spread',evidenceCommand='CmdProjectileCollision')
                    continue
                mm=[m for m in marks if oid in m['sourceProjectileIds'] and m['start']['tick']==c['tick'] and m['targetObjectId']==target]
                if len(mm)!=1 or mm[0]['sourceStatus']!='unique' or mm[0]['parentIndices']!=[i]:
                    reason='enemy-contact-mark-origin-unresolved';continue
                m=mm[0]
                if not m['complete']:reason='mark-lifetime-not-closed';continue
                additions=m['fetterAdditions']
                if len(additions)>1:reason='multiple-fetter-additions-for-one-mark';continue
                hits.update((a['tick'],target) for a in additions)
                for a in additions:
                    details[a['tick'],target,oid]=dict(hitTick=a['tick'],targetObjectId=target,projectileObjectId=oid,
                        phase='first-projectile' if p['kind']=='initial' else 'screen-spread',evidenceCommand='CmdAddState',
                        markStartTick=m['start']['tick'],markFinishTick=m['finish']['tick'],fetterStateCode=a['stateCode'])
                state_proofs.append(dict(projectileObjectId=oid,targetObjectId=target,markStartOrder=m['start']['wireOrder'],
                    markFinishOrder=m['finish']['wireOrder'],fetterOrder=additions[0]['wireOrder'] if additions else None))
        if reason:unknown.update([reason]);continue
        out_contacts.append(hits);ticks.append(r['start']['tick'])
        ds=[details[k] for k in sorted(details)];contact_details.append(ds)
        phase_details.append(dict(firstProjectileCount=len(roots),spreadBranchObserved=spread_triggered,
            spreadProjectileCount=len(projectiles)-len(roots),
            firstProjectileEnemyCount=len({d['targetObjectId'] for d in ds if d['phase']=='first-projectile'}),
            spreadEnemyCount=len({d['targetObjectId'] for d in ds if d['phase']=='screen-spread'})))
        proofs.append(dict(rootProjectileObjectId=roots[0]['projectile']['projectileObjectId'],
                           spreadProjectileCount=len(projectiles)-1,marks=state_proofs,
                           activeEnds=[dict(projectileObjectId=p['projectile']['projectileObjectId'],**ends[p['projectile']['projectileObjectId']]) for p in projectiles]))
    diag.update(unresolvedCombatCastCount=sum(unknown.values()),unresolvedCastReasons=dict(unknown),
                perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False)
    if diag['observedCombatCastCount'] and not out_contacts:return fail('no complete E mark outcomes')
    row=_result(spec,out_contacts,'static-E-callback-frame-'+('mark-consumption-fetter' if spec['mode']=='fetter' else 'root-and-spread-collisions'),cast_ticks=ticks)
    row.update(diag,markEvidenceByAttempt=proofs,staticSpreadDelayFrames=SPREAD_WAIT_FRAMES,
               fixedDurationWindowUsed=False,nearestCastUsed=False,
               evidenceReview='deliverables/theodore-mark-consumption-static-proof-v1.json')
    row['activeEndEvidenceReview']='deliverables/projectile-removal-active-end-static-proof-v1.json'
    row['contactDetailsByAttempt']=contact_details
    row['projectilePhasesByAttempt']=phase_details
    return row
