"""Johann Q normal/W-reinforced healing, from restored code and exact wire facts."""
from collections import Counter, defaultdict
try:
    from .requested_skill_hit_rates import _unavailable
    from .skill_wire_order import command_order, event_within_cast, finish_lookup
except ImportError:
    from requested_skill_hit_rates import _unavailable
    from skill_wire_order import command_order, event_within_cast, finish_lookup


def _position(event):
    order=command_order(event)
    return (event['tick'],*order) if order is not None else None


def johann_ally_projectile_metric(spec,starts,finishes,spawns,terminals,heals,
        player,teams,intervals,catalog,skill_rows,effect_rows,evidence_gaps,skill_ids=None,*,actions=None,damages=None,states=None,state_rows=None,state_groups=None):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
        from .skill_team_target_metrics import team_contact_result
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
        from skill_team_target_metrics import team_contact_result
    def fail(reason):
        return {**_unavailable(spec,reason), 'incompleteUsesCountedAsMisses':False,
                'delayedVisualDestroyUsedAsActiveLifetime':False,
                'recallSkillIdUsedAsReinforcement':False,'effectSuffixUsedAsVariant':False}
    if spec.get('characterCode')!=41 or spec.get('skillGroup') not in {1041200,1041210} or spec.get('mode') not in {'ally','any','either-team','fetter'}:
        return fail('unsupported Johann Q metric')
    cohort=spec.get('targetCohort','enemy')
    required={'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdProjectileExplosion','CmdHeal','CmdPlaySkillActionWithTargets'}
    if cohort!='ally':
        required.add('CmdDamage')
        if damages is None:return fail('complete exact Q damage stream required')
    fetter=spec.get('mode')=='fetter'
    state_codes=set()
    if fetter:
        required.update({'CmdAddState','CmdAddStateExtended'})
        groups=[r for r in state_groups or [] if r.get('group')==1041210 and r.get('stateType')=='Fetter']
        state_codes={r['code'] for r in state_rows or [] if r.get('group')==1041210}
        if states is None or len(groups)!=1 or state_codes!=set(range(1041211,1041216)):
            return fail('exact Johann Q dedicated Fetter state stream/definition missing')
    if any(x is None for x in (spawns,terminals,heals,actions,evidence_gaps)) or any(
            g.get('count',0) and g.get('packetName') in required for g in evidence_gaps):
        return fail('complete owned Q spawn, explosion, ordered action and heal streams required')
    if any(catalog.get('skillGroups',{}).get(str(g),{}).get('skillId')!=n for g,n in
           [(1041200,'JohannActive1_1'),(1041210,'JohannActive1_2')]) or not {1041211,1041212}<={r.get('code') for r in effect_rows}:
        return fail('same-version Johann Q skill/heal identity mismatch')
    primary=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1041200]
    recall=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1041210]
    if any(s.get('skillIdCode')!=579 for s in primary) or any(s.get('skillIdCode')!=580 for s in recall):return fail('Q wire skill ID mismatch')
    lives,reason=exact_cast_lifetimes(primary,finishes,player)
    if reason:return fail(reason)
    recalls,reason=exact_cast_lifetimes(recall,finishes,player)
    if reason:return fail(reason)
    lookup=finish_lookup(finishes,player)
    secondary=spec['skillGroup']==1041210
    records=[dict(start=s,end=end,primary=None,back=None,closed=None,reinforced=None,unknown=set(),contact_unknown={'ally':set(),'enemy':set()},contacts=[],enemy=[],fetter=set(),recall=None) for s,end in lives]
    owned=[s for s in spawns if s.get('ownerObjectId')==player and s.get('projectileCode') in {104111,104112}]
    if len({s['projectileObjectId'] for s in owned})!=len(owned):return fail('duplicate owned Q projectile identity')
    by_id={}
    for spawn in owned:
        if spawn['projectileCode']!=104111:continue
        matches=[r for r in records if event_within_cast(r['start'],r['end'],spawn,lookup)]
        if len(matches)!=1:return fail('outbound Q has no unique exact cast')
        r=matches[0]
        if r['primary'] is not None:return fail('multiple outbound Q projectiles in one cast')
        r['primary']=spawn;by_id[spawn['projectileObjectId']]=r
        if _position(spawn) is None:r['unknown'].add('outbound-spawn-order-missing')
    # OnExploreDamage synchronously calls ReturnProjectileLaunch. The unique
    # same-frame owner/explosion pair is required; no nearest or timed window.
    explosions=defaultdict(set);returns=defaultdict(list)
    for t in terminals:
        if t.get('event')=='CmdProjectileExplosion' and t['objectId'] in by_id:explosions[t['tick']].add(t['objectId'])
    for s in owned:
        if s['projectileCode']==104112:returns[s['tick']].append(s)
    for tick,ids in explosions.items():
        if len(ids)!=1 or len(returns[tick])!=1:
            for oid in ids:by_id[oid]['unknown'].add('return-parent-ambiguous-or-absent')
            continue
        r=by_id[next(iter(ids))];s=returns[tick][0]
        if r['back'] is not None:r['unknown'].add('duplicate-return-emission');continue
        r['back']=s;by_id[s['projectileObjectId']]=r
        if _position(s) is None:r['unknown'].add('return-spawn-order-missing')
        matches=[s0 for s0,end in recalls if event_within_cast(s0,end,s,lookup)]
        if len(matches)==1:r['recall']=matches[0]
        elif len(matches)>1:r['unknown'].add('recall-parent-ambiguous')
    for a in actions:
        if a.get('sourceObjectId')!=player or a.get('skillIdCode')!=579 or a.get('actionNo') not in {1,3}:continue
        ts=a.get('targets',[])
        if len(ts)!=1 or ts[0].get('targetObjectId') not in by_id:return fail('reinforce/dead action has no unique owned projectile')
        oid=ts[0]['targetObjectId'];r=by_id[oid];at=_position(a)
        if at is None:r['unknown'].add('reinforce-or-dead-order-missing');continue
        field='reinforced' if a['actionNo']==1 else 'closed';expected=r['primary'] if field=='reinforced' else r['back']
        if expected is None or expected['projectileObjectId']!=oid:r['unknown'].add('action-phase-mismatch');continue
        if r[field] is not None:r['unknown'].add('duplicate-'+field)
        r[field]=at
    for r in records:
        for field in ['primary','back','closed']:
            if r[field] is None:r['unknown'].add('missing-'+field)
        if r['primary'] and r['closed'] and _position(r['primary']):
            if r['closed']<_position(r['primary']):r['unknown'].add('invalid-Q-lifetime')
            if r['reinforced'] and not _position(r['primary'])<=r['reinforced']<=r['closed']:r['unknown'].add('reinforcement-outside-Q-lifetime')
    def in_requested_phase(r,event,side):
        if not secondary:return True
        boundary=r['back'] if side=='ally' else r['recall']
        if boundary is None or _position(boundary) is None:return True
        at=_position(event)
        # Missing order at the boundary remains unknown; a strictly earlier
        # tick can still prove that an application is outside this phase.
        return at>=_position(boundary) if at is not None else event['tick']>=boundary['tick']
    def contact_problem(matches,event,side,reason):
        for r in matches:
            if in_requested_phase(r,event,side):r['contact_unknown'][side].add(reason)
    # Keep structural identity failures separate from application completeness.
    # An ally/union hit needs one exact application; additional ambiguous
    # applications affect target completeness, not an already proven hit.
    # No ambiguous application is assigned a parent.
    for h in (heals if cohort != 'enemy' else []):
        if h.get('effectCode') not in {1041211,1041212}:continue
        target=h.get('targetObjectId');caster=h.get('casterObjectId')
        if target==player or target not in teams or teams.get(player)!=teams[target] or caster in teams and caster!=player:continue
        at=_position(h)
        matches=[r for r in records if r['primary'] and r['primary']['tick']<=h['tick'] and (r['closed'] is None or h['tick']<=r['closed'][0])]
        if at is not None:matches=[r for r in matches if _position(r['primary']) is None or _position(r['primary'])<=at and (r['closed'] is None or at<=r['closed'])]
        if caster!=player or at is None or len(matches)!=1:
            contact_problem(matches,h,'ally','ally-heal-owner-order-or-parent-unresolved')
            if caster==player and not matches:
                contact_problem(records,h,'ally','owned Q heal outside observed Q lifetimes')
            continue
        r=matches[0];reinforced=r['reinforced'] is not None and r['reinforced']<=at
        if not in_requested_phase(r,h,'ally'):continue
        if h['effectCode']!=(1041212 if reinforced else 1041211):
            contact_problem([r],h,'ally','heal-effect-disagrees-with-reinforcement');continue
        phase='outbound' if r['back'] is None or _position(r['back']) is None or at<_position(r['back']) else 'return'
        r['contacts'].append((h['tick'],target,reinforced,phase))
    for d in ((damages or []) if cohort != 'ally' else []):
        if d.get('effectCode') not in {1041201,1041202} or d.get('attackerObjectId')!=player:continue
        target=d.get('targetObjectId')
        if target not in teams or teams[target]==teams.get(player):continue
        at=_position(d)
        matches=[r for r in records if r['primary'] and r['primary']['tick']<=d['tick'] and
                 (r['closed'] is None or d['tick']<=r['closed'][0])]
        if at is not None:matches=[r for r in matches if _position(r['primary']) is None or _position(r['primary'])<=at and (r['closed'] is None or at<=r['closed'])]
        # Nullable damage is the optional numeric payload. The exact CmdDamage
        # target/effect command remains an application; no HP amount is inferred.
        if at is None or len(matches)!=1:
            contact_problem(matches,d,'enemy','enemy-damage-order-or-parent-unresolved')
            if not matches:return fail('owned Q damage outside observed Q lifetimes')
            continue
        r=matches[0];reinforced=r['reinforced'] is not None and r['reinforced']<=at
        if not in_requested_phase(r,d,'enemy'):continue
        if d['effectCode']!=(1041202 if reinforced else 1041201):
            contact_problem([r],d,'enemy','damage-effect-disagrees-with-reinforcement');continue
        r['enemy'].append((d['tick'],target,at))
    if fetter:
        for state in states:
            if state.get('event')!='add' or state.get('stateCode') not in state_codes or state.get('casterObjectId')!=player:continue
            target=state.get('targetObjectId')
            if target not in teams or teams[target]==teams.get(player):continue
            # The dedicated state and exact same tick/target Q damage are both
            # required. No order is invented for legacy unordered state facts.
            matches=[r for r in records if any(t==state['tick'] and o==target for t,o,at in r['enemy'])]
            if len(matches)!=1:return fail('Q Fetter has no unique same-event owned damage parent')
            matches[0]['fetter'].add((state['tick'],target))
    def start(r):return r['recall'] if secondary else r['start']
    def contacts(r):
        if fetter:return r['fetter']
        ally={(t,o) for t,o,_,phase in r['contacts'] if not secondary or phase=='return'}
        enemy={(t,o) for t,o,at in r['enemy'] if not secondary or _position(r['recall']) is not None and at>=_position(r['recall'])}
        return ally if cohort=='ally' else ally|enemy if cohort=='either-team' else enemy
    combat=[r for r in records if (not secondary or r['recall'] is not None) and any(l<=start(r)['tick']<h for l,h in intervals)]
    def contact_reasons(r):return r['contact_unknown']['ally']|r['contact_unknown']['enemy']
    def binary_complete(r):
        if r['unknown']:return False
        if not contact_reasons(r):return True
        return cohort in {'ally','either-team'} and not fetter and bool(contacts(r))
    valid=[r for r in combat if binary_complete(r)]
    partial=[r for r in valid if contact_reasons(r)]
    observed=sum(any(l<=s['tick']<h for l,h in intervals) for s in (recall if secondary else primary))
    try:
        from .requested_skill_hit_rates import _result
    except ImportError:
        from requested_skill_hit_rates import _result
    result_fn=(lambda spec,selected,method,**kw:team_contact_result(spec,selected if cohort=='either-team' else None,selected,method,**kw)) if cohort!='enemy' else _result
    row=(result_fn(spec,[contacts(r) for r in valid],
        'static-Johann-Q-W-reinforcement-owned-return-ordered-heal-and-damage',cast_ticks=[start(r)['tick'] for r in valid])
        if valid or not observed else fail('no complete unambiguous Q use in observed combat casts'))
    row.update(observedCombatCastCount=observed,verifiedCombatCastCount=len(valid),unresolvedCombatCastCount=observed-len(valid),
        unresolvedCastReasons=dict(Counter(reason for r in combat if not binary_complete(r) for reason in r['unknown']|contact_reasons(r))),
        reinforcementMappingStatus='static-body-and-wire-confirmed',normalHealEffectCode=1041211,reinforcedHealEffectCode=1041212,
        normalDamageEffectCode=1041201,reinforcedDamageEffectCode=1041202,
        reinforcementPerAttempt=[r['reinforced'] is not None for r in valid],reinforcementTickPerAttempt=[r['reinforced'][0] if r['reinforced'] else None for r in valid],
        variantBreakdown={name:dict(attemptCount=sum((r['reinforced'] is not None)==flag for r in valid),hitCount=sum(bool(contacts(r)) for r in valid if (r['reinforced'] is not None)==flag)) for name,flag in [('normal',False),('W-reinforced',True)]},
        selfIncluded=False,zeroEffectiveHealingStillCountsAsApplication=True,incompleteUsesCountedAsMisses=False,delayedVisualDestroyUsedAsActiveLifetime=False,
        recallSkillIdUsedAsReinforcement=False,effectSuffixUsedAsVariant=False,fixedDurationWindowUsed=False,nearestCastUsed=False,measuredOutcome='ally-skill-effect-application' if cohort=='ally' else 'enemy-damage' if cohort=='enemy' else 'per-cast-enemy-and-ally-union',
        perUseCompletenessTracked=True,outcomeScope='fully linked Q uses with complete binary outcomes; ally/union positives may retain incomplete target details',
        phaseScope=('return ally applications and explosion damage after explicit recall' if secondary else 'outbound and directly linked return'),
        reinforcementVariantMeaning='a cast is W-reinforced when its exact reinforcement action occurs; contacts keep actual times',
        evidenceReview='deliverables/johann-q-static-branch-proof-v2.json')
    if partial:
        row.update(targetCountsAreLowerBounds=True,completeTargetCountsAvailable=False,
            multiTargetStatus='confirmed-contact-lower-bounds',
            binaryPositiveWithIncompleteCohortCount=len(partial),
            incompleteCohortEvidence=[dict(castTick=start(r)['tick'],reasons=sorted(contact_reasons(r))) for r in partial])
    return row
