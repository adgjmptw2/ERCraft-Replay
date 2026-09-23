"""Cat Q grants a mount; its separate reinforced normal attack does the damage."""
from collections import Counter

try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order as order
    from .skill_attempt_timing import exact_outcome
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order as order
    from skill_attempt_timing import exact_outcome
    from skill_action_stage_evidence import load_exact_skill_ids


def irem_cat_q_execution_metric(spec, starts, finishes, player, teams, intervals, catalog, inputs):
    roots = [s for s in starts if s['playerObjectId'] == player and s['skillGroup'] == 1061210]
    combat = lambda r: any(a <= r['start']['tick'] < b for a, b in intervals)
    diag = dict(observedCastCount=len(roots), observedCombatCastCount=sum(any(a <= s['tick'] < b for a,b in intervals) for s in roots))
    fail = lambda why: {**_unavailable(spec, why), **diag}
    if (spec['characterCode'],spec['skillGroup'],spec['unit'],spec['mode']) != (61,1061210,'skill-cast','any'):
        return fail('unsupported Irem Cat Q scope')
    if any(inputs.get(k) is None for k in ('skillContexts','stateScripts','states','damages','actions','gaps')):
        return fail('missing ordered Cat Q mount/attack evidence')
    ids = load_exact_skill_ids()
    if any(ids.get(n) != v for n,v in [('IremCatActive1',909),('IremCatNormalAttackReinforce',908),('IremReinforce',897)]) or any(catalog['skillGroups'].get(str(g),{}).get('skillId') != n for g,n in [(1061210,'IremCatActive1'),(1061030,'IremCatNormalAttackReinforce')]):
        return fail('version-locked Cat Q identities differ')
    required={'CmdStartSkill','CmdFinishSkill','CmdStartNormalAttackSkill','CmdStartStateSkill','CmdFinishStateSkill','CmdAddState','CmdAddStateExtended','CmdDamage','CmdPlaySkillAction'}
    if any(g.get('count',0) and g.get('packetName') in required for g in inputs['gaps']):
        return fail('incomplete Cat Q evidence stream')
    if any(s['skillIdCode'] != 909 or s['skillCode'] not in range(1061211,1061216) for s in roots):
        return fail('Cat Q parent code mismatch')
    parents,why=ordered_cast_records(roots,finishes,player,allow_same_tick_finishes=True)
    if why:return fail(why)
    scripts=[s for s in inputs['stateScripts'] if s.get('sourceObjectId')==player and s.get('skillIdCode')==897]
    if any(s.get('stateGroup')!=1061100 for s in scripts):return fail('Irem reinforcement state group mismatch')
    state_starts=[{**s,'playerObjectId':player} for s in scripts if s['event']=='CmdStartStateSkill']
    state_finishes=[{**s,'playerObjectId':player} for s in scripts if s['event']=='CmdFinishStateSkill']
    mounts,why=ordered_cast_records(state_starts,state_finishes,player,allow_same_tick_finishes=True)
    if why:return fail('Cat Q mount: '+why)
    all_normals=[s for s in inputs['skillContexts'] if s.get('sourceObjectId')==player and s.get('event')=='CmdStartNormalAttackSkill']
    if any(order(s) is None for s in all_normals):return fail('normal attack context lacks command order')
    normals=[{**s,'playerObjectId':player,'skillIdCode':908,'skillGroup':1061030} for s in all_normals if s.get('skillCode')==1061031]
    attacks,why=ordered_cast_records(normals,finishes,player,allow_same_tick_finishes=True)
    if why:return fail('Cat Q reinforced attack: '+why)
    attack_parent={};parent_attack={};parent_mount={};unknown={};unused=[]
    for i,p in enumerate(parents):
        if not p['complete']:
            unknown[i]='Q request remains open';continue
        ms=[m for m in mounts if order(p['start'])<order(m['start'])<order(p['finish'])]
        if len(ms)!=1 or ms[0]['start'].get('skillCode')!=p['start']['skillCode']:
            unknown[i]='Q does not create one exact matching reinforcement state';continue
        m=ms[0];parent_mount[i]=m
        adds=[s for s in inputs['states'] if s.get('event')=='add' and s.get('targetObjectId')==player and s.get('stateCode')==1061101 and s['tick']==p['start']['tick']]
        if any(order(s) is None for s in adds):
            unknown[i]='reinforcement state creation lacks order';continue
        adds=[s for s in adds if order(p['start'])<order(s)<order(m['start'])]
        if len(adds)!=1 or not m['complete']:
            unknown[i]='reinforcement state is missing or remains open';continue
        ns=[j for j,n in enumerate(attacks) if order(m['start'])<order(n['start'])<order(m['finish'])]
        if not ns:
            if type(m['finish'].get('reason')) is int and m['finish']['reason'] in (0,2,3,4,6):unused.append(i)
            else:unknown[i]='unreviewed unspent reinforcement termination'
            continue
        if len(ns)!=1 or ns[0] in attack_parent or m['finish']['tick']!=attacks[ns[0]]['start']['tick']:
            unknown[i]='reinforcement does not have one exact consuming attack';continue
        j=ns[0];attack_parent[j]=i;parent_attack[i]=j
    if any(j not in attack_parent for j in range(len(attacks))):return fail('reinforced attack lacks one proved Q/state lineage')
    contacts={};damages={};markers={};phases={};open_hits=set()
    for j,n in enumerate(attacks):
        i=attack_parent[j];contacts[i]=set();damages[i]=[];phases[i]={'initial':set(),'bonus':set(),'area':set()}
        open_attack=not n['complete']
        lo,hi=order(n['start']),order(n['finish']) if n['complete'] else None
        def within(e):return lo<order(e) and (hi is None or order(e)<hi)
        acts=[x for x in inputs['actions'] if x.get('sourceObjectId')==player and x.get('skillIdCode')==908 and x['tick']>=n['start']['tick'] and (hi is None or x['tick']<=n['finish']['tick'])]
        if any(order(x) is None for x in acts):
            unknown[i]='reinforced punch action lacks order';continue
        acts=[x for x in acts if within(x)]
        if len(acts)!=1 or acts[0].get('actionNo')!=1:
            unknown[i]='reinforced attack lacks one exact punch-start action';continue
        markers[i]=acts[0]
        if any(within(s) for s in all_normals):unknown[i]='another normal attack overlaps reinforced contact attribution'
        if not open_attack and n['finish'].get('reason') not in (0,2,3):unknown[i]='unreviewed reinforced attack termination'
        ds=[d for d in inputs['damages'] if d.get('attackerObjectId')==player and d.get('effectCode') in (1061002,1061206,1061207) and n['start']['tick']<=d['tick'] and (hi is None or d['tick']<=n['finish']['tick'])]
        if any(order(d) is None for d in ds):
            unknown[i]='Cat Q damage lacks command order';continue
        ds=[d for d in ds if within(d)];damages[i]=ds
        first=[d for d in ds if d['effectCode'] in (1061002,1061206)]
        if len({d['tick'] for d in first})>1 or any(d['targetObjectId']!=n['start']['targetObjectId'] for d in first) or any(sum(d['effectCode']==fx for d in first)>1 for fx in(1061002,1061206)):
            unknown[i]='first punch damage does not match one targeted phase'
        for d in ds:
            if order(d)<=order(acts[0]) or d.get('damageType')!=(1 if d['effectCode']==1061002 else 2) or type(d.get('damageIsNull')) is not bool:
                unknown[i]='Cat Q contact lacks exact phase/type/discriminator';continue
            target=d['targetObjectId']
            if target not in teams or teams[target]==teams[player]:continue
            hit=(d['tick'],target);contacts[i].add(hit)
            phases[i]['area' if d['effectCode']==1061207 else 'initial'].add(hit)
            if d['effectCode']==1061206:phases[i]['bonus'].add(hit)
        if open_attack:
            if i not in unknown and phases[i]['initial']:open_hits.add(i)
            elif i not in unknown:unknown[i]='reinforced attack remains open'
        elif n['finish'].get('reason')!=0 and not ds:unknown[i]='cancelled punch has no proved damage phase'
    # Only the Cat Q bonus/area codes are exclusive to this route. The ordinary
    # cat melee code1061002 also occurs outside Q and must not be stolen.
    for d in inputs['damages']:
        if d.get('attackerObjectId')!=player or d.get('effectCode') not in (1061206,1061207):continue
        if order(d) is None:return fail('exclusive Cat Q damage has no wire order')
        owners=[j for j,n in enumerate(attacks) if order(n['start'])<order(d) and (n['finish'] is None or order(d)<order(n['finish']))]
        if len(owners)!=1:return fail('exclusive Cat Q damage outside one reinforced attack')
    chosen=[i for i in parent_attack if i not in unknown and combat(parents[i])]
    row=_result(spec,[contacts[i] for i in chosen],'static-Irem-Q-state-mounted-reinforced-attack',cast_ticks=[parents[i]['start']['tick'] for i in chosen])
    reasons=Counter(why for i,why in unknown.items() if combat(parents[i]))
    phase_counts={k:{'hitAttemptCount':sum(bool(phases[i][k]) for i in chosen),'recordedContactCount':sum(len(phases[i][k]) for i in chosen),'distinctEnemyTargetsSummed':sum(len({who for _,who in phases[i][k]}) for i in chosen)} for k in('initial','bonus','area')}
    row.update(diag,outcomes=[exact_outcome(parents[i]['start']['tick'],markers[i]['tick'],contacts[i]) for i in chosen],
        unresolvedCombatCastCount=sum(reasons.values()),unresolvedCastReasons=dict(reasons),
        unresolvedNonCombatCastCount=sum(not combat(parents[i]) for i in unknown),
        nonExecutedCastCount=sum(combat(parents[i]) for i in unused),nonExecutedAllCastCount=len(unused),
        phaseCounts=phase_counts,phaseContactsByAttempt=[{k:sorted(v) for k,v in phases[i].items()} for i in chosen],
        enemyAreaDamageCommandCount=sum(d['effectCode']==1061207 and d['targetObjectId'] in teams and teams[d['targetObjectId']]!=teams[player] for i in chosen for d in damages[i]),
        executionEvidenceByAttempt=[dict(request=parents[i],reinforcement=parent_mount[i],attack=attacks[parent_attack[i]],punchStartAction=markers[i],damageCommands=damages[i]) for i in chosen],
        nonExecutionEvidence=[dict(request=parents[i],reinforcement=parent_mount[i]) for i in unused],
        postExecutionCancelledCount=sum(attacks[parent_attack[i]]['complete'] and attacks[parent_attack[i]]['finish']['reason']!=0 for i in chosen),
        denominatorMeaning='Reinforced attack executions linked to Q-created state; unspent mounts are separate, area contacts do not multiply attempts',
        attemptTickMeaning='Actual CmdPlaySkillAction908/action1 begins the punch animation; damage phase timestamps come only from recorded contacts',
        phaseCountMeaning='Observed contacts within executed combo attempts, not inferred per-punch attempts or damage amounts',
        perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,damageAmountInferred=False,fixedDurationWindowUsed=False,
        evidenceReview='deliverables/irem-cat-q-execution-static-proof-v1.json')
    recovered=sorted(open_hits.intersection(chosen))
    if recovered:
        row.update(openPositiveCastTicks=[parents[i]['start']['tick'] for i in recovered],
            positiveContactRequiresFinish=False,finishInvented=False,
            normalFinishRequiredForNegativeOutcome=True,targetCountsAreLowerBounds=True,
            completeTargetCountsAvailable=False,fullRequestedMetricComplete=False)
    return row
