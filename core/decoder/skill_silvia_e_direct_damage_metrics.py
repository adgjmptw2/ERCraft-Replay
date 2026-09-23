"""Reviewed direct-damage route for Silvia bike E (12.3.0)."""
from __future__ import annotations
try:
    from .requested_skill_scope import exact_cast_lifetimes
    from .requested_skill_hit_rates import _result, _unavailable
except ImportError:
    from requested_skill_scope import exact_cast_lifetimes
    from requested_skill_hit_rates import _result, _unavailable

SILVIA_CHARACTER = 16
SILVIA_BIKE_E_GROUP = 1016800
SILVIA_BIKE_E_SKILL = 1016801

def silvia_bike_e_direct_damage_metric(spec, player_starts, finishes, damages, player, teams, intervals, skill_rows, gaps=None):
    """Calculate Silvia bike-E cast contacts only after all identity gates pass."""
    if spec.get('characterCode') != SILVIA_CHARACTER or spec.get('skillGroup') != SILVIA_BIKE_E_GROUP:
        return _unavailable(spec, '실비아 바이크 E 전용 경로가 아님')
    definitions = [row for row in skill_rows if row.get('group') == SILVIA_BIKE_E_GROUP]
    skill_codes = {row.get('code') for row in definitions}
    if skill_codes != {1016801, 1016802, 1016803, 1016804, 1016805}:
        return _unavailable(spec, '12.3.0 실비아 바이크 E의 레벨별 Skill code 정의 불일치')
    all_starts = [row for row in player_starts if row.get('playerObjectId') == player]
    e_starts = [row for row in all_starts if row.get('skillGroup') == SILVIA_BIKE_E_GROUP]
    if not e_starts:
        return _unavailable(spec, '실비아 바이크 E 시전이 관측되지 않음')
    # This route already requires E's dedicated damage code. An unrelated
    # skill's missing finish cannot change the ownership of that code.
    lifetimes, reason = exact_cast_lifetimes(e_starts, finishes, player)
    if reason:
        return _unavailable(spec, reason)
    e_lifetimes = [(start, end) for start, end in lifetimes if start.get('skillGroup') == SILVIA_BIKE_E_GROUP]
    if len(e_lifetimes) != len(e_starts):
        return _unavailable(spec, '실비아 바이크 E 시전과 종료가 일대일로 연결되지 않음')
    candidates = [row for row in damages if row.get('attackerObjectId') == player and row.get('effectCode') == SILVIA_BIKE_E_SKILL]
    assigned = [[] for _ in e_lifetimes]
    for damage in candidates:
        owners = [index for index, (start, end) in enumerate(e_lifetimes) if start.get('tick') <= damage.get('tick') <= end]
        if len(owners) != 1:
            return _unavailable(spec, '실비아 바이크 E effect 피해가 한 실제 시전 수명에 배타적으로 연결되지 않음')
        assigned[owners[0]].append(damage)
    from .skill_development_effect_metrics import development_policy,annotate_provisional_rate
    from .skill_wire_order import command_order
    from .skill_partial_cast_lifetimes import normal_completion_partition
    policy=development_policy()
    development=(policy['enabled'] and policy.get('lifecycleEstimatesEnabled',False) and gaps is not None
        and not any(g.get('count',0) and g.get('packetName') in {'CmdStartSkill','CmdFinishSkill','CmdDamage'} for g in gaps))
    if not development and any(len(rows)!=1 for rows in assigned):
        return _unavailable(spec,'실비아 바이크 E 일부 시전에 전용 effect 피해가 없거나 중복됨')
    selected=[i for i,(start,end) in enumerate(e_lifetimes) if any(l<=start['tick']<h for l,h in intervals)]
    _,unknown=normal_completion_partition(e_lifetimes,finishes,player,selected)
    for i,rows in enumerate(assigned):
        if rows:unknown.pop(i,None)
        if len(rows)>1 and (any(command_order(d) is None for d in rows) or len({command_order(d) for d in rows})!=len(rows)):
            unknown[i]='duplicate-or-unordered-direct-effect-packets'
    experimental=[i for i in selected if i not in unknown and len(assigned[i])!=1]
    player_team = teams.get(player)
    if player_team is None:
        return _unavailable(spec, '시전자 팀 정보가 없음')
    contacts = []
    enemy_packets = 0
    eligible=[];cast_ticks=[]
    for i in selected:
        if i in unknown:continue
        rows=assigned[i];start=e_lifetimes[i][0]
        contact={(d['tick'],d['targetObjectId']) for d in rows if d.get('targetObjectId') in teams and teams[d['targetObjectId']]!=player_team}
        enemy_packets+=sum(d.get('targetObjectId') in teams and teams[d['targetObjectId']]!=player_team for d in rows)
        eligible.append(contact);cast_ticks.append(start['tick'])
    result=_result(spec,eligible,'reviewed-Silvia-bike-E-exact-effect-cast-lifetime',cast_ticks=cast_ticks)
    from .skill_lifecycle_result_policy import finalize_lifecycle_result
    result=finalize_lifecycle_result(result,selected,unknown,observed_positive=any(eligible))
    if experimental:
        result=annotate_provisional_rate(result,policy)
        result.update(normalCompletedUsesWithoutEffectCount=sum(not assigned[i] for i in experimental),
            exactEffectPacketPerCast=False,developmentAssumption='완전한 명령 스트림의 정상 종료 시전은 전용 피해가 없으면 개발 중 미적중으로 집계')
    result.update(effectCode=SILVIA_BIKE_E_SKILL,
                  effectCodeIdentity='Skill.group=1016800,base-hit-effect-code=1016801,levels=1..5',
                  exactEffectPacketCount=len(candidates), exactEffectPacketPerCast=all(len(assigned[i])==1 for i in selected if i not in unknown),
                  enemyPlayerEffectPacketCount=enemy_packets,
                  damageValueNullPolicy='CmdDamage damage nullity is retained; packet target/contact is the evidence')
    return result
