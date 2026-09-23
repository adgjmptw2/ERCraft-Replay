"""Versioned implementation of the full user scope, processed by shared methods.

The scope contract contains all 90 characters. A registered metric is not a
verified metric. Reviewed versioned rules execute without being relearned from
each match; actual event identity and outcome completeness remain mandatory.
This module deliberately does not use numeric Skill/CharacterState/effectCode
coincidence as a foreign-key relationship.
"""
from collections import defaultdict
from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
import json
import hashlib
from pathlib import Path
try:
    from .skill_execution_plan import with_execution_plan, begin_metric, planned_route, execute_planned
except ImportError:
    from skill_execution_plan import with_execution_plan, begin_metric, planned_route, execute_planned

try:
    from .requested_skill_hit_rates import CLIENT_VERSION, GAME_DB_SHA256, _result, _unavailable
    from .projectile_hit_catalog import _apply_corroborated_action_target_cast_rate
    from .requested_wall_stun_metrics import calculate_wall_stun_metrics
except ImportError:
    from requested_skill_hit_rates import CLIENT_VERSION, GAME_DB_SHA256, _result, _unavailable
    from projectile_hit_catalog import _apply_corroborated_action_target_cast_rate
    from requested_wall_stun_metrics import calculate_wall_stun_metrics

POLICY_ID = 'user-request-20260905-all90-v1'
SCOPE_PATH = Path(__file__).resolve().parent.parent / 'data/requested_skill_scope_20260905.json'
REGISTRY_PATH = SCOPE_PATH.with_name('requested_skill_group_registry_20260905.json')


def implementation_source_files():
    files=['decoder/skill_opening_cast_scope.py','decoder/skill_adina_w_explosion_effects.py','data/adina-w-explosion-effects-v1.json','data/adina-w-exact-revisions-v1.json',
           'data/adina-w-native-effects-proof-v1.json',
           'data/requested_skill_scope_20260905.json','data/user-hit-rate-scope-20260912.json','decoder/requested_skill_scope.py',
           'decoder/skill_recorded_attempt_policy.py',
           'decoder/skill_execution_plan.py','decoder/skill_rule_candidates.py','schema/skill-execution-plan-v1.json',
           'decoder/skill_preselected_effect_routes.py','data/preselected-effect-routes-v1.json',
           'data/preselected-terminal-routes-v1.json','data/preselected-contact-routes-v1.json',
           'data/preselected-projectile-lifetime-routes-v1.json',
           'decoder/skill_combined_contact_evidence.py','data/combined-contact-routes-v1.json',
           'decoder/skill_game_data_contract.py','schema/skill-game-data-revisions-v1.json',
           'schema/skill-execution-plan-20260909013633-v1.json','schema/full-corpus-equivalence-v1.json',
           'schema/native-cc-state-producers-v1.json',
           'data/requested_skill_group_registry_20260905.json','decoder/build_requested_skill_registry.py',
           'decoder/skill_effect_lifetimes.py','decoder/audit_projectile_runtime_replay.py','decoder/corpus_runtime_source.py','decoder/skill_wire_order.py',
           'decoder/skill_projectile_outcomes.py','decoder/skill_cancelled_projectiles.py','decoder/skill_attempt_timing.py',
           'decoder/skill_delayed_projectile_lifetimes.py','decoder/skill_hit_action_lifetimes.py',
           'decoder/skill_scope_evidence_cache.py','decoder/projectile_movement_facts.py','decoder/recalculate_skill_scope.py','schema/schema.json',
           'decoder/skill_static_effect_families.py','decoder/skill_development_effect_metrics.py','data/development-hit-rate-policy-v1.json','decoder/skill_owned_state_execution.py','decoder/skill_action_cc_outcomes.py','decoder/skill_created_object_hit_metrics.py','decoder/skill_created_object_phase_metrics.py',
           'decoder/skill_complete_projectile_lifetimes.py','decoder/skill_partial_projectile_metrics.py','decoder/skill_partial_cast_lifetimes.py','decoder/skill_henry_linked_e_metrics.py','decoder/skill_action_effect_stage_metrics.py','decoder/skill_projectile_marked_hit_metrics.py','decoder/skill_screen_beam_metrics.py','decoder/skill_reviewed_multi_stage_metrics.py','decoder/skill_camilo_r3_metrics.py','decoder/skill_silvia_e_direct_damage_metrics.py',
           'decoder/skill_summon_ownership.py','decoder/skill_state_end_damage_metrics.py','decoder/skill_sequential_explosion_metrics.py',
           'decoder/skill_summon_direct_cast_metrics.py','decoder/skill_wall_connection_movement.py',
           'decoder/skill_theodore_mark_lineage.py','decoder/skill_theodore_fetter_metrics.py',
           'decoder/skill_projectile_active_end.py',
           'decoder/skill_manual_projectile_trigger.py',
           'decoder/skill_eva_orb_phases.py',
           'decoder/skill_owned_child_damage_metrics.py',
           'decoder/skill_owned_child_projectile_metrics.py',
           'decoder/skill_niah_parent_q_metrics.py',
           'decoder/skill_niah_parent_w_metrics.py',
           'decoder/skill_niah_parent_r_metrics.py',
           'decoder/skill_martina_parent_w_metrics.py',
           'decoder/skill_fiora_e_execution.py',
           'decoder/skill_charlotte_q_lineage.py',
           'decoder/skill_trap_events.py',
           'decoder/skill_isol_r_traps.py',
           'deliverables/isol-w-native-unit-exclusions-v2.json',
           'decoder/skill_chloe_q_execution.py',
           'decoder/skill_rotation_events.py',
           'decoder/skill_chloe_w_phases.py',
           'decoder/skill_chloe_e_execution.py',
           'decoder/skill_aiden_spear_execution.py',
           'decoder/skill_evasion_events.py',
           'decoder/skill_irem_cat_q_execution.py',
           'decoder/skill_aiden_second_thunderbolt.py',
           'decoder/skill_aiden_first_thunderbolt.py',
           'decoder/skill_debi_marlene_e2_execution.py',
           'decoder/skill_adina_star_e_execution.py',
           'decoder/skill_adina_e_celestial_execution.py',
           'decoder/skill_sissela_q_execution.py',
           'decoder/skill_sissela_e_execution.py',
           'decoder/skill_elena_e_execution.py',
           'decoder/skill_elena_r_execution.py',
           'decoder/skill_elena_freeze_events.py',
           'decoder/skill_ian_possession_e_execution.py',
           'decoder/skill_sua_rq_execution.py',
           'decoder/skill_sua_rq_details.py',
           'decoder/skill_sua_hit_scope.py',
           'decoder/skill_match_end_scope.py',
           'decoder/corpus_state_inventory.py',
           'decoder/skill_static_applicability.py','deliverables/sua-initial-r-non-executable-proof-v1.json',
           'deliverables/echion-initial-r-non-executable-proof-v1.json',
           'decoder/skill_adela_state_admission.py','schema/adela-pushed-pawn-state-admission-v1.json',
           'decoder/skill_fiora_state_admission.py','schema/fiora-q-slow-admission-v1.json',
           'schema/sua-center-slow-admission-v3.json',
           'schema/state-admission-enum-correction-v1.json',
           'schema/slow-specific-immunity-admission-v1.json',
           'decoder/skill_box_circle_rule.py','decoder/skill_sua_stop_source_bridge.py','schema/sua-stop-source-cell-bridge-v1.json','decoder/skill_barbara_region_rule.py',
           'schema/game-native-center-math-v1.json',
           'schema/stationary-player-sub-collision-rule-v1.json',
           'decoder/skill_sua_rw_execution.py',
           'decoder/skill_sua_re_execution.py',
           'decoder/skill_martina_state_execution_metrics.py',
           'decoder/skill_niah_pull_disposition.py',
           'decoder/skill_ordered_match_end.py',
           'decoder/skill_context_casts.py',
           'decoder/skill_enhanced_normal_attack_metrics.py',
           'decoder/skill_judgment_kill_metrics.py',
           'decoder/skill_owned_stage_casts.py',
           'decoder/skill_summon_projectile_metrics.py','decoder/skill_tazia_wall_contact_metrics.py','decoder/skill_jan_ring_rope_metrics.py','decoder/skill_jan_direct_execution.py','decoder/skill_jan_state_admission.py','decoder/skill_jan_rope_admission.py','deliverables/native-jan-rope-action-completeness-v1.json','decoder/skill_garnet_execute_metrics.py',
           'decoder/skill_tazia_q_projectile_metrics.py',
           'decoder/skill_echion_black_mamba_metrics.py',
           'decoder/corpus_echion_primary_inputs.py',
           'decoder/skill_echion_primary_execution.py',
           'decoder/skill_echion_start_clock.py',
           'schema/echion-start-capped-update-clock-v1.json',
           'schema/echion-start-capped-update-clock-v2.json',
           'decoder/skill_state_clock_constraints.py',
           'decoder/skill_server_frame_time.py',
           'schema/common-state-clock-brackets-v1.json',
           'schema/state-created-time-server-frame-v1.json',
           'schema/echion-congestion-required-signal-v1.json',
           'schema/echion-congestion-required-signal-v2.json',
           'schema/echion-congestion-required-signal-v3.json',
           'schema/echion-primary-frame-frontier-v1.json',
           'schema/echion-secondary-execution-v1.json',
           'schema/echion-secondary-state-v1.json',
           'schema/echion-secondary-required-signal-v1.json',
           'schema/echion-execution-clock-propagation-v1.json',
           'deliverables/barbara-reinforced-w-direct-producer-proof-v1.json',
           'schema/state-add-duration-initial-value-v1.json',
           'decoder/skill_barbara_grenade_metrics.py',
           'decoder/skill_projectile_schema_metrics.py',
           'decoder/skill_observed_projectile_metrics.py',
           'decoder/skill_static_projectile_metrics.py',
           'decoder/skill_fast_projectile_lifetimes.py',
           'decoder/skill_summon_railgun_metrics.py',
           'decoder/skill_autonomous_projectile_metrics.py',
           'decoder/skill_owned_followup_metrics.py',
            'decoder/skill_owned_state_metrics.py','decoder/skill_owned_object_metrics.py','decoder/skill_fenrir_absorb_metrics.py',
           'decoder/skill_target_count_metrics.py',
           'decoder/skill_team_target_metrics.py',
           'decoder/skill_johann_ally_projectile_metrics.py',
           'decoder/skill_cathy_region_metrics.py','decoder/skill_fiora_q_region.py','decoder/skill_pose_events.py','schema/unity-native-yaw-forward-v1.bin',
           'decoder/skill_barbara_child_metrics.py','decoder/skill_barbara_region_execution.py',
           'decoder/corpus_barbara_region_inputs.py','schema/barbara-base-region-runtime-v1.json',
           'schema/post-stop-current-position-bound-v1.json',
           'decoder/skill_evaluation_position_bounds.py','decoder/corpus_pose_evidence.py','schema/fiora-q-position-envelope-v1.json',
           'schema/native-rotation-lock-counter-v1.json',
           'schema/ordinary-next-source-cell-bound-v1.json','schema/non-nav-linear-prefix-bound-v1.json',
           'schema/straight-nav-radius-prefix-bound-v1.json',
           'decoder/skill_arda_parent_metrics.py',
           'decoder/skill_persistent_cc_metrics.py','decoder/skill_effect_linked_cc_metrics.py','decoder/skill_projectile_cc_metrics.py','decoder/skill_projectile_state_metrics.py','decoder/recalculate_incremental_skill_scope.py',
           'decoder/skill_action_stage_evidence.py','data/skill_id_enum_12_3_0.json',
           'decoder/projectile_hit_catalog.py','decoder/requested_skill_hit_rates.py',
           'decoder/requested_wall_stun_metrics.py','decoder/delta_payloads.py',
           'acquire/audit_requested_skills_once.py']
    return files


def implementation_file_hashes(*,precompiled=False):
    root=SCOPE_PATH.parent.parent
    files=implementation_source_files()
    try:
        from .skill_implementation_provenance import implementation_dependency_hashes,prepared_dependency_hashes
    except ImportError:
        from skill_implementation_provenance import implementation_dependency_hashes,prepared_dependency_hashes
    if precompiled:
        return prepared_dependency_hashes(root,files,root/'schema/implementation-source-closure-v1.json')
    return implementation_dependency_hashes(root,files)


_fingerprint_scope = ContextVar('skill_implementation_fingerprint_scope', default=None)


def _fresh_implementation_fingerprint():
    hashes=implementation_file_hashes(precompiled=True)
    return hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()


def implementation_fingerprint():
    scoped = _fingerprint_scope.get()
    return scoped if scoped is not None else _fresh_implementation_fingerprint()


@contextmanager
def verified_implementation_scope():
    """Share provenance within one calculation; reject changes before returning."""
    if _fingerprint_scope.get() is not None:
        yield
        return
    original = _fresh_implementation_fingerprint()
    token = _fingerprint_scope.set(original)
    try:
        yield
        if _fresh_implementation_fingerprint() != original:
            raise ValueError('skill implementation changed during calculation')
    finally:
        _fingerprint_scope.reset(token)

# Explicit stage identities, never derived from an effect-code numeric prefix.
FIRST_BATCH = {
    1: [(1001200,'Q1','any'), (1001210,'Q2','any')],
    2: [(1002300,'W','shot'), (1002500,'R 공포','fear')],
    3: [(1003200,'Q 전체 적중 + 끝부분 확정 횟수','any'),
        (1003300,'W','any'), (1003400,'E1','any'), (1003500,'R1','any'),
        (1003510,'R2','any'), (1003520,'R3','any')],
    4: [(1004200,'Q','any'), (1004400,'E 일반 타격','any'),
        (1004400,'E 벽 기절','wall'), (1004500,'R','any')],
    5: [(1005200,'Q','any'), (1005300,'W','any'), (1005400,'E','any'), (1005500,'R','any')],
}


def active_manifest():
    """Current user scope, shared by normal analysis and cache recalculation.

    manifest() remains the complete versioned rule catalogue for compilation
    and explicitly requested diagnostics. User-excluded rows are not defaults.
    """
    path=SCOPE_PATH.parent.parent/'derived/skill-scope-request-bundles-current.json'
    authority=json.loads(path.read_bytes())
    ids={row['metricId'] for bundle in authority['bundles'] if bundle['status']!='excluded-user-request'
         for row in bundle['relatedMetrics'] if row['status']!='excluded-user-request'}
    specs=manifest()
    missing=ids-{spec['metricId'] for spec in specs}
    if missing:raise ValueError('active request contains unregistered metric identities: '+', '.join(sorted(missing)))
    amendment=json.loads((SCOPE_PATH.parent/'user-hit-rate-scope-20260912.json').read_bytes())
    if amendment.get('format')!='er-user-hit-rate-scope-amendment.v1':
        raise ValueError('Invalid explicit user scope amendment')
    excluded=set(amendment['excludedMetricIds'])
    if not excluded <= {spec['metricId'] for spec in specs}:
        raise ValueError('User scope amendment contains unregistered metric identities')
    return [spec for spec in specs if spec['metricId'] in ids and spec['metricId'] not in excluded]


def scope_contract():
    value = json.loads(SCOPE_PATH.read_text(encoding='utf-8'))
    if (value['policyId'] != POLICY_ID or value['clientVersion'] != CLIENT_VERSION or
        value['gameDataSha256'] != GAME_DB_SHA256 or
        [r['characterCode'] for r in value['characters']] != list(range(1,91))):
        raise ValueError('requested all-90 scope contract mismatch')
    return value


def manifest():
    registry=json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
    if registry.get('scopeSourceSha256')!=hashlib.sha256(SCOPE_PATH.read_bytes()).hexdigest() or registry.get('gameDataSha256')!=GAME_DB_SHA256:
        raise ValueError('compiled all-90 scope does not match its adopted source and gameDb')
    if [r['characterCode'] for r in registry['characters']]!=list(range(1,91)):
        raise ValueError('compiled scope must preserve all 90 characters')
    output=[]
    for row in registry['characters']:
        character=row['characterCode']
        for stage in row['registeredStages']:
            group,label,mode=stage['skillGroup'],stage['label'],stage['mode']
            spec=dict(metricId=f'{POLICY_ID}:{character}:{group}:{mode}',
                 characterCode=character,skillGroup=group,label=label,mode=mode,
                 unit='emitted-shot' if mode.startswith('summon-railgun-') else 'projectile-shot' if mode in {'shot','shot-with-camera','summon-shot','autonomous-shot','intercepted-shot'} else 'skill-cast',
                 numerator={'shot':'enemy-hit-actual-shots','fear':'enemy-fear-application-casts',
                            'shot-with-camera':'enemy-player-or-camera-hit-actual-shots','enemy-kill':'enemy-kills-by-judgment',
                            'summon-shot':'enemy-hit-actual-summon-shots',
                            'autonomous-shot':'enemy-hit-actual-autonomous-shots',
                            'execute-success':'execution-casts-killing-enemy',
                            'movement-success':'successful-wall-connection-movement-casts',
                            'world-edge-stun':'casts-applying-world-edge-stun-to-enemy',
                            'enemy-pull':'casts-applying-pull-state-to-enemy',
                            'self-pull':'casts-pulling-self',
                            'scan':'casts-scanning-enemy',
                            'target-count':'casts-acquiring-at-least-one-enemy-target',
                            'attach':'casts-attaching-skill-to-enemy',
                            'pull-hit':'casts-applying-second-hit-pull-to-enemy',
                            'guard-success':'casts-with-recorded-guard-success',
                            'blind':'enemy-player-targeted-casts-applying-blindness',
                            'fetter':'casts-applying-fetter-to-enemy',
                            'stun':'casts-applying-stun-to-enemy',
                            'both-hit':'casts-hitting-the-same-enemy-with-both-stages',
                            'marked-hit':'casts-hitting-crystal-marked-enemy',
                            'unmarked-hit':'casts-hitting-unmarked-enemy',
                            'inner-hit':'casts-hitting-enemy-in-inner-damage-region',
                            'outer-hit':'casts-hitting-enemy-in-outer-damage-region',
                            'central-stun':'casts-applying-central-stun-to-enemy',
                            'intercepted-shot':'actual-shots-hitting-an-enemy-other-than-aimed-target',
                            'wall':'wall-stun-success-casts','tip':'casts-hitting-any-enemy-with-tip',
                            'non-tip':'casts-hitting-any-enemy-with-non-tip'}.get(mode,'casts-hitting-any-enemy'))
            if character==30 and group==1030300 and mode=='any':
                spec.update(label='W 도발 성공률', numerator='casts-applying-taunt-to-any-enemy')
            if character==41 and mode in {'ally','either-team'}:
                spec.update(targetCohort=mode,numerator='casts-applying-effect-to-'+mode)
            if character>5:
                spec.update(scopeTier=stage['scopeTier'],reportMultiTarget=stage.get('reportMultiTarget',True))
            if mode.startswith('summon-railgun-'):
                spec.update(scopeEvent='attack-emission',numerator='enemy-hit-actual-railgun-emissions')
            if mode=='autonomous-shot':
                spec.update(scopeEvent='projectile-spawn',label='T 회수 단검 자동 발사')
            if group==1072500 and mode in {'shot','intercepted-shot'}:
                spec['scopeEvent']='projectile-spawn'
            if mode=='summon-shot':
                spec['scopeEvent']='projectile-spawn' if group in {1026200,1026210} else 'cast-start'
                if group in {1026200,1026210}:
                    spec['label']='Q 일반 센트리건 일반탄' if group==1026200 else 'RQ 강화 센트리건 일반탄'
            elif group==1076510:
                spec['label']='R2 처형 성공'
            elif group==1045500:
                spec.update(label='R 자기 사용 후 W 연계 피해',numerator='self-targeted-R-casts-hitting-enemy-with-followup')
            elif group==1045300:
                spec['label']='W 종료 범위 피해'
            elif group==1047400:
                spec.update(label='E1 벽 연결 이동 성공',reportMultiTarget=False)
            elif group==1064500:
                spec['label']='R 첫 타격'
            elif group in (1028200,1028510) and mode=='any':
                spec.update(phaseScope='any-hit-only')
            elif group==1029500 and mode=='any':
                spec.update(label='R 첫 타격',phaseScope='initial-wave-hit-only')
            elif group==1081500:
                spec['label']='R 가장자리 기절' if mode=='world-edge-stun' else 'R 누적 공격 후 범위 피해'
            elif group==1043500:
                spec['label']='R 융합 폭탄 폭발'
            elif group==1079300:
                spec['label']='W 두 차례 포격'
            elif group==1015400 and mode=='any':
                spec['label']='E 일반 접촉'
            elif group==1072500:
                spec['label']='R 타겟 포착 인원'
            elif group in {1023300,1026400} and mode=='any':
                spec['label']=('W' if group==1023300 else 'E')+' 전체 적중(영역 미분리)'
            if group==1052410 and mode=='any':
                spec['directGroundTargetDeathPolicy']='count-identified-enemy-branch-as-hit'
            if mode=='blind':
                spec.update(label=('W' if group==1028300 else 'R-W')+' 적 지정 실명 적용',attemptScope='enemy-player-targeted-cast')
            output.append(spec)
    return output


def runtime_source_group(spec):
    if spec['mode']=='summon-railgun-normal':return 1026200
    if spec['mode']=='summon-railgun-reinforced':return 1026210
    if spec['skillGroup']==1062210:return 1062200
    return spec['skillGroup']


def exact_cast_lifetimes(starts, finishes, player):
    """Pair each explicit same-player/same-wire-skill start with one finish.

    Every finish in the relevant identities must also have one owner. Do not use
    cooldowns, animation estimates, the next cast alone, or a nearest event.
    """
    by_identity = defaultdict(list)
    for start in starts:
        by_identity[start['skillIdCode']].append(start)
    output, used = [], set()
    try:
        from .skill_wire_order import player_finishes
    except ImportError:
        from skill_wire_order import player_finishes
    relevant = [f for f in player_finishes(finishes,player) if f.get('playerObjectId')==player and f.get('skillIdCode') in by_identity]
    if any(f.get('reason') == 15 for f in relevant):
        return None, '재생 종료 표식은 게임 내 시전 결과를 확정하는 종료가 아님'
    for identity, rows in by_identity.items():
        identity_ends=[(j,f) for j,f in enumerate(relevant) if f['skillIdCode']==identity]
        ordered=all(r.get('wireCategory')=='commands' and isinstance(r.get('wireOrder'),list)
                    and len(r['wireOrder'])==2 and all(type(x) is int and x>=0 for x in r['wireOrder'])
                    for r in [*rows,*[f for _,f in identity_ends]])
        if ordered:
            # Same-frame finish -> start is unambiguous within the ordered
            # command stream. Tick-only caches must retain the conservative path.
            stream=[(tuple(s['wireOrder']),'start',s,None) for s in rows]
            stream += [(tuple(f['wireOrder']),'finish',f,j) for j,f in identity_ends]
            if len({f['tick'] for _,f in identity_ends})!=len(identity_ends):
                return None,'동일 프레임의 여러 종료 결과는 현재 타격 수명 표현으로 구별 불가'
            if len({order for order,_,_,_ in stream})!=len(stream):
                return None,'시전/종료 명령 순서가 중복되어 연결 불가'
            active=None;last_tick=None
            for _,kind,event,index in sorted(stream,key=lambda value:value[0]):
                if last_tick is not None and event['tick']<last_tick:
                    return None,'명령 순서와 시전 시간 순서 불일치'
                last_tick=event['tick']
                if kind=='start':
                    if active is not None:return None,'실제 명령 순서에서도 같은 스킬 시전이 겹침'
                    active=event
                else:
                    if active is None:return None,'실제 명령 순서에서 시작 없는 종료'
                    output.append((active,event['tick']));used.add(index);active=None
            if active is not None:return None,'실제 명령 순서에서 종료 없는 시전'
            continue
        rows = sorted(rows, key=lambda s:s['tick'])
        for i,start in enumerate(rows):
            next_tick = rows[i+1]['tick'] if i+1<len(rows) else float('inf')
            ends = [(j,f) for j,f in enumerate(relevant)
                    if f['skillIdCode']==identity and start['tick']<=f['tick']<next_tick]
            if len(ends)!=1 or ends[0][0] in used:
                return None, '시전과 종료가 모두 일대일로 연결되지 않음'
            used.add(ends[0][0])
            output.append((start,ends[0][1]['tick']))
    if len(used)!=len(relevant):
        return None, '시전 없이 남은 종료 이벤트가 있어 완전한 시전 수명 아님'
    return output,None


def state_application_metric(spec, starts, finishes, states, player, teams,
                             intervals, state_rows, state_groups):
    """Aya R measures actual fear application; immunity is not fear success."""
    if spec['skillGroup']!=1002500 or spec['mode']!='fear':
        return _unavailable(spec,'이 스킬의 상태 적용 판정은 아직 검증되지 않음')
    definitions = [r for r in state_groups if r.get('group')==1002500]
    if len(definitions)!=1 or definitions[0].get('stateType')!='Fear' or definitions[0].get('skillId')!='Fear':
        return _unavailable(spec,'같은 gameDb의 아야 R 전용 Fear 정의 불일치')
    codes = {r['code'] for r in state_rows if r.get('group')==1002500}
    if not codes or not starts:
        return _unavailable(spec,'전용 상태 코드 또는 시전이 관측되지 않음')
    lifetimes,reason = exact_cast_lifetimes(starts,finishes,player)
    if reason:
        return _unavailable(spec,reason)
    contacts = [set() for _ in lifetimes]
    for state in states:
        if (state.get('event')!='add' or state.get('casterObjectId')!=player or
            state.get('stateCode') not in codes or state.get('targetObjectId') not in teams or
            teams[state['targetObjectId']]==teams[player]):
            continue
        owners = [i for i,(start,end) in enumerate(lifetimes) if start['tick']<=state['tick']<=end]
        if len(owners)!=1:
            return _unavailable(spec,'R 공포 적용이 한 시전의 실제 수명에 배타적으로 연결되지 않음')
        contacts[owners[0]].add((state['tick'],state['targetObjectId']))
    eligible = [c for c,(start,_) in zip(contacts,lifetimes) if any(l<=start['tick']<r for l,r in intervals)]
    result = _result(spec,eligible,'exact-Aya-R-cast-finish-and-dedicated-Fear-state',
                     cast_ticks=[s['tick'] for s,_ in lifetimes if any(l<=s['tick']<r for l,r in intervals)])
    result['interpretation']='실제 적 공포 적용 성공률; 공포 면역 대상은 공포 성공으로 세지 않음'
    return result


@with_execution_plan
def calculate_scope_metrics(catalog,runtime,starts,spawns,collisions,teams,intervals,
                            *,client_version,game_db_sha256,wall_inputs,damages=None,effect_rows=None,projectile_owners=None,
                            projectile_terminals=None,raw_actions=None,summon_inputs=None,metric_specs=None,direct_heals=None,_output_players=None,
                            _preparation=None):
    try:
        from .skill_game_data_contract import revision_identity
    except ImportError:
        from skill_game_data_contract import revision_identity
    revision_identity(client_version,game_db_sha256)
    scope_contract()
    try:
        from .skill_wire_order import IndexedFinishEvents
    except ImportError:
        from skill_wire_order import IndexedFinishEvents
    wall_inputs={**wall_inputs,'finishes':IndexedFinishEvents(wall_inputs['finishes'])}
    if _preparation is not None:
        niah_q_preparations = _preparation.setdefault('niah_q_by_player', {})
    else:
        niah_q_preparations = {}
    summon_inputs = {**(summon_inputs or {}),
                     '_niah_q_preparation_store': niah_q_preparations}
    from .skill_match_end_scope import match_end_combat_intervals
    end_policy=json.loads((SCOPE_PATH.parent/'user-hit-rate-scope-20260912.json').read_bytes()).get('postMatchCastPolicy',{})
    match_end_scope=None
    if end_policy.get('enabled') is True:
        intervals,match_end_scope=match_end_combat_intervals(intervals,starts,wall_inputs.get('gameTerminals'),(summon_inputs or {}).get('gaps'))
    output = {}
    secondary={}
    child_casts={}
    owned_graphs={}
    current_specs=manifest()
    scope_specs=active_manifest() if metric_specs is None else metric_specs
    if any(spec not in current_specs for spec in scope_specs):
        raise ValueError('partial calculation requires exact current manifest specs')
    player_characters={p['objectId']:p['characterCode'] for p in (summon_inputs or {}).get('players',[])}
    autonomous_players={s['ownerPlayerObjectId'] for s in spawns
                        if s['projectileCode']==101802 and player_characters.get(s['ownerPlayerObjectId'])==18}
    if summon_inputs and summon_inputs.get('players'):
        try:
            from .skill_action_stage_evidence import load_exact_skill_ids,observe_action_stages
        except ImportError:
            from skill_action_stage_evidence import load_exact_skill_ids,observe_action_stages
        secondary,_=observe_action_stages(catalog,load_exact_skill_ids(),{s['skillGroup'] for s in scope_specs},
            summon_inputs['players'],starts,raw_actions or [],summon_inputs.get('nonPlayerSkillStarts'),
            summon_inputs.get('summons',[]),projectile_terminals or [],intervals)
        try:
            from .skill_owned_stage_casts import owned_stage_cast_evidence
        except ImportError:
            from skill_owned_stage_casts import owned_stage_cast_evidence
        child_casts,_=owned_stage_cast_evidence(catalog,load_exact_skill_ids(),{s['skillGroup'] for s in scope_specs},
            summon_inputs['players'],summon_inputs.get('nonPlayerSkillStarts'),wall_inputs['finishes'],
            wall_inputs.get('state_scripts'),summon_inputs.get('summons',[]),projectile_terminals or [],
            intervals,wall_inputs.get('skill_rows',[]),wall_inputs.get('state_groups',[]))
    by_collision = defaultdict(set)
    for c in collisions:
        by_collision[c['projectileObjectId']].add((c['tick'],c['targetObjectId']))
    for player_key in dict.fromkeys([*runtime['players'],*[str(p) for p in secondary],*[str(p) for p in child_casts],*[str(p) for p in sorted(autonomous_players)]]):
        if _output_players is not None and int(player_key) not in _output_players:continue
        legacy_rows=runtime['players'].get(player_key,[])
        player=int(player_key)
        player_starts=[s for s in starts if s['playerObjectId']==player]
        player_spawns=[s for s in spawns if s['ownerPlayerObjectId']==player]
        by_group={r['skillGroup']:r for r in legacy_rows}
        rows=[]
        arda_child_use_cache={}
        for spec in scope_specs:
            begin_metric(spec, player)
            group=spec['skillGroup']
            character=spec['characterCode']
            if group in {1028500,1044500}:
                if player_characters.get(player)!=character:continue
                try:
                    from .skill_static_applicability import static_applicability_metric
                except ImportError:
                    from skill_static_applicability import static_applicability_metric
                result=static_applicability_metric(spec,player_starts,player)
                if result is not None:
                    rows.append(result)
                    continue
            if group in {1026420,1026430}:
                if player_characters.get(player)!=26:continue
                try:
                    from .skill_barbara_child_metrics import barbara_child_metric
                except ImportError:
                    from skill_barbara_child_metrics import barbara_child_metric
                inputs=summon_inputs or {}
                result=barbara_child_metric(spec,inputs.get('nonPlayerSkillStarts'),wall_inputs['finishes'],
                    inputs.get('summons',[]),projectile_terminals or [],damages or [],player,teams,
                    intervals.get(player,[]),catalog,wall_inputs['skill_rows'],inputs.get('summon_rows',[]),effect_rows or [],inputs.get('gaps'))
                result.update(allCastCount=result['observedOwnedChildCastCount'],combatCastCount=result['observedCombatChildCastCount'])
                rows.append(result)
                continue
            if character==24 and group in {1024300,1024400}:
                if player_characters.get(player)!=character:continue
                try:
                    from .skill_adela_parent_state_metrics import adela_parent_state_metric
                except ImportError:
                    from skill_adela_parent_state_metrics import adela_parent_state_metric
                inputs=summon_inputs or {}
                result=adela_parent_state_metric(spec,player_starts,wall_inputs['finishes'],wall_inputs.get('state_scripts'),
                    inputs.get('summons',[]),projectile_terminals or [],raw_actions or [],damages or [],player,teams,
                    intervals.get(player,[]),wall_inputs['skill_rows'],wall_inputs.get('state_groups') or [],effect_rows or [],inputs.get('gaps'),
                    game_terminals=wall_inputs.get('gameTerminals'),state_inventory=inputs.get('adelaStateInventory'))
                result.update(allCastCount=result['observedCastCount'],combatCastCount=result['observedCombatCastCount'])
                rows.append(result)
                continue
            if group==1014310:
                if player_characters.get(player)!=character:continue
                try:
                    from .skill_state_removal_burst_metrics import state_removal_burst_metric
                except ImportError:
                    from skill_state_removal_burst_metrics import state_removal_burst_metric
                result=state_removal_burst_metric(spec,player_starts,wall_inputs['finishes'],wall_inputs.get('state_scripts'),
                    raw_actions or [],damages or [],player,teams,intervals.get(player,[]),catalog,wall_inputs['skill_rows'],
                    wall_inputs.get('state_groups') or [],effect_rows or [],(summon_inputs or {}).get('gaps'))
                result.update(allCastCount=result['observedCastCount'],combatCastCount=result['observedCombatCastCount'])
                rows.append(result)
                continue
            if group==1057330:
                if player_characters.get(player)!=character:continue
                try:
                    from .skill_martina_state_execution_metrics import state_execution_metric
                except ImportError:
                    from skill_martina_state_execution_metrics import state_execution_metric
                inputs=summon_inputs or {}
                result=state_execution_metric(spec,wall_inputs.get('state_scripts'),inputs.get('summons',[]),
                    projectile_terminals or [],raw_actions or [],damages or [],player,teams,intervals.get(player,[]),
                    catalog,inputs.get('summon_rows',[]),effect_rows or [],wall_inputs.get('state_groups') or [],inputs.get('gaps'))
                result.update(allCastCount=result['observedOwnedChildCastCount'],combatCastCount=result['observedCombatChildCastCount'])
                rows.append(result)
                continue
            if group==1081600:
                if player_characters.get(player)!=character:continue
                try:
                    from .skill_owned_child_projectile_metrics import owned_child_projectile_metric
                except ImportError:
                    from skill_owned_child_projectile_metrics import owned_child_projectile_metric
                inputs=summon_inputs or {}
                result=owned_child_projectile_metric(spec,inputs.get('nonPlayerSkillStarts'),wall_inputs['finishes'],
                    inputs.get('summons',[]),spawns,collisions,projectile_terminals or [],raw_actions,damages or [],player,teams,
                    intervals.get(player,[]),catalog,wall_inputs['skill_rows'],inputs.get('summon_rows',[]),effect_rows or [],inputs.get('gaps'))
                result.update(allCastCount=result['observedOwnedChildCastCount'],combatCastCount=result['observedCombatChildCastCount'])
                rows.append(result)
                continue
            if group==1015400 and spec['mode'] in {'any','enemy-pull','self-pull'}:
                if player_characters.get(player)!=15:continue
                try:
                    from .skill_sissela_e_execution import sissela_e_execution_metric
                except ImportError:
                    from skill_sissela_e_execution import sissela_e_execution_metric
                result=sissela_e_execution_metric(spec,starts,(wall_inputs or {}).get('finishes',[]),player,
                    teams,intervals.get(player,[]),catalog,
                    {**(summon_inputs or {}),'collisions':collisions,'terminals':projectile_terminals,
                     'stateScripts':(wall_inputs or {}).get('state_scripts'),'movement':(wall_inputs or {}).get('movement'),
                     'gameTerminals':(wall_inputs or {}).get('gameTerminals')})
                result.update(allCastCount=result['observedCastCount'],combatCastCount=result['observedCombatCastCount'])
                rows.append(result)
                continue
            if group in {1015200,1015700} and spec['mode']=='any':
                if player_characters.get(player)!=15:continue
                try:
                    from .skill_sissela_q_execution import sissela_q_execution_metric
                except ImportError:
                    from skill_sissela_q_execution import sissela_q_execution_metric
                result=sissela_q_execution_metric(spec,starts,(wall_inputs or {}).get('finishes',[]),player,
                    teams,intervals.get(player,[]),catalog,
                    {**(summon_inputs or {}),'terminals':projectile_terminals,'actions':raw_actions,'damages':damages,
                     'gameTerminals':(wall_inputs or {}).get('gameTerminals')})
                result.update(allCastCount=result['observedCastCount'],combatCastCount=result['observedCombatCastCount'])
                rows.append(result)
                continue
            if group in {1066600,1066610,1066700,1066710,1015700,1057330,1081710,1081720}:
                if player_characters.get(player)!=character:continue
                try:
                    from .skill_owned_child_damage_metrics import owned_child_damage_metric
                except ImportError:
                    from skill_owned_child_damage_metrics import owned_child_damage_metric
                inputs=summon_inputs or {}
                result=owned_child_damage_metric(spec,inputs.get('nonPlayerSkillStarts'),wall_inputs['finishes'],
                    inputs.get('summons',[]),projectile_terminals or [],raw_actions or [],damages or [],player,teams,
                    intervals.get(player,[]),catalog,wall_inputs['skill_rows'],inputs.get('summon_rows',[]),
                    effect_rows or [],inputs.get('gaps'),state_scripts=wall_inputs.get('state_scripts'),
                    state_groups=wall_inputs.get('state_groups'))
                result.update(allCastCount=result['observedOwnedChildCastCount'],
                              combatCastCount=result['observedCombatChildCastCount'])
                rows.append(result)
                continue
            if group==1052400 and spec['mode']=='any':
                if player_characters.get(player)!=52:continue
                try:
                    from .skill_adina_e_celestial_execution import adina_e_celestial_execution_metric
                    from .skill_development_effect_metrics import development_policy
                except ImportError:
                    from skill_adina_e_celestial_execution import adina_e_celestial_execution_metric
                    from skill_development_effect_metrics import development_policy
                result=adina_e_celestial_execution_metric(spec,starts,(wall_inputs or {}).get('finishes',[]),player,
                    teams,intervals.get(player,[]),catalog,
                    {**(summon_inputs or {}),'collisions':collisions,'terminals':projectile_terminals,
                     'stateScripts':(wall_inputs or {}).get('state_scripts')},development=development_policy().get('lifecycleEstimatesEnabled',False))
                result.update(allCastCount=result['observedCastCount'],combatCastCount=result['observedCombatCastCount'])
                rows.append(result)
                continue
            if group==1052410 and spec['mode']=='any':
                if player_characters.get(player)!=52:continue
                try:
                    from .skill_adina_star_e_execution import adina_star_e_execution_metric
                except ImportError:
                    from skill_adina_star_e_execution import adina_star_e_execution_metric
                result=adina_star_e_execution_metric(spec,starts,(wall_inputs or {}).get('finishes',[]),player,
                    teams,intervals.get(player,[]),catalog,
                    {**(summon_inputs or {}),'collisions':collisions,'terminals':projectile_terminals,
                     'stateScripts':(wall_inputs or {}).get('state_scripts'),'damages':damages})
                result.update(allCastCount=result['observedCastCount'],combatCastCount=result['observedCombatCastCount'])
                rows.append(result)
                continue
            if group==1065410 and spec['mode']=='any':
                if player_characters.get(player)!=65:continue
                try:
                    from .skill_debi_marlene_e2_execution import debi_marlene_e2_execution
                except ImportError:
                    from skill_debi_marlene_e2_execution import debi_marlene_e2_execution
                result=debi_marlene_e2_execution(spec,starts,(wall_inputs or {}).get('finishes',[]),player,
                    teams,intervals.get(player,[]),catalog,
                    {'actions':raw_actions,'movement':(wall_inputs or {}).get('movement'),'damages':damages,
                     'states':(wall_inputs or {}).get('states',[]),'gaps':(summon_inputs or {}).get('gaps')})
                result.update(allCastCount=result['observedCastCount'],combatCastCount=result['observedCombatCastCount'])
                rows.append(result)
                continue
            if group==1046500 and spec['mode']=='any':
                if player_characters.get(player)!=46:continue
                try:
                    from .skill_aiden_first_thunderbolt import aiden_first_thunderbolt
                except ImportError:
                    from skill_aiden_first_thunderbolt import aiden_first_thunderbolt
                result=aiden_first_thunderbolt(spec,player,teams,intervals.get(player,[]),catalog,
                    {**(summon_inputs or {}),'starts':player_starts,'collisions':collisions,
                     'terminals':projectile_terminals,'damages':damages,'states':(wall_inputs or {}).get('states'),
                     'gameTerminals':(wall_inputs or {}).get('gameTerminals')})
                result.update(allCastCount=result['observedCastCount'],combatCastCount=result['observedCombatCastCount'])
                rows.append(result)
                continue
            if group==1046510 and spec['mode']=='any':
                if player_characters.get(player)!=46:continue
                try:
                    from .skill_aiden_second_thunderbolt import aiden_second_thunderbolt_metric
                except ImportError:
                    from skill_aiden_second_thunderbolt import aiden_second_thunderbolt_metric
                result=aiden_second_thunderbolt_metric(spec,player,teams,intervals.get(player,[]),catalog,
                    {**(summon_inputs or {}),'starts':player_starts,'actions':raw_actions,
                     'collisions':collisions,'terminals':projectile_terminals,'damages':damages,
                     'gameTerminals':(wall_inputs or {}).get('gameTerminals')})
                result.update(allCastCount=result['observedCastCount'],combatCastCount=result['observedCombatCastCount'],
                    castCountMeaning='actual second-lightning projectile creations; optional warp is not another attempt')
                rows.append(result)
                continue
            if group in {1040300,1040310,1040350}:
                if player_characters.get(player)!=40:continue
                try:
                    from .skill_chloe_w_phases import chloe_w_phase_metric
                    from .skill_development_effect_metrics import development_policy
                except ImportError:
                    from skill_chloe_w_phases import chloe_w_phase_metric
                    from skill_development_effect_metrics import development_policy
                inputs={**(summon_inputs or {}),'terminals':projectile_terminals,
                        'damages':damages,'actions':raw_actions,
                        'rotationEvents':wall_inputs.get('rotationEvents')}
                result=chloe_w_phase_metric(spec,player_starts,wall_inputs['finishes'],
                    player,teams,intervals.get(player,[]),catalog,inputs,development=development_policy().get('lifecycleEstimatesEnabled',False))
                result.update(allCastCount=result['observedCastCount'],combatCastCount=result['observedCombatCastCount'])
                rows.append(result)
                continue
            if group in {1040200,1040250}:
                if player_characters.get(player)!=40:continue
                try:
                    from .skill_chloe_q_execution import chloe_q_execution_metric
                except ImportError:
                    from skill_chloe_q_execution import chloe_q_execution_metric
                inputs={**(summon_inputs or {}),'terminals':projectile_terminals,
                        'damages':damages}
                result=chloe_q_execution_metric(spec,player_starts,wall_inputs['finishes'],
                    player,teams,intervals.get(player,[]),catalog,inputs)
                result.update(allCastCount=result['observedCastCount'],combatCastCount=result['observedCombatCastCount'])
                rows.append(result)
                continue
            if group in {1040400,1040410,1040450}:
                if player_characters.get(player)!=40:continue
                try:
                    from .skill_chloe_e_execution import chloe_e_execution_metric
                except ImportError:
                    from skill_chloe_e_execution import chloe_e_execution_metric
                inputs={**(summon_inputs or {}),'terminals':projectile_terminals,
                        'damages':damages,'movement':wall_inputs.get('movement')}
                result=chloe_e_execution_metric(spec,player_starts,wall_inputs['finishes'],
                    player,teams,intervals.get(player,[]),catalog,inputs)
                result.update(allCastCount=result['observedCastCount'],combatCastCount=result['observedCombatCastCount'])
                rows.append(result)
                continue
            if spec['mode']=='autonomous-shot':
                if player not in autonomous_players:continue
                try:
                    from .skill_autonomous_projectile_metrics import autonomous_shot_metric
                except ImportError:
                    from skill_autonomous_projectile_metrics import autonomous_shot_metric
                result=autonomous_shot_metric(spec,player_spawns,collisions,projectile_terminals or [],damages or [],
                    player,teams,intervals.get(player,[]),catalog,(summon_inputs or {}).get('projectile_rows',[]),effect_rows or [])
                result.update(allCastCount=0,combatCastCount=0,castStartApplicable=False)
                rows.append(result)
                continue
            source_group=runtime_source_group(spec)
            if source_group not in by_group and not (group==1062210 and 1062200 in by_group):
                stage_applies=secondary_stage_applies(spec,player,summon_inputs)
                child=child_casts.get(player,{}).get(group) if stage_applies else None
                if child:
                    result=_unavailable(spec,'소유 소환물의 실제 스킬 사용은 확인됨; 적중·미적중 연결은 미확정')
                    result.update(allCastCount=child['observedOwnedChildCastCount'],
                        combatCastCount=child['observedCombatChildCastCount'],ownedChildCastEvidence=child,
                        castCountMeaning='explicit independent owned child skill starts; not parent uses')
                    rows.append(result)
                elif stage_applies and group in secondary.get(player,{}):
                    result=_unavailable(spec,'정확한 스킬 행동·소환물 시전은 관측됐지만 플레이어 시도와 후속 단계의 완전한 귀속 미확정')
                    result.update(allCastCount=0,combatCastCount=0,actionStageEvidence=secondary[player][group])
                    rows.append(result)
                elif player_characters.get(player)==character:
                    rows.append(absent_scope_source_row(spec,player_starts,source_group,raw_actions,summon_inputs,wall_inputs))
                continue
            legacy=by_group[source_group]
            selected=[s for s in player_starts if s['skillGroup']==group]
            route_id = planned_route(spec)
            if route_id:
                inputs=summon_inputs or {}
                result=execute_planned(spec, dict(starts=player_starts, finishes=wall_inputs['finishes'],
                    legacy=legacy, selected=selected, window=runtime['linkWindowTicks'], by_collision=by_collision,
                    actions=raw_actions if group in {1090500,1076300,1034300,1060300} else (raw_actions or []), spawns=player_spawns, collisions=collisions,
                    summons=inputs.get('summons') if group in {1076300,1034300} else inputs.get('summons',[]), terminals=projectile_terminals if group in {1031300,1076300,1082400,1034300,1060300} else (projectile_terminals or []),
                    damages=damages if group in {1090500,1076300,1031300,1082400,1034300,1060300} else (damages or []), deaths=wall_inputs.get('deaths',[]), player=player, teams=teams,
                    **({'game_terminals':wall_inputs.get('gameTerminals')} if group in {1090500,1076300,1031300,1082400,1034300,1060300} else {}),
                    **({'all_projectile_spawns':inputs.get('allProjectileSpawns')} if group in {1031300,1076300,1082400,1060300} else {}),
                    **({'raw_objects':inputs.get('objects')} if group==1034300 else {}),
                    intervals=intervals.get(player,[]), catalog=catalog, skill_rows=wall_inputs['skill_rows'],
                    summon_rows=inputs.get('summon_rows',[]), projectile_rows=inputs.get('projectile_rows',[]),
                    states=wall_inputs['states'], state_rows=wall_inputs['state_rows'], state_groups=wall_inputs['state_groups'],
                    effect_rows=effect_rows or [], gaps=inputs.get('gaps'),state_scripts=wall_inputs.get('state_scripts'),
                    raw_actions=raw_actions,nonPlayerSkillStarts=inputs.get('nonPlayerSkillStarts'),
                    projectile_owners=projectile_owners if group in {1076300,1082400} else (projectile_owners or {}),
                    route_inputs=dict(player_starts=player_starts, wall_inputs=wall_inputs,
                        player=player, teams=teams, intervals=intervals, catalog=catalog,
                        summon_inputs=summon_inputs, projectile_terminals=projectile_terminals,
                        raw_actions=raw_actions, damages=damages, player_spawns=player_spawns,
                        projectile_owners=projectile_owners, spawns=spawns, collisions=collisions, effect_rows=effect_rows,
                        arda_child_use_cache=arda_child_use_cache, direct_heals=direct_heals,
                        selected=selected)))
            elif group in {1060500,1054500} and spec['mode'] in {'any','stun'}:
                try:
                    from .skill_owned_object_metrics import owned_object_metric
                except ImportError:
                    from skill_owned_object_metrics import owned_object_metric
                inputs=summon_inputs or {}
                result=owned_object_metric(spec,player_starts,wall_inputs['finishes'],inputs.get('summons',[]),
                    projectile_terminals or [],damages or [],wall_inputs['states'],player,teams,intervals.get(player,[]),
                    catalog,wall_inputs['skill_rows'],inputs.get('summon_rows',[]),wall_inputs['state_rows'],
                    wall_inputs['state_groups'],effect_rows or [])
            elif group==1076510 and spec['mode']=='execute-success':
                try:
                    from .skill_garnet_execute_metrics import garnet_execute_metric
                except ImportError:
                    from skill_garnet_execute_metrics import garnet_execute_metric
                result=garnet_execute_metric(spec,player_starts,wall_inputs['finishes'],raw_actions or [],
                    wall_inputs.get('deaths',[]),player,teams,intervals.get(player,[]),catalog,
                    wall_inputs['skill_rows'])
            elif group==1060300 and spec['mode']=='any':
                try:
                    from .skill_tazia_wall_contact_metrics import tazia_wall_contact_metric
                except ImportError:
                    from skill_tazia_wall_contact_metrics import tazia_wall_contact_metric
                result=tazia_wall_contact_metric(spec,player_starts,wall_inputs['finishes'],raw_actions,
                    player_spawns,collisions,projectile_terminals,player,teams,intervals.get(player,[]),
                    catalog,wall_inputs['skill_rows'],(summon_inputs or {}).get('projectile_rows',[]),game_terminals=wall_inputs.get('gameTerminals'),gaps=(summon_inputs or {}).get('gaps'),all_projectile_spawns=(summon_inputs or {}).get('allProjectileSpawns'))
            elif group==1042500 and spec['mode'] in {'any','creation-hit','end-hit'}:
                try:
                    from .skill_created_object_phase_metrics import created_object_phase_metric
                except ImportError:
                    from skill_created_object_phase_metrics import created_object_phase_metric
                result=created_object_phase_metric(spec,player_starts,wall_inputs['finishes'],raw_actions or [],
                    (summon_inputs or {}).get('summons',[]),projectile_terminals or [],damages or [],player,
                    teams,intervals.get(player,[]),catalog,wall_inputs['skill_rows'],
                    (summon_inputs or {}).get('summon_rows',[]))
            elif group==1083300 and spec['mode']=='any':
                try:
                    from .skill_created_object_hit_metrics import created_object_hit_metric
                except ImportError:
                    from skill_created_object_hit_metrics import created_object_hit_metric
                result=created_object_hit_metric(spec,player_starts,wall_inputs['finishes'],raw_actions or [],
                    (summon_inputs or {}).get('summons',[]),projectile_terminals or [],damages or [],player,
                    teams,intervals.get(player,[]),catalog,wall_inputs['skill_rows'],
                    (summon_inputs or {}).get('summon_rows',[]),effect_rows or [])
            elif group in {1034300,1090500} and spec['mode'] in {'any','fetter','stun','marked-hit','unmarked-hit'}:
                try:
                    from .skill_action_cc_outcomes import action_cc_outcome_metric
                except ImportError:
                    from skill_action_cc_outcomes import action_cc_outcome_metric
                result=action_cc_outcome_metric(spec,player_starts,wall_inputs['finishes'],raw_actions,
                    wall_inputs['states'],damages,(summon_inputs or {}).get('summons'),
                    projectile_terminals,player,teams,intervals.get(player,[]),catalog,
                    wall_inputs['skill_rows'],wall_inputs['state_rows'],wall_inputs['state_groups'],
                    (summon_inputs or {}).get('summon_rows',[]),game_terminals=wall_inputs.get('gameTerminals'),gaps=(summon_inputs or {}).get('gaps'),raw_objects=(summon_inputs or {}).get('objects'))
            elif spec['mode']=='fetter' and group==1014400:
                try:
                    from .skill_projectile_state_metrics import projectile_state_metric
                except ImportError:
                    from skill_projectile_state_metrics import projectile_state_metric
                result=projectile_state_metric(spec,player_starts,wall_inputs['finishes'],player_spawns,collisions,
                    projectile_terminals or [],wall_inputs['states'],player,teams,intervals.get(player,[]),catalog,
                    wall_inputs['skill_rows'],wall_inputs['state_rows'],wall_inputs['state_groups'])
            elif spec['mode'] in {'attach','blind'}:
                try:
                    from .skill_projectile_state_metrics import projectile_state_metric
                except ImportError:
                    from skill_projectile_state_metrics import projectile_state_metric
                result=projectile_state_metric(spec,player_starts,wall_inputs['finishes'],player_spawns,collisions,
                    projectile_terminals or [],wall_inputs['states'],player,teams,intervals.get(player,[]),catalog,
                    wall_inputs['skill_rows'],wall_inputs['state_rows'],wall_inputs['state_groups'])
            elif group==1026400 and spec['mode'] in {'inner-hit','outer-hit'}:
                try:
                    from .skill_barbara_region_execution import barbara_region_execution
                except ImportError:
                    from skill_barbara_region_execution import barbara_region_execution
                result=barbara_region_execution(spec,player,teams,intervals.get(player,[]),catalog,
                    wall_inputs['skill_rows'],(summon_inputs or {}).get('barbaraRegionInputs'))
            elif group==1023300 and spec['mode'] in {'any','inner-hit','outer-hit'}:
                try:
                    from .skill_cathy_region_metrics import cathy_region_metric
                except ImportError:
                    from skill_cathy_region_metrics import cathy_region_metric
                result=cathy_region_metric(spec,player_starts,wall_inputs['finishes'],raw_actions,damages,
                    player,teams,intervals.get(player,[]),catalog,wall_inputs['skill_rows'],(summon_inputs or {}).get('gaps'))
            elif spec['mode'] in {'inner-hit','outer-hit','central-stun'}:
                result=_unavailable(spec,
                    '중앙 기절의 실제 스킬·대상 귀속이 필요하며 일반 피해나 안쪽 타격으로 대체하지 않음'
                    if spec['mode']=='central-stun' else
                    '안쪽/바깥쪽 피해 분기와 실제 대상의 연결이 필요함; 일반 적중·FX 이름·정적 범위로 영역을 추정하지 않음')
            elif group==1047400 and spec['mode']=='movement-success':
                try:
                    from .skill_wall_connection_movement import wall_connection_movement_metric
                except ImportError:
                    from skill_wall_connection_movement import wall_connection_movement_metric
                result=wall_connection_movement_metric(spec,player_starts,wall_inputs['finishes'],player_spawns,
                    projectile_terminals or [],raw_actions,wall_inputs.get('movement'),player,intervals.get(player,[]),
                    catalog,wall_inputs['skill_rows'],(summon_inputs or {}).get('gaps'))
            elif spec['mode'] in {'movement-success','execute-success','world-edge-stun','self-pull','scan','intercepted-shot'}:
                result=_unavailable(spec,{'movement-success':'실제 벽 연결·이동 완료가 필요하며 적 피해로 대체하지 않음',
                    'scan':'실제 스캔된 적 대상 귀속이 필요하며 드론 스캔 시작·종료나 이후 발사로 대체하지 않음',
                    'intercepted-shot':'탄환별 원래 조준 대상이 없어 실제 충돌한 적을 대신 맞은 적으로 분류할 수 없음',
                    'world-edge-stun':'R 가장자리의 실제 적 기절 귀속이 필요하며 누적 공격 범위 피해로 대체하지 않음',
                    'self-pull':'자기 끌기의 실제 이동 귀속이 필요하며 적 끌기나 보호막만으로 대체하지 않음',
                    'execute-success':'R2에 의한 실제 처치 원인 귀속이 필요하며 일반 타격으로 대체하지 않음'}[spec['mode']])
            elif spec['mode']=='shot-with-camera':
                result=_unavailable(spec,'실제 발사 수와 적 실험체·감시카메라 적중의 완전한 연결 필요')
                if group==1009300 and spec['characterCode']==9:
                    result.update(
                        reason='아이솔 W는 확인한 서버 코드에서 대상별 재타격 루프이며, 요청한 탄환 1발의 생성·기록 단위가 아직 확정되지 않음',
                        reasonCode='isol-W-native-shot-unit-unestablished',
                        denominatorEvidence={
                            'status':'native-target-rehit-loop-confirmed-shot-unit-unestablished',
                            'proof':'deliverables/isol-w-native-unit-exclusions-v2.json',
                            'excludedShotIdentities':['per-cast-IndividualId','per-cast-SkillNumber'],
                            'damageTermMeaning':'per-target-rehit-cooldown-not-global-shot-period',
                            'newReplayKnownToResolve':False,
                            'allPossibleWireRoutesExhausted':False,
                            'requestedBulletMetricRetained':True,
                            'castRateSubstituted':False})
            elif spec['mode']=='fear':
                result=state_application_metric(spec,selected,wall_inputs['finishes'],wall_inputs['states'],player,teams,
                    intervals.get(player,[]),wall_inputs['state_rows'],wall_inputs['state_groups'])
            elif spec['mode']=='wall':
                result=calculate_wall_stun_metrics(spec,selected,wall_inputs['finishes'],wall_inputs['states'],player,teams,
                    intervals.get(player,[]),wall_inputs['skill_rows'],wall_inputs['state_rows'],wall_inputs['state_groups'])
            elif group == 1035300 and effect_rows is not None:
                try:
                    from .skill_jan_direct_execution import jan_direct_execution
                except ImportError:
                    from skill_jan_direct_execution import jan_direct_execution
                result=jan_direct_execution(spec,player_starts,wall_inputs['finishes'],raw_actions,
                    damages,wall_inputs['states'],(summon_inputs or {}).get('summons'),projectile_terminals,
                    player,teams,intervals.get(player,[]),catalog,wall_inputs['skill_rows'],
                    wall_inputs['state_rows'],wall_inputs['state_groups'],effect_rows,
                    (summon_inputs or {}).get('summon_rows') or [],
                    state_inventory=wall_inputs.get('janStateInventory'),state_identity=wall_inputs.get('janStateIdentity'),rope_inventory=wall_inputs.get('janRopeInventory'))
            elif group == 1034400 and effect_rows is not None:
                try:
                    from .skill_projectile_schema_metrics import nathapon_e_schema_metric
                except ImportError:
                    from skill_projectile_schema_metrics import nathapon_e_schema_metric
                result=nathapon_e_schema_metric(spec,player_starts,wall_inputs['finishes'],
                    player_spawns,projectile_terminals or [],collisions,damages or [],
                    player,teams,intervals.get(player,[]))
            elif group == 1034200 and effect_rows is not None:
                try:
                    from .skill_projectile_schema_metrics import nathapon_q_schema_metric
                except ImportError:
                    from skill_projectile_schema_metrics import nathapon_q_schema_metric
                result=nathapon_q_schema_metric(spec,player_starts,wall_inputs['finishes'],
                    player_spawns,projectile_terminals or [],damages or [],
                    player,teams,intervals.get(player,[]))
            elif group == 1028200 and effect_rows is not None:
                try:
                    from .skill_projectile_schema_metrics import sua_q_schema_metric
                except ImportError:
                    from skill_projectile_schema_metrics import sua_q_schema_metric
                result=sua_q_schema_metric(spec,player_starts,wall_inputs['finishes'],
                    player_spawns,projectile_terminals or [],damages or [],
                    player,teams,intervals.get(player,[]))
            elif group == 1026400 and effect_rows is not None:
                try:
                    from .skill_barbara_grenade_metrics import barbara_grenade_metric
                except ImportError:
                    from skill_barbara_grenade_metrics import barbara_grenade_metric
                result=barbara_grenade_metric(spec,player_starts,wall_inputs['finishes'],
                    damages or [],player,teams,intervals.get(player,[]),effect_rows)
            elif group in {1044530, 1044540} and effect_rows is not None:
                try:
                    from .skill_echion_black_mamba_metrics import (
                        echion_black_mamba_metric, echion_sidewinder_metric)
                except ImportError:
                    from skill_echion_black_mamba_metrics import (
                        echion_black_mamba_metric, echion_sidewinder_metric)
                route = (echion_black_mamba_metric if group == 1044530
                         else echion_sidewinder_metric)
                result=route(spec,player_starts,wall_inputs['finishes'],damages or [],
                    player,teams,intervals.get(player,[]),effect_rows)
                if (summon_inputs or {}).get('echionPrimaryInputs'):
                    try:
                        from .skill_echion_primary_execution import echion_primary_execution
                    except ImportError:
                        from skill_echion_primary_execution import echion_primary_execution
                    result=echion_primary_execution(spec,player,intervals.get(player,[]),summon_inputs['echionPrimaryInputs'])
            else:
                result=_unavailable(spec,'No matching explicit execution route or required route input',
                    category='implementation-unclassified',reason_code='no-matching-execution-route')
            if group==1044520 and (summon_inputs or {}).get('echionPrimaryInputs'):
                try:
                    from .skill_echion_primary_execution import echion_primary_execution
                except ImportError:
                    from skill_echion_primary_execution import echion_primary_execution
                phases=echion_primary_execution(spec,player,intervals.get(player,[]),summon_inputs['echionPrimaryInputs'])
                for key in ('primaryExecutionEvidence','secondaryExecutionEvidence'):
                    if key in phases:result[key]=phases[key]
                result['fullVariantScopeComplete']=False
            if spec.get('reportMultiTarget') is False and result['status'] in {'calculable-observed','calculable-experimental','no-combat-sample'}:
                for key in ['multiTargetAttemptCount','multiTargetAttemptRate','distinctEnemyTargetsSummedAcrossAttempts',
                            'meanDistinctEnemyTargetsPerAttempt','deduplicatedEnemyContactEventCount',
                            'distinctEnemyTargetsPerAttempt','distinctEnemyTargetFirstHitTicksPerAttempt']:
                    result[key]=None
            result.update(allCastCount=legacy['allCastCount'],combatCastCount=legacy['castCount'])
            if result.get('confirmedCastHitEvidence'):
                try:
                    from .skill_partial_projectile_metrics import promote_complete_binary_cast_result
                except ImportError:
                    from skill_partial_projectile_metrics import promote_complete_binary_cast_result
                result=promote_complete_binary_cast_result(result)
            child=child_casts.get(player,{}).get(group)
            if child and not selected and not legacy['allCastCount']:
                result.update(allCastCount=child['observedOwnedChildCastCount'],
                    combatCastCount=child['observedCombatChildCastCount'],ownedChildCastEvidence=child,
                    castCountMeaning='explicit independent owned child skill starts; not parent uses')
            if group in secondary.get(player,{}):
                result['actionStageEvidence']=secondary[player][group]
            if group in {1015400,1035500,1043500,1079300,1081500}:
                try:
                    from .skill_owned_followup_metrics import owned_followup_windows,FOLLOWUPS
                except ImportError:
                    from skill_owned_followup_metrics import owned_followup_windows,FOLLOWUPS
                if (player,group) not in owned_graphs:
                    inputs=summon_inputs or {}
                    owned_graphs[player,group]=owned_followup_windows(group,player_starts,wall_inputs['finishes'],
                        inputs.get('nonPlayerSkillStarts'),inputs.get('summons',[]),projectile_terminals or [],
                        player,teams,catalog,wall_inputs.get('skill_rows',[]))
                windows,graph_reason=owned_graphs[player,group]
                result['ownedFollowupEvidence']={
                    'status':'linked' if windows is not None else 'unresolved',
                    'childSkillGroup':FOLLOWUPS[group]['childGroup'],
                    'parentUseCount':len(windows) if windows is not None else None,
                    'childCastCount':sum(len(w['children']) for w in windows) if windows is not None else None,
                    'childCastsCountedAsIndependentUses':False,'reason':graph_reason}
            from .skill_development_cancellation import apply_development_cancellation_tree
            result=apply_development_cancellation_tree(result,player_starts,wall_inputs['finishes'],
                player_spawns,player,(summon_inputs or {}).get('gaps'),intervals=intervals.get(player,[]),
                catalog=catalog,game_db_sha256=game_db_sha256,raw_actions=raw_actions)
            from .skill_development_cancellation import exclude_winner_interrupted_unemitted_uses
            result=exclude_winner_interrupted_unemitted_uses(result,player_starts,wall_inputs['finishes'],
                player_spawns,player,(summon_inputs or {}).get('gaps'),wall_inputs.get('gameTerminals'),intervals.get(player,[]))
            rows.append(result)
        output[str(player)]=rows
    # Apply the user's denominator rule to every route, including early-return
    # adapters. Each nested phase restores only its own recorded exclusions.
    from .skill_recorded_attempt_policy import finalize_recorded_attempt_policy
    attempt_policy=json.loads((SCOPE_PATH.parent/'user-hit-rate-scope-20260912.json').read_bytes()).get('recordedUseMissPolicy',{})
    if attempt_policy.get('enabled') is True:
        output={player:[finalize_recorded_attempt_policy(row,enabled=True) for row in rows]
                for player,rows in output.items()}
    if match_end_scope is not None:
        for player, rows in output.items():
            for row in rows:
                row['matchEndScope']={k:v for k,v in match_end_scope.items() if k!='excludedStarts'}
                row['postMatchExcludedStarts']=[s for s in match_end_scope.get('excludedStarts',[])
                    if s.get('playerObjectId')==int(player) and s.get('skillGroup')==row['skillGroup']]
    return output


def secondary_stage_applies(spec,player,summon_inputs):
    """A shared child skill ID does not prove every emitter variant was used."""
    from .skill_summon_railgun_metrics import CONFIG
    cfg=CONFIG.get(spec['mode'])
    if cfg is None:return True
    inputs=summon_inputs or {}
    if inputs.get('summons') is None or inputs.get('gaps') is None:return True
    if any(g.get('count',0) and g.get('packetName')=='CmdSpawn' for g in inputs['gaps']):return True
    return any(s.get('summonCode')==cfg[0] and s.get('ownerObjectId') in {player,0,None}
               for s in inputs['summons'])


def absent_scope_source_row(spec,player_starts,source_group,raw_actions,summon_inputs,wall_inputs):
    """Called only after the runtime, reviewed action and child-stage indices are empty."""
    inputs=summon_inputs or {};wall=wall_inputs or {}
    required={'CmdStartSkill','CmdStartStateSkill','CmdPlaySkillAction','CmdPlaySkillActionWithTargets','CmdSpawn'}
    streams=(raw_actions,inputs.get('nonPlayerSkillStarts'),inputs.get('summons'),inputs.get('gaps'),wall.get('states'),wall.get('state_scripts'))
    if any(s is None for s in streams) or any(g.get('count',0) and g.get('packetName') in required for g in inputs['gaps']):
        return _unavailable(spec,'스킬 사용 증거 스트림이 불완전하여 시전 표본 부재를 확인할 수 없음')
    if any(s['skillGroup'] in {spec['skillGroup'],source_group} for s in player_starts):
        return _unavailable(spec,'실제 시전이 있지만 실행 색인에 누락됨; 추가 리플레이보다 색인 수정 필요')
    return {**_unavailable(spec,'해독된 시전·행동·소유 스킬 단계에서 이 단계의 사용이 관측되지 않음'),
        'status':'checked-sources-no-cast','allCastCount':0,'combatCastCount':0,
        'verifiedCompletionCredit':False,'checkedSourceAbsence':True,
        'absenceScope':'decoded-player-starts-and-reviewed-action-child-stage-indices',
        'additionalReplayRequired':None}


def validate_scope_result(row):
    spec=next((s for s in manifest() if s['metricId']==row.get('metricId')),None)
    if spec is None or any(row.get(k)!=v for k,v in spec.items()) or row.get('fallbackUsed') is not False:
        raise ValueError('all-90 metric identity, unit or fallback mismatch')
    if row.get('status')=='calculable-experimental' and (row.get('calculationConfidence')!='experimental' or
            row.get('verifiedCompletionCredit') is not False or row.get('fullRequestedMetricComplete') is not False or
            row.get('verifiedCombatCastCount')!=0):
        raise ValueError('provisional rate cannot claim verified completion')
    if row.get('status') in {'calculable-observed','calculable-experimental','no-combat-sample'}:
        attempts,hits=row.get('attemptCount'),row.get('hitCount')
        if type(attempts) is not int or type(hits) is not int or not 0<=hits<=attempts:
            raise ValueError('invalid all-90 metric counts')
        if (attempts>0)!=(row['status'] in {'calculable-observed','calculable-experimental'}) or row['hitRate']!=(round(hits/attempts,6) if attempts else None):
            raise ValueError('all-90 metric rate mismatch')
        multi=row.get('multiTargetAttemptCount')
        if spec.get('reportMultiTarget') is False:
            if any(row.get(k) is not None for k in ['multiTargetAttemptCount','multiTargetAttemptRate',
                'distinctEnemyTargetsSummedAcrossAttempts','meanDistinctEnemyTargetsPerAttempt','deduplicatedEnemyContactEventCount']):
                raise ValueError('unrequested multi-target counts must not be published for this metric')
        elif row.get('binaryCastSuccessComplete') is True:
            evidence=row.get('confirmedCastHitEvidence',{})
            if ((spec['mode'],spec['unit'])!=('any','skill-cast') or spec.get('targetCohort')
                    or row.get('fullRequestedMetricComplete') is not False
                    or row.get('completeTargetCountsAvailable') is not False
                    or evidence.get('aggregateRateEligible') is not False or evidence.get('hitRate') is not None
                    or evidence.get('unknownCastCount')!=0
                    or evidence.get('confirmedHitCastCount')!=attempts or evidence.get('observedCombatCastCount')!=attempts
                    or row.get('combatCastCount')!=attempts or hits!=attempts
                    or len(evidence.get('outcomes',[]))!=attempts
                    or row.get('outcomes')!=[[u['castTick'],1,u['castTick'],u['firstConfirmedEnemyContactTick']] for u in evidence['outcomes']]
                    or len({tuple(u['castWireOrder']) for u in evidence['outcomes']})!=attempts
                    or any(u['firstConfirmedEnemyContactTick']<u['castTick'] for u in evidence['outcomes'])
                    or any(row.get(k) is not None for k in ('multiTargetAttemptCount','multiTargetAttemptRate',
                        'distinctEnemyTargetsSummedAcrossAttempts','distinctEnemyTargetsPerAttempt',
                        'distinctEnemyTargetFirstHitTicksPerAttempt','meanDistinctEnemyTargetsPerAttempt',
                        'deduplicatedEnemyContactEventCount'))):
                raise ValueError('binary cast success cannot imply complete target counts or hide unknown uses')
        elif type(multi) is not int or not 0<=multi<=hits or row.get('multiTargetAttemptRate')!=(round(multi/attempts,6) if attempts else None):
            raise ValueError('all-90 multi-target rate mismatch')
        if spec.get('targetCohort'):
            total=row.get('distinctTargetsSummedAcrossAttempts')
            if (type(total) is not int or total<hits or row.get('distinctEnemyTargetsSummedAcrossAttempts') is not None or
                    row.get('meanDistinctTargetsPerAttempt')!=(round(total/attempts,6) if attempts else None)):
                raise ValueError('team targets must use neutral count fields, never enemy counts')
        if spec['skillGroup']==1043500:
            stages=row.get('detonationStageCounts')
            without=row.get('usesWithoutDetonationCount')
            if not isinstance(stages,dict) or set(stages)!={'1','2','3','4'} or type(without) is not int or without<0:
                raise ValueError('Celine detonation stage partition missing')
            for part in stages.values():
                a,h=part.get('attemptCount'),part.get('hitCount')
                targets=part.get('distinctEnemyTargetsSummedAcrossAttempts')
                if (type(a) is not int or type(h) is not int or not 0<=h<=a or type(targets) is not int or targets<h or
                        part.get('hitRate')!=(round(h/a,6) if a else None)):
                    raise ValueError('invalid Celine detonation stage counts')
            if (sum(p['attemptCount'] for p in stages.values())+without!=attempts or
                    sum(p['hitCount'] for p in stages.values())!=hits or
                    sum(p['distinctEnemyTargetsSummedAcrossAttempts'] for p in stages.values())!=row['distinctEnemyTargetsSummedAcrossAttempts']):
                raise ValueError('Celine stage counts do not partition parent uses')
    elif any(row.get(k) is not None for k in ['attemptCount','hitCount','hitRate']):
        raise ValueError('unknown all-90 metric cannot be zero')
    if row.get('status')=='checked-sources-no-cast' and (row.get('checkedSourceAbsence') is not True or
            row.get('allCastCount')!=0 or row.get('combatCastCount')!=0 or row.get('verifiedCompletionCredit') is not False):
        raise ValueError('absent source requires explicit observation scope and cannot credit a rule')
