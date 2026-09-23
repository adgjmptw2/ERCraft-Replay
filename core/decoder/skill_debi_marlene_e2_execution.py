"""Marlene-to-Debi E: actual dash execution and its explicit damage parameter.

The constructor sets DebiDamageParameterData's effect code1065202. Its separate
Airborne1065421 can be immuned, so missing crowd control never erases damage.
"""
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


def debi_marlene_e2_execution(spec, starts, finishes, player, teams, intervals, catalog, inputs, skill_ids=None):
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1065410]
    combat=lambda e:any(a<=e['tick']<b for a,b in intervals)
    diag=dict(observedCastCount=len(selected),observedCombatCastCount=sum(map(combat,selected)))
    fail=lambda why:{**_unavailable(spec,why),**diag}
    if (spec['characterCode'],spec['skillGroup'],spec['mode'],spec['unit'])!=(65,1065410,'any','skill-cast'):
        return fail('unsupported Debi/Marlene E2 scope')
    if player not in teams or any(inputs.get(k) is None for k in ('actions','movement','damages','gaps')):
        return fail('missing ordered Debi/Marlene E2 execution facts')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    definition=catalog.get('skillGroups',{}).get('1065410',{})
    if ids.get('DebiMarleneActive3_2')!=980 or definition.get('skillId')!='DebiMarleneActive3_2':
        return fail('Debi/Marlene E2 pinned identity differs')
    if any(s['skillIdCode']!=980 or s['skillCode'] not in range(1065411,1065416) for s in selected):
        return fail('Debi/Marlene E2 wire/group mismatch')
    required={'CmdStartSkill','CmdFinishSkill','CmdMoveStraight','CmdDamage','CmdPlaySkillAction','CmdPlaySkillActionWithTargets'}
    if any(g.get('count',0) and g.get('packetName') in required for g in inputs['gaps']):
        return fail('incomplete Debi/Marlene E2 command stream')
    records,why=ordered_cast_records(selected,finishes,player,allow_same_tick_finishes=True)
    if why:return fail(why)
    own_actions=[a for a in inputs['actions'] if a['sourceObjectId']==player and a['skillIdCode']==980]
    own_damage=[d for d in inputs['damages'] if d['attackerObjectId']==player and d.get('effectCode')==1065202]
    own_moves=[m for m in inputs['movement'] if m['objectId']==player and m['event']=='CmdMoveStraight']
    unknown={};uses=[];cancelled=[]
    for i,rec in enumerate(records):
        start,end=rec['start'],rec['finish']
        if not rec['complete'] or end is None:
            unknown[i]=rec['reason'];continue
        inside=lambda e:order(e) is not None and order(start)<order(e)<order(end)
        rows=[e for e in [*own_actions,*own_damage,*own_moves] if start['tick']<=e['tick']<=end['tick']]
        if any(order(e) is None for e in rows):
            unknown[i]='missing E2 execution command order';continue
        actions=[a for a in own_actions if inside(a)]
        moves=[m for m in own_moves if inside(m)]
        damage=[d for d in own_damage if inside(d)]
        # No movement and no E2-specific attack marker/damage proves only an
        # actual pre-execution cancellation, never an attempted miss.
        if end.get('reason') in (2,3) and not moves and not actions and not damage:
            cancelled.append(i);continue
        markers=[a for a in actions if a['actionNo']==2 and a.get('targets')==[]
                 and a.get('wireStatus')=='decoded-exact-CmdPlaySkillAction']
        if len(markers)!=1:
            unknown[i]='E2 dash execution marker/movement is not unique';continue
        marker=markers[0]
        execution_moves=[m for m in moves if m.get('ease')==1 and m['tick']==marker['tick'] and order(m)>order(marker)]
        phase6=[a for a in actions if a['actionNo']==6 and a.get('targets')==[]
                and a.get('wireStatus')=='decoded-exact-CmdPlaySkillAction' and order(a)>order(marker)]
        stationary_execution=(not execution_moves and end.get('reason')==0 and len(phase6)==1)
        if len(execution_moves)!=1 and not stationary_execution:
            unknown[i]='E2 movement differs from completed casting frame';continue
        move=execution_moves[0] if execution_moves else None
        # Process ignores Move's return value and runs its timed collision
        # loop even if movement is suppressed. Its delayed action6 plus normal
        # finish proves that execution; a foreign earlier move is not E's dash.
        execution=move if move is not None else marker
        # Attack-speed-dependent casting completes at action2, then Process
        # emits its real movement and enters the collision loop in that frame.
        if move is not None and (marker['tick']!=move['tick'] or not order(marker)<order(move)):
            unknown[i]='E2 movement differs from completed casting frame';continue
        if any(a['actionNo'] not in (2,6) for a in actions) or sum(a['actionNo']==6 for a in actions)>1:
            unknown[i]='unreviewed E2 action branch';continue
        if type(end.get('reason')) is not int or end['reason'] not in (0,2,3):
            unknown[i]='unreviewed E2 termination reason';continue
        competitors=[s for s in starts if s['playerObjectId']==player and s['skillGroup']!=1065410
                     and execution['tick']<=s['tick']<=end['tick']]
        if any(order(s) is None or (inside(s) and order(s)>order(execution)) for s in competitors):
            unknown[i]='competing manual cast during E2 dash';continue
        if any(order(d)<=order(execution) or d['tick']<execution['tick'] or d.get('damageType')!=2
               or type(d.get('damageIsNull')) is not bool for d in damage):
            unknown[i]='E2 damage lacks exact execution/type/discriminator';continue
        if len({d['targetObjectId'] for d in damage})!=len(damage):
            unknown[i]='duplicate E2 damage contradicts cachedEnemy guard';continue
        if end['tick']==execution['tick'] and end['reason']!=0 and not damage:
            unknown[i]='same-frame cancellation may precede E2 collision query';continue
        contacts={(d['tick'],d['targetObjectId']) for d in damage
                  if d['targetObjectId'] in teams and teams[d['targetObjectId']]!=teams[player]}
        cc=[s for s in inputs.get('states',[]) if s.get('casterObjectId')==player and s.get('stateCode')==1065421
            and s.get('event')=='add' and any(s['tick']==d['tick'] and s['targetObjectId']==d['targetObjectId'] for d in damage)]
        uses.append((i,start,execution,contacts,dict(lifetime=rec,castingCompleted=marker,movement=move,
            noDashMovementCommand=move is None,delayedPhaseActions=phase6,
            otherMovementCommands=[m for m in moves if m is not move],damageCommands=damage,airborneCorroboration=cc)))
    chosen=[u for u in uses if combat(u[1])]
    row=_result(spec,[u[3] for u in chosen],'static-Debi-Marlene-E2-ordered-dash-explicit-effect',cast_ticks=[u[1]['tick'] for u in chosen])
    reasons=Counter(why for i,why in unknown.items() if combat(records[i]['start']))
    row.update(diag,outcomes=[exact_outcome(u[1]['tick'],u[2]['tick'],u[3]) for u in chosen],
        unresolvedCombatCastCount=sum(reasons.values()),unresolvedCastReasons=dict(reasons),
        unresolvedNonCombatCastCount=sum(not combat(records[i]['start']) for i in unknown),
        nonExecutedCastCount=sum(combat(records[i]['start']) for i in cancelled),nonExecutedAllCastCount=len(cancelled),
        executionEvidenceByAttempt=[u[4] for u in chosen],
        noDashMovementCommandCount=sum(u[4]['noDashMovementCommand'] for u in chosen),
        perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,fixedDurationWindowUsed=False,
        damageAmountInferred=False,airborneRequiredForHit=False,
        denominatorMeaning='Actual Marlene-to-Debi E collision-phase executions, including normal executions with movement suppressed; explicit pre-execution cancellations are separate',
        evidenceReview='deliverables/debi-marlene-e2-static-proof-v1.json')
    return row
