"""Static producer links from an owned explosion to its actual target state."""
from collections import defaultdict,Counter
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import event_within_cast,finish_lookup
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_action_stage_evidence import load_exact_skill_ids
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import event_within_cast,finish_lookup


# NathaponActive4.Start binds ProjectileProperty.onExplosionHitTarget (+88).
# Its callback creates the state from NathaponSkillActive4Data.StatisStateCode
# (+18), whose constructor stores 1034431. Neither code is a numeric Skill FK.
ROUTES={(34,1034500):dict(skill='NathaponActive4',projectile=103451,
    prefab='Projectile_FX_BI_Nathapon_PanningShot',state=1034431,stateGroup=1034430,stateType='Stasis')}


def explosion_state_metric(spec,starts,finishes,spawns,terminals,states,player,teams,intervals,
                           catalog,projectile_rows,state_rows,state_groups,gaps,skill_ids=None):
    cfg=ROUTES.get((spec.get('characterCode'),spec.get('skillGroup')))
    def fail(reason):return _unavailable(spec,reason)
    if not cfg or spec.get('unit')!='skill-cast' or spec.get('mode')!='any':return fail('unsupported explosion-state metric')
    required={'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdProjectileExplosion','CmdAddState','CmdAddStateExtended'}
    if any(x is None for x in (spawns,terminals,states,gaps)) or any(g.get('count',0) and g.get('packetName') in required for g in gaps):
        return fail('complete explosion and target-state streams required')
    pd=[p for p in projectile_rows if p.get('code')==cfg['projectile']]
    sd=[s for s in state_rows if s.get('code')==cfg['state']]
    sg=[s for s in state_groups if s.get('group')==cfg['stateGroup']]
    if (catalog['skillGroups'].get(str(spec['skillGroup']),{}).get('skillId')!=cfg['skill'] or
        len(pd)!=1 or pd[0].get('prefabName')!=cfg['prefab'] or pd[0].get('isExplosionWithoutCollision') is not True or
        len(sd)!=1 or sd[0].get('group')!=cfg['stateGroup'] or len(sg)!=1 or
        sg[0].get('stateType')!=cfg['stateType'] or sg[0].get('notCheckCasterId') is not False):
        return fail('static producer identity differs from exact gameDb')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    wire=ids.get(cfg['skill']);own=[s for s in starts if s.get('skillGroup')==spec['skillGroup']]
    if type(wire) is not int or any(s.get('skillIdCode')!=wire for s in own):return fail('exact R wire skill identity missing')
    records,reason=ordered_cast_records(own,finishes,player,allow_same_tick_finishes=True)
    if reason:return fail(reason)
    finish_orders=finish_lookup(finishes,player);by_cast=defaultdict(list);unknown=defaultdict(set)
    for i,r in enumerate(records):
        if not r['complete'] or r['finish'].get('reason')!=0:unknown[i].add('parent-not-normally-complete')
    owned=[s for s in spawns if s.get('ownerPlayerObjectId')==player and s.get('projectileCode')==cfg['projectile']]
    if len({s['projectileObjectId'] for s in owned})!=len(owned):return fail('projectile object has multiple spawn identities')
    for shot in owned:
        candidates=[i for i,r in enumerate(records) if r['complete'] and
            event_within_cast(r['start'],r['finish']['tick'],shot,finish_orders)]
        if len(candidates)!=1:return fail('explosion projectile has no unique closed parent')
        by_cast[candidates[0]].append(shot)
    explosions=defaultdict(set)
    for event in terminals:
        if event.get('event')=='CmdProjectileExplosion':explosions[event['objectId']].add(event['tick'])
    by_explosion=defaultdict(set)
    for i,r in enumerate(records):
        if len(by_cast[i])!=1:unknown[i].add('expected-one-owned-projectile');continue
        shot=by_cast[i][0];ticks=explosions[shot['projectileObjectId']]
        if len(ticks)!=1:unknown[i].add('explosion-missing-or-conflicting');continue
        tick=next(iter(ticks))
        if type(tick) is not int or tick<shot['tick']:unknown[i].add('explosion-before-spawn');continue
        by_explosion[tick].add(i)
    for tick,owners in by_explosion.items():
        if len(owners)>1:
            for i in owners:unknown[i].add('simultaneous-owned-explosions')
    contacts=[set() for _ in records]
    for state in states:
        if state.get('event')!='add' or state.get('stateCode')!=cfg['state'] or state.get('casterObjectId')!=player:continue
        target=state.get('targetObjectId')
        if target not in teams or teams[target]==teams[player]:continue
        candidates=by_explosion.get(state['tick'],set())
        if not candidates:return fail('R stasis state lies outside every exact owned explosion')
        if len(candidates)!=1:continue
        i=next(iter(candidates));contacts[i].add((state['tick'],target))
    combat=[i for i,r in enumerate(records) if any(l<=r['start']['tick']<end for l,end in intervals)]
    valid=[i for i in combat if not unknown[i]]
    diag=dict(perUseCompletenessTracked=True,observedCombatCastCount=len(combat),verifiedCombatCastCount=len(valid),
        unresolvedCombatCastCount=len(combat)-len(valid),unresolvedCastReasons=dict(Counter(k for i in combat for k in unknown[i])),
        incompleteUsesCountedAsMisses=False,measuredOutcome='enemy-stasis-application',damageUsedAsHitProof=False,
        exactProjectileCode=cfg['projectile'],exactStateCode=cfg['state'],fixedDurationWindowUsed=False)
    if combat and not valid:return {**fail('no complete unambiguous explosion-state uses'),**diag}
    row=_result(spec,[contacts[i] for i in valid],'static-owned-projectile-explosion-and-exact-target-Stasis',
        cast_ticks=[records[i]['start']['tick'] for i in valid])
    row.update(diag)
    return row
