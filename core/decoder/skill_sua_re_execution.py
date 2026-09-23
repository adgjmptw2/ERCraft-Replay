"""Normal and copied Don Quixote share native launch/contact/no-contact actions."""
from collections import Counter
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order as order
    from .skill_attempt_timing import exact_outcome
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order as order
    from skill_attempt_timing import exact_outcome


def sua_re_execution(spec,starts,finishes,actions,player,teams,intervals,catalog,gaps):
    group=spec['skillGroup']
    profiles={1028400:('SuaActive3',402,(1028401,1028402,1028403,1028404,1028405)),
              1028530:('SuaActive4_4',407,(1028531,1028532,1028533))}
    starts=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==group]
    combat=lambda e:any(a<=e['tick']<b for a,b in intervals)
    diag=dict(observedCastCount=len(starts),observedCombatCastCount=sum(map(combat,starts)))
    fail=lambda why:{**_unavailable(spec,why),**diag}
    if group not in profiles or (spec['characterCode'],spec['mode'],spec['unit'])!=(28,'any','skill-cast'):
        return fail('unsupported Sua R-E scope')
    skill,wire,codes=profiles[group]
    if player not in teams or actions is None or gaps is None:return fail('missing Sua R-E actions/teams')
    if catalog.get('skillGroups',{}).get(str(group),{}).get('skillId')!=skill or any(s['skillIdCode']!=wire or s['skillCode'] not in codes for s in starts):
        return fail('pinned Sua R-E identity mismatch')
    required={'CmdStartSkill','CmdFinishSkill','CmdPlaySkillAction','CmdPlaySkillActionWithTargets'}
    if any(g.get('count',0) and g.get('packetName') in required for g in gaps):return fail('incomplete R-E command stream')
    records,why=ordered_cast_records(starts,finishes,player,allow_same_tick_finishes=True)
    if why:return fail(why)
    markers=[a for a in actions if a.get('sourceObjectId')==player and a.get('skillIdCode')==wire]
    if any(order(a) is None or a.get('wireStatus') not in ('decoded-exact-CmdPlaySkillAction','decoded-exact-CmdPlaySkillActionWithTargets') for a in markers):
        return fail('R-E requires exact action command order')
    if len({order(a) for a in markers})!=len(markers):return fail('duplicate R-E action identity')
    uses=[];unknown={};cancelled=[];assigned=set()
    for i,r in enumerate(records):
        s,f=r['start'],r['finish'];aa=[a for a in markers if order(s)<order(a) and (f is None or order(a)<order(f))]
        assigned.update(order(a) for a in aa)
        if not aa and r['complete'] and f['reason']==3:cancelled.append(i);continue
        launches=[a for a in aa if a['actionNo']==1];outcomes=[a for a in aa if a['actionNo'] in (3,4,5)]
        # The main Execute coroutine directly yields DonQuixote.Process.
        # OnFinishEvent removes that exact routine on gameplay death; there
        # is no detached collision producer to outlive this boundary.
        # Keep the actual finish record, never manufacture an action5 packet.
        if (len(aa)==1 and len(launches)==1 and not launches[0].get('targets')
                and r['complete'] and f['reason']==3):
            uses.append(dict(index=i,start=s,launch=launches[0],outcome=f,contacts=set(),
                closure='native-main-coroutine-removed-on-death'))
            continue
        if len(aa)!=2 or len(launches)!=1 or len(outcomes)!=1 or order(launches[0])>=order(outcomes[0]):
            unknown[i]='missing unique launch and terminal contact/no-contact action';continue
        launch,end=launches[0],outcomes[0]
        # Process starts after the generic casting delay. The actual action1
        # timestamp is the execution time; never force it onto CmdStartSkill.
        if launch.get('targets'):
            unknown[i]='R-E launch action unexpectedly has targets';continue
        ts=end.get('targets',[]);contacts=set()
        if end['actionNo']==5:
            if ts:unknown[i]='no-contact action unexpectedly has targets';continue
        else:
            if len(ts)!=1 or type(ts[0].get('targetObjectId')) is not int or ts[0]['targetObjectId']<=0:
                unknown[i]='contact action lacks exactly one actual target';continue
            target=ts[0]['targetObjectId']
            if target in teams and teams[target]!=teams[player]:contacts.add((end['tick'],target))
        uses.append(dict(index=i,start=s,launch=launch,outcome=end,contacts=contacts))
    if assigned!={order(a) for a in markers}:return fail('orphan R-E action outside its exact cast')
    chosen=[u for u in uses if combat(u['start'])]
    result=_result(spec,[u['contacts'] for u in chosen],'static-Sua-RE-action1-launch-action3-4-contact-action5-no-contact',cast_ticks=[u['start']['tick'] for u in chosen])
    result['outcomes']=[exact_outcome(u['start']['tick'],u['launch']['tick'],u['contacts']) for u in chosen]
    result.update(diag,executionEvidenceByAttempt=[{k:v for k,v in u.items() if k!='contacts'} for u in chosen],
        gameplayDeathClosedCastCount=sum('closure' in u for u in chosen),
        gameplayDeathClosureProof='deliverables/sua-main-coroutine-death-closure-v1.json',
        unresolvedCombatCastCount=sum(combat(records[i]['start']) for i in unknown),
        unresolvedNonCombatCastCount=sum(not combat(records[i]['start']) for i in unknown),
        unresolvedCastReasons=dict(Counter(v for i,v in unknown.items() if combat(records[i]['start']))),
        unknownUseEvidence=[dict(cast=records[i],reason=v) for i,v in unknown.items()],
        nonExecutedCastCount=sum(combat(records[i]['start']) for i in cancelled),nonExecutedAllCastCount=len(cancelled),
        perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,damageEffectGuessed=False,
        meaning='Enemy contact selected by the native collision branch; does not claim positive HP loss, actual CC application or healing.',
        evidenceReview=('deliverables/sua-don-quixote-shared-contract-v1.json' if group==1028400 else 'deliverables/sua-re-actions-static-proof-v1.json'))
    return result
