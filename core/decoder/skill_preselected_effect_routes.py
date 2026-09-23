"""Direct execution of existing effect routes named in reviewed request records.

Candidate effect mappings remain candidates. Complete zero-hit samples may
use the explicit development policy; missing or ambiguous data never causes
a projectile/action retry or confers new completion credit.
"""
from .skill_static_effect_families import shared_effect_lifetime_metric
from .skill_effect_lifetimes import effect_lifetime_metric
from .skill_projectile_outcomes import explosion_contact_metric
from .requested_skill_hit_rates import _unavailable, _projectile_contacts, _corroborated_action_contacts
from .skill_cancelled_projectiles import direct_cast_with_cancellations


def preselected_zahir_r_metric(spec,selected,starts,spawns,collisions,damages,
                              teams,player,catalog,intervals,window,finishes,effect_rows,route_inputs,gaps=None):
    from .skill_delayed_projectile_lifetimes import zahir_r_lifetime_metric
    from .skill_development_effect_metrics import development_policy
    required={'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdDamage','CmdDestroy'}
    if (gaps is None or any(g.get('count',0) and (g.get('packetName') in required or
             str(g.get('packetName','')).startswith('CmdProjectile')) for g in gaps)
            or any(route_inputs.get(k) is None for k in ('projectile_terminals','damages','effect_rows','spawns','collisions'))):
        return _unavailable(spec,'Zahir R requires its recorded marker, terminal and damage streams')
    policy=development_policy()
    zero=policy if policy['enabled'] and policy.get('reuseReviewedCandidateRulesWithoutPositiveSamples') else None
    return zahir_r_lifetime_metric(spec,selected,starts,spawns,collisions,
        route_inputs['projectile_terminals'],damages,teams,player,catalog,intervals,window,finishes,effect_rows,zero_hit_policy=zero)


def fixed_projectile_candidates(spec, legacy, catalog):
    """Reject declared weapon-prefab candidates from character skill routes.

    Broad family-name candidates are not stage mappings. Preserve existing
    stage evidence unless the exact-version prefab explicitly says WSkill.
    This is input classification, before any result is evaluated.
    """
    from .skill_execution_plan import planned_candidate_review,planned_family_candidates
    review=planned_candidate_review(spec)
    family=planned_family_candidates(spec,'projectileCodes') if review else None
    if family and family[1]:
        return dict(legacy,projectileCodeCandidateEvidence=dict(
            authority='compiled-reviewed-primary-projectiles',staticCandidates=sorted(family[1]),
            runtimeExclusiveCandidates=sorted(family[1]),producerReview=review))
    original=legacy.get('projectileCodeCandidateEvidence',{})
    runtime=original.get('runtimeExclusiveCandidates') or []
    rejected=[code for code in runtime if '_WSkill_' in
              str(catalog.get('projectileDefinitions',{}).get(str(code),{}).get('prefabName',''))]
    if not rejected:return legacy
    codes=catalog.get('skillGroups',{}).get(str(spec['skillGroup']),{}).get('projectileCodeCandidates',[])
    evidence=dict(legacy.get('projectileCodeCandidateEvidence',{}),
                  authority='exact-version-foreign-weapon-prefab-excluded',
                  staticCandidates=sorted(codes),runtimeExclusiveCandidates=[c for c in runtime if c not in rejected],
                  rejectedForeignWeaponProjectileCodes=sorted(rejected))
    return dict(legacy,projectileCodeCandidateEvidence=evidence)


def annotate_candidate_filter(row,legacy):
    review=legacy.get('projectileCodeCandidateEvidence',{}).get('producerReview')
    if review:
        from .skill_development_effect_metrics import annotate_provisional_rate,development_policy
        if row.get('status') in {'calculable-observed','calculable-experimental'}:
            row.update(perUseCompletenessTracked=True,unresolvedCombatCastCount=row.get('unresolvedCombatCastCount',0))
            row=annotate_provisional_rate(row,development_policy())
        row['frozenObservedCandidateReview']=review
    rejected=legacy.get('projectileCodeCandidateEvidence',{}).get('rejectedForeignWeaponProjectileCodes')
    if rejected:
        from .skill_development_effect_metrics import annotate_provisional_rate,development_policy
        if row.get('status')=='calculable-observed':
            row.update(perUseCompletenessTracked=True,
                       unresolvedCombatCastCount=row.get('unresolvedCombatCastCount',0))
            row=annotate_provisional_rate(row,development_policy())
        row['rejectedForeignWeaponProjectileCodes']=rejected
    return row


def preselected_effect_metric(spec,starts,finishes,damages,player,teams,intervals,
                              effect_rows,projectile_owners,catalog,gaps=None,route_inputs=None):
    from .skill_development_effect_metrics import development_policy
    policy=development_policy()
    required={'CmdStartSkill','CmdFinishSkill','CmdDamage','CmdSpawn'}
    complete=(gaps is not None and not any(g.get('count',0) and g.get('packetName') in required for g in gaps)
              and route_inputs is not None and route_inputs.get('damages') is not None
              and all(stream is not None for stream in (starts,finishes,damages)))
    reuse=(complete and policy['enabled'] and policy.get('reuseReviewedCandidateRulesWithoutPositiveSamples'))
    row=shared_effect_lifetime_metric(spec,starts,finishes,damages,player,teams,
        intervals,effect_rows,projectile_owners,catalog,allow_partial=True,gaps=gaps,
        unowned_effect_policy={**policy,'relaxedConditions':[
            'localize-unowned-effects-without-erasing-independent-positive-uses']} if reuse else None,
        unobserved_stage_policy={**policy,'relaxedConditions':[
            'reuse-reviewed-candidate-mapping-without-per-match-positive-sample']} if reuse else None)
    from .skill_execution_plan import planned_candidate_review
    review=planned_candidate_review(spec)
    if review:
        from .skill_development_effect_metrics import annotate_provisional_rate
        row=annotate_provisional_rate(row,policy)
        row['frozenObservedCandidateReview']=review
    return row


def preselected_explicit_effect_metric(spec, starts, finishes, damages, player,
                                      teams, intervals, effect_rows, projectile_owners, gaps=None, route_inputs=None):
    from .skill_development_effect_metrics import development_policy
    from .skill_wire_order import command_order
    policy=development_policy()
    required={'CmdStartSkill','CmdFinishSkill','CmdDamage','CmdSpawn'}
    complete=(gaps is not None and not any(g.get('count',0) and g.get('packetName') in required for g in gaps)
        and route_inputs is not None and route_inputs.get('damages') is not None
        and all(x is not None for x in (starts,finishes,damages,effect_rows,projectile_owners))
        and all(command_order(s) is not None for s in starts)
        and all(command_order(f) is not None for f in finishes if f.get('playerObjectId')==player))
    reuse=complete and policy['enabled'] and policy.get('reuseReviewedCandidateRulesWithoutPositiveSamples')
    return effect_lifetime_metric(spec, starts, finishes, damages, player, teams,
                                 intervals, effect_rows, projectile_owners,allow_partial=reuse,zero_hit_policy=policy if reuse else None)


def preselected_action_metric(spec, legacy, selected, player, teams, intervals,finishes=None,gaps=None,route_inputs=None):
    from .skill_lifecycle_result_policy import reviewed_rule_reuse_policy
    inputs=route_inputs or {}
    policy=reviewed_rule_reuse_policy(gaps,{'CmdStartSkill','CmdFinishSkill','CmdPlaySkillAction','CmdDamage','CmdSpawn','CmdProjectile'},
        selected,finishes,inputs.get('raw_actions'),inputs.get('damages'),inputs.get('spawns'),inputs.get('collisions'))
    row,reason=_corroborated_action_contacts(spec,legacy,selected,player,teams,intervals,1,allow_partial=True,
        zero_hit_policy=policy,finishes=finishes)
    return row if row is not None else _unavailable(spec,reason)


def _winner_unemitted_policy(spec,selected,starts,finishes,route_inputs,player,teams,gaps):
    if spec.get('skillGroup') not in {1089200,1027400} or spec.get('unit')!='skill-cast':return {}
    import json
    from pathlib import Path
    from .skill_wire_order import command_order
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_ordered_match_end import ordered_winner_match_end
    if json.loads((Path(__file__).resolve().parents[1]/'data/user-hit-rate-scope-20260912.json').read_bytes())['recordedUseMissPolicy'].get('enabled') is not True:return {}
    raw=route_inputs or {};wall=raw.get('wall_inputs') or {};extra=raw.get('summon_inputs') or {}
    streams=[raw.get('damages'),raw.get('raw_actions'),raw.get('collisions'),raw.get('projectile_terminals'),wall.get('states'),extra.get('allProjectileSpawns'),extra.get('summons'),gaps,finishes]
    if any(v is None for v in streams) or any(g.get('count',0) for g in gaps):return {}
    winner=ordered_winner_match_end(wall.get('gameTerminals'),gaps)
    if winner is None:return {}
    records,why=ordered_cast_records(starts,finishes,player,allow_same_tick_finishes=True)
    if why:return {}
    result={}
    for r in records:
        start=r['start'];left=command_order(start);right=command_order(winner)
        if not any(start is x for x in selected) or r.get('reason') not in {None,'open-final-cast'}:continue
        if left is None or left>=right or start['tick']>winner['tick']:continue
        f=r.get('finish')
        if f and (f.get('reason') not in set(range(15))|{16,17} or command_order(f) is None or not left<command_order(f)<right or f['tick']>winner['tick']):continue
        def after(e):
            if e.get('tick') is not None and e['tick']<start['tick']:return False
            return command_order(e) is None or e.get('tick',start['tick'])>start['tick'] or command_order(e)>=left
        if any(after(e) for e in extra['allProjectileSpawns'] if e.get('ownerObjectId')==player or e.get('ownerObjectId') not in teams):continue
        if any(after(e) for e in extra['summons'] if e.get('ownerObjectId')==player or e.get('ownerObjectId') not in teams):continue
        if any(after(e) for e in raw['damages'] if e.get('attackerObjectId')==player or e.get('attackerObjectId') not in teams):continue
        aa=[a for a in raw['raw_actions'] if a.get('sourceObjectId')==player and a.get('skillIdCode')==start['skillIdCode'] and after(a)]
        def harmless_targets(a):
            import math
            if not a.get('targets'):return True
            if spec['skillGroup']!=1027400 or a.get('skillIdCode')!=379 or a.get('actionNo')!=1:return False
            ts=a['targets']
            if not isinstance(ts,list) or len(ts)!=1 or not isinstance(ts[0],dict):return False
            t=ts[0];xyz=t.get('targetPosition')
            return type(t.get('targetObjectId')) is int and t['targetObjectId']==0 and t.get('hasTargetPosition') is True and isinstance(xyz,(list,tuple)) and len(xyz)==3 and all(type(v) in (int,float) and math.isfinite(v) for v in xyz) and not any(v is True for k,v in t.items() if 'damage' in k.lower() or 'collision' in k.lower() or 'corroborat' in k.lower())
        if any(command_order(a) is None or command_order(a)>=right or a.get('wireStatus') not in {'decoded-exact-CmdPlaySkillAction','decoded-exact-CmdPlaySkillActionWithTargets'} or not harmless_targets(a) for a in aa):continue
        result[id(start)]=dict(startTick=start['tick'],startOrder=left,finish=f,winnerEnd=winner,skillFinishInvented=False,projectileSpawnInvented=False,stateUsedAsProjectileHitProof=False)
    return result


def _bernice_cancelled_unemitted_policy(spec,legacy,selected,starts,finishes,route_inputs,player,teams,gaps):
    if (spec.get('characterCode'),spec.get('skillGroup'),spec.get('mode'),spec.get('unit'))!=(25,1025200,'any','skill-cast'):return {}
    from .skill_partial_cast_lifetimes import ordered_cast_records,user_cancelled_no_recorded_contact_indices
    from .skill_wire_order import command_order,event_within_cast,finish_lookup
    candidate=legacy.get('projectileCodeCandidateEvidence',{})
    if set(candidate.get('runtimeExclusiveCandidates') or candidate.get('staticCandidates') or [])!={102503}:return {}
    raw=route_inputs or {};extra=raw.get('summon_inputs') or {}
    raw_shots=extra.get('allProjectileSpawns')
    if any(x is None for x in (raw_shots,raw.get('collisions'),raw.get('projectile_terminals'),raw.get('damages'),gaps,finishes)) or any(g.get('count',0) for g in gaps):return {}
    own=[s for s in starts if s.get('playerObjectId')==player and s.get('skillGroup')==1025200]
    if any(s.get('skillIdCode')!=336 for s in own):return {}
    records,why=ordered_cast_records(own,finishes,player,allow_same_tick_finishes=True)
    if why:return {}
    lookup=finish_lookup(finishes,player);pending=set();other_uses={}
    for i,r in enumerate(records):
        start=r['start'];left=command_order(start);ignored=[]
        for shot in raw_shots:
            if shot.get('projectileCode')!=102503:continue
            owner=shot.get('ownerObjectId')
            if owner in teams and owner!=player:continue
            if shot.get('tick') is not None and shot['tick']<start['tick']:continue
            so=command_order(shot)
            matches=[j for j,z in enumerate(records) if so is not None and command_order(z['start']) is not None and z['start']['tick']<=shot['tick'] and command_order(z['start'])<=so and (z.get('finish') is None or (shot['tick']<=z['finish']['tick'] and so<=command_order(z['finish']) and event_within_cast(z['start'],z['finish']['tick'],shot,lookup)))]
            if owner==player and len(matches)==1 and matches[0]!=i and records[matches[0]].get('finish'):
                ignored.append(shot['projectileObjectId']);continue
            if so is None or left is None or so>=left or shot.get('tick',start['tick'])>start['tick']:pending.add(i)
        other_uses[i]=ignored
    unknown={i:'missing-recorded-projectile-emission' for i,r in enumerate(records) if any(r['start'] is x for x in selected)}
    admitted=user_cancelled_no_recorded_contact_indices(records,[set() for _ in records],unknown,
        cancellation_reason='missing-recorded-projectile-emission',pending_continuations=pending,gaps=gaps)
    return {id(records[i]['start']):dict(policyKind='actual-gameplay-cancel-without-primary-emission',
        startTick=records[i]['start']['tick'],startOrder=command_order(records[i]['start']),
        finishTick=records[i]['finish']['tick'],finishOrder=command_order(records[i]['finish']),finishReason=records[i]['finish']['reason'],
        primaryProjectileCodes=[102503],recordedPrimaryEmission=False,
        exactlyAssignedOtherUseProjectileIds=other_uses[i],skillFinishInvented=False,stateUsedAsProjectileHitProof=False) for i in admitted}


def preselected_projectile_metric(spec, legacy, selected, starts, spawns,
                                  by_collision, teams, player, catalog, intervals, window, finishes=None,route_inputs=None,gaps=None):
    from .skill_recorded_action_projectiles import PROFILES,recorded_action_projectile_metric
    if spec['skillGroup'] in PROFILES:
        if route_inputs is None:return _unavailable(spec,'recorded action emission inputs unavailable')
        return recorded_action_projectile_metric(spec,starts,finishes,spawns,route_inputs.get('raw_actions'),
            route_inputs.get('collisions'),route_inputs.get('projectile_terminals'),player,teams,intervals,
            catalog,(route_inputs.get('wall_inputs') or {}).get('skill_rows'),gaps,
            game_terminals=(route_inputs.get('wall_inputs') or {}).get('gameTerminals'),route_inputs=route_inputs)
    legacy=fixed_projectile_candidates(spec,legacy,catalog)
    from .skill_fenrir_launch_edge import launch_parents
    edges=launch_parents(spec,starts,finishes,spawns,(route_inputs or {}).get('raw_actions'),player,gaps)
    row,reason=_projectile_contacts(spec,legacy,selected,starts,spawns,
        by_collision,teams,player,catalog,intervals,window,allow_partial=True,emission_finishes=finishes,
        explicit_launch_parents=edges,winner_unemitted=_winner_unemitted_policy(spec,selected,starts,finishes,route_inputs,player,teams,gaps),
        cancelled_unemitted=_bernice_cancelled_unemitted_policy(spec,legacy,selected,starts,finishes,route_inputs,player,teams,gaps))
    return annotate_candidate_filter(row if row is not None else _unavailable(spec,reason),legacy)


def preselected_cancelled_projectile_metric(spec, legacy, selected, starts, spawns,
                                            by_collision, teams, player, catalog,
                                            intervals, window, finishes, route_inputs=None, gaps=None):
    legacy=fixed_projectile_candidates(spec,legacy,catalog)
    # Classify emitted uses once. A normal or open use without an emission
    # cannot invalidate other uses or become a miss. The shared postprocessor
    # alone decides whether an exact pre-emission cancellation is excluded.
    from .skill_fenrir_launch_edge import launch_parents
    edges=launch_parents(spec,starts,finishes,spawns,(route_inputs or {}).get('raw_actions'),player,gaps)
    row,reason=_projectile_contacts(spec,legacy,selected,starts,spawns,
        by_collision,teams,player,catalog,intervals,window,allow_partial=True,emission_finishes=finishes,
        explicit_launch_parents=edges,winner_unemitted=_winner_unemitted_policy(spec,selected,starts,finishes,route_inputs,player,teams,gaps),
        cancelled_unemitted=_bernice_cancelled_unemitted_policy(spec,legacy,selected,starts,finishes,route_inputs,player,teams,gaps))
    return annotate_candidate_filter(row if row is not None else _unavailable(spec,reason),legacy)


def preselected_projectile_lifetime_metric(spec, starts, spawns, collisions, terminals,
                                           finishes, damages, player, teams, intervals,
                                           catalog, effect_rows, projectile_owners,gaps=None,route_inputs=None):
    from .skill_partial_projectile_metrics import partial_projectile_lifetime_metric
    from .skill_development_effect_metrics import development_policy
    policy=development_policy()
    required={'CmdStartSkill','CmdFinishSkill','CmdDamage','CmdSpawn','CmdDestroy'}
    complete=(gaps is not None and not any(g.get('count',0) and
        (g.get('packetName') in required or str(g.get('packetName','')).startswith('CmdProjectile')) for g in gaps)
        and route_inputs is not None and all(route_inputs.get(k) is not None for k in
            ('projectile_terminals','damages','spawns','collisions','effect_rows')))
    reuse=complete and policy['enabled'] and policy.get('reuseReviewedCandidateRulesWithoutPositiveSamples')
    return partial_projectile_lifetime_metric(spec,starts,spawns,collisions,terminals,
        finishes,damages,player,teams,intervals,catalog,effect_rows,projectile_owners,
        development=policy.get('lifecycleEstimatesEnabled',False),route_inputs=route_inputs,gaps=gaps,
        unobserved_stage_policy={**policy,'relaxedConditions':[
            'reuse-reviewed-projectile-rule-without-per-match-positive-sample']} if reuse else None)


def preselected_explosion_metric(spec, legacy, selected, starts, spawns, collisions,
                                damages, teams, player, catalog, intervals, window,
                                projectile_owners, finishes, route_inputs):
    terminals = route_inputs['projectile_terminals']
    if terminals is None:
        return _unavailable(spec, 'projectile terminal stream unavailable for compiled explosion route')
    return explosion_contact_metric(spec, legacy, selected, starts, spawns, collisions,
        terminals, damages, teams, player, catalog, intervals, window,
        projectile_owners, finishes, gaps=(route_inputs.get("summon_inputs") or {}).get("gaps"),
        game_terminals=(route_inputs.get("wall_inputs") or {}).get("gameTerminals"))
