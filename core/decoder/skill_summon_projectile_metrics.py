"""Actual emitted bullets from a verified skill-created summon, per exact patch."""
from collections import defaultdict
try:
    from .skill_attempt_timing import exact_outcome
    from .requested_skill_scope import exact_cast_lifetimes
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_summon_ownership import resolve_projectile_owner_chains
except ImportError:
    from skill_attempt_timing import exact_outcome
    from requested_skill_scope import exact_cast_lifetimes
    from requested_skill_hit_rates import _result,_unavailable
    from skill_summon_ownership import resolve_projectile_owner_chains

DEFINITIONS={
    1026200:{'characterCode':26,'summonCode':1100,'prefabPath':'WP_Barbara_ANY_Turret_01_LOD',
             'projectileCode':102620,'projectilePrefab':'Projectile_FX_BI_Turret_NormalAttack'},
    1026210:{'characterCode':26,'summonCode':1101,'prefabPath':'WP_Barbara_ANY_Turret_01_LOD',
             'projectileCode':102621,'projectilePrefab':'Projectile_FX_BI_Turret_NormalAttack_P'},
    1090300:{'characterCode':90,'summonCode':1800,'prefabPath':'Lucia_Skill02_Musket',
             'projectileCode':109031,'projectilePrefab':'Projectile_FX_BI_Lucia_Skill02'},
}


def summon_shot_metric(spec, starts, finishes, summons, all_projectiles, collisions, terminals,
                       damages, player, teams, intervals, catalog, summon_rows):
    config=DEFINITIONS.get(spec['skillGroup'])
    if not config or all_projectiles is None:
        return _unavailable(spec,'이 입력에는 소환물 소유의 전체 발사체 근거가 없음; 발사 횟수를 추정하지 않음')
    definitions=[r for r in summon_rows if r['code']==config['summonCode']]
    projectile=catalog['projectileDefinitions'].get(str(config['projectileCode']),{})
    if (len(definitions)!=1 or definitions[0].get('objectType')!='SummonServant' or
        definitions[0].get('prefabPath')!=config['prefabPath'] or
        projectile.get('prefabName')!=config['projectilePrefab'] or projectile.get('projectileType')!='Target' or
        projectile.get('isExplosion') or projectile.get('isExplosionWithoutCollision') or projectile.get('collisionAfterArrival')):
        return _unavailable(spec,'소환물·발사체의 정확한 gameDb 정의 불일치')
    if not starts:return _unavailable(spec,'소환 스킬 시전이 관측되지 않음')
    lifetimes,reason=exact_cast_lifetimes(starts,finishes,player)
    if reason:return _unavailable(spec,reason)
    emitters=[s for s in summons if s['ownerObjectId']==player and s['summonCode']==config['summonCode']]
    source_cast={}
    for summon in emitters:
        owners=[s for s,end in lifetimes if s['tick']<=summon['tick']<=end]
        if summon.get('identityVerifiedAgainstGameDb') is not True or len(owners)!=1:
            return _unavailable(spec,'소환물 생성이 검증된 소유자와 단 하나의 실제 시전 수명에 연결되지 않음')
        source_cast[summon['objectId']]=owners[0]
    selected=[s for s in all_projectiles if s['ownerObjectId'] in source_cast]
    if not selected or any(s['projectileCode']!=config['projectileCode'] for s in selected):
        return _unavailable(spec,'소환물이 발사한 공격 코드 또는 실제 발사 기록 미확정')
    resolved,unknown=resolve_projectile_owner_chains(selected,summons,terminals,set(teams))
    if unknown or any(s['ownerPlayerObjectId']!=player for s in resolved):
        return _unavailable(spec,'발사 시점 소환물의 실제 소유 관계 미확정')
    by_terminal=defaultdict(list);by_collision=defaultdict(set)
    for t in terminals:by_terminal[t['objectId']].append(t)
    for c in collisions:by_collision[c['projectileObjectId']].add((c['tick'],c['targetObjectId']))
    damage_actors={player,*source_cast,*[s['projectileObjectId'] for s in selected]}
    damage_contacts={(d['tick'],d['targetObjectId']) for d in damages if d['attackerObjectId'] in damage_actors}
    outcomes=[];hit_sample=0;all_closed=0;scoped_casts=set()
    timings=[]
    from .skill_projectile_active_end import projectile_active_end_records
    active_ends=projectile_active_end_records(terminals)
    for spawn in resolved:
        events=by_terminal[spawn['projectileObjectId']]
        arrivals={e['tick'] for e in events if e['event']=='CmdProjectileArrived'}
        active=active_ends.get(spawn['projectileObjectId'],{})
        if (len(arrivals)!=1 or not active.get('complete') or
            not spawn['tick']<=next(iter(arrivals))<=active['endTick']):
            return _unavailable(spec,'실제 발사 탄환의 도착·최종 소멸이 완전하지 않아 실패 확정 불가')
        contacts=by_collision[spawn['projectileObjectId']]
        arrival=next(iter(arrivals))
        if any(tick!=arrival for tick,target in contacts):
            return _unavailable(spec,'추적 탄환의 충돌 시점과 실제 도착이 일치하지 않음')
        hits={(tick,target) for tick,target in contacts if target in teams and teams[target]!=teams[player]}
        if hits-damage_contacts:
            return _unavailable(spec,'소환물 탄환의 실제 적 충돌에 소유 계열·동일 tick 피해 근거가 없음')
        hit_sample+=bool(hits);all_closed+=1
        cast=source_cast[spawn['ownerObjectId']]
        scope_tick=spawn['tick'] if spec.get('scopeEvent')=='projectile-spawn' else cast['tick']
        if any(left<=scope_tick<right for left,right in intervals):
            outcomes.append(hits);scoped_casts.add(cast['tick'])
            timings.append(exact_outcome(scope_tick,spawn['tick'],hits))
    row=_result(spec,outcomes,'exact-skill-summon-owner-chain-actual-bullets-arrival-collision-and-destruction')
    row['outcomes']=timings
    row.update(actualProjectileCount=len(outcomes),enemyHitProjectileCount=sum(bool(hits) for hits in outcomes),
        projectileContactEventCount=sum(len(hits) for hits in outcomes),completeProjectileLifetimeCount=all_closed,
        verifiedSummonCount=len(emitters),sourceCastsWithCombatShots=len(scoped_casts),
        fixedShotCountUsed=False,normalAttackOrRailgunCombined=False,
        positiveSampleRequired=False,visualDestructionRequired=False)
    return row
