"""Version-bound execution plans and invocation-local failure provenance.

Compilation never reads a replay. Runtime never promotes a candidate rule.
Unmigrated routes stay explicit; their failures are not evidence that a replay
omits the required information. The ledger preserves every attempted route.
"""
from contextvars import ContextVar
from functools import lru_cache, wraps
from importlib import import_module
from inspect import signature
from pathlib import Path
import hashlib
import json

if __package__:
    from .skill_direct_route_bindings import DIRECT_BINDINGS, METRIC_ROUTES
else:
    from skill_direct_route_bindings import DIRECT_BINDINGS, METRIC_ROUTES

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / 'schema/skill-execution-plan-v1.json'

# Existing reviewed object/action routes. This is an execution contract, not
# a claim that their remaining candidate effect mappings are statically proven.
# Positive-sample gates are retained and declared where mapping is incomplete.
ROUTES = {
    'tazia-wall-object': ('skill_tazia_wall_contact_metrics', 'tazia_wall_contact_metric',
                          {1060300}, {'any'}, False),
    'henry-created-object': ('skill_created_object_hit_metrics', 'created_object_hit_metric',
                             {1083300}, {'any'}, True),
    'bianca-object-phases': ('skill_created_object_phase_metrics', 'created_object_phase_metric',
                            {1042500}, {'any', 'creation-hit', 'end-hit'}, False),
    'recorded-attack-sequence': ('skill_reviewed_multi_stage_metrics', 'reviewed_multi_stage_metric',
                               {1020500, 1078200}, {'any', 'first-hit', 'end-hit', 'both-hit'}, False),
    'garnet-exact-kill': ('skill_garnet_execute_metrics', 'garnet_execute_metric',
                        {1076510}, {'execute-success'}, False),
    'recorded-hit-and-cc': ('skill_action_cc_outcomes', 'action_cc_outcome_metric',
                            {1034300,1090500}, {'any','fetter','stun','marked-hit','unmarked-hit'}, False),
    'recorded-guard-outcome': ('skill_action_cc_outcomes', 'guard_action_outcome_metric',
                             {1033300}, {'guard-success'}, False),
    'native-direct-effect': ('skill_static_effect_families', 'native_direct_effect_metric',
                            {1007400,1033400,1058210,1061310,1055300,1055310,1055410,1065300,1089400,1050300,1039400,1039220,1071200}, {'any'}, False),
    'native-owned-state-execution': ('skill_owned_state_execution', 'owned_state_execution_metric',
                                    {1080200,1080300,1080400}, {'any'}, False),
    'named-projectile-state': ('skill_projectile_state_metrics', 'projectile_state_metric',
                              {1014400,1009200,1021500,1028300}, {'fetter','attach','blind'}, False),
    'native-projectile-pull': ('skill_projectile_state_metrics', 'projectile_state_metric',
                              {1020300}, {'pull-hit'}, False),
    'native-owned-object-effects': ('skill_complete_projectile_lifetimes', 'native_projectile_effect_metric',
                                    {1031300,1050200,1052200,1052210,1058200,1017500}, {'any'}, False),
    'native-initial-projectile-contact': ('skill_complete_projectile_lifetimes', 'native_projectile_effect_metric',
                                        {1082200}, {'first-hit'}, False),
    'recorded-region-union': ('skill_cathy_region_metrics', 'cathy_region_metric',
                              {1023300}, {'any','inner-hit','outer-hit'}, False),
    'owned-object-outcome': ('skill_owned_object_metrics', 'owned_object_metric',
                             {1036300,1054500,1060500}, {'any','stun'}, True),
    'native-garnet-state': ('skill_effect_linked_cc_metrics', 'effect_linked_cc_metric',
                            {1076300}, {'fetter'}, False),
}

_RUN = ContextVar('skill_execution_run', default=None)

# Direct registration of existing implementations, not a new mapping review.
# A declared sample gate is preserved until its producer contract is reviewed.
EXISTING_ROUTES = {
    'existing-zahir-r': ('skill_preselected_effect_routes','preselected_zahir_r_metric',
                        {1005500},{'any'},False),
    'existing-tazia-q': ('skill_tazia_q_projectile_metrics', 'tazia_q_projectile_metric',
                        {1060200}, {'any'}, False),
    'existing-sequential-explosion': ('skill_sequential_explosion_metrics', 'sequential_explosion_metric',
                                    {1077500}, {'any','first-hit','end-hit','both-hit'}, True),
    'existing-attack-sequence': ('skill_reviewed_multi_stage_metrics', 'reviewed_multi_stage_metric',
                               {1017200,1039300,1067200}, {'any','first-hit','end-hit','both-hit'}, None),
    'existing-action-effect-stage': ('skill_action_effect_stage_metrics', 'action_effect_stage_metric',
                                    {1046410,1047200,1083500}, {'any','first-hit','end-hit','stun'}, True),
    'existing-henry-linked-e': ('skill_henry_linked_e_metrics', 'henry_linked_e_metric',
                              {1083400}, {'any','fetter'}, True),
    'existing-camilo-r3': ('skill_camilo_r3_metrics', 'camilo_r3_metric',
                         {1039520}, {'any'}, False),
    'existing-silvia-bike-e': ('skill_silvia_e_direct_damage_metrics', 'silvia_bike_e_direct_damage_metric',
                             {1016800}, {'any'}, True),
    'existing-projectile-marked-hit': ('skill_projectile_marked_hit_metrics', 'projectile_marked_hit_metric',
                                     {1046400}, {'any'}, True),
    'existing-state-end-damage': ('skill_state_end_damage_metrics', 'state_end_damage_metric',
                                {1045300,1045500}, {'any'}, True),
    'existing-persistent-damage': ('skill_persistent_cc_metrics', 'persistent_damage_metric',
                                 {1051400}, {'any'}, True),
    'existing-persistent-cc': ('skill_persistent_cc_metrics', 'persistent_cc_metric',
                             {1025500,1051400}, {'fetter'}, True),
    'existing-screen-beam': ('skill_screen_beam_metrics', 'screen_beam_metric',
                            {1062200,1062210}, {'any','direct-hit','screen-hit'}, True),
    'existing-static-direct-family': ('skill_static_direct_family_metrics', 'static_direct_family_metric',
                                     {1026310,1047410,1063400,1063410,1086500}, {'any'}, False),
}

# Preserve the exact arguments of the removed direct call sites. In particular,
# missing raw action/child streams must not silently become synthesized lists.
INPUT_ALIASES = {
    'existing-silvia-bike-e': {'player_starts': 'starts'},
    'existing-state-end-damage': {'scripts': 'state_scripts'},
    'existing-persistent-damage': {'scripts': 'state_scripts'},
    'existing-persistent-cc': {'scripts': 'state_scripts'},
    'existing-screen-beam': {'child_starts': 'nonPlayerSkillStarts'},
    'existing-static-direct-family': {'actions': 'raw_actions'},
}


def route_definitions():
    if (set(ROUTES) & set(EXISTING_ROUTES) or
            (set(ROUTES) | set(EXISTING_ROUTES)) & set(DIRECT_BINDINGS)):
        raise ValueError('duplicate execution route identity')
    direct = {key: ('skill_direct_route_bindings', adapter, set(), set(), None)
              for key, (adapter, _, _, _) in DIRECT_BINDINGS.items()}
    return {**ROUTES, **EXISTING_ROUTES, **direct,
            'preselected-existing-effect-family': ('skill_preselected_effect_routes','preselected_effect_metric',set(),set(),False),
            'preselected-explicit-effect-family': ('skill_preselected_effect_routes','preselected_explicit_effect_metric',set(),set(),True),
            'preselected-explosion-contact': ('skill_preselected_effect_routes','preselected_explosion_metric',set(),set(),None),
            'preselected-action-contact': ('skill_preselected_effect_routes','preselected_action_metric',set(),set(),True),
            'preselected-projectile-contact': ('skill_preselected_effect_routes','preselected_projectile_metric',set(),set(),False),
            'preselected-cancelled-projectile-contact': ('skill_preselected_effect_routes','preselected_cancelled_projectile_metric',set(),set(),None),
            'preselected-ordered-projectile-lifetime': ('skill_preselected_effect_routes','preselected_projectile_lifetime_metric',set(),set(),False),
            'combined-recorded-contact': ('skill_combined_contact_evidence','combined_contact_metric',set(),set(),False),
            'development-effect-family': ('skill_development_effect_metrics','development_effect_metric',set(),set(),False)}


def evidence_channels(spec):
    # Henry R's recorded projectile contacts remain useful even when its phase
    # ownership is unresolved. This channel never supplies misses or a rate.
    # It is selected at compilation, independent of the primary result status.
    eligible = spec['mode'] == 'any' and spec['unit'] == 'skill-cast'
    explicit = spec['skillGroup'] == 1083500 or (
        spec.get('metricId') in METRIC_ROUTES and spec['characterCode'] not in {35,44})
    return ['confirmed-owned-projectile-contact'] if eligible and explicit else []

# Exact diagnostics emitted by shared helpers, not keyword inference about
# whether the original replay contains information. Missing extracted fields
# and an insufficient in-memory representation are deliberately distinct.
COMMON_FAILURES = {
    '해당 단계 반복 시전 표본 부족': ('rule-mapping-incomplete', 'legacy-per-match-repeat-gate'),
    '해당 단계 자체의 적 타격 FX 표본 없음; 다른 단계로 미적중을 확정하지 않음': ('rule-mapping-incomplete', 'legacy-per-match-positive-effect-gate'),
    '해당 전용 CC가 실제 적에게 적용된 검증 표본 없음': ('rule-mapping-incomplete', 'legacy-per-match-positive-state-gate'),
    '전용 객체 결과의 실제 적 적용 검증 표본 없음': ('rule-mapping-incomplete', 'legacy-per-match-positive-object-gate'),
    '같은 gameDb에 스킬 단계와 타격을 함께 명시한 후보 FX 계열이 없음': ('rule-mapping-incomplete', 'producer-effect-mapping-not-implemented'),
    '후보 타격 FX가 한 단계의 실제 시전 수명에 배타적으로 연결되지 않음': ('attribution-unresolved', 'candidate-effect-parent-not-unique'),
    '같은 플레이어의 다른 수동 스킬 수명이 불완전하여 FX 중복 귀속 배제 불가': ('attribution-unresolved', 'competing-cast-effect-ownership'),
    '실제 CC 적용이 한 스킬의 완전한 수명에 배타적으로 연결되지 않음': ('attribution-unresolved', 'state-parent-not-unique'),
    '같은 효과를 재사용하는 다른 상태가 겹쳐 단독 스킬 CC 판정을 확정하지 않음': ('attribution-unresolved', 'shared-state-effect-ownership'),
    '본체의 정확한 반복 시전·wire 정체성 검증 부족': ('implementation-unclassified', 'combined-repeat-and-wire-gate'),
    '전용 객체의 소유·생성·최종 소멸 기록 불완전': ('observation-incomplete', 'object-ownership-or-lifetime-incomplete'),
    '전용 상태가 실제 투사체 수명·충돌 하나에 연결되지 않음; 겹친 폭탄이나 최근 시전으로 추정하지 않음': ('attribution-unresolved', 'state-projectile-contact-not-unique'),
    'Sua Q projectile lacks an exact CmdDestroyDelayStart': ('observation-incomplete', 'projectile-removal-event-missing'),
    '실제 투사체 최종 소멸이 없어 미적용 결과를 확정하지 않음': ('observation-incomplete', 'projectile-final-end-missing'),
    '링·기둥·로프 객체 수명이 완결되지 않음': ('observation-incomplete', 'ring-object-lifetime-incomplete'),
    '버니스 R 실제 투사체 생성·최종 소멸 불완전': ('observation-incomplete', 'projectile-spawn-or-end-missing'),
    '발사체가 없는 정상 종료 시전은 몸체·후속 발사 누락과 구분되지 않음': ('attribution-unresolved', 'normal-cast-emission-not-established'),
    '생성 발사체의 최종 소멸이 확인되지 않아 미적중 확정 불가': ('observation-incomplete', 'projectile-final-end-missing'),
    '실제 발사 탄환의 도착·최종 소멸이 완전하지 않아 실패 확정 불가': ('observation-incomplete', 'projectile-arrival-or-end-missing'),
    '동일 프레임의 여러 종료 결과는 현재 타격 수명 표현으로 구별 불가': ('implementation-limitation', 'tick-only-lifetime-representation'),
    '같은 tick의 복수 종료는 현재 수명 표현으로 구분 불가': ('implementation-limitation', 'tick-only-lifetime-representation'),
    '실제 명령 순서에서 종료 없는 시전': ('observation-incomplete', 'missing-finish-event'),
    '실제 명령 순서에서 시작 없는 종료': ('observation-incomplete', 'missing-start-event'),
    '시전 없이 남은 종료 이벤트가 있어 완전한 시전 수명 아님': ('observation-incomplete', 'missing-start-event'),
    '재생 종료 표식은 게임 내 시전 결과를 확정하는 종료가 아님': ('observation-incomplete', 'replay-end-without-gameplay-end'),
    '시전과 종료가 모두 일대일로 연결되지 않음': ('observation-ambiguous', 'nonunique-cast-finish-link'),
    '실제 명령 순서에서도 같은 스킬 시전이 겹침': ('observation-ambiguous', 'overlapping-cast-identities'),
    '사용별 부분 완결성에는 정확한 시전/종료 명령 순서가 필요함': ('observation-incomplete', 'extracted-command-order-missing'),
    '명령 순서와 시전 시간 순서 불일치': ('observation-conflict', 'command-time-order-conflict'),
    '시전/종료 명령 순서가 중복되어 연결 불가': ('observation-conflict', 'duplicate-command-order'),
    '사용과 생성 객체가 일대일 아님': ('observation-ambiguous', 'nonunique-created-object-link'),
    '시전과 생성 객체 지정 행동이 일대일 아님': ('observation-ambiguous', 'nonunique-created-object-link'),
    '단계 행동이 하나의 생성 객체를 직접 지정하지 않음': ('observation-ambiguous', 'nonunique-phase-object-reference'),
    '대상 타격에 같은 시각의 명시 단계 객체가 없음': ('observation-incomplete', 'hit-phase-object-reference-missing'),
    '미검토 객체 단계 행동이 있음': ('rule-mapping-incomplete', 'unreviewed-object-phase-action'),
    '정확한 스킬·생성 객체 정의 불일치': ('rule-definition-mismatch', 'game-data-object-definition-mismatch'),
}


def known_failure(reason):
    return COMMON_FAILURES.get(reason, (None, None))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compile_plan(specs, client_version, game_db_sha256):
    """Offline compilation: exact manifest + reviewed code, zero match samples."""
    from .skill_game_data_contract import archive_for_revision,revision_identity
    from .skill_development_effect_metrics import development_policy,automatic_development_family
    policy=development_policy()
    archive=archive_for_revision(client_version,game_db_sha256)
    identity=revision_identity(client_version,game_db_sha256)
    import zipfile
    from .projectile_hit_catalog import build_projectile_skill_catalog
    from .skill_rule_candidates import compile_candidate_families,compile_development_form_families
    catalog=build_projectile_skill_catalog(archive)
    with zipfile.ZipFile(archive) as data:
        effect_rows=json.loads(data.read('EffectAndSound.json'))
        candidate_families=compile_candidate_families(catalog,effect_rows)
    dependencies = {'decoder/skill_execution_plan.py', 'decoder/requested_skill_scope.py',
                    'decoder/skill_game_data_contract.py','schema/skill-game-data-revisions-v1.json',
                    'decoder/requested_skill_hit_rates.py', 'decoder/skill_wire_order.py',
                    'decoder/skill_projectile_active_end.py', 'decoder/skill_action_stage_evidence.py',
                    'decoder/skill_partial_cast_lifetimes.py', 'decoder/skill_attempt_timing.py',
                    'decoder/skill_static_effect_families.py', 'decoder/skill_summon_ownership.py',
                    'decoder/skill_rule_candidates.py','decoder/projectile_hit_catalog.py',
                    'decoder/skill_user_phase_metrics.py','decoder/skill_development_lifecycle.py',
                    'decoder/skill_development_cancellation.py',
                    'data/skill_id_enum_12_3_0.json','data/development-hit-rate-policy-v1.json',
                    'data/development-effect-contracts-v1.json',
                    'schema/native-cc-state-producers-v1.json',
                    'deliverables/native-process-direct-effects-v1.json',
                    'deliverables/eva-w-owned-producer-static-proof-20260910-v1.json',
                    'deliverables/native-estelle-shield-disjoint-producer-v1.json',
                    'deliverables/native-estelle-q-disjoint-producer-v1.json',
                    'deliverables/native-isol-q-ground-attachment-v1.json',
                    'deliverables/native-elena-w-both-forms-contract-v1.json',
                    'deliverables/native-camilo-e-two-steps-contract-v1.json',
                    'deliverables/native-camilo-q3-foreign-producer-contract-v1.json',
                    'deliverables/cathy-q-completed-target-contract-v1.json',
                    'deliverables/native-kenneth-q-disjoint-e-contract-v1.json',
                    'deliverables/native-kenneth-q-atomic-closure-v1.json',
                    'deliverables/native-adriana-r-bomb-object-contract-v1.json',
                    'data/requested_skill_scope_20260905.json',
                    'data/requested_skill_group_registry_20260905.json'}
    definitions = route_definitions()
    contracts={
        'data/preselected-effect-routes-v1.json': {'preselected-existing-effect-family'},
        'data/preselected-terminal-routes-v1.json': {'preselected-explicit-effect-family','preselected-explosion-contact'},
        'data/preselected-contact-routes-v1.json': {'preselected-action-contact','preselected-projectile-contact','preselected-cancelled-projectile-contact'},
        'data/preselected-projectile-lifetime-routes-v1.json': {'preselected-ordered-projectile-lifetime'},
        'data/combined-contact-routes-v1.json': {'combined-recorded-contact'},
    }
    fixed_routes={}
    for path,allowed_routes in contracts.items():
        contract=json.loads((ROOT/path).read_bytes())
        dependencies.add(path)
        for entry in contract['entries']:
            metric_id=entry['spec']['metricId']
            route=entry.get('routeId',contract.get('routeId'))
            if metric_id in fixed_routes or route not in allowed_routes:
                raise ValueError('duplicate or invalid preselected route contract')
            fixed_routes[metric_id]=dict(spec=entry['spec'],routeId=route)
    # Frozen observed mappings may use asset names unrelated to the slot
    # (bike Q = Skill05). Never rediscover these per match.
    override_path=('data/candidate-producer-overrides-12.4-v1.json' if client_version=='12.4.0'
                   else 'data/candidate-producer-overrides-v1.json')
    if client_version=='12.4.0':
        dependencies.update({'schema/skill-game-data-revisions-12.4-v1.json',
                             'schema/schema-12.4.json','decoder/replay_schema_inputs.py',
                             'decoder/replay_input_context.py','data/skill_id_enum_12_4_0.json'})
    overrides=json.loads((ROOT/override_path).read_bytes());dependencies.add(override_path)
    effect_defs={r['code']:r for r in effect_rows}
    for override in overrides['entries']:
        group=str(override['skillGroup']);definition=catalog['skillGroups'][group]
        if (definition['skillId']!=override['skillId'] or definition['characterCode']!=override['characterCode']
                or game_db_sha256 not in override['gameDataSha256']):
            raise ValueError('candidate producer override belongs to a different definition/version')
        proof=override['proofPath'];dependencies.add(proof)
        if sha(ROOT/proof)!=override['proofSha256']:raise ValueError('candidate producer review changed')
        for code,name in override['effectDefinitions'].items():
            if effect_defs.get(int(code),{}).get('effectPrefabName')!=name:raise ValueError('candidate effect asset changed')
        for code,name in override['projectileDefinitions'].items():
            if catalog['projectileDefinitions'].get(code,{}).get('prefabName')!=name:raise ValueError('candidate projectile asset changed')
        candidate_families[group]=dict(characterCode=override['characterCode'],family=definition['family'],
            groups=[int(group)],effectCodes=sorted(map(int,override['effectDefinitions'])),
            projectileCodes=sorted(map(int,override['projectileDefinitions'])),
            mappingStatus='frozen-observed-candidate-not-static-FK',reviewedMapping=False,
            producerOverride=True,proofPath=proof,proofSha256=override['proofSha256'])
    development_families=compile_development_form_families(catalog,effect_rows,candidate_families)
    for spec in specs:
        if spec['metricId'] in fixed_routes and spec!=fixed_routes[spec['metricId']]['spec']:
            raise ValueError('requested scope changed; review its preselected route contract')
    dependencies.update('decoder/' + r[0] + '.py' for r in definitions.values())
    dependencies.update('decoder/' + module + '.py'
                        for _, module, _, _ in DIRECT_BINDINGS.values())
    dependencies.add('decoder/skill_partial_projectile_metrics.py')
    dependencies.add(archive.relative_to(ROOT).as_posix())
    entries = []
    for spec in specs:
        matches = [key for key, (_, _, groups, modes, _) in definitions.items()
                   if spec['skillGroup'] in groups and spec['mode'] in modes and spec['unit'] == 'skill-cast']
        if spec['metricId'] in METRIC_ROUTES:
            matches.append(METRIC_ROUTES[spec['metricId']])
        if len(matches) > 1:
            raise ValueError('multiple execution contracts for one metric')
        key = matches[0] if matches else None
        if (key is None or spec['metricId'] in policy.get('preselectedRouteOverrides',[])) and policy['enabled'] and spec['metricId'] in policy['metricIds']:
            key='development-effect-family'
        elif key is None and policy['enabled'] and automatic_development_family(
                spec,development_families.get(str(spec['skillGroup']),{}),policy):
            key='development-effect-family'
        if key is None and spec['metricId'] in fixed_routes:
            key=fixed_routes[spec['metricId']]['routeId']
        entries.append(dict(metricId=spec['metricId'], spec=spec, routeId=key,
                            ruleStatus=('experimental-executable' if key in {'development-effect-family','combined-recorded-contact'} else 'reviewed-executable' if key in ROUTES else
                                        'implemented-executable' if key else 'legacy-uncompiled'),
                            minimumRuntimeUses=1 if key in ROUTES or key=='preselected-action-contact' else None,
                            positiveSampleRequired=(False if key in {'preselected-explicit-effect-family','existing-henry-linked-e','existing-projectile-marked-hit',
                                'existing-state-end-damage','existing-sequential-explosion','existing-action-effect-stage',
                                'existing-screen-beam','henry-created-object','preselected-action-contact',
                                'existing-silvia-bike-e','owned-object-outcome','existing-persistent-damage',
                                'existing-persistent-cc'} and policy['enabled']
                                and policy.get('reuseReviewedCandidateRulesWithoutPositiveSamples') else definitions[key][4] if key else None),
                            evidenceChannels=evidence_channels(spec) if key else []))
    return dict(schemaVersion=1, clientVersion=client_version, gameDbSha256=game_db_sha256,
                gameDataRuleIdentity=identity,
                candidateFamilies=candidate_families,developmentCandidateFamilies=development_families,runtimeFamilyNameMatchingAllowed=False,
                replaySamplesUsedForCompilation=0, fallbackAllowed=False,
                dependencies={name: sha(ROOT / name) for name in sorted(dependencies)}, entries=entries)


@lru_cache(maxsize=4)
def _validated_plan(raw, file_stamps):
    plan = json.loads(raw)
    if plan['schemaVersion'] != 1 or plan['fallbackAllowed'] is not False:
        raise ValueError('unsupported execution plan')
    if plan.get('runtimeFamilyNameMatchingAllowed') is not False or not isinstance(plan.get('candidateFamilies'),dict):
        raise ValueError('missing offline candidate-family contract; rebuild offline')
    if not isinstance(plan.get('developmentCandidateFamilies'),dict):
        raise ValueError('missing offline development form partitions; rebuild offline')
    for name, digest in plan['dependencies'].items():
        if sha(ROOT / name) != digest:
            raise ValueError('stale execution contract: ' + name + '; rebuild offline')
    return plan


def load_plan(client_version, game_db_sha256):
    try:
        from .skill_game_data_contract import revision_identity
    except ImportError:
        from skill_game_data_contract import revision_identity
    identity=revision_identity(client_version,game_db_sha256)
    path=PLAN_PATH if identity['planPath']=='schema/skill-execution-plan-v1.json' else ROOT/identity['planPath']
    raw = path.read_bytes()
    data = json.loads(raw)
    stamps = tuple((name, (ROOT / name).stat().st_mtime_ns, (ROOT / name).stat().st_size)
                   for name in data['dependencies'])
    plan = _validated_plan(raw, stamps)
    if (client_version, game_db_sha256) != (plan['clientVersion'], plan['gameDbSha256']):
        raise ValueError('execution plan version/gameDb mismatch; no fallback')
    if plan.get('gameDataRuleIdentity')!=identity:
        raise ValueError('execution plan gameDb rule identity mismatch; no fallback')
    return plan, hashlib.sha256(raw).hexdigest()


def metric_key(spec):
    return spec.get('metricId') or (spec.get('characterCode'), spec.get('skillGroup'), spec.get('mode'))


def begin_metric(spec, player):
    run = _RUN.get()
    if run is not None:
        run['current'] = (str(player), metric_key(spec))


def record_failure(spec, reason, category, reason_code, route):
    run = _RUN.get()
    if run is None or run.get('current') is None:
        return
    player, _ = run['current']
    key = (player, metric_key(spec))
    evidence = dict(route=route, category=category or 'implementation-unclassified',
                    reasonCode=reason_code, reason=reason)
    bucket = run['failures'].setdefault(key, [])
    if evidence not in bucket:
        bucket.append(evidence)


def planned_route(spec):
    run = _RUN.get()
    return run['entries'].get(spec.get('metricId'), {}).get('routeId') if run else None


def planned_family_candidates(spec, kind, *, required=True):
    """Runtime lookup only. Missing inputs must never trigger rediscovery."""
    run=_RUN.get()
    if run is None:
        return None  # Caller is an offline builder/standalone evidence audit.
    families=run['developmentCandidateFamilies'] if planned_route(spec)=='development-effect-family' else run['candidateFamilies']
    family=families.get(str(spec['skillGroup']))
    if family is None and not required:return None
    if family is None or family['characterCode']!=spec['characterCode']:
        raise ValueError('compiled candidate-family identity missing or mismatched')
    if kind not in {'effectCodes','projectileCodes'}:
        raise ValueError('unsupported compiled family field')
    run['candidateFamilyLookups']+=1
    return set(family['groups']),set(family[kind])


def planned_candidate_review(spec):
    run=_RUN.get()
    if run is None:return None
    family=run['candidateFamilies'].get(str(spec['skillGroup']),{})
    return {k:family[k] for k in ('mappingStatus','proofPath','proofSha256')} if family.get('producerOverride') else None


def projectile_end_index(terminals, build):
    """Reuse immutable terminal facts within this calculation only."""
    run = _RUN.get()
    if run is None or not isinstance(terminals, (list, tuple)):
        return build(terminals)
    indices = run.setdefault('projectileEndIndices', {})
    key = id(terminals)
    entry = indices.get(key)
    if entry is None or entry[0] is not terminals or entry[1] != len(terminals):
        entry = (terminals, len(terminals), build(terminals))
        indices[key] = entry
    return entry[2]


def damage_events_for_player(damages, player, teams, projectile_owners):
    """Index immutable input streams once per evaluation and ownership map.

    This uses exactly the existing owner resolution; it adds no attribution.
    Standalone callers retain their linear scan. Indices never cross games.
    """
    run = _RUN.get()
    if run is None:
        return damages
    key = (id(damages), id(teams), id(projectile_owners) if projectile_owners else None)
    indices = run.setdefault('damageIndices', {})
    index = indices.get(key)
    if index is None or index['length'] != len(damages):
        by_owner = {}
        for damage in damages:
            attacker = damage['attackerObjectId']
            owner = attacker if attacker in teams else projectile_owners.get(attacker)
            by_owner.setdefault(owner, []).append(damage)
        index = dict(length=len(damages), byOwner=by_owner,
                     sources=(damages, teams, projectile_owners))
        indices[key] = index
        run['damageIndexBuilds'] = run.get('damageIndexBuilds', 0) + 1
        run['damageRowsIndexed'] = run.get('damageRowsIndexed', 0) + len(damages)
    run['damageIndexQueries'] = run.get('damageIndexQueries', 0) + 1
    return index['byOwner'].get(player, ())


@lru_cache(maxsize=None)
def _handler(route_id):
    module, name, _, _, _ = route_definitions()[route_id]
    fn = getattr(import_module('.' + module, __package__) if __package__ else import_module(module), name)
    params = {name: p.default for name, p in signature(fn).parameters.items()}
    return fn, params


def execute_planned(spec, inputs):
    route_id = planned_route(spec)
    if route_id is None:
        raise ValueError('no compiled handler for this metric')
    run = _RUN.get()
    key = (str(inputs['player']), metric_key(spec))
    calls = run.setdefault('executions', {})
    if calls.get(key, 0):
        raise ValueError('compiled metric already executed; route retry is forbidden')
    calls[key] = 1
    evidence = {}
    for channel in run['entries'][spec['metricId']]['evidenceChannels']:
        if channel != 'confirmed-owned-projectile-contact':
            raise ValueError('unregistered evidence channel')
        if __package__:
            from .skill_partial_projectile_metrics import confirmed_projectile_cast_evidence
        else:
            from skill_partial_projectile_metrics import confirmed_projectile_cast_evidence
        positive = confirmed_projectile_cast_evidence(spec, inputs['starts'], inputs['spawns'],
            inputs['collisions'], inputs['terminals'], inputs['finishes'], inputs['damages'],
            inputs['player'], inputs['teams'], inputs['intervals'], inputs['catalog'],
            inputs['effect_rows'], inputs['projectile_owners'])
        if positive is not None:
            evidence['confirmedCastHitEvidence'] = positive
    fn, params = _handler(route_id)
    aliases = INPUT_ALIASES.get(route_id, {})
    args = {'spec': spec, **{key: inputs[aliases.get(key,key)] for key in params
                            if aliases.get(key,key) in inputs}}
    if 'development' in params:
        from .skill_development_effect_metrics import development_policy
        args['development']=bool(development_policy().get('lifecycleEstimatesEnabled'))
    # Missing required evidence is an error, never an empty synthesized stream.
    missing = [key for key, default in params.items() if key not in args and default is signature(fn).empty]
    if missing:
        raise ValueError('compiled route inputs missing: ' + ', '.join(missing))
    row = fn(**args)
    row.update(evidence)
    row['executionRouteId'] = route_id
    return row


def execution_diagnostics(row, entry, failures, plan_sha):
    unresolved = row.get('status') == 'unresolved-evidence'
    # Provisional confidence does not imply an unclassified use. Confidence
    # annotations intentionally keep fullRequestedMetricComplete false even
    # when every recorded use has a result; do not request those uses again.
    uses_complete = (row.get('perUseCompletenessTracked') is True
                     and row.get('unresolvedCombatCastCount') == 0)
    partial = bool(row.get('unresolvedCombatCastCount') or
                   (row.get('fullRequestedMetricComplete') is False and not uses_complete))
    categories = sorted({f['category'] for f in failures}) if unresolved else []
    explicit = row.get('failureCategory')
    if unresolved and explicit:
        categories = sorted(set(categories) | {explicit})
    if unresolved and not categories:
        categories = ['implementation-unclassified']
    return dict(planSha256=plan_sha, ruleStatus=entry['ruleStatus'],
                routeId=entry['routeId'], outcomeStatus='unresolved' if unresolved else 'partial' if partial else row.get('status'),
                blockerCategories=categories, attemptedRouteFailures=failures,
                unresolvedUseReasons=dict(row.get('unresolvedCastReasons') or {}),
                additionalReplayRequired=None, replayInformationAbsenceProven=False,
                nextAction='resolve-rule-or-diagnose-existing-evidence' if unresolved else
                           'classify-remaining-uses' if partial else None)


def with_execution_plan(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        plan, digest = load_plan(kwargs['client_version'], kwargs['game_db_sha256'])
        entries = {e['metricId']: e for e in plan['entries']}
        for spec in kwargs.get('metric_specs') or [e['spec'] for e in entries.values()]:
            if spec.get('metricId') not in entries or entries[spec['metricId']]['spec'] != spec:
                raise ValueError('metric does not match compiled versioned scope')
        state = dict(entries=entries, failures={}, current=None,
                     candidateFamilies=plan['candidateFamilies'],developmentCandidateFamilies=plan['developmentCandidateFamilies'],candidateFamilyLookups=0)
        token = _RUN.set(state)
        try:
            output = fn(*args, **kwargs)
            for player, rows in output.items():
                for row in rows:
                    entry = entries[row['metricId']]
                    row['executionDiagnostics'] = execution_diagnostics(
                        row, entry, state['failures'].get((str(player), metric_key(row)), []), digest)
                    row['executionDiagnostics']['plannedHandlerInvocationCount'] = state.get(
                        'executions', {}).get((str(player), metric_key(row)), 0)
            return output
        finally:
            _RUN.reset(token)
    return run


if __name__ == '__main__':
    from .requested_skill_scope import manifest, CLIENT_VERSION, GAME_DB_SHA256
    from .skill_game_data_contract import CONTRACT_PATH
    contract=json.loads(CONTRACT_PATH.read_bytes())
    specs=manifest()
    for digest,entry in contract['archives'].items():
        data=compile_plan(specs,CLIENT_VERSION,digest)
        (ROOT/entry['planPath']).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        print(json.dumps(dict(metrics=len(data['entries']),compiled=sum(bool(e['routeId']) for e in data['entries']),
                              gameDbSha256=digest,replaySamplesUsedForCompilation=0)))
    from .skill_implementation_provenance import write_dependency_manifest
    from .requested_skill_scope import implementation_source_files
    closure=write_dependency_manifest(ROOT,implementation_source_files(),ROOT/'schema/implementation-source-closure-v1.json')
    print(json.dumps(dict(precompiledSourceFiles=len(closure['sourceHashes']),importResolutionProbes=len(closure['resolutionTrace']))))
