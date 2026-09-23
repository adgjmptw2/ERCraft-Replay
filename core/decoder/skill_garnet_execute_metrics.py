"""Garnet R2 execution success through exact target and CmdKill identity."""
from collections import Counter

try:
    from .requested_skill_hit_rates import _result, _unavailable
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable


def garnet_execute_metric(spec, starts, finishes, actions, deaths, player, teams,
                           intervals, catalog, skill_rows, skill_ids=None):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
        from .skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
        from skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS
    fail = lambda reason: _unavailable(spec, reason)
    if (spec.get('characterCode'), spec.get('skillGroup'), spec.get('mode'), spec.get('unit')) != (76, 1076510, 'execute-success', 'skill-cast'):
        return fail('가넷 R2 처형 전용 요청이 아님')
    definition = catalog['skillGroups'].get('1076510', {})
    if (definition.get('characterCode'), definition.get('skillId')) != (76, 'GarnetActive4_2'):
        return fail('정확한 가넷 R2 스킬 정의 불일치')
    codes = {r['code'] for r in skill_rows if r.get('group') == 1076510}
    if not {1076511, 1076512, 1076513} <= codes:
        return fail('가넷 R2 레벨별 스킬 정의 불일치')
    ids = load_exact_skill_ids() if skill_ids is None else skill_ids
    wire = ids.get('GarnetActive4_2')
    selected = sorted((s for s in starts if s['playerObjectId'] == player and s['skillGroup'] == 1076510), key=lambda s: s['tick'])
    if not selected or any(s['skillIdCode'] != wire or s['skillCode'] not in codes for s in selected):
        return fail('가넷 R2 시전 없음 또는 wire 정체성 불일치')
    casts, reason = exact_cast_lifetimes(selected, finishes, player)
    if reason:
        return fail(reason)
    own = [a for a in actions if a.get('sourceObjectId') == player and a.get('skillIdCode') == wire and a.get('wireStatus') in ORDINARY_ACTIONS]
    outcomes = []; unresolved = Counter(); unresolved_ticks = []
    kills = {(d.get('tick'), d.get('deadObjectId')) for d in deaths if d.get('event') == 'CmdKill' and d.get('killerObjectId') == player}
    for start, finish in casts:
        if not any(l <= start['tick'] < r for l, r in intervals):
            continue
        target_rows = []
        for action in own:
            if not start['tick'] <= action['tick'] <= finish:
                continue
            for target in action.get('targets', []):
                target_id = target.get('targetObjectId')
                if (target_id in teams and teams[target_id] != teams[player]
                        and target.get('sameTickOwnedProjectileCollision') is True):
                    target_rows.append((target_id, True))
        if len(target_rows) != 1:
            unresolved['실제 대상과 같은 시각의 소유 발사체 충돌이 일대일로 기록되지 않음'] += 1
            unresolved_ticks.append(start['tick'])
            continue
        target_id = target_rows[0][0]
        outcomes.append({
            'contact': {(finish, target_id)} if (finish, target_id) in kills else set(),
            'castTick': start['tick'], 'finishTick': finish, 'targetObjectId': target_id,
        })
    combat = [o for o in outcomes if any(l <= o['castTick'] < r for l, r in intervals)]
    completeness = dict(unresolvedCombatCastCount=sum(unresolved.values()),
                        unresolvedCastReasons=dict(unresolved), unresolvedCastTicks=unresolved_ticks,
                        observedCombatCastCount=len(combat)+len(unresolved_ticks),
                        verifiedCombatCastCount=len(combat), perUseCompletenessTracked=True,
                        incompleteUsesCountedAsMisses=False)
    if not combat and unresolved_ticks:
        return {**fail('완결된 R2 실제 대상 시전 표본 없음'), **completeness}
    row = _result(spec, [o['contact'] for o in combat], 'exact-R2-target-collision-and-same-finish-tick-CmdKill',
                  cast_ticks=[o['castTick'] for o in combat])
    row.update(executionTargetCount=len(combat), exactExecutionKillCount=sum(bool(o['contact']) for o in combat),
               **completeness,
               fixedDelayJoinUsed=False, killAttributionFallbackUsed=False,
               interpretation='R2의 실제 적 대상·동일 시각 소유 발사체 충돌과 같은 대상의 CmdKill(killerObjectId=가넷, tick=R2 종료)을 연결. 종료 뒤 킬은 성공으로 추정하지 않음.')
    return row
