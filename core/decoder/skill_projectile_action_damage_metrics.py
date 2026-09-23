"""Delayed damage whose execution command names its exact projectile object."""
from collections import Counter, defaultdict
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order
    from skill_action_stage_evidence import load_exact_skill_ids


def projectile_action_damage_metric(spec, starts, finishes, spawns, terminals, actions,
                                   damages, player, teams, intervals, catalog,
                                   skill_rows, effect_rows, gaps, skill_ids=None, *,
                                   states=None,state_scripts=None,state_groups=None,state_rows=None):
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==spec['skillGroup']]
    combat_start=lambda s:any(l<=s['tick']<r for l,r in intervals)
    diag=dict(observedCastCount=len(selected),observedCombatCastCount=sum(map(combat_start,selected)))
    def fail(reason):return {**_unavailable(spec,reason),**diag}
    if (spec.get('characterCode'),spec['skillGroup'],spec.get('mode'),spec.get('unit'))!=(48,1048700,'any','skill-cast'):
        return fail('unsupported projectile-targeted damage action')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    wire=ids.get('TiaActive4');codes={r['code'] for r in skill_rows if r.get('group')==1048700}
    projectile=catalog['projectileDefinitions'].get('104804',{})
    if (type(wire) is not int or not codes or player not in teams
            or catalog['skillGroups'].get('1048700',{}).get('skillId')!='TiaActive4'
            or projectile.get('prefabName')!='Projectile_FX_BI_Tia_Skill04_Trail'
            or projectile.get('collisionEnabled') is not False
            or [r.get('effectPrefabName') for r in effect_rows if r.get('code')==1048107]!=['FX_BI_Tia_Skill04_Rainbow_Hit']):
        return fail('pinned projectile/action/damage identities differ')
    required={'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdDestroy','CmdDestroyDelayStart','CmdDamage','CmdPlaySkillAction','CmdPlaySkillActionWithTargets'}
    if gaps is None or any(g.get('count',0) and g.get('packetName') in required for g in gaps):
        return fail('complete cast/object/action/damage streams required')
    if not selected:return fail('no observed casts')
    if any(s['skillIdCode']!=wire or s['skillCode'] not in codes for s in selected):return fail('wire skill identity mismatch')
    records,reason=ordered_cast_records(selected,finishes,player,allow_same_tick_finishes=True)
    if reason:return fail(reason)
    unknown={i:'cast-not-normally-complete' for i,r in enumerate(records)
             if not r['complete'] or r['finish'] is None or r['finish'].get('reason')!=0}
    objects={};by_cast=defaultdict(list)
    for spawn in spawns:
        if spawn['ownerPlayerObjectId']!=player or spawn['projectileCode']!=104804:continue
        if spawn.get('ownerObjectId')!=player:return fail('projectile wire owner is not the caster')
        at=command_order(spawn)
        if at is None:return fail('projectile spawn command order missing')
        owners=[i for i,r in enumerate(records) if command_order(r['start'])<at
                and spawn['tick']>=r['start']['tick'] and (r['finish'] is None or
                (at<command_order(r['finish']) and spawn['tick']<=r['finish']['tick']))]
        if len(owners)!=1:return fail('projectile not owned by exactly one recorded cast')
        oid=spawn['projectileObjectId'];i=owners[0]
        if oid in objects:return fail('duplicate projectile identity')
        objects[oid]=(i,spawn);by_cast[i].append(oid)
    markers=defaultdict(list)
    for action in actions:
        if action.get('sourceObjectId')!=player or action.get('skillIdCode')!=wire or action.get('actionNo')!=6003:continue
        ts=action.get('targets',[])
        if action.get('wireStatus')!='decoded-exact-CmdPlaySkillActionWithTargets' or len(ts)!=1 or ts[0].get('targetObjectId') not in objects:
            return fail('execution action does not name an owned projectile')
        markers[ts[0]['targetObjectId']].append(action)
    phases=defaultdict(list)
    from .skill_projectile_active_end import projectile_active_end_records
    active_ends=projectile_active_end_records(terminals)
    for i,r in enumerate(records):
        if len(by_cast[i])!=1:
            unknown[i]='missing-or-multiple-static-single-projectile';continue
        oid=by_cast[i][0];spawn=objects[oid][1]
        # Cache views may both report the same destruction. Distinct object/
        # event/frame boundaries matter here, not duplicate representations.
        active=active_ends.get(oid,{})
        aa=markers[oid]
        if len(aa)!=1 or spawn['tick']>aa[0]['tick']:
            unknown[i]='missing-or-ambiguous-execution-and-destruction';continue
        phases[aa[0]['tick']].append(i)
        if not active.get('complete') or aa[0]['tick']>active['endTick']:
            unknown[i]='missing-or-ambiguous-destruction'
    contacts=[set() for _ in records]
    for tick,owners in phases.items():
        if len(owners)>1:
            for i in owners:unknown[i]='multiple-projectile-damage-phases-in-one-frame'
    for damage in damages:
        if damage.get('attackerObjectId')!=player or damage.get('effectCode')!=1048107:continue
        owners=phases.get(damage['tick'],[])
        if not owners:return fail('dedicated damage outside recorded execution phase')
        if len(owners)!=1:continue
        i=owners[0]
        if type(damage.get('damageIsNull')) is not bool:
            unknown[i]='missing-damage-discriminator';continue
        target=damage.get('targetObjectId')
        if target in teams and teams[target]!=teams[player]:contacts[i].add((damage['tick'],target))
    combat=[i for i,r in enumerate(records) if combat_start(r['start'])]
    valid=[i for i in combat if i not in unknown]
    diag.update(unresolvedCombatCastCount=len(combat)-len(valid),
                unresolvedCastReasons=dict(Counter(unknown[i] for i in combat if i in unknown)),
                perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False)
    if combat and not valid:return fail('no complete projectile execution uses')
    row=_result(spec,[contacts[i] for i in valid],'static-projectile-targeted-action-synchronous-damage',
                cast_ticks=[records[i]['start']['tick'] for i in valid])
    row.update(diag,exactProjectileCodes=[104804],candidateEffectCodes=[1048107],executionActionNo=6003,
               executionActionTargetMeaning='projectile object ID, never an enemy contact',
               fixedDurationWindowUsed=False,damageAmountInferred=False,
               evidenceReview='deliverables/tia-r-projectile-action-producer-proof-v1.json')
    try:
        from .skill_tia_r_details import tia_r_contact_details
    except ImportError:
        from skill_tia_r_details import tia_r_contact_details
    row['contactDetailsByAttempt']=[tia_r_contact_details(contacts[i],damages,states,state_scripts,
        player,state_groups,gaps,skill_ids,state_rows) for i in valid]
    row['contactDetailMeaning']='own-caster color presence immediately before damage; separately observed stun addition, not actual CC duration'
    return row
