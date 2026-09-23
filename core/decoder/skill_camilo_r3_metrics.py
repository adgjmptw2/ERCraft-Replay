"""Exact Camilo R3 stage metric from the recorded skill command and damage family."""
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .requested_skill_scope import exact_cast_lifetimes
    from .skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from requested_skill_scope import exact_cast_lifetimes
    from skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS


def camilo_r3_metric(spec, starts, finishes, actions, damages, player, teams,
                     intervals, catalog, skill_rows, effect_rows):
    if (spec['characterCode'], spec['skillGroup'], spec['mode'], spec['unit']) != (39, 1039520, 'any', 'skill-cast'):
        return _unavailable(spec, '카밀로 R3 전용 규칙 아님')
    definition = catalog.get('skillGroups', {}).get('1039520', {})
    if (definition.get('characterCode'), definition.get('skillId')) != (39, 'CamiloActive4_3'):
        return _unavailable(spec, '카밀로 R3 정확한 gameDb 정체성 불일치')
    effect = [r for r in effect_rows if r.get('code') == 1039505]
    if len(effect) != 1 or (effect[0].get('effectPrefabName'), effect[0].get('soundName')) != (
        'FX_BI_Camilo_Skill04_Debuff_Hit', 'Camilo_Skill04_Debuff_Hit'):
        return _unavailable(spec, '카밀로 R3 피해 EffectAndSound 정의 불일치')
    skill_codes = {r['code']: r['group'] for r in skill_rows}
    wire = load_exact_skill_ids().get('CamiloActive4_3')
    selected = [s for s in starts if s.get('playerObjectId') == player and s.get('skillGroup') == 1039520]
    # The stage is identified by the exact CmdStartSkill group/code and the
    # exact EffectAndSound row; one complete use is sufficient per match.
    if not selected or wire is None or any(
        s.get('skillIdCode') != wire or skill_codes.get(s.get('skillCode')) != 1039520
        for s in selected
    ):
        return _unavailable(spec, '카밀로 R3 정확한 반복 시전·wire 검증 부족')
    records, reason = exact_cast_lifetimes(selected, finishes, player)
    if reason:
        return _unavailable(spec, reason)
    hits = []
    for start, end in records:
        contacts = set()
        for d in damages:
            target = d.get('targetObjectId')
            if (d.get('attackerObjectId') == player and d.get('effectCode') == 1039505
                    and target in teams and teams[target] != teams[player]
                    and start['tick'] <= d['tick'] <= end):
                contacts.add((d['tick'], target))
        hits.append(contacts)
    combat = [i for i, (s, _) in enumerate(records)
              if any(lo <= s['tick'] < hi for lo, hi in intervals)]
    if not combat:
        return _unavailable(spec, '카밀로 R3 교전 시전 표본 없음')
    row = _result(spec, [hits[i] for i in combat], 'exact-R3-command-and-in-cast-damage-family',
                  cast_ticks=[records[i][0]['tick'] for i in combat])
    row.update(
        exactSkillGroup=1039520,
        exactSkillCodes=sorted({s['skillCode'] for s, _ in records}),
        exactDamageEffectCode=1039505,
        fixedWindowUsed=False,
        fallbackUsed=False,
        combatCastCount=len(combat),
        interpretation='R3 CmdStartSkill의 정확한 stage group과 동일 시전 수명 내 Camilo R3 피해 EffectAndSound를 연결; 미적중은 완료 시전 내 적 피해 부재로만 계산.',
    )
    return row
