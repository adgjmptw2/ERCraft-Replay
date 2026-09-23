"""Zahir R waves linked through the real, co-created marker-object lifetime.

No cooldown/nearest-cast or fixed-duration window is used. Every actual marker
and damaging wave must have a complete lifetime, and the whole family must
corroborate its explosion targets against exact damage packets.
"""
from collections import defaultdict
try:
    from .requested_skill_hit_rates import _matches, _result, _unavailable
    from .requested_skill_scope import exact_cast_lifetimes
except ImportError:
    from requested_skill_hit_rates import _matches, _result, _unavailable
    from requested_skill_scope import exact_cast_lifetimes


def zahir_r_lifetime_metric(spec,starts,all_starts,spawns,collisions,terminals,damages,
                           teams,player,catalog,intervals,window,finishes,effect_rows,*,zero_hit_policy=None):
    if spec['skillGroup']!=1005500:
        return _unavailable(spec,'이 스킬의 후속 공격 객체 수명은 아직 검증되지 않음')
    definitions=catalog['projectileDefinitions']
    expected={76:'Projectile_Zahir_Skill04_Circle_01',82:'Projectile_Zahir_Skill04_Circle_02',
              100501:'Projectile_Zahir_Skill04_Circle_03'}
    if any(definitions.get(str(c),{}).get('prefabName')!=name for c,name in expected.items()):
        return _unavailable(spec,'정확한 자히르 R 투사체 계열 정의 불일치')
    if definitions['100501'].get('collisionEnabled') or definitions['100501'].get('isExplosion'):
        return _unavailable(spec,'R 표시 객체 자체에 공격 판정이 있어 별도 판정 필요')
    effects=[r for r in effect_rows if r['code']==1005006]
    if len(effects)!=1 or (effects[0].get('effectPrefabName'),effects[0].get('soundName')) != ('FX_BI_Zahir_Skill04_Hit','zahir_Skill04_Hit'):
        return _unavailable(spec,'정확한 R 타격 EffectAndSound 정의 불일치')
    lifetimes,reason=exact_cast_lifetimes(starts,finishes,player)
    if reason: return _unavailable(spec,reason)
    if not starts: return _unavailable(spec,'R 시전이 관측되지 않음')
    own=[s for s in spawns if s['ownerPlayerObjectId']==player and s['projectileCode'] in expected]
    by_terminal=defaultdict(list)
    for event in terminals: by_terminal[event['objectId']].append(event)
    from .skill_projectile_active_end import projectile_active_end_records
    active_ends=projectile_active_end_records(terminals)
    initial=[s for s in own if s['projectileCode']==76]
    markers=[s for s in own if s['projectileCode']==100501]
    marker_ids={s['projectileObjectId'] for s in markers}
    if any(c['projectileObjectId'] in marker_ids for c in collisions):
        return _unavailable(spec,'표시 객체에 실제 충돌이 있어 공격 누락 여부 확인 필요')
    waves=[s for s in own if s['projectileCode']==82]
    roots=defaultdict(list)
    for spawn in initial:
        owners=[s for s in all_starts if 76 in catalog['skillGroups'][str(s['skillGroup'])].get('projectileCodeCandidates',[])
                and _matches(spawn,s,catalog['skillGroups'],window)]
        if len(owners)!=1 or owners[0]['skillGroup']!=1005500:
            return _unavailable(spec,'R 첫 공격을 한 실제 시전에 연결하지 못함')
        roots[id(owners[0])].append(spawn)
    cancelled=set()
    for start,end in lifetimes:
        linked=roots[id(start)]
        if len(linked)==1: continue
        finish=next(f for f in finishes if f.get('playerObjectId')==player and f.get('skillIdCode')==start['skillIdCode'] and f['tick']==end)
        if not linked and finish.get('reason') in set(range(1,15))|{16,17}:
            cancelled.add(id(start))
        else:
            return _unavailable(spec,'R 첫 발 누락을 실제 시전 취소와 구분하지 못함')
    marker_roots=defaultdict(list)
    for marker in markers:
        owners=[s for s in starts if roots[id(s)] and roots[id(s)][0]['tick']==marker['tick']]
        if len(owners)!=1:
            return _unavailable(spec,'R 표시 객체가 첫 공격과 같은 tick에 단독 생성되지 않음')
        events=by_terminal[marker['projectileObjectId']]
        active=active_ends.get(marker['projectileObjectId'],{})
        ends=[active['endTick']] if active.get('complete') else []
        arrivals=[t['tick'] for t in events if t['event']=='CmdProjectileArrived']
        if len(ends)!=1 or arrivals!=[marker['tick']] or ends[0]<=marker['tick']:
            return _unavailable(spec,'R 표시 객체의 실제 생성·도착·소멸 수명 불완전')
        marker_roots[id(owners[0])].append((marker,ends[0]))
    for spawn in waves:
        owners=[s for s in starts if marker_roots[id(s)] and
                any(m['tick']<=spawn['tick']<=end for m,end in marker_roots[id(s)])]
        if len(owners)!=1:
            return _unavailable(spec,'후속 R 공격이 한 묶음의 실제 표시 객체 수명에 배타적으로 연결되지 않음')
        roots[id(owners[0])].append(spawn)
    for start in starts:
        if id(start) in cancelled: continue
        if not marker_roots[id(start)] or len(roots[id(start)])!=len(marker_roots[id(start)]):
            return _unavailable(spec,'실제 생성 표시 객체 수와 완료된 R 공격 객체 수가 일치하지 않음')
    # R has a dedicated hit effect family. Unlike Q's common effect, a target
    # can be recovered from its R-specific damage packet even when the blast
    # target list omits it, but only on a uniquely linked real explosion tick.
    target_lists=defaultdict(set)
    for c in collisions:
        if c['targetObjectId'] in teams and teams[c['targetObjectId']]!=teams[player]:
            target_lists[c['projectileObjectId']].add((c['tick'],c['targetObjectId']))
    family_damage={(d['tick'],d['targetObjectId']) for d in damages if d['attackerObjectId']==player
        and d.get('effectCode')==1005006 and d['targetObjectId'] in teams and teams[d['targetObjectId']]!=teams[player]}
    contacts_by_cast={id(s):set() for s in starts}
    explosion_casts=defaultdict(set)
    all_contacts=set()
    for start in starts:
        for spawn in roots[id(start)]:
            code=spawn['projectileCode']
            definition=definitions[str(code)]
            events=by_terminal[spawn['projectileObjectId']]
            explosions=[t['tick'] for t in events if t['event']=='CmdProjectileExplosion']
            active=active_ends.get(spawn['projectileObjectId'],{})
            ends=[active['endTick']] if active.get('complete') else []
            if not definition.get('isExplosion') or definition.get('collisionAfterArrival') or len(explosions)!=1 or len(ends)!=1 or not spawn['tick']<=explosions[0]<=ends[0]:
                return _unavailable(spec,'R 공격 객체의 단발 폭발·최종 소멸 수명 불완전')
            hits=target_lists[spawn['projectileObjectId']]
            if any(t!=explosions[0] for t,_ in hits) or not hits<=family_damage:
                return _unavailable(spec,'R 폭발 대상과 실제 R 타격 피해의 tick·대상이 불일치')
            all_contacts.update(hits)
            explosion_casts[explosions[0]].add(id(start))
    if not family_damage and (zero_hit_policy is None or not initial):
        return _unavailable(spec,'R 전용 피해의 적 플레이어 검증 표본 없음')
    for tick,target in family_damage:
        owners=explosion_casts[tick]
        if len(owners)!=1:
            return _unavailable(spec,'R 전용 피해가 한 시전의 실제 폭발 tick에 배타적으로 연결되지 않음')
        contacts_by_cast[next(iter(owners))].add((tick,target))
    chosen=[s for s in starts if any(l<=s['tick']<r for l,r in intervals)]
    row=_result(spec,[contacts_by_cast[id(s)] for s in chosen],
        'exact-R-marker-lifetime-wave-explosions-and-exclusive-effect-damage',
        cast_ticks=[s['tick'] for s in chosen])
    row.update(cancelledBeforeAttackCount=sum(id(s) in cancelled for s in chosen),
        actualDamageWaveCount=sum(len(roots[id(s)]) for s in chosen),
        actualMarkerObjectCount=sum(len(marker_roots[id(s)]) for s in chosen),
        damageContactsWithoutCollisionListCount=len(family_damage-all_contacts),
        fixedDurationWindowUsed=False,nearestCastUsed=False)
    if not family_damage and zero_hit_policy is not None:
        from .skill_development_effect_metrics import annotate_provisional_rate
        row.update(perUseCompletenessTracked=True,unresolvedCombatCastCount=0,
                   observedCombatCastCount=len(chosen),perMatchPositiveSampleRequired=False)
        row=annotate_provisional_rate(row,zero_hit_policy)
    return row
