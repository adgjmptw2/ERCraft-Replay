"""User-enabled provisional rates; never reported as verified outcomes."""
import json
from functools import lru_cache
from pathlib import Path
try:
    from .requested_skill_hit_rates import _unavailable
    from .skill_static_effect_families import shared_effect_lifetime_metric
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from requested_skill_hit_rates import _unavailable
    from skill_static_effect_families import shared_effect_lifetime_metric
    from skill_action_stage_evidence import load_exact_skill_ids

POLICY_PATH=Path(__file__).resolve().parents[1]/'data/development-hit-rate-policy-v1.json'

@lru_cache(maxsize=1)
def development_policy():
    policy=json.loads(POLICY_PATH.read_text(encoding='utf8'))
    if policy['schemaVersion']!=1 or policy['verifiedCompletionCredit'] is not False:
        raise ValueError('invalid development rate policy')
    return policy


def automatic_development_family(spec,family,policy):
    """Offline admission for ordinary unbound damage rates, never CC/regions.

    A new match cannot change this decision. Names provide candidates only;
    resulting rows remain experimental and earn no verified completion credit.
    """
    return (policy.get('compileUnboundDamageFamilies') is True
            and spec.get('skillGroup') in policy.get('unboundDamageFamilyGroups',[])
            and spec.get('mode')=='any' and spec.get('unit')=='skill-cast'
            and spec.get('numerator')=='casts-hitting-any-enemy'
            and not spec.get('targetCohort')
            and family.get('characterCode')==spec.get('characterCode')
            and bool(family.get('effectCodes')))

def development_effect_metric(spec,starts,finishes,damages,player,teams,intervals,
                              effect_rows,catalog,skill_rows,gaps=None,projectile_owners=None,
                              actions=None,spawns=None,summons=None,terminals=None,states=None,state_scripts=None,collisions=None,game_terminals=None,all_projectile_spawns=None):
    policy=development_policy()
    from .skill_execution_plan import planned_route
    compiled_selection=(policy.get('compileUnboundDamageFamilies') is True
                        and planned_route(spec)=='development-effect-family')
    if not policy['enabled'] or (spec['metricId'] not in policy['metricIds'] and not compiled_selection):
        return _unavailable(spec,'development policy does not select this metric')
    required={'CmdStartSkill','CmdFinishSkill','CmdDamage','CmdSpawn'}
    if gaps is None or any(g.get('count',0) and g.get('packetName') in required for g in gaps):
        return _unavailable(spec,'development estimate requires complete cast/damage/owner streams')
    if any(stream is None for stream in (starts,finishes,damages)):
        return _unavailable(spec,'development estimate has a missing event stream')
    if spec['skillGroup']==1082400 and any(v is None for v in (projectile_owners,all_projectile_spawns,terminals,collisions)):
        return _unavailable(spec,'phase winner policy original object streams missing')
    own=[s for s in starts if s['playerObjectId']==player]
    codes={r['code']:r['group'] for r in skill_rows}
    ids=load_exact_skill_ids()
    for s in own:
        definition=catalog['skillGroups'].get(str(s['skillGroup']),{})
        if s['skillGroup']==spec['skillGroup'] and (definition.get('characterCode')!=spec['characterCode'] or
                codes.get(s['skillCode'])!=s['skillGroup'] or ids.get(definition.get('skillId'))!=s['skillIdCode']):
            return _unavailable(spec,'development estimate skill identity mismatch')
    from .skill_user_phase_metrics import (PHASE_EFFECTS,celine_bomb_metric,
        yumin_first_pulse_metric,exclude_adela_r_piece_damage,laura_explosion_metric)
    phase=PHASE_EFFECTS.get((spec['characterCode'],spec['skillGroup'],spec['mode']))
    rejected=[]
    if spec['characterCode']==24 and spec['skillGroup'] in {1024200,1024410}:
        if state_scripts is None or summons is None:
            return _unavailable(spec,'Adela Q/E needs recorded piece producer context to exclude R-triggered damage')
        damages,rejected=exclude_adela_r_piece_damage(damages,state_scripts,summons,player)
    if spec['skillGroup']==1047500 and spec['mode']=='end-hit':
        if state_scripts is None:return _unavailable(spec,'R explosion needs its recorded delay-state lifetime')
        if any(g.get('count',0) and g.get('packetName') in {'CmdStartStateSkill','CmdFinishStateSkill'} for g in gaps):
            return _unavailable(spec,'R explosion delay-state stream has decode gaps')
        row=laura_explosion_metric(spec,starts,finishes,damages,state_scripts,player,teams,intervals,allow_absent_phase=policy.get('lifecycleEstimatesEnabled',False),gaps=gaps)
    elif spec['skillGroup'] in {1043200,1043300,1077210}:
        extra=(actions,spawns,summons,terminals,states)
        if any(x is None for x in extra) or any(g.get('count',0) and g.get('packetName') in {'CmdAddState','CmdPlaySkillActionWithTargets','CmdDestroyDelayStart'} for g in gaps):
            return _unavailable(spec,'phase requires saved object/action/state streams')
        if spec['skillGroup']==1077210:
            row=yumin_first_pulse_metric(spec,starts,finishes,damages,actions,spawns,summons,terminals,player,teams,intervals)
        else:
            row=celine_bomb_metric(spec,starts,finishes,damages,states,actions,summons,terminals,player,teams,intervals)
    elif policy.get('lifecycleEstimatesEnabled') and phase is None:
        if spawns is None or terminals is None:
            return _unavailable(spec,'development lifecycle policy requires saved projectile and terminal streams')
        from .skill_development_lifecycle import provisional_family_lifecycle_metric
        row=provisional_family_lifecycle_metric(spec,starts,finishes,damages,spawns,terminals,
            player,teams,intervals,effect_rows,catalog,projectile_owners if spec['skillGroup']==1076300 else (projectile_owners or {}),states=states,collisions=collisions,summons=summons,actions=actions,gaps=gaps,game_terminals=game_terminals,all_projectile_spawns=all_projectile_spawns)
        if rejected:row['foreignRDamageExcludedBeforeProvisionalAttribution']=True
    else:
        phase_ends={}
        if phase and policy.get('lifecycleEstimatesEnabled'):
            from .skill_user_phase_metrics import recorded_phase_activity_ends
            if any(g.get('count',0) and g.get('packetName') in {'CmdProjectileExplosion','CmdDestroyDelayStart','CmdDestroy'} for g in gaps):
                return _unavailable(spec,'phase activity end stream is incomplete')
            phase_ends=recorded_phase_activity_ends(spec,starts,finishes,spawns,terminals,player)
        row=shared_effect_lifetime_metric(spec,own,finishes,damages,player,teams,intervals,effect_rows,
            projectile_owners or {},catalog,allow_partial=True,gaps=gaps,development=True,
            development_contract={'effects':phase} if phase else None,development_foreign_events=rejected,
            development_phase_ends=phase_ends,game_terminals=game_terminals,all_projectile_spawns=all_projectile_spawns,terminals=terminals,collisions=collisions)
    if phase:row.update(phaseEffectCodes=sorted(phase),excludedSecondaryDamage=True)
    if rejected:row.update(excludedRTriggeredPieceDamageEvents=len(rejected),pieceProducerContextUsed=True)
    return annotate_provisional_rate(row,policy)


def annotate_provisional_rate(row,policy):
    """Keep provisional confidence identical across primary execution routes."""
    if row['status']=='calculable-observed':
        row.update(status='calculable-experimental',calculationConfidence='experimental',
            developmentLabel=policy['label'],developmentPolicy='data/development-hit-rate-policy-v1.json',
            developmentRelaxations=policy['relaxedConditions'],verifiedCompletionCredit=False,
            provisionalCombatCastCount=row['attemptCount'],verifiedCombatCastCount=0,
            hitRateScope='provisional-classified-uses',wholeCombatHitRate=None,fullRequestedMetricComplete=False,
            recordedCastOutcomesComplete=False,binaryCastSuccessComplete=False,
            interpretation=row.get('interpretation','')+' 개발 중 잠정값. 연결되지 않은 시전은 별도 표시.',
            method='development-'+row.get('method','candidate-effects-with-single-family-cast-owner'),
            positiveSampleRequired=False,nearestCastUsed=row.get('nearestCastUsed',False),fixedTimeWindowUsed=False)
    return row
