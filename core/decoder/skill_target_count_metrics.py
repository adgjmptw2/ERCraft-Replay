"""Count actual acquired enemy targets per use, independently of later hits."""
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_action_stage_evidence import load_exact_skill_ids


def target_count_metric(spec, starts, finishes, states, player, teams, intervals,
                        catalog, skill_rows, state_rows, state_groups, skill_ids=None, gaps=None):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    if (spec['characterCode'], spec['skillGroup'], spec['mode']) != (72,1072500,'target-count'):
        return _unavailable(spec,'이 스킬의 실제 타겟 포착 정의는 검증되지 않음')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    group=catalog['skillGroups'].get('1072500',{})
    definitions=[s for s in state_groups if s.get('group')==1072500]
    if (group.get('skillId')!='KatjaActive4' or group.get('characterCode')!=72 or
            len(definitions)!=1 or definitions[0].get('skillId')!='KatjaActive4ScanTargetState'):
        return _unavailable(spec,'같은 gameDb의 카티야 R 및 전용 타겟 포착 상태 정의 불일치')
    codes={s['code'] for s in state_rows if s.get('group')==definitions[0]['group']}
    code_groups={s['code']:s['group'] for s in skill_rows}
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1072500]
    if (not codes or not selected or any(s['skillIdCode']!=ids.get('KatjaActive4') or
            code_groups.get(s['skillCode'])!=1072500 for s in selected)):
        return _unavailable(spec,'R 시전 누락 또는 전용 타겟 상태·wire 정의 불일치')
    lives,reason=exact_cast_lifetimes(selected,finishes,player)
    if reason:return _unavailable(spec,reason)
    if any(f['reason']==15 for f in finishes if f['playerObjectId']==player and f['skillIdCode']==ids.get('KatjaActive4')):
        return _unavailable(spec,'재생 종료로 닫힌 R은 전체 타겟 포착 결과를 확정하지 않음')
    targets=[set() for _ in lives]
    for state in states:
        target=state['targetObjectId']
        if state['event']!='add' or state.get('stateCode') not in codes or target not in teams or teams[target]==teams[player]:continue
        if state.get('stateGroup') not in (None,1072500):
            return _unavailable(spec,'타겟 포착 상태 코드와 명시 그룹 불일치')
        if state['casterObjectId'] not in teams:
            return _unavailable(spec,'타겟 포착 상태의 실제 시전자 미확정')
        if state['casterObjectId']!=player:continue
        candidates=[i for i,(s,end) in enumerate(lives) if s['tick']<=state['tick']<=end]
        if len(candidates)!=1:
            return _unavailable(spec,'타겟 포착 상태가 정확히 하나의 R 사용에 연결되지 않음')
        targets[candidates[0]].add((state['tick'],target))
    from .skill_lifecycle_result_policy import reviewed_rule_reuse_policy,annotate_reviewed_rule_reuse
    reuse=reviewed_rule_reuse_policy(gaps,{'CmdStartSkill','CmdFinishSkill','CmdAddState'},starts,finishes,states)
    if not any(targets) and reuse is None:return _unavailable(spec,'타겟 상태 스트림 완전성 확인 필요')
    chosen=[i for i,(s,_) in enumerate(lives) if any(left<=s['tick']<right for left,right in intervals)]
    row=_result(spec,[targets[i] for i in chosen],'exact-named-scan-target-state-caster-and-complete-R-lifetime',
                cast_ticks=[lives[i][0]['tick'] for i in chosen])
    row.update(measuredOutcome='distinct-acquired-enemy-targets-per-use',
        primaryNumeratorField='distinctEnemyTargetsSummedAcrossAttempts',primaryDenominatorField='attemptCount',
        countsProjectileHits=False,interpretation='사용마다 실제 타겟으로 포착한 서로 다른 적 인원을 합산; 같은 적 재포착은 한 사용에서 한 명이며 발사·피해·탄환 적중 여부와 독립적')
    return annotate_reviewed_rule_reuse(row,reuse,positive_gate_unsatisfied=not any(targets))
