"""Requested autonomous attacks: actual emissions, without inventing cast starts."""
from collections import defaultdict

try:
    from .skill_attempt_timing import exact_outcome
    from .requested_skill_hit_rates import _result, _unavailable
except ImportError:
    from skill_attempt_timing import exact_outcome
    from requested_skill_hit_rates import _result, _unavailable


DEFINITIONS = {
    1018100: dict(characterCode=18, projectileCode=101802,
                  prefabName='Projectile_FX_BI_Shoichi_Passive',
                  effectCode=1018403, effectPrefabName='FX_BI_Shoichi_Passive_Hit'),
}


def autonomous_shot_metric(spec, spawns, collisions, terminals, damages,
                           player, teams, intervals, catalog, projectile_rows, effect_rows):
    config = DEFINITIONS.get(spec['skillGroup'])
    if (not config or spec['characterCode'] != config['characterCode'] or
            spec['mode'] != 'autonomous-shot' or spec['unit'] != 'projectile-shot' or
            spec.get('scopeEvent') != 'projectile-spawn'):
        return _unavailable(spec, '자동 공격의 실제 발사 기준 정의 불일치')
    stage = catalog['skillGroups'].get(str(spec['skillGroup']), {})
    definitions = [r for r in projectile_rows if r['code'] == config['projectileCode']]
    effects = [r for r in effect_rows if r['code'] == config['effectCode']]
    if (stage.get('characterCode') != config['characterCode'] or stage.get('family') != 'Passive' or
            len(definitions) != 1 or len(effects) != 1):
        return _unavailable(spec, '자동 공격의 동일 버전 패시브·발사체·FX 정의 없음')
    definition = definitions[0]
    if (definition.get('prefabName') != config['prefabName'] or definition.get('type') != 'Target' or
            definition.get('collisionTargetEffectAndSoundCode') != config['effectCode'] or
            definition.get('penetrationCount') != 1 or
            effects[0].get('effectPrefabName') != config['effectPrefabName'] or
            definition.get('isExplosion') is not False or definition.get('isExplosionWithoutCollision') is not False or
            definition.get('enableObjectCollsionCheckAfterArrival') is not False or
            definition.get('lifeTimeAfterArrival') != 0):
        return _unavailable(spec, '자동 추적 단검의 정확한 발사체·도착 타격 정의 불일치')
    selected = [s for s in spawns if s['ownerPlayerObjectId'] == player and s['projectileCode'] == config['projectileCode']]
    if not selected:
        return _unavailable(spec, '자동 단검의 실제 발사가 관측되지 않음')
    ids = {s['projectileObjectId'] for s in selected}
    if len(ids) != len(selected) or any(s['ownerObjectId'] != player for s in selected):
        return _unavailable(spec, '자동 단검 생성 객체 중복 또는 직접 소유 미확정')
    by_terminal = defaultdict(list)
    by_collision = defaultdict(set)
    for event in terminals:
        by_terminal[event['objectId']].append(event)
    for event in collisions:
        by_collision[event['projectileObjectId']].add((event['tick'], event['targetObjectId']))
    own_damage = [d for d in damages if d['attackerObjectId'] in {player, *ids} and
                  d['targetObjectId'] in teams and teams[d['targetObjectId']] != teams[player]]
    damage_contacts = {(d['tick'], d['targetObjectId']) for d in own_damage}
    outcomes = []
    timings = []
    all_hits = set()
    for spawn in selected:
        events = by_terminal[spawn['projectileObjectId']]
        arrivals = [e['tick'] for e in events if e['event'] == 'CmdProjectileArrived']
        destroys = [e['tick'] for e in events if e['event'] == 'CmdDestroy']
        if (len(arrivals) != 1 or len(destroys) != 1 or
                not spawn['tick'] <= arrivals[0] <= destroys[0]):
            return _unavailable(spec, '자동 단검의 도착·최종 소멸 미확인; 실패로 처리하지 않음')
        contacts = by_collision[spawn['projectileObjectId']]
        if any(tick != arrivals[0] for tick, _ in contacts):
            return _unavailable(spec, '자동 추적 단검 충돌과 실제 도착 시점 불일치')
        hits = {(tick, target) for tick, target in contacts if target in teams and teams[target] != teams[player]}
        all_hits.update(hits)
        if any(left <= spawn['tick'] < right for left, right in intervals):
            outcomes.append(hits)
            timings.append(exact_outcome(spawn['tick'], spawn['tick'], hits))
    # The projectile's visual collision FX is not a CmdDamage effect-code FK.
    # Object-specific collisions attribute each hit; owner/tick damage only
    # corroborates contact, as for other non-explosive tracking bullets.
    if not all_hits or all_hits - damage_contacts:
        return _unavailable(spec, '자동 단검의 실제 적 충돌에 동일 소유자·대상·tick 피해 근거가 없음')
    row = _result(spec, outcomes, 'exact-autonomous-projectiles-complete-arrival-collision-destruction-and-owner-damage')
    row['outcomes'] = timings
    row.update(actualProjectileCount=len(outcomes), enemyHitProjectileCount=sum(bool(h) for h in outcomes),
               projectileContactEventCount=sum(len(h) for h in outcomes), completeProjectileLifetimeCount=len(selected),
               fixedShotCountUsed=False, castStartApplicable=False,
               interpretation='회수 후 실제 자동 발사된 단검의 적 실험체 적중 비율; 회수 횟수나 수동 시전 횟수가 아님')
    return row
