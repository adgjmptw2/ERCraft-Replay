"""Count explicitly cancelled, unlaunched cast attempts as requested misses."""
try:
    from .requested_skill_hit_rates import _matches,_projectile_contacts,_unavailable
    from .requested_skill_scope import exact_cast_lifetimes
except ImportError:
    from requested_skill_hit_rates import _matches,_projectile_contacts,_unavailable
    from requested_skill_scope import exact_cast_lifetimes


def direct_cast_with_cancellations(spec,legacy,starts,all_starts,spawns,collisions_by_id,
                                  teams,player,catalog,intervals,window,finishes):
    if spec['unit']!='skill-cast':
        return _unavailable(spec,'이 스킬의 발사 전 취소 경로는 아직 검증되지 않음')
    evidence=legacy.get('projectileCodeCandidateEvidence',{})
    candidates=set(evidence.get('runtimeExclusiveCandidates') or evidence.get('staticCandidates') or [])
    relevant=[s for s in spawns if s['projectileCode'] in candidates]
    emitted={id(start):any(_matches(s,start,catalog['skillGroups'],window) for s in relevant)
             for start in starts}
    # Input-shape dispatch, before evaluating a rate. Already emitted casts
    # use the existing collision rule once; only absent emissions need the
    # actual cancellation record. A failed rule never triggers another rule.
    if starts and all(emitted.values()):
        result,reason=_projectile_contacts(spec,legacy,starts,all_starts,spawns,
            collisions_by_id,teams,player,catalog,intervals,window)
        return result if result is not None else _unavailable(spec,reason)
    lifetimes,reason=exact_cast_lifetimes(starts,finishes,player)
    if reason: return _unavailable(spec,reason)
    cancelled=[]
    fired=[]
    for start,end in lifetimes:
        if emitted[id(start)]:
            fired.append(start)
            continue
        finish=next(f for f in finishes if f.get('playerObjectId')==player and f.get('skillIdCode')==start['skillIdCode'] and f['tick']==end)
        if finish.get('reason') not in set(range(1,15))|{16,17}:
            return _unavailable(spec,'발사체가 없는 시전의 실제 취소 사유 미확정')
        cancelled.append(start)
    result,reason=_projectile_contacts(spec,legacy,fired,all_starts,spawns,collisions_by_id,
        teams,player,catalog,intervals,window)
    if result is None: return _unavailable(spec,reason)
    count=sum(any(l<=s['tick']<r for l,r in intervals) for s in cancelled)
    # Sort the entire per-attempt record together, including zero-contact uses.
    # Sorting only outcomes left the target arrays shorter and misaligned.
    parallel=['outcomes','distinctEnemyTargetsPerAttempt','distinctEnemyTargetFirstHitTicksPerAttempt']
    if any(len(result.get(k,[]))!=result['attemptCount'] for k in parallel):
        return _unavailable(spec,'실제 발사 시도별 대상·시각 배열 불일치')
    attempts_data=list(zip(*(result[k] for k in parallel)))
    attempts_data.extend(([s['tick'],0,s['tick'],None],0,[]) for s in cancelled
                         if any(l<=s['tick']<r for l,r in intervals))
    attempts_data.sort(key=lambda item:(item[0][0],item[0][2]))
    for j,key in enumerate(parallel):result[key]=[item[j] for item in attempts_data]
    attempts=result['attemptCount']+count
    result.update(attemptCount=attempts,
        status='calculable-observed' if attempts else 'no-combat-sample',
        hitRate=round(result['hitCount']/attempts,6) if attempts else None,
        multiTargetAttemptRate=round(result['multiTargetAttemptCount']/attempts,6) if attempts else None,
        meanDistinctEnemyTargetsPerAttempt=round(result['distinctEnemyTargetsSummedAcrossAttempts']/attempts,6) if attempts else None,
        cancelledBeforeAttackCount=count,
        method='exclusive-projectile-cast-with-explicit-cancellations-counted-as-misses')
    return result
