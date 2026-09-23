"""Recorded preparation key -> launch object ID, independent of parent finish.

Only explicitly reviewed action profiles enter this graph. Equal pending keys
remain ambiguous; no nearest cast, time tolerance or invented finish is used.
"""
from collections import Counter,defaultdict
import math
from .requested_skill_hit_rates import _result,_unavailable
from .skill_partial_cast_lifetimes import ordered_cast_records
from .skill_wire_order import command_order
from .skill_projectile_active_end import collision_only_outcome,projectile_active_end_records
from .skill_lifecycle_result_policy import finalize_lifecycle_result
from .skill_attempt_timing import projectile_outcomes
from .skill_ordered_match_end import ordered_winner_match_end


PROFILES={
    1066200:dict(character=66,skill='ArdaActive1',wire=997,code=106621,prefab='Projectile_FX_BI_Arda_Skill01',prepare=21,launch=22),
    1066210:dict(character=66,skill='ArdaActive1Reinforce',wire=998,code=106622,prefab='Projectile_FX_BI_Arda_Skill01R',prepare=21,launch=22),
}


def _target(action):
    targets=action.get('targets')
    if (action.get('wireStatus')!='decoded-exact-CmdPlaySkillActionWithTargets'
        or not isinstance(targets,list) or len(targets)!=1):return None
    target=targets[0];point=target.get('targetPosition')
    if (target.get('hasTargetPosition') is not True or not isinstance(point,list) or len(point)!=3
        or any(type(v) not in (int,float) or not math.isfinite(v) for v in point)):return None
    return target.get('targetObjectId'),tuple(point)


def recorded_action_projectile_metric(spec,starts,finishes,spawns,actions,collisions,terminals,
                                      player,teams,intervals,catalog,skill_rows,gaps,game_terminals=None,route_inputs=None):
    fail=lambda why:_unavailable(spec,why)
    cfg=PROFILES.get(spec['skillGroup'])
    if not cfg or (spec['characterCode'],spec['mode'],spec['unit'])!=(cfg['character'],'any','skill-cast'):
        return fail('no reviewed preparation/launch action profile')
    streams=[starts,finishes,spawns,actions,collisions,terminals,skill_rows,gaps]
    if any(s is None for s in streams) or player not in teams:return fail('missing recorded action/projectile streams')
    required={'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdPlaySkillAction','CmdPlaySkillActionWithTargets','CmdDestroy','CmdDestroyDelayStart'}
    if any(g.get('count',0) and (g.get('packetName') in required or str(g.get('packetName','')).startswith('CmdProjectile')) for g in gaps):
        return fail('recorded action/projectile streams contain decode gaps')
    definition=catalog.get('skillGroups',{}).get(str(spec['skillGroup']),{})
    projectile=catalog.get('projectileDefinitions',{}).get(str(cfg['code']),{})
    if (definition.get('skillId')!=cfg['skill'] or projectile.get('prefabName')!=cfg['prefab']
        or not collision_only_outcome(projectile)):
        return fail('exact game data disagrees with recorded launch profile')
    code_groups={r['code']:r['group'] for r in skill_rows}
    selected=[s for s in starts if s.get('playerObjectId')==player and s['skillGroup']==spec['skillGroup']]
    if any(s['skillIdCode']!=cfg['wire'] or code_groups.get(s['skillCode'])!=spec['skillGroup'] for s in selected):
        return fail('recorded launch wire/skill code mismatch')
    records,why=ordered_cast_records(selected,finishes,player,allow_same_tick_finishes=True)
    if why:return fail(why)
    aa=[a for a in actions if a.get('sourceObjectId')==player and a.get('skillIdCode')==cfg['wire']
        and a.get('actionNo') in {cfg['prepare'],cfg['launch']}]
    if any(command_order(a) is None or _target(a) is None for a in aa):return fail('missing exact preparation/launch key')
    prepared={};unknown={};assigned={};ambiguous=set()
    for i,r in enumerate(records):
        candidates=[a for a in aa if a['actionNo']==cfg['prepare'] and command_order(r['start'])<=command_order(a)
                    and (r['finish'] is None or command_order(a)<=command_order(r['finish']))]
        if len(candidates)!=1 or _target(candidates[0])[0]!=0:
            unknown[i]='missing-or-ambiguous-recorded-preparation';continue
        prepared[i]=(candidates[0],_target(candidates[0])[1])
    owned=defaultdict(list)
    for s in spawns:
        if s.get('ownerPlayerObjectId')==player and s.get('projectileCode')==cfg['code']:owned[s['projectileObjectId']].append(s)
    launch_by_object=defaultdict(list)
    for a in aa:
        if a['actionNo']==cfg['launch']:launch_by_object[_target(a)[0]].append(a)
    orphan=[]
    for oid,launches in sorted(launch_by_object.items(),key=lambda item:min(command_order(a) for a in item[1])):
        if len(launches)!=1 or len(owned[oid])!=1:return fail('launch object identity is missing or reused')
        action=launches[0];spawn=owned[oid][0];at=command_order(spawn)
        if at is None or spawn['tick']!=action['tick'] or at>=command_order(action):return fail('launch action does not confirm an earlier same-frame spawn')
        key=_target(action)[1]
        candidates=[i for i,(a,point) in prepared.items() if i not in assigned and point==key and command_order(a)<at]
        if len(candidates)!=1:
            ambiguous.update(candidates)
            if not candidates:orphan.append(dict(tick=spawn['tick'],projectileObjectId=oid))
            continue
        i=candidates[0]
        # Earlier ambiguity cannot be settled by whichever later launch remains.
        if i in ambiguous:continue
        assigned[i]=spawn
    if set(owned)-set(launch_by_object):return fail('owned projectile lacks its recorded launch object reference')
    for i in range(len(records)):
        if i in ambiguous:unknown[i]='coincident-pending-preparation-keys'
        elif i not in assigned:unknown.setdefault(i,'missing-recorded-projectile-emission')
    # A launch preceding every recorded preparation cannot be a future cast.
    if any(any(r['start']['tick']<=o['tick'] for r in records) for o in orphan):return fail('launch has no unique earlier recorded preparation')
    ends=projectile_active_end_records(terminals);contacts={};incomplete=set();match_closed=set()
    match_end=ordered_winner_match_end(game_terminals,gaps)
    by_object=defaultdict(list)
    for c in collisions:by_object[c['projectileObjectId']].append(c)
    for i,spawn in assigned.items():
        oid=spawn['projectileObjectId'];end=ends.get(oid)
        scope_end=(match_end if end is None and match_end is not None
                   and command_order(spawn)<command_order(match_end) and spawn['tick']<=match_end['tick'] else None)
        hits=set()
        for c in by_object[oid]:
            if scope_end is not None and (command_order(c) is None or command_order(c)>command_order(scope_end)):
                unknown[i]='projectile-contact-after-or-without-order-at-gameplay-end';continue
            if c['tick']<spawn['tick'] or (end and end['complete'] and c['tick']>end['endTick']):
                unknown[i]='collision-outside-recorded-projectile-activity';continue
            target=c['targetObjectId']
            if target in teams and teams[target]!=teams[player]:hits.add((c['tick'],target))
        contacts[i]=hits
        if scope_end is not None:
            match_closed.add(i)
        elif end is None or not end['complete']:
            incomplete.add(i)
            if not hits:unknown[i]='projectile-removal-missing-without-positive'
        elif end['endTick']<spawn['tick']:unknown[i]='projectile-removal-before-spawn'
    combat=[i for i,r in enumerate(records) if any(a<=r['start']['tick']<b for a,b in intervals)]
    policy_misses=[];policy_blocked=[]
    if spec['skillGroup']==1066200:
        import json
        from pathlib import Path
        enabled=json.loads((Path(__file__).resolve().parents[1]/'data/user-hit-rate-scope-20260912.json').read_bytes())['recordedUseMissPolicy'].get('enabled') is True
        raw=(route_inputs or {}).get('summon_inputs') or {}
        raw_shots=raw.get('allProjectileSpawns')
        for i in combat:
            if not enabled or unknown.get(i)!='missing-recorded-projectile-emission' or i not in prepared:continue
            start=records[i]['start'];left=command_order(start);right=command_order(match_end) if match_end else None
            prep=prepared[i][0];reasons=[]
            if records[i]['finish'] is not None:reasons.append('not-open-final-use')
            if left is None or right is None or not left<=command_order(prep)<right or not start['tick']<=prep['tick']<=match_end['tick']:reasons.append('ordered-preparation-before-winner-unproven')
            if raw_shots is None or any(g.get('count',0) for g in gaps):reasons.append('original-stream-incomplete')
            def after(e):
                if e.get('tick') is not None and e['tick']<start['tick']:return False
                return command_order(e) is None or left is None or e.get('tick',start['tick'])>start['tick'] or command_order(e)>=left
            if any(s.get('projectileCode')==cfg['code'] and (s.get('ownerObjectId')==player or s.get('ownerObjectId') not in teams) and after(s) for s in raw_shots or []):reasons.append('possible-projectile-emission')
            own_actions=[a for a in actions if a.get('sourceObjectId')==player and a.get('skillIdCode')==cfg['wire'] and after(a)]
            if own_actions!=[prep]:reasons.append('extra-skill-action')
            if any(t.get('sameTickDirectPlayerDamage') is True or t.get('sameTickOwnedProjectileCollision') is True for t in prep.get('targets',[])):reasons.append('preparation-contact-needs-classification')
            proof=dict(startTick=start['tick'],startOrder=left,preparation=prep,winnerEnd=match_end,recordedEmission=False,skillFinishInvented=False)
            if reasons:policy_blocked.append(dict(proof,reasons=reasons));continue
            unknown.pop(i);contacts[i]=set();policy_misses.append(proof)
    valid=[i for i in combat if i not in unknown]
    row=_result(spec,[contacts[i] for i in valid],'recorded-preparation-key-and-explicit-launch-object-collision',
                cast_ticks=[records[i]['start']['tick'] for i in valid])
    row['outcomes']=[]
    for i in valid:
        if i in assigned:row['outcomes']+=projectile_outcomes('skill-cast',[(records[i]['start']['tick'],[(assigned[i]['tick'],contacts[i])],False)])
        else:row['outcomes'].append([records[i]['start']['tick'],0,records[i]['start']['tick'],None])
    row=finalize_lifecycle_result(row,combat,unknown,incomplete_positive=incomplete-set(unknown),observed_positive=True)
    if spec['skillGroup']==1066200:
        row.update(userPolicyMissEvidence=policy_misses,userPolicyBlockedEvidence=policy_blocked,userPolicyMissCastCount=len(policy_misses),userPolicyMissAuthority='explicit-user-rule')
        if policy_misses:row.update(verifiedCompletionCredit=False,fullRequestedMetricComplete=False,
            verifiedCombatCastCount=max(0,(row.get('verifiedCombatCastCount') or 0)-len(policy_misses)))
    row.update(emissionLink='exact-preparation-key-and-recorded-launch-object-id',
        fixedEmissionWindowUsed=False,parentFinishUsedAsEmissionEnd=False,positionToleranceUsed=False,
        projectileCodes=[cfg['code']],preparationAction=cfg['prepare'],launchAction=cfg['launch'],
        linkedAfterParentFinishCount=sum(r['finish'] is not None and i in assigned and command_order(r['finish'])<command_order(assigned[i]) for i,r in enumerate(records)),
        preFirstPreparationLaunchCount=len(orphan),
        winnerMatchEndClosedProjectileCount=len(match_closed.intersection(valid)),
        projectileRemovalInvented=False,
        unresolvedUseEvidence=[dict(startTick=records[i]['start']['tick'],startOrder=command_order(records[i]['start']),
            reasons=[unknown[i]],hasRecordedEmission=i in assigned) for i in combat if i in unknown],
        measuredOutcome='direct-projectile-collision; subsequent scroll explosion is not substituted',
        evidenceReview='deliverables/recorded-action-projectile-emission-20260910-v1.json')
    if spec['skillGroup']==1066210 and route_inputs is not None:
        from .skill_arrival_summon_phase import arrival_summon_phase
        inputs=route_inputs.get('summon_inputs') or {}
        row['phaseMetrics']={'scroll-explosion':arrival_summon_phase(spec,records,assigned,
            parent_unknown=unknown,game_terminals=game_terminals,
            summons=inputs.get('summons'),terminals=terminals,actions=actions,damages=route_inputs.get('damages'),
            player=player,teams=teams,intervals=intervals,gaps=gaps,effect_rows=route_inputs.get('effect_rows'),
            summon_rows=inputs.get('summon_rows'),profile=dict(summon=1453,summonPrefab='Arda_Skill01R_Scroll_Dummy',
                effect=1066213,effectPrefab='FX_BI_Arda_Skill01R_Hit',wire=998,action=24))}
    return row
