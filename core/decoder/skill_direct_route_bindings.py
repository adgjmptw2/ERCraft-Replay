"""Exact direct-call bindings moved out of the runtime branch ladder.

These are existing implementations, not newly verified hit-rate mappings.
The explicit metric IDs preserve branch precedence and units.
"""
from functools import lru_cache
from importlib import import_module

@lru_cache(maxsize=None)
def _function(module, name):
    loaded = import_module("." + module, __package__) if __package__ else import_module(module)
    return getattr(loaded, name)

def bind_eleven_w_taunt(spec, route_inputs):
    from .skill_recorded_state_success import eleven_w_taunt_metric
    return eleven_w_taunt_metric(spec, route_inputs)

def bind_leon_r_metric(spec, route_inputs):
    # Explicit import also includes the new module in source-closure hashing.
    from .skill_leon_r_execution import leon_r_metric
    a = route_inputs
    w = a['wall_inputs']
    return leon_r_metric(spec, a['player_starts'], w['finishes'], a['damages'],
        w.get('states'), a['player'], a['teams'], a['intervals'].get(a['player'], []),
        a['catalog'], w['skill_rows'], w['state_rows'], w['state_groups'],
        a['effect_rows'], (a['summon_inputs'] or {}).get('gaps'),
        spawns=a['player_spawns'],collisions=a['collisions'],terminals=a.get('projectile_terminals'))

def bind_fiora_q_region(spec, route_inputs):
    return _function('skill_fiora_q_region', 'fiora_q_region')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['damages'], route_inputs['wall_inputs'].get('poseEvents'), route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], (route_inputs['summon_inputs'] or {}).get('gaps'), states=route_inputs['wall_inputs'].get('states'), motion_inputs=route_inputs['wall_inputs'].get('fioraMotionInputs'), state_inventory=route_inputs['wall_inputs'].get('fioraStateInventory'))

def bind_fiora_e_execution(spec, route_inputs):
    return _function('skill_fiora_e_execution', 'fiora_e_execution')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['raw_actions'], route_inputs['damages'], route_inputs['wall_inputs'].get('states'), route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], route_inputs['effect_rows'] or [], (route_inputs['summon_inputs'] or {}).get('gaps'))

def bind_isol_r_trap_metric(spec, route_inputs):
    return _function('skill_isol_r_traps', 'isol_r_trap_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], {**(route_inputs['summon_inputs'] or {}), 'trapEvents': route_inputs['wall_inputs'].get('trapEvents'), 'terminals': route_inputs['projectile_terminals'], 'deaths': route_inputs['wall_inputs'].get('deaths'), 'gameTerminals': route_inputs['wall_inputs'].get('gameTerminals')})

def bind_observed_projectile_metric(spec, route_inputs):
    return _function('skill_observed_projectile_metrics', 'observed_projectile_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['player_spawns'], route_inputs['collisions'], route_inputs['projectile_terminals'], route_inputs['damages'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), game_terminals=route_inputs['wall_inputs'].get('gameTerminals'),gaps=(route_inputs.get('summon_inputs') or {}).get('gaps'),all_projectile_spawns=(route_inputs.get('summon_inputs') or {}).get('allProjectileSpawns'),projectile_owners=route_inputs.get('projectile_owners'))

def bind_judgment_kill_metric(spec, route_inputs,development=False):
    types={}
    objects=(route_inputs.get('summon_inputs') or {}).get('objects')
    for obj in objects or []:
        types.setdefault(obj['objectId'],set()).add(obj.get('objectType'))
    return _function('skill_judgment_kill_metrics', 'judgment_kill_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['raw_actions'] or [], route_inputs['damages'] or [], route_inputs['wall_inputs']['deaths'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], route_inputs['effect_rows'] or [], (route_inputs['summon_inputs'] or {}).get('gaps'),development=development,target_object_types=types,target_objects=objects)

def bind_owned_enemy_pull_metric(spec, route_inputs):
    return _function('skill_owned_state_metrics', 'owned_enemy_pull_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], (route_inputs['summon_inputs'] or {}).get('nonPlayerSkillStarts'), (route_inputs['summon_inputs'] or {}).get('summons', []), route_inputs['projectile_terminals'] or [], route_inputs['wall_inputs']['states'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], route_inputs['wall_inputs']['state_rows'], route_inputs['wall_inputs']['state_groups'])

def bind_effect_linked_cc_metric(spec, route_inputs):
    return _function('skill_effect_linked_cc_metrics', 'effect_linked_cc_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['wall_inputs']['states'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], route_inputs['wall_inputs']['state_rows'], route_inputs['wall_inputs']['state_groups'], route_inputs['effect_rows'] or [], gaps=(route_inputs['summon_inputs'] or {}).get('gaps'))

def bind_enhanced_normal_attack_metric(spec, route_inputs,development=False):
    return _function('skill_enhanced_normal_attack_metrics', 'enhanced_normal_attack_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['damages'] or [], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], route_inputs['effect_rows'] or [], gaps=(route_inputs['summon_inputs'] or {}).get('gaps'),development=development)

def bind_railgun_metric(spec, route_inputs):
    return _function('skill_summon_railgun_metrics', 'railgun_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['raw_actions'] or [], (route_inputs['summon_inputs'] or {}).get('summons', []), route_inputs['projectile_terminals'] or [], route_inputs['damages'] or [], (route_inputs['summon_inputs'] or {}).get('allProjectileSpawns'), route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], (route_inputs['summon_inputs'] or {}).get('summon_rows', []), route_inputs['effect_rows'] or [])

def bind_summon_shot_metric(spec, route_inputs):
    return _function('skill_summon_projectile_metrics', 'summon_shot_metric')(spec, route_inputs['selected'], route_inputs['wall_inputs']['finishes'], (route_inputs['summon_inputs'] or {}).get('summons', []), (route_inputs['summon_inputs'] or {}).get('allProjectileSpawns'), route_inputs['collisions'], route_inputs['projectile_terminals'] or [], route_inputs['damages'] or [], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], (route_inputs['summon_inputs'] or {}).get('summon_rows', []))

def bind_barbara_central_stun_metric(spec, route_inputs):
    return _function('skill_barbara_child_metrics', 'barbara_central_stun_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], (route_inputs['summon_inputs'] or {}).get('allProjectileSpawns'), (route_inputs['summon_inputs'] or {}).get('summons'), route_inputs['projectile_terminals'], (route_inputs['summon_inputs'] or {}).get('nonPlayerSkillStarts'), route_inputs['damages'], route_inputs['wall_inputs']['states'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], route_inputs['wall_inputs']['state_rows'], route_inputs['wall_inputs']['state_groups'], (route_inputs['summon_inputs'] or {}).get('gaps'),game_terminals=route_inputs['wall_inputs'].get('gameTerminals'))

def bind_sua_rq_execution(spec, route_inputs):
    return _function('skill_sua_rq_execution', 'sua_rq_execution')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], {**(route_inputs['summon_inputs'] or {}), 'terminals': route_inputs['projectile_terminals'], 'damages': route_inputs['damages'], 'states': route_inputs['wall_inputs'].get('states'), 'skill_rows': route_inputs['wall_inputs'].get('skill_rows'), 'gameTerminals': route_inputs['wall_inputs'].get('gameTerminals'), 'collisions': route_inputs['collisions']}, include_details=spec.get('phaseScope')!='any-hit-only')

def bind_sua_rw_execution(spec, route_inputs):
    return _function('skill_sua_rw_execution', 'sua_rw_execution')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], {**(route_inputs['summon_inputs'] or {}), 'terminals': route_inputs['projectile_terminals'], 'states': route_inputs['wall_inputs'].get('states')})

def bind_sua_re_execution(spec, route_inputs):
    return _function('skill_sua_re_execution', 'sua_re_execution')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['raw_actions'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], (route_inputs['summon_inputs'] or {}).get('gaps'))

def bind_explosion_state_metric(spec, route_inputs):
    return _function('skill_static_explosion_state_metrics', 'explosion_state_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['player_spawns'], route_inputs['projectile_terminals'], route_inputs['wall_inputs']['states'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], (route_inputs['summon_inputs'] or {}).get('projectile_rows', []), route_inputs['wall_inputs']['state_rows'], route_inputs['wall_inputs']['state_groups'], (route_inputs['summon_inputs'] or {}).get('gaps'))

def bind_jan_w_details(spec, route_inputs):
    from .skill_jan_direct_execution import jan_direct_execution
    a=route_inputs; w=a['wall_inputs']; su=a['summon_inputs'] or {}
    return jan_direct_execution(spec,a['player_starts'],w['finishes'],a['raw_actions'],
        a['damages'],w['states'],su.get('summons'),a['projectile_terminals'],a['player'],
        a['teams'],a['intervals'].get(a['player'],[]),a['catalog'],w['skill_rows'],
        w['state_rows'],w['state_groups'],a['effect_rows'],su.get('summon_rows'),gaps=su.get('gaps'),
        state_inventory=w.get('janStateInventory'),state_identity=w.get('janStateIdentity'),rope_inventory=w.get('janRopeInventory'))

def bind_jan_ring_rope_metric(spec, route_inputs):
    return _function('skill_jan_ring_rope_metrics', 'jan_ring_rope_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['wall_inputs']['states'], (route_inputs['summon_inputs'] or {}).get('summons', []), route_inputs['projectile_terminals'] or [], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], (route_inputs['summon_inputs'] or {}).get('summon_rows', []), route_inputs['wall_inputs']['state_rows'], route_inputs['wall_inputs']['state_groups'], game_terminals=route_inputs['wall_inputs'].get('gameTerminals'), gaps=(route_inputs['summon_inputs'] or {}).get('gaps'), raw_actions=route_inputs.get('raw_actions'))

def bind_eva_orb_phase_metric(spec, route_inputs):
    return _function('skill_eva_orb_phases', 'eva_orb_phase_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['player_spawns'], route_inputs['collisions'], route_inputs['projectile_terminals'] or [], route_inputs['raw_actions'], route_inputs['damages'] or [], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], (route_inputs['summon_inputs'] or {}).get('gaps'))

def bind_manual_projectile_trigger_metric(spec, route_inputs):
    return _function('skill_manual_projectile_trigger', 'manual_projectile_trigger_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['player_spawns'], route_inputs['projectile_terminals'] or [], route_inputs['raw_actions'], route_inputs['damages'] or [], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], (route_inputs['summon_inputs'] or {}).get('gaps'))

def bind_johann_ally_projectile_metric(spec, route_inputs):
    return _function('skill_johann_ally_projectile_metrics', 'johann_ally_projectile_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], (route_inputs['summon_inputs'] or {}).get('allProjectileSpawns'), route_inputs['projectile_terminals'], route_inputs['direct_heals'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], route_inputs['effect_rows'] or [], (route_inputs['summon_inputs'] or {}).get('gaps'), actions=route_inputs['raw_actions'], damages=route_inputs['damages'], states=route_inputs['wall_inputs']['states'], state_rows=route_inputs['wall_inputs']['state_rows'], state_groups=route_inputs['wall_inputs']['state_groups'])

def bind_johann_team_metric(spec, route_inputs):
    return _function('skill_team_target_metrics', 'johann_team_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['wall_inputs']['states'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], route_inputs['wall_inputs']['state_rows'], route_inputs['wall_inputs']['state_groups'], direct_heals=route_inputs['direct_heals'], effect_rows=route_inputs['effect_rows'], spawns=(route_inputs['summon_inputs'] or {}).get('allProjectileSpawns'), terminals=route_inputs['projectile_terminals'], evidence_gaps=(route_inputs['summon_inputs'] or {}).get('gaps'), actions=route_inputs['raw_actions'])

def bind_owned_followup_damage_metric(spec, route_inputs):
    return _function('skill_owned_followup_metrics', 'owned_followup_damage_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], (route_inputs['summon_inputs'] or {}).get('nonPlayerSkillStarts'), (route_inputs['summon_inputs'] or {}).get('summons', []), route_inputs['projectile_terminals'] or [], route_inputs['raw_actions'] or [], route_inputs['damages'] or [], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], route_inputs['effect_rows'] or [])

def bind_aiden_spear_execution(spec, route_inputs,development=False):
    return _function('skill_aiden_spear_execution', 'aiden_spear_execution')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['raw_actions'], route_inputs['damages'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], (route_inputs['summon_inputs'] or {}).get('gaps'), evasion_events=route_inputs['wall_inputs'].get('evasionEvents'),
        summons=(route_inputs['summon_inputs'] or {}).get('summons'),development=development)

def bind_projectile_action_damage_metric(spec, route_inputs):
    return _function('skill_projectile_action_damage_metrics', 'projectile_action_damage_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['player_spawns'], route_inputs['projectile_terminals'] or [], route_inputs['raw_actions'] or [], route_inputs['damages'] or [], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], route_inputs['effect_rows'] or [], (route_inputs['summon_inputs'] or {}).get('gaps'), states=route_inputs['wall_inputs'].get('states'), state_scripts=route_inputs['wall_inputs'].get('state_scripts'), state_groups=route_inputs['wall_inputs'].get('state_groups'), state_rows=route_inputs['wall_inputs'].get('state_rows'))

def bind_elena_e_execution(spec, route_inputs):
    return _function('skill_elena_e_execution', 'elena_e_execution')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['raw_actions'], (route_inputs['summon_inputs'] or {}).get('summons'), (route_inputs['summon_inputs'] or {}).get('objects'), route_inputs['wall_inputs'].get('deaths'), route_inputs['projectile_terminals'], route_inputs['damages'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], (route_inputs['summon_inputs'] or {}).get('summon_rows', []), route_inputs['effect_rows'] or [], (route_inputs['summon_inputs'] or {}).get('gaps'))

def bind_elena_r_execution(spec, route_inputs):
    return _function('skill_elena_r_execution', 'elena_r_execution')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], {**(route_inputs['summon_inputs'] or {}), 'deaths': route_inputs['wall_inputs'].get('deaths'), 'terminals': route_inputs['projectile_terminals'], 'damages': route_inputs['damages'], 'states': route_inputs['wall_inputs'].get('states')})

def bind_martina_parent_w_metric(spec, route_inputs):
    return _function('skill_martina_parent_w_metrics', 'martina_parent_w_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['raw_actions'], route_inputs['damages'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], route_inputs['effect_rows'], route_inputs['wall_inputs'].get('state_groups'), {**(route_inputs['summon_inputs'] or {}), 'state_scripts': route_inputs['wall_inputs'].get('state_scripts'), 'deaths': route_inputs['wall_inputs'].get('deaths'), 'gameTerminals': route_inputs['wall_inputs'].get('gameTerminals'), 'terminals': route_inputs['projectile_terminals'] or []})

def bind_irem_cat_q_execution_metric(spec, route_inputs):
    return _function('skill_irem_cat_q_execution', 'irem_cat_q_execution_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], {'skillContexts': route_inputs['wall_inputs'].get('skillContexts'), 'stateScripts': route_inputs['wall_inputs'].get('state_scripts'), 'states': route_inputs['wall_inputs'].get('states'), 'actions': route_inputs['raw_actions'], 'damages': route_inputs['damages'], 'gaps': (route_inputs['summon_inputs'] or {}).get('gaps')})

def bind_theodore_fetter_metric(spec, route_inputs):
    return _function('skill_theodore_fetter_metrics', 'theodore_fetter_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['player_spawns'], route_inputs['collisions'], route_inputs['projectile_terminals'] or [], route_inputs['wall_inputs']['states'], route_inputs['wall_inputs'].get('state_scripts'), (route_inputs['summon_inputs'] or {}).get('projectileMovement'), route_inputs['raw_actions'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], route_inputs['wall_inputs']['state_rows'], route_inputs['wall_inputs']['state_groups'], (route_inputs['summon_inputs'] or {}).get('gaps'))

def bind_ian_possession_e_execution(spec, route_inputs):
    return _function('skill_ian_possession_e_execution', 'ian_possession_e_execution')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], dict(actions=route_inputs['raw_actions'], damages=route_inputs['damages'], states=route_inputs['wall_inputs'].get('states'), stateScripts=route_inputs['wall_inputs'].get('state_scripts'), gaps=(route_inputs['summon_inputs'] or {}).get('gaps')), route_inputs['wall_inputs'].get('state_rows', []))

def bind_arda_parent_metric(spec, route_inputs):
    return _function('skill_arda_parent_metrics', 'arda_parent_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], (route_inputs['summon_inputs'] or {}).get('nonPlayerSkillStarts'), (route_inputs['summon_inputs'] or {}).get('summons', []), (route_inputs['summon_inputs'] or {}).get('objects'), route_inputs['projectile_terminals'] or [], route_inputs['raw_actions'] or [], route_inputs['damages'] or [], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], (route_inputs['summon_inputs'] or {}).get('summon_rows', []), route_inputs['effect_rows'] or [], (route_inputs['summon_inputs'] or {}).get('gaps'), child_use_cache=route_inputs['arda_child_use_cache'])

def bind_target_count_metric(spec, route_inputs):
    return _function('skill_target_count_metrics', 'target_count_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['wall_inputs']['states'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], route_inputs['wall_inputs']['state_rows'], route_inputs['wall_inputs']['state_groups'], gaps=(route_inputs.get('summon_inputs') or {}).get('gaps'))

def bind_charlotte_q_metric(spec, route_inputs):
    return _function('skill_charlotte_q_lineage', 'charlotte_q_metric')(spec, {**(route_inputs['summon_inputs'] or {}), 'starts': route_inputs['player_starts'], 'finishes': route_inputs['wall_inputs']['finishes'], 'actions': route_inputs['raw_actions'], 'states': route_inputs['wall_inputs'].get('states'), 'stateScripts': route_inputs['wall_inputs'].get('state_scripts'), 'damages': route_inputs['damages'], 'collisions': route_inputs['collisions'], 'terminals': route_inputs['projectile_terminals'], 'deaths': route_inputs['wall_inputs'].get('deaths'), 'gameTerminals': route_inputs['wall_inputs'].get('gameTerminals')}, route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'])

def bind_niah_parent_q_metric(spec, route_inputs):
    inputs={**(route_inputs['summon_inputs'] or {}), 'deaths': route_inputs['wall_inputs'].get('deaths'), 'gameTerminals':route_inputs['wall_inputs'].get('gameTerminals')}
    prep_store=inputs.get('_niah_q_preparation_store')
    if prep_store is not None:
        prep=prep_store.get(route_inputs['player'])
        if prep is None:
            from .skill_niah_parent_q_metrics import NiahParentQPreparation
            prep=NiahParentQPreparation(route_inputs['catalog'],route_inputs['player_starts'],
                route_inputs['wall_inputs']['finishes'],route_inputs['spawns'],route_inputs['collisions'],
                route_inputs['projectile_terminals'],route_inputs['raw_actions'],route_inputs['damages'],
                route_inputs['wall_inputs']['skill_rows'],route_inputs['effect_rows'] or [],inputs,None)
            prep_store[route_inputs['player']]=prep
        inputs['_niah_q_preparation']=prep
    return _function('skill_niah_parent_q_metrics', 'niah_parent_q_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['spawns'], route_inputs['collisions'], route_inputs['projectile_terminals'], route_inputs['raw_actions'], route_inputs['damages'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], route_inputs['effect_rows'] or [], inputs, _preparation=inputs.get('_niah_q_preparation'))

def bind_niah_parent_w_metric(spec, route_inputs):
    return _function('skill_niah_parent_w_metrics', 'niah_parent_w_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['spawns'], route_inputs['projectile_terminals'] or [], route_inputs['raw_actions'], route_inputs['damages'] or [], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], route_inputs['effect_rows'] or [], {**(route_inputs['summon_inputs'] or {}), 'deaths': route_inputs['wall_inputs'].get('deaths'), 'states': route_inputs['wall_inputs'].get('states'), 'state_groups': route_inputs['wall_inputs'].get('state_groups'), 'state_rows': route_inputs['wall_inputs'].get('state_rows'), 'gameTerminals': route_inputs['wall_inputs'].get('gameTerminals')})

def bind_niah_parent_r_metric(spec, route_inputs):
    return _function('skill_niah_parent_r_metrics', 'niah_parent_r_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['raw_actions'], route_inputs['damages'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], route_inputs['wall_inputs'].get('state_rows'), route_inputs['wall_inputs'].get('state_groups'), route_inputs['effect_rows'], {**(route_inputs['summon_inputs'] or {}), 'states': route_inputs['wall_inputs'].get('states')})

def bind_fenrir_absorb_metric(spec, route_inputs):
    return _function('skill_fenrir_absorb_metrics', 'fenrir_absorb_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['wall_inputs'].get('state_scripts'), route_inputs['wall_inputs']['states'], route_inputs['raw_actions'] or [], route_inputs['damages'] or [], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], route_inputs['wall_inputs']['state_rows'], route_inputs['wall_inputs']['state_groups'])

def bind_projectile_cc_metric(spec, route_inputs):
    return _function('skill_projectile_cc_metrics', 'projectile_cc_metric')(spec, route_inputs['player_starts'], route_inputs['wall_inputs']['finishes'], route_inputs['player_spawns'], route_inputs['collisions'], route_inputs['projectile_terminals'] or [], route_inputs['wall_inputs']['states'], route_inputs['player'], route_inputs['teams'], route_inputs['intervals'].get(route_inputs['player'], []), route_inputs['catalog'], route_inputs['wall_inputs']['skill_rows'], route_inputs['wall_inputs']['state_rows'], route_inputs['wall_inputs']['state_groups'], route_inputs['effect_rows'] or [])

# route -> (adapter, target module, target function, exact metric IDs)
DIRECT_BINDINGS = {
    'jan-w-recorded-details': ('bind_jan_w_details','skill_jan_direct_execution','jan_direct_execution',('user-request-20260905-all90-v1:35:1035300:any',)),
    'eleven-w-recorded-taunt': ('bind_eleven_w_taunt', 'skill_recorded_state_success', 'eleven_w_taunt_metric', ('user-request-20260905-all90-v1:30:1030300:any',)),
    'leon-r-state-phases': ('bind_leon_r_metric', 'skill_leon_r_execution', 'leon_r_metric', ('user-request-20260905-all90-v1:29:1029500:any',)),
    'direct-fiora-q-region': ('bind_fiora_q_region', 'skill_fiora_q_region', 'fiora_q_region',
        ('user-request-20260905-all90-v1:3:1003200:any',)),
    'direct-fiora-e-execution': ('bind_fiora_e_execution', 'skill_fiora_e_execution', 'fiora_e_execution',
        ('user-request-20260905-all90-v1:3:1003400:any',)),
    'direct-isol-r-trap-metric': ('bind_isol_r_trap_metric', 'skill_isol_r_traps', 'isol_r_trap_metric',
        ('user-request-20260905-all90-v1:9:1009500:any', 'user-request-20260905-all90-v1:25:1025300:any')),
    'direct-observed-projectile-metric': ('bind_observed_projectile_metric', 'skill_observed_projectile_metrics', 'observed_projectile_metric',
        ('user-request-20260905-all90-v1:13:1013200:any', 'user-request-20260905-all90-v1:14:1014400:any', 'user-request-20260905-all90-v1:18:1018400:any', 'user-request-20260905-all90-v1:32:1032300:any')),
    'direct-judgment-kill-metric': ('bind_judgment_kill_metric', 'skill_judgment_kill_metrics', 'judgment_kill_metric',
        ('user-request-20260905-all90-v1:14:1014510:enemy-kill',)),
    'direct-owned-enemy-pull-metric': ('bind_owned_enemy_pull_metric', 'skill_owned_state_metrics', 'owned_enemy_pull_metric',
        ('user-request-20260905-all90-v1:15:1015400:enemy-pull',)),
    'direct-effect-linked-cc-metric': ('bind_effect_linked_cc_metric', 'skill_effect_linked_cc_metrics', 'effect_linked_cc_metric',
        ('user-request-20260905-all90-v1:19:1019500:fetter', 'user-request-20260905-all90-v1:46:1046300:fetter', 'user-request-20260905-all90-v1:49:1049320:fetter', 'user-request-20260905-all90-v1:75:1075300:fetter')),
    'direct-enhanced-normal-attack-metric': ('bind_enhanced_normal_attack_metric', 'skill_enhanced_normal_attack_metrics', 'enhanced_normal_attack_metric',
        ('user-request-20260905-all90-v1:23:1023210:any',)),
    'direct-railgun-metric': ('bind_railgun_metric', 'skill_summon_railgun_metrics', 'railgun_metric',
        ('user-request-20260905-all90-v1:26:1026020:summon-railgun-normal', 'user-request-20260905-all90-v1:26:1026020:summon-railgun-reinforced')),
    'direct-summon-shot-metric': ('bind_summon_shot_metric', 'skill_summon_projectile_metrics', 'summon_shot_metric',
        ('user-request-20260905-all90-v1:26:1026200:summon-shot', 'user-request-20260905-all90-v1:26:1026210:summon-shot', 'user-request-20260905-all90-v1:90:1090300:summon-shot')),
    'direct-barbara-central-stun-metric': ('bind_barbara_central_stun_metric', 'skill_barbara_child_metrics', 'barbara_central_stun_metric',
        ('user-request-20260905-all90-v1:26:1026400:any', 'user-request-20260905-all90-v1:26:1026400:central-stun')),
    'direct-sua-rq-execution': ('bind_sua_rq_execution', 'skill_sua_rq_execution', 'sua_rq_execution',
        ('user-request-20260905-all90-v1:28:1028510:any', 'user-request-20260905-all90-v1:28:1028200:any')),
    'direct-sua-rw-execution': ('bind_sua_rw_execution', 'skill_sua_rw_execution', 'sua_rw_execution',
        ('user-request-20260905-all90-v1:28:1028520:blind',)),
    'direct-sua-re-execution': ('bind_sua_re_execution', 'skill_sua_re_execution', 'sua_re_execution',
        ('user-request-20260905-all90-v1:28:1028530:any', 'user-request-20260905-all90-v1:28:1028400:any')),
    'direct-explosion-state-metric': ('bind_explosion_state_metric', 'skill_static_explosion_state_metrics', 'explosion_state_metric',
        ('user-request-20260905-all90-v1:34:1034500:any',)),
    'direct-jan-ring-rope-metric': ('bind_jan_ring_rope_metric', 'skill_jan_ring_rope_metrics', 'jan_ring_rope_metric',
        ('user-request-20260905-all90-v1:35:1035500:any',)),
    'direct-eva-orb-phase-metric': ('bind_eva_orb_phase_metric', 'skill_eva_orb_phases', 'eva_orb_phase_metric',
        ('user-request-20260905-all90-v1:36:1036200:any',)),
    'direct-manual-projectile-trigger-metric': ('bind_manual_projectile_trigger_metric', 'skill_manual_projectile_trigger', 'manual_projectile_trigger_metric',
        ('user-request-20260905-all90-v1:36:1036210:any',)),
    'direct-johann-ally-projectile-metric': ('bind_johann_ally_projectile_metric', 'skill_johann_ally_projectile_metrics', 'johann_ally_projectile_metric',
        ('user-request-20260905-all90-v1:41:1041200:any', 'user-request-20260905-all90-v1:41:1041200:fetter', 'user-request-20260905-all90-v1:41:1041200:ally', 'user-request-20260905-all90-v1:41:1041200:either-team', 'user-request-20260905-all90-v1:41:1041210:any', 'user-request-20260905-all90-v1:41:1041210:ally', 'user-request-20260905-all90-v1:41:1041210:either-team')),
    'direct-johann-team-metric': ('bind_johann_team_metric', 'skill_team_target_metrics', 'johann_team_metric',
        ('user-request-20260905-all90-v1:41:1041300:ally', 'user-request-20260905-all90-v1:41:1041300:either-team', 'user-request-20260905-all90-v1:41:1041310:ally', 'user-request-20260905-all90-v1:41:1041310:either-team')),
    'direct-owned-followup-damage-metric': ('bind_owned_followup_damage_metric', 'skill_owned_followup_metrics', 'owned_followup_damage_metric',
        ('user-request-20260905-all90-v1:43:1043500:any', 'user-request-20260905-all90-v1:79:1079300:any')),
    'direct-aiden-spear-execution': ('bind_aiden_spear_execution', 'skill_aiden_spear_execution', 'aiden_spear_execution',
        ('user-request-20260905-all90-v1:46:1046200:any',)),
    'direct-projectile-action-damage-metric': ('bind_projectile_action_damage_metric', 'skill_projectile_action_damage_metrics', 'projectile_action_damage_metric',
        ('user-request-20260905-all90-v1:48:1048700:any',)),
    'direct-elena-e-execution': ('bind_elena_e_execution', 'skill_elena_e_execution', 'elena_e_execution',
        ('user-request-20260905-all90-v1:50:1050400:any',)),
    'direct-elena-r-execution': ('bind_elena_r_execution', 'skill_elena_r_execution', 'elena_r_execution',
        ('user-request-20260905-all90-v1:50:1050500:any',)),
    'direct-martina-parent-w-metric': ('bind_martina_parent_w_metric', 'skill_martina_parent_w_metrics', 'martina_parent_w_metric',
        ('user-request-20260905-all90-v1:57:1057310:any',)),
    'direct-irem-cat-q-execution-metric': ('bind_irem_cat_q_execution_metric', 'skill_irem_cat_q_execution', 'irem_cat_q_execution_metric',
        ('user-request-20260905-all90-v1:61:1061210:any',)),
    'direct-theodore-fetter-metric': ('bind_theodore_fetter_metric', 'skill_theodore_fetter_metrics', 'theodore_fetter_metric',
        ('user-request-20260905-all90-v1:62:1062400:any', 'user-request-20260905-all90-v1:62:1062400:fetter')),
    'direct-ian-possession-e-execution': ('bind_ian_possession_e_execution', 'skill_ian_possession_e_execution', 'ian_possession_e_execution',
        ('user-request-20260905-all90-v1:63:1063420:any',)),
    'direct-arda-parent-metric': ('bind_arda_parent_metric', 'skill_arda_parent_metrics', 'arda_parent_metric',
        ('user-request-20260905-all90-v1:66:1066300:any', 'user-request-20260905-all90-v1:66:1066310:any', 'user-request-20260905-all90-v1:66:1066400:any', 'user-request-20260905-all90-v1:66:1066410:any', 'user-request-20260905-all90-v1:66:1066420:any', 'user-request-20260905-all90-v1:66:1066430:any')),
    'direct-target-count-metric': ('bind_target_count_metric', 'skill_target_count_metrics', 'target_count_metric',
        ('user-request-20260905-all90-v1:72:1072500:target-count',)),
    'direct-charlotte-q-metric': ('bind_charlotte_q_metric', 'skill_charlotte_q_lineage', 'charlotte_q_metric',
        ('user-request-20260905-all90-v1:73:1073200:any',)),
    'direct-niah-parent-q-metric': ('bind_niah_parent_q_metric', 'skill_niah_parent_q_metrics', 'niah_parent_q_metric',
        ('user-request-20260905-all90-v1:81:1081200:any', 'user-request-20260905-all90-v1:81:1081210:any')),
    'direct-niah-parent-w-metric': ('bind_niah_parent_w_metric', 'skill_niah_parent_w_metrics', 'niah_parent_w_metric',
        ('user-request-20260905-all90-v1:81:1081300:any', 'user-request-20260905-all90-v1:81:1081310:any')),
    'direct-niah-parent-r-metric': ('bind_niah_parent_r_metric', 'skill_niah_parent_r_metrics', 'niah_parent_r_metric',
        ('user-request-20260905-all90-v1:81:1081500:any', 'user-request-20260905-all90-v1:81:1081500:world-edge-stun')),
    'direct-fenrir-absorb-metric': ('bind_fenrir_absorb_metric', 'skill_fenrir_absorb_metrics', 'fenrir_absorb_metric',
        ('user-request-20260905-all90-v1:86:1086300:absorb', 'user-request-20260905-all90-v1:86:1086300:absorb-pulse', 'user-request-20260905-all90-v1:86:1086500:absorb', 'user-request-20260905-all90-v1:86:1086500:absorb-pulse')),
    'direct-projectile-cc-metric': ('bind_projectile_cc_metric', 'skill_projectile_cc_metrics', 'projectile_cc_metric',
        ('user-request-20260905-all90-v1:87:1087400:fetter',)),
}

METRIC_ROUTES = {metric: route for route, (_, _, _, metrics) in DIRECT_BINDINGS.items()
                 for metric in metrics}
