"""Wilson E: parent/child/hook contact and pull application, including stationary self E."""
from collections import Counter, defaultdict
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_summon_ownership import live_summon_owner_resolver
    from .skill_wire_order import command_order as order
    from .skill_attempt_timing import exact_outcome
    from .skill_action_stage_evidence import load_exact_skill_ids
    from .skill_ordered_match_end import ordered_winner_match_end
    from .skill_adina_star_e_execution import _caster, _unique
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_summon_ownership import live_summon_owner_resolver
    from skill_wire_order import command_order as order
    from skill_attempt_timing import exact_outcome
    from skill_action_stage_evidence import load_exact_skill_ids
    from skill_ordered_match_end import ordered_winner_match_end
    from skill_adina_star_e_execution import _caster, _unique


def sissela_e_execution_metric(spec, starts, finishes, player, teams, intervals, catalog, inputs):
    roots=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1015400]
    combat=lambda s:any(a<=s['tick']<b for a,b in intervals)
    diag=dict(observedCastCount=len(roots),observedCombatCastCount=sum(map(combat,roots)))
    fail=lambda reason:{**_unavailable(spec,reason),**diag}
    mode=spec['mode']
    if (spec['characterCode'],spec['skillGroup'],spec['unit'])!=(15,1015400,'skill-cast') or mode not in ('any','enemy-pull','self-pull'):
        return fail('unsupported Sissela E scope')
    required=('nonPlayerSkillStarts','summons','allProjectileSpawns','collisions','terminals','stateScripts','gaps')
    if player not in teams or any(inputs.get(k) is None for k in required):return fail('missing Wilson E lineage evidence')
    ids=load_exact_skill_ids()
    if (ids.get('SisselaActive3'),ids.get('SisselaWilsonActive3'),ids.get('SisselaActive3Pull'))!=(201,208,202):return fail('version-locked E wire IDs differ')
    if catalog['skillGroups'].get('1015400',{}).get('skillId')!='SisselaActive3' or catalog['skillGroups'].get('1015900',{}).get('skillId')!='SisselaWilsonActive3' or '101503' not in catalog['projectileDefinitions']:
        return fail('version-locked Wilson E definitions differ')
    packets={'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdProjectileCollision','CmdProjectileArrived','CmdDestroyDelayStart','CmdDestroy','CmdStartStateSkill','CmdFinishStateSkill','SummonSnapshot:11'}
    if any(g.get('count',0) and g.get('packetName') in packets for g in inputs['gaps']):return fail('incomplete Wilson E execution stream')
    if any(s.get('skillIdCode')!=201 or s.get('skillCode') not in range(1015401,1015406) for s in roots):return fail('parent E code differs')
    parents,why=ordered_cast_records(roots,finishes,player,allow_same_tick_finishes=True)
    if why:return fail(why)
    end=ordered_winner_match_end(inputs.get('gameTerminals'),inputs['gaps'])
    resolve=live_summon_owner_resolver(inputs['summons'],inputs['terminals'],set(teams))
    child_starts=defaultdict(list)
    for s in inputs['nonPlayerSkillStarts']:
        if s.get('skillIdCode')!=208:continue
        owner,path,why=resolve(s['sourceObjectId'],s['tick'])
        if why:return fail('Wilson E ownership: '+why)
        if owner!=player:continue
        if path!=[1030] or s.get('skillCode')!=1015901:return fail('Wilson E child identity differs')
        child_starts[s['sourceObjectId']].append({**s,'playerObjectId':s['sourceObjectId']})
    children=[]
    for actor,ss in child_starts.items():
        rr,why=ordered_cast_records(ss,finishes,actor,allow_same_tick_finishes=True)
        if why:return fail('Wilson E lifetime: '+why)
        children.extend(rr)
    if end:
        for r in [*parents,*children]:
            if not r['complete'] and order(r['start'])<order(end):r.update(finish=end,complete=True,closureKind='actual-winner-match-end')
    shots=[s for s in inputs['allProjectileSpawns'] if s.get('projectileCode')==101503 and s.get('ownerObjectId') in child_starts]
    if any(order(s) is None for s in shots) or len({s['projectileObjectId'] for s in shots})!=len(shots):return fail('hook identity/order ambiguous')
    shotids={s['projectileObjectId'] for s in shots};cs,ts=defaultdict(list),defaultdict(list)
    try:
        for c in _unique([c for c in inputs['collisions'] if c['projectileObjectId'] in shotids],('tick','projectileObjectId','targetObjectId')):cs[c['projectileObjectId']].append(c)
        for t in _unique([t for t in inputs['terminals'] if t['objectId'] in shotids],('tick','objectId','event','isCollision')):ts[t['objectId']].append(t)
    except ValueError as exc:return fail(str(exc))
    states=[s for s in inputs['stateScripts'] if s['event']=='CmdStartStateSkill' and s.get('skillIdCode')==202 and _caster(s)==player]
    if any(s.get('skillCode')!=1015901 or s.get('stateGroup')!=1015400 or order(s) is None for s in states):return fail('pull state identity/order mismatch')
    owner_children=defaultdict(list)
    for c in children:
        pp=[i for i,p in enumerate(parents) if p['complete'] and order(p['start'])<order(c['start'])<order(p['finish'])]
        if len(pp)!=1:return fail('E child has no unique parent request')
        owner_children[pp[0]].append(c)
    self_moves_by_tick=defaultdict(list)
    for move in inputs.get('movement') or []:
        if move.get('event')=='CmdMoveStraightWithoutNav' and move.get('objectId')==player and order(move) is not None:
            self_moves_by_tick[move['tick']].append(move)
    unknown={};unused=set();executions={};used_shots=set();used_states=set()
    enemy=lambda who:who in teams and teams[who]!=teams[player]
    for i,p in enumerate(parents):
        cc=owner_children[i]
        if not p['complete']:unknown[i]='parent E remains open';continue
        if not cc:unused.add(i);continue
        if len(cc)!=1:unknown[i]='multiple child requests for one E';continue
        child=cc[0]
        if not child['complete']:unknown[i]='child E remains open';continue
        ss=[s for s in shots if s['ownerObjectId']==child['start']['sourceObjectId'] and order(child['start'])<order(s)<order(child['finish'])]
        if not ss and (child['finish'].get('reason') in (1,2,3,4,11) or p['finish'].get('reason') in (1,2,3,4,11)):unused.add(i);continue
        if len(ss)!=1:unknown[i]='child E does not own one hook launch';continue
        shot=ss[0];pid=shot['projectileObjectId'];used_shots.add(pid)
        closed=[t for t in ts[pid] if t['event'] in ('CmdDestroyDelayStart','CmdDestroy') and order(t)>order(shot)]
        if not closed:unknown[i]='hook contact lifetime not closed';continue
        close=min(closed,key=order);collisions=sorted(cs[pid],key=order)
        if any(not order(shot)<order(c)<order(close) for c in collisions):unknown[i]='hook collision lies outside its closed lifetime';continue
        # Callback returns when grabTarget(+0xe8) is already set (2d4b73a).
        # Later wire contacts during retraction do not create another grab.
        effective_collisions=collisions[:1]
        contacts={'any':set(),'enemy-pull':set(),'self-pull':set()};linked=[];moves=[]
        for c in effective_collisions:
            target=c['targetObjectId']
            if enemy(target):contacts['any'].add((c['tick'],target))
            matches=[s for s in states if s['sourceObjectId']==target and s['tick']==c['tick'] and order(c)<order(s)<order(close)]
            if len(matches)>1:unknown[i]='collision creates multiple pull states';continue
            if not matches:continue
            state=matches[0];used_states.add(order(state));linked.append(state)
            if enemy(target):contacts['enemy-pull'].add((state['tick'],target))
            if target!=player:continue
            # User scope includes stationary self E that grants its shield.
            # Applying the actual self pull state is success even without any
            # displacement. Movement is diagnostic, never the numerator gate.
            contacts['self-pull'].add((state['tick'],player))
            mm=[m for m in self_moves_by_tick[state['tick']] if order(state)<order(m)<order(close)]
            moves.extend(mm)
        executions[i]=dict(request=p,child=child,projectile=shot,collisions=collisions,effectiveCollisions=effective_collisions,ignoredRetractionCollisions=collisions[1:],pullApplications=linked,selfMovementCommands=moves,closure=close,contacts=contacts)
    if any(s['projectileObjectId'] not in used_shots for s in shots):return fail('hook has no unique parent/child execution')
    if any(order(s) not in used_states for s in states):return fail('pull state has no unique hook collision')
    chosen=[i for i in executions if i not in unknown and combat(parents[i]['start'])]
    contacts=[executions[i]['contacts'][mode] for i in chosen]
    result=_result(spec,contacts,'static-Wilson-E-hook-pull-self-movement',cast_ticks=[parents[i]['start']['tick'] for i in chosen])
    result.update(diag,outcomes=[exact_outcome(parents[i]['start']['tick'],executions[i]['projectile']['tick'],c) for i,c in zip(chosen,contacts)],
        unresolvedCombatCastCount=sum(combat(parents[i]['start']) for i in unknown),unresolvedNonCombatCastCount=sum(not combat(parents[i]['start']) for i in unknown),
        unresolvedCastReasons=dict(Counter(v for i,v in unknown.items() if combat(parents[i]['start']))),
        nonExecutedCastCount=sum(combat(parents[i]['start']) for i in unused),nonExecutedAllCastCount=len(unused),
        executionEvidenceByAttempt=[{k:v for k,v in executions[i].items() if k!='contacts'} for i in chosen],
        denominatorMeaning='One actual owned Wilson hook launched for E; cancelled requests without a hook are separate',
        hitMeaning={'any':'Enemy contact by exact hook','enemy-pull':'Actual pull202 application to enemy from hook collision','self-pull':'Actual self pull202 application from own hook collision; stationary self E with shield counts, displacement is not required'}[mode],
        selfPullRequiresDisplacement=False,stationarySelfEIncluded=True,shieldUsedAsMovementProof=False,perUseCompletenessTracked=True,
        incompleteUsesCountedAsMisses=False,fixedDurationWindowUsed=False,damageAmountInferred=False,
        evidenceReview='deliverables/sissela-e-execution-static-proof-v1.json')
    if spec.get('reportMultiTarget') is False:
        for key in ('multiTargetAttemptCount','multiTargetAttemptRate','distinctEnemyTargetsSummedAcrossAttempts','meanDistinctEnemyTargetsPerAttempt','deduplicatedEnemyContactEventCount','distinctEnemyTargetsPerAttempt','distinctEnemyTargetFirstHitTicksPerAttempt'):
            result[key]=None
    return result
