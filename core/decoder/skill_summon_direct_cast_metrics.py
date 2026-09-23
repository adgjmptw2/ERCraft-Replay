"""Explicit summon skill uses with their own actor and exact finish commands.

Only the requested child metric is measured. A child use never substitutes for
the parent's denominator. CmdDamage's nullable amount is a wire optimization,
not absence of contact; the explicit nullable discriminator must be retained.
"""
from collections import defaultdict, Counter
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids
    from .skill_summon_ownership import live_summon_owner_resolver
    from .skill_wire_order import finish_lookup, event_within_cast, command_order
    from .skill_attempt_timing import exact_outcome
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_action_stage_evidence import load_exact_skill_ids
    from skill_summon_ownership import live_summon_owner_resolver
    from skill_wire_order import finish_lookup, event_within_cast, command_order
    from skill_attempt_timing import exact_outcome

ROUTES={
    1040250:dict(character=40,name='NinaActive1',summon=1191,prefab='Chloe_Nina',
                 effect=1040011,effectPrefab='FX_BI_Nina_Skill01_Hit'),
    1040450:dict(character=40,name='NinaActive3',summon=1191,prefab='Chloe_Nina',
                 effect=1040032,effectPrefab='FX_BI_Nina_Skill03_Dash_Hit'),
}


def summon_direct_cast_metric(spec, nonplayer_starts, finishes, summons, terminals,
                              damages, player, teams, intervals, catalog, skill_rows,
                              summon_rows, effect_rows, skill_ids=None, *, evidence_gaps=None,
                              states=None,state_rows=None,state_groups=None):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    cfg=ROUTES.get(spec['skillGroup'])
    diag=dict(observedOwnedChildCastCount=0,observedCombatChildCastCount=0,
              childCastsCountedAsIndependentUses=True,parentUsesInferred=False)
    def fail(reason):return {**_unavailable(spec,reason),**diag}
    if (not cfg or spec.get('characterCode')!=cfg['character'] or
            spec.get('unit')!='skill-cast' or spec.get('mode')!='any'):
        return fail('unsupported independent summon skill metric')
    definition=catalog['skillGroups'].get(str(spec['skillGroup']),{})
    sr=[r for r in summon_rows if r.get('code')==cfg['summon']]
    fx=[r for r in effect_rows if r.get('code')==cfg['effect']]
    if (definition.get('skillId')!=cfg['name'] or definition.get('characterCode')!=cfg['character'] or
            len(sr)!=1 or sr[0].get('prefabPath')!=cfg['prefab'] or sr[0].get('useAttackerType')!='MySelf' or
            len(fx)!=1 or fx[0].get('effectPrefabName')!=cfg['effectPrefab']):
        return fail('summon actor, skill and effect identities differ from exact gameDb')
    if nonplayer_starts is None:return fail('non-player skill starts were not retained')
    if evidence_gaps is None or any(g.get('count',0) and g.get('packetName') in
            {'CmdStartSkill','CmdFinishSkill','CmdDamage','CmdSpawn','CmdDestroy','SummonSnapshot:11'}
            for g in evidence_gaps):
        return fail('required child evidence stream completeness is unavailable')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    wire=ids.get(cfg['name']);code_groups={r['code']:r['group'] for r in skill_rows}
    if type(wire) is not int:return fail('exact child SkillId enum missing')
    resolve=live_summon_owner_resolver(summons,terminals,set(teams))
    by_actor=defaultdict(list)
    for start in nonplayer_starts:
        if start['skillIdCode']!=wire and code_groups.get(start['skillCode'])!=spec['skillGroup']:continue
        owner,path,reason=resolve(start['sourceObjectId'],start['tick'])
        if reason:return fail('child start owner unavailable: '+reason)
        if owner!=player:continue
        if (path!=[cfg['summon']] or start['skillIdCode']!=wire or
                code_groups.get(start['skillCode'])!=spec['skillGroup']):
            return fail('child wire skill and direct summon owner disagree')
        by_actor[start['sourceObjectId']].append({**start,'playerObjectId':start['sourceObjectId'],
                                               'skillGroup':spec['skillGroup']})
    diag['observedOwnedChildCastCount']=sum(map(len,by_actor.values()))
    diag['observedCombatChildCastCount']=sum(any(l<=s['tick']<r for l,r in intervals)
                                           for ss in by_actor.values() for s in ss)
    if not by_actor:return fail('no exact owned child starts')
    lives=[]
    for actor,starts in by_actor.items():
        local,reason=exact_cast_lifetimes(starts,finishes,actor)
        if reason:return fail('child lifetime: '+reason)
        for start,end in local:
            ends=[f for f in finishes if f['playerObjectId']==actor and f['skillIdCode']==wire and f['tick']==end]
            if len(ends)!=1 or ends[0].get('reason') not in set(range(15))|{16,17}:
                return fail('child finish is not an explicit gameplay completion or cancellation')
            owner,path,reason=resolve(actor,end)
            if reason or owner!=player or path!=[cfg['summon']]:return fail('child owner changed before finish')
            lives.append((start,end))
    lookup={actor:finish_lookup(finishes,actor) for actor in by_actor}
    contacts=[set() for _ in lives];unknown=set();packets=Counter()
    # Only NotCancel is a completed skill use. Keep other lifetimes as
    # attribution candidates so their damage cannot be stolen by a later use.
    for i,(start,end) in enumerate(lives):
        ends=[f for f in finishes if f['playerObjectId']==start['sourceObjectId'] and
              f['skillIdCode']==wire and f['tick']==end]
        if type(ends[0].get('reason')) is not int or ends[0]['reason']!=0:unknown.add(i)
    for damage in damages:
        actor=damage['attackerObjectId'];target=damage['targetObjectId']
        if damage.get('effectCode')!=cfg['effect'] or target not in teams or teams[target]==teams[player]:continue
        if actor in teams:
            if actor==player:return fail('child effect is also emitted by the player actor')
            continue
        owner,path,reason=resolve(actor,damage['tick'])
        if reason:return fail('child effect actor ownership unavailable: '+reason)
        if owner!=player:continue
        if path!=[cfg['summon']] or actor not in by_actor:return fail('child effect has no explicit skill start')
        candidates=[i for i,(s,end) in enumerate(lives) if s['sourceObjectId']==actor and
                    event_within_cast(s,end,damage,lookup[actor])]
        if len(candidates)!=1:return fail('child effect lies outside one exact child lifetime')
        i=candidates[0]
        # WorldCharacter.Damage omits the integer when it equals the HP
        # delta (RVA 2FCF684..2FCF6A9). LocalCharacter.AddDamage reconstructs
        # it (2812F31..2812F51). The decoded packet still proves this exact
        # target/effect contact. Do not synthesize a numeric damage amount.
        if type(damage.get('damageIsNull')) is not bool:
            unknown.add(i);packets['missingDamageDiscriminatorPackets']+=1
        else:
            contacts[i].add((damage['tick'],target))
            packets['implicitHpDeltaDamagePackets' if damage['damageIsNull'] else 'explicitDamageAmountPackets']+=1
    combat=[i for i,(s,_) in enumerate(lives) if any(l<=s['tick']<r for l,r in intervals)]
    valid=[i for i in combat if i not in unknown]
    diag.update(exactChildLifetimeCount=len(lives),unresolvedCombatCastCount=sum(i in unknown for i in combat),
                **dict(packets))
    if combat and not valid:return fail('all observed combat child uses remain unresolved')
    method='static-Nina-MySelf-child-dedicated-damage-command'
    row=_result(spec,[contacts[i] for i in valid],method)
    row.update(diag,outcomes=[exact_outcome(lives[i][0]['tick'],lives[i][0]['tick'],contacts[i]) for i in valid],
        denominatorMeaning='actual explicitly recorded child skill casts; parent uses are not inferred',
        outcomeScope='normally completed independent child casts; missing wire discriminator and cancelled uses remain unknown',
        damageAmountRequiredForContact=False,damageAmountInferred=False,
        evidenceReview='deliverables/cmd-damage-nullable-semantics-proof-v1.json',
        perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,
        exactSummonCode=cfg['summon'],candidateEffectCodes=[cfg['effect']])
    return row
