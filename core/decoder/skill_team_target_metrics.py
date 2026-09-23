"""Team-aware outcomes keep targets per use, never add aggregate success counts."""
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_action_stage_evidence import load_exact_skill_ids


def team_contact_result(spec, enemy_contacts, ally_contacts, method, *, cast_ticks=None):
    cohort = spec['targetCohort']
    selected = ally_contacts if cohort == 'ally' else enemy_contacts
    if cohort == 'either-team':
        if enemy_contacts is None or ally_contacts is None:
            return _unavailable(spec, '적·아군 양쪽의 사용별 대상 연결이 필요함; 한쪽 미확정을 0으로 보완하거나 성공 횟수를 더하지 않음')
        if len(enemy_contacts) != len(ally_contacts):
            raise ValueError('team cohorts must share the same ordered attempts')
        selected = [enemy | ally for enemy, ally in zip(enemy_contacts, ally_contacts)]
    if selected is None:
        return _unavailable(spec, '해당 팀 대상의 실제 효과와 사용별 귀속 미확정')
    row = _result(spec, selected, method, cast_ticks=cast_ticks)
    for old, new in [('distinctEnemyTargetsSummedAcrossAttempts', 'distinctTargetsSummedAcrossAttempts'),
                     ('meanDistinctEnemyTargetsPerAttempt', 'meanDistinctTargetsPerAttempt'),
                     ('deduplicatedEnemyContactEventCount', 'deduplicatedTargetContactEventCount'),
                     ('distinctEnemyTargetsPerAttempt','distinctTargetsPerAttempt'),
                     ('distinctEnemyTargetFirstHitTicksPerAttempt','distinctTargetFirstHitTicksPerAttempt')]:
        row[new] = row.pop(old)
    return row


def johann_team_metric(spec, starts, finishes, states, player, teams, intervals,
                       catalog, skill_rows, state_rows, state_groups, skill_ids=None,
                       *,direct_heals=None,effect_rows=None,spawns=None,terminals=None,evidence_gaps=None,actions=None):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    if spec.get('characterCode') != 41 or spec.get('targetCohort') not in {'ally', 'either-team'}:
        return _unavailable(spec, '검증되지 않은 팀 대상 스킬')
    if spec['skillGroup'] in {1041200,1041210} and spec['targetCohort']=='ally':
        if spec['skillGroup'] in {1041200,1041210}:
            try:
                from .skill_johann_ally_projectile_metrics import johann_ally_projectile_metric
            except ImportError:
                from skill_johann_ally_projectile_metrics import johann_ally_projectile_metric
            return johann_ally_projectile_metric(spec,starts,finishes,spawns,terminals,direct_heals,
                player,teams,intervals,catalog,skill_rows,effect_rows or [],evidence_gaps,skill_ids,actions=actions)
        return johann_q_ally_metric(spec,starts,finishes,direct_heals,player,teams,intervals,
                                   effect_rows or [])
    if spec['targetCohort'] == 'either-team':
        return team_contact_result(spec, None, None, '')
    definitions = {
        1041400: ('JohannActive3', 1041400, 'JohannActive3SmokeState'),
        1041500: ('JohannActive4', 1041510, 'JohannActive4Buff'),
    }
    group = spec['skillGroup']
    if group not in definitions:
        return _unavailable(spec, 'Q 회복·W 향로 효과의 실제 아군 대상과 생성물 수명 연결이 필요함; 피해·공용 상태로 대체하지 않음')
    skill_name, state_group, state_name = definitions[group]
    identity = catalog['skillGroups'].get(str(group), {})
    definition = [s for s in state_groups if s.get('group') == state_group]
    if (identity.get('skillId') != skill_name or identity.get('characterCode') != 41 or
            len(definition) != 1 or definition[0].get('skillId') != state_name or
            definition[0].get('stateType') != 'Common'):
        return _unavailable(spec, '같은 gameDb의 요한 스킬 및 전용 아군 상태 정의 불일치')
    codes = {s['code'] for s in state_rows if s.get('group') == state_group}
    ids = load_exact_skill_ids() if skill_ids is None else skill_ids
    code_groups = {s['code']: s['group'] for s in skill_rows}
    selected = [s for s in starts if s['playerObjectId'] == player and s['skillGroup'] == group]
    if (not codes or not selected or any(s['skillIdCode'] != ids.get(skill_name) or
            code_groups.get(s['skillCode']) != group for s in selected)):
        return _unavailable(spec, '스킬 시전 누락 또는 아군 전용 상태·wire 정의 불일치')
    lives, reason = exact_cast_lifetimes(selected, finishes, player)
    if reason:
        return _unavailable(spec, reason)
    contacts = [set() for _ in lives]
    for state in states:
        target = state['targetObjectId']
        if (state['event'] != 'add' or state.get('stateCode') not in codes or
                target == player or target not in teams or teams[target] != teams[player]):
            continue
        if state.get('stateGroup') not in (None, state_group):
            return _unavailable(spec, '아군 전용 상태 코드와 명시 그룹 불일치')
        if state['casterObjectId'] not in teams:
            return _unavailable(spec, '아군 효과의 실제 시전자 미확정')
        if state['casterObjectId'] != player:
            continue
        candidates = [i for i, (s, end) in enumerate(lives) if s['tick'] <= state['tick'] <= end]
        if len(candidates) != 1:
            return _unavailable(spec, '아군 효과가 정확히 한 번의 요한 사용에 연결되지 않음')
        contacts[candidates[0]].add((state['tick'], target))
    if not any(contacts):
        return _unavailable(spec, '실제 아군 효과 적용 검증 표본 없음')
    chosen = [contacts[i] for i, (s, _) in enumerate(lives)
              if any(left <= s['tick'] < right for left, right in intervals)]
    row = team_contact_result(spec, None, chosen, 'exact-named-Johann-ally-state-caster-and-complete-cast-lifetime',
                              cast_ticks=[s['tick'] for s,_ in lives if any(l<=s['tick']<r for l,r in intervals)])
    row.update(measuredOutcome='ally-skill-effect-application', selfIncluded=False,
               interpretation='교전 중 전체 해당 스킬 사용 대비 다른 아군에게 실제 효과가 적용된 사용; 같은 아군 반복 적용은 다인 수에서 한 명, 회복량·피해량은 측정하지 않음')
    return row


def johann_q_ally_metric(spec,starts,finishes,heals,player,teams,intervals,effect_rows):
    # Kept as a compatibility entry point. The old effect suffix -> stage map
    # is contradicted by outgoing projectile heals and must not publish rates.
    try:
        from .skill_johann_ally_projectile_metrics import johann_ally_projectile_metric
    except ImportError:
        from skill_johann_ally_projectile_metrics import johann_ally_projectile_metric
    return johann_ally_projectile_metric(spec, starts, finishes, None, None,
        heals, player, teams, intervals, {}, [], effect_rows, None)
