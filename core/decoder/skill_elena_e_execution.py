"""Elena E trail lineage: owned1261 spawn -> death callback -> delayed removal.

The ice patch damage runs when a trail summon dies, commonly after E finishes.
The parent's command lifetime bounds creation, not the later contact interval.
"""
from collections import Counter,defaultdict
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order as order
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order as order
    from skill_action_stage_evidence import load_exact_skill_ids


def elena_e_execution(spec,starts,finishes,actions,summons,objects,deaths,terminals,
                      damages,player,teams,intervals,catalog,skill_rows,summon_rows,
                      effect_rows,gaps,skill_ids=None):
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1050400]
    combat=lambda s:any(a<=s['tick']<b for a,b in intervals)
    diag=dict(observedCastCount=len(selected),observedCombatCastCount=sum(map(combat,selected)))
    def fail(reason):return {**_unavailable(spec,reason),**diag}
    if (spec['characterCode'],spec['skillGroup'],spec['mode'],spec['unit'])!=(50,1050400,'any','skill-cast'):
        return fail('unsupported Elena E execution')
    if any(x is None for x in (actions,summons,objects,deaths,terminals,damages,gaps)) or player not in teams:
        return fail('missing Elena trail command stream or caster team')
    needed={'CmdStartSkill','CmdFinishSkill','CmdPlaySkillAction','CmdSpawn','CmdDead','CmdDestroyDelayStart','CmdDamage'}
    if any(g.get('count',0) and g.get('packetName') in needed for g in gaps):
        return fail('incomplete Elena trail creation/death/damage commands')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    codes={s['code'] for s in skill_rows if s.get('group')==1050400}
    sd=[s for s in summon_rows if s.get('code')==1261];fx=[e for e in effect_rows if e.get('code')==1050402]
    if (ids.get('ElenaActive3')!=738 or not codes
            or catalog['skillGroups'].get('1050400',{}).get('skillId')!='ElenaActive3'
            or len(sd)!=1 or sd[0].get('objectType')!='SummonArtifact' or sd[0].get('useAttackerType')!='None'
            or sd[0].get('prefabPath')!='Projectile_FX_BI_Elena_IceFlower_02'
            or len(fx)!=1 or fx[0].get('effectPrefabName')!='' or fx[0].get('soundName')!='Elena_Skill03_Hit'
            or any(s['skillIdCode']!=738 or s['skillCode'] not in codes for s in selected)):
        return fail('pinned Elena E producer identity mismatch')
    records,why=ordered_cast_records(selected,finishes,player,allow_same_tick_finishes=True)
    if why:return fail(why)
    objects_by_id=defaultdict(list)
    for obj in objects:objects_by_id[obj['objectId']].append(obj)
    deaths_by_id=defaultdict(list)
    for event in deaths:
        if event.get('event')=='CmdDead':deaths_by_id[event.get('deadObjectId')].append(event)
    ends_by_id=defaultdict(list)
    for event in terminals:
        if event.get('event')=='CmdDestroyDelayStart':ends_by_id[event.get('objectId')].append(event)
    children=[s for s in summons if s.get('ownerObjectId')==player and s.get('summonCode')==1261]
    if len({s['objectId'] for s in children})!=len(children):return fail('duplicate Elena trail object identity')
    graph=[];unknown={};by_cast=defaultdict(list)
    for child in children:
        oid=child['objectId'];spawn=objects_by_id[oid]
        if (len(spawn)!=1 or order(spawn[0]) is None or spawn[0].get('objectType')!=21
                or spawn[0]['tick']!=child['tick'] or child.get('objectType')!=21
                or child.get('snapshotType')!='SummonSnapshot' or child.get('identityVerifiedAgainstGameDb') is not True):
            return fail('missing exact Elena trail spawn/owner identity')
        spawn=spawn[0]
        owners=[i for i,r in enumerate(records) if order(r['start'])<order(spawn)
                and (r['finish'] is None or order(spawn)<order(r['finish']))]
        if len(owners)!=1:return fail('trail creation outside unique ordered Elena E lifetime')
        owner=owners[0];ds=deaths_by_id[oid]
        ends=ends_by_id[oid]
        node=dict(owner=owner,spawn=spawn,child=child,death=None,end=None,contacts=[])
        if (len(ds)!=1 or len(ends)!=1 or order(ds[0]) is None or order(ends[0]) is None
                or not order(spawn)<order(ds[0])<order(ends[0]) or ds[0]['tick']!=ends[0]['tick']):
            unknown[owner]='trail-death-callback-not-closed'
        else:node.update(death=ds[0],end=ends[0])
        by_cast[owner].append(len(graph));graph.append(node)
    # Dedicated producer effect is still not a cast key. The actual summon
    # death-to-removal callback order supplies that key even after cast finish.
    candidates=[d for d in damages if d.get('attackerObjectId')==player and d.get('effectCode')==1050402]
    for d in candidates:
        if order(d) is None:return fail('missing exact Elena damage command order')
        matches=[n for n in graph if n['death'] is not None and n['death']['tick']==d['tick']
                 and order(n['death'])<order(d)<order(n['end'])]
        if len(matches)!=1:return fail('Elena damage outside unique trail death callback')
        node=matches[0]
        if d.get('damageType')!=2 or type(d.get('damageIsNull')) is not bool:
            unknown[node['owner']]='Elena damage discriminator mismatch';continue
        target=d.get('targetObjectId')
        if target in teams and teams[target]!=teams[player]:node['contacts'].append(d)
    aa=[a for a in actions if a.get('sourceObjectId')==player and a.get('skillIdCode')==738]
    if any(order(a) is None for a in aa):return fail('missing exact Elena movement phase order')
    contacts=[];ticks=[];attempt_ticks=[];details=[];execution=[];cancelled=[];empty_trails=[];assigned=set()
    for i,r in enumerate(records):
        s,end=r['start'],r['finish'];nodes=[graph[n] for n in by_cast[i]]
        owned=[a for a in aa if order(s)<order(a) and (end is None or order(a)<order(end))];assigned.update(map(order,owned))
        markers=[a for a in owned if a.get('actionNo') in (1,101) and a.get('wireStatus')=='decoded-exact-CmdPlaySkillAction' and a.get('targets')==[]]
        if not r['complete'] or end is None:
            unknown[i]=r['reason'];continue
        if end.get('reason')==3 and not markers and not nodes:
            cancelled.append(i);unknown.pop(i,None);continue
        if end.get('reason')==0 and len(markers)==1 and not nodes:
            closes=[a for a in owned if a.get('actionNo')==markers[0]['actionNo']+1
                    and a.get('wireStatus')=='decoded-exact-CmdPlaySkillAction'
                    and order(a)>order(markers[0])]
            if len(closes)==1:
                # The only E damage producer is an owned trail death callback.
                # A recorded start and normal phase end with no spawned trail
                # never emitted that attack; do not fabricate a missed patch.
                empty_trails.append(i);unknown.pop(i,None);continue
        if len(markers)!=1 or not nodes:
            unknown[i]='missing-unique-Elena-trail-execution';continue
        marker=markers[0]
        if any(order(n['spawn'])<=order(marker) for n in nodes):
            unknown[i]='trail-spawn-before-execution-marker';continue
        closing=[a for a in owned if a.get('actionNo')==marker['actionNo']+1 and a.get('wireStatus')=='decoded-exact-CmdPlaySkillAction']
        if end.get('reason')==0 and (len(closing)!=1 or order(closing[0])<=order(marker)):
            unknown[i]='normal-Elena-movement-phase-not-closed';continue
        if i in unknown:continue
        enemy=[(n,d) for n in nodes for d in n['contacts']]
        if combat(s):
            contacts.append({(d['tick'],d['targetObjectId']) for n,d in enemy});ticks.append(s['tick'])
            attempt_ticks.append(min(n['spawn']['tick'] for n in nodes))
            details.append([dict(targetObjectId=d['targetObjectId'],hitTick=d['tick'],damageOrder=d['wireOrder'],
                                 trailObjectId=n['child']['objectId'],trailSpawnOrder=n['spawn']['wireOrder'],
                                 trailDeathOrder=n['death']['wireOrder'],callbackEndOrder=n['end']['wireOrder']) for n,d in enemy])
            execution.append(dict(startOrder=s['wireOrder'],markerOrder=marker['wireOrder'],finishOrder=end['wireOrder'],
                                  finishReason=end['reason'],trailObjectCount=len(nodes),
                                  lastCallbackTick=max(n['end']['tick'] for n in nodes),
                                  postFinishEnemyDamageCount=sum(order(d)>order(end) for n,d in enemy)))
    if assigned!={order(a) for a in aa}:return fail('orphan Elena E phase action')
    reasons=Counter(v for i,v in unknown.items() if combat(records[i]['start']))
    diag.update(unresolvedCombatCastCount=sum(reasons.values()),unresolvedCastReasons=dict(reasons),
                unresolvedNonCombatCastCount=sum(not combat(records[i]['start']) for i in unknown),
                unresolvedNonCombatCastReasons=dict(Counter(v for i,v in unknown.items() if not combat(records[i]['start']))),
                cancelledBeforeAttackCount=sum(combat(records[i]['start']) for i in cancelled),
                cancelledBeforeAttackCastTicks=[records[i]['start']['tick'] for i in cancelled if combat(records[i]['start'])],
                cancelledBeforeAttackNonCombatCount=sum(not combat(records[i]['start']) for i in cancelled),
                noTrailCreatedCount=sum(combat(records[i]['start']) for i in empty_trails),
                noTrailCreatedNonCombatCount=sum(not combat(records[i]['start']) for i in empty_trails),
                noTrailCreationEvidence=[records[i] for i in empty_trails],
                perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False)
    if (diag['observedCombatCastCount'] and not contacts
            and not diag['cancelledBeforeAttackCount'] and not diag['noTrailCreatedCount']):
        return fail('no closed Elena E trail executions')
    result=_result(spec,contacts,'static-Elena-owned-trail-death-callback-lineage',cast_ticks=ticks)
    for outcome,tick in zip(result.get('outcomes',[]),attempt_ticks):outcome[2]=tick
    result.update(diag,contactDetailsByAttempt=details,executionEvidenceByAttempt=execution,
                  trailObjectCount=len(children),postFinishEnemyDamageCount=sum(x['postFinishEnemyDamageCount'] for x in execution),
                  enemyDamageCommandCount=sum(len(x) for x in details),
                  evidenceReview='deliverables/elena-e-execution-static-proof-v1.json',
                  assumedTrailExpirationUsed=False,castFinishUsedAsTrailEnd=False,
                  positionsUsedAsContactProof=False,effectCodeAloneUsed=False,damageAmountInferred=False)
    return result
