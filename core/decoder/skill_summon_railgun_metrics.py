"""Barbara turret railgun emissions, distinct from projectile-object bullets.

The exact 12.3 metadata hash for wire enum identities is recorded below.
An action is an emission only when all normal attack completions and railgun
damage agree with that action. A target in the action is aim, never hit proof.
"""
from collections import defaultdict
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .requested_skill_scope import exact_cast_lifetimes
    from .skill_summon_ownership import resolve_projectile_owner_chains
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from requested_skill_scope import exact_cast_lifetimes
    from skill_summon_ownership import resolve_projectile_owner_chains

METADATA_SHA256='ede0935bbc00dcd93707d279f5a3f63570b8f09fcd9119d4582637f957afed61'
# SkillId.BarbaraTurretActive1_RailGunAttack and BarbaraSkillAction.Turret_ProcessRailGun.
RAILGUN_SKILL_ID=366
FIRE_ACTION=112
CONFIG={'summon-railgun-normal':(1100,1026200), 'summon-railgun-reinforced':(1101,1026210)}


def railgun_metric(spec, starts, finishes, actions, summons, terminals, damages,
                   all_projectiles, player, teams, intervals, catalog, summon_rows, effect_rows):
    code,source_group=CONFIG[spec['mode']]
    stage=catalog['skillGroups'].get('1026020',{})
    definitions=[r for r in summon_rows if r['code']==code]
    effects=[r for r in effect_rows if r['code']==1026102]
    if (all_projectiles is None or len(definitions)!=1 or
        definitions[0].get('objectType')!='SummonServant' or
        definitions[0].get('prefabPath')!='WP_Barbara_ANY_Turret_01_LOD' or
        stage.get('skillId')!='BarbaraTurretActive1_RailGunAttack' or
        len(effects)!=1 or effects[0].get('effectPrefabName')!='FX_BI_Barbara_Pistol_NormalAttack_Shot'):
        return _unavailable(spec,'레일건의 정확한 스킬·소환물·FX 또는 전체 발사체 근거 없음')
    installations=[s for s in starts if s['skillGroup']==source_group]
    lifetimes,reason=exact_cast_lifetimes(installations,finishes,player)
    if reason:return _unavailable(spec,reason)
    emitters={s['objectId']:s for s in summons if s['ownerObjectId']==player and s['summonCode']==code}
    if not emitters:return _unavailable(spec,'이 형태의 센트리건 생성 표본 없음')
    for summon in emitters.values():
        if (summon.get('identityVerifiedAgainstGameDb') is not True or
            sum(s['tick']<=summon['tick']<=end for s,end in lifetimes)!=1):
            return _unavailable(spec,'센트리건 생성과 실제 Q/RQ 시전의 배타적 귀속 미확정')
    fired=[a for a in actions if a['sourceObjectId'] in emitters and a['skillIdCode']==RAILGUN_SKILL_ID]
    if not fired:return _unavailable(spec,'해당 센트리건 형태의 레일건 발사가 관측되지 않음')
    if any(a['actionNo']!=FIRE_ACTION or a.get('wireStatus')!='decoded-exact-CmdPlaySkillActionWithTargets'
           or len(a.get('targets',[]))!=1 or type(a['targets'][0].get('targetObjectId')) is not int for a in fired):
        return _unavailable(spec,'레일건 발사 행동과 조준 대상 형식 불일치')
    identities={(a['sourceObjectId'],a['tick']) for a in fired}
    if len(identities)!=len(fired):return _unavailable(spec,'같은 센트리건·tick에 레일건 발사 행동 중복')
    # Reuse the generic live-owner resolver, without claiming these are spawned projectiles.
    _,unresolved=resolve_projectile_owner_chains([
        dict(tick=a['tick'],ownerObjectId=a['sourceObjectId'],projectileObjectId=i)
        for i,a in enumerate(fired)],summons,terminals,set(teams))
    if unresolved:return _unavailable(spec,'레일건 발사 시점 소환물의 실제 소유·수명 미확정')
    paired=set();cancelled_without_emission=0
    for object_id,summon in emitters.items():
        previous=summon['tick']-1
        ends=sorted([e for e in finishes if e['playerObjectId']==object_id and e['skillIdCode']==RAILGUN_SKILL_ID],key=lambda e:e['tick'])
        if len({e['tick'] for e in ends})!=len(ends):return _unavailable(spec,'레일건 종료 이벤트 중복')
        for end in ends:
            emissions=[a for a in fired if a['sourceObjectId']==object_id and previous<a['tick']<=end['tick']]
            reason=end.get('reason')
            if (type(reason) is not int or reason not in set(range(15))|{16,17} or
                len(emissions)>1 or (reason==0 and len(emissions)!=1)):
                return _unavailable(spec,'모든 레일건 정상 종료와 실제 발사 행동이 일대일로 연결되지 않음')
            if not emissions:cancelled_without_emission+=1
            paired.update((a['sourceObjectId'],a['tick']) for a in emissions)
            previous=end['tick']
    if paired!=identities:return _unavailable(spec,'종료가 없는 레일건 발사는 완결된 판정 아님')
    # The owner's normal bullets and player damage cannot enter a turret railgun shot.
    by_emission=defaultdict(set)
    mixed_damage=defaultdict(list)
    for damage in damages:
        source,tick=damage['attackerObjectId'],damage['tick']
        if source not in emitters:continue
        if damage.get('effectCode')==1026102:
            if (source,tick) not in identities:
                return _unavailable(spec,'센트리건 레일건 후보 피해가 실제 발사 행동 밖에 존재')
            by_emission[source,tick].add(damage['targetObjectId'])
        elif (source,tick) in identities:
            # The unique validated emission bounds the uncertainty. Preserve
            # the foreign packet; neither assign it as a hit nor call it zero.
            mixed_damage[source,tick].append(dict(damage))
    mixed_evidence=[dict(sourceObjectId=source,emissionTick=tick,
        damageRecords=[dict(d) for d in damages if d['attackerObjectId']==source and d['tick']==tick],
        assignedAsHit=False) for source,tick in sorted(mixed_damage)]
    if not any(target in teams and teams[target]!=teams[player]
               for key,targets in by_emission.items() if key not in mixed_damage for target in targets):
        row=_unavailable(spec,'해당 형태 레일건의 실제 적 실험체 피해 검증 표본 없음')
        if mixed_evidence:row['mixedDamageEmissionEvidence']=mixed_evidence
        return row
    observed=[a for a in fired if any(left<=a['tick']<right for left,right in intervals)]
    unknown={i:'mixed-turret-damage-at-emission' for i,a in enumerate(observed)
             if (a['sourceObjectId'],a['tick']) in mixed_damage}
    chosen=[a for i,a in enumerate(observed) if i not in unknown]
    contacts=[{(a['tick'],target) for target in by_emission[a['sourceObjectId'],a['tick']]
               if target in teams and teams[target]!=teams[player]} for a in chosen]
    row=_result(spec,contacts,'exact-live-turret-railgun-action-completion-bijection-and-exclusive-damage')
    try:
        from .skill_attempt_timing import exact_outcome
    except ImportError:
        from skill_attempt_timing import exact_outcome
    row['outcomes']=[exact_outcome(a['tick'],a['tick'],contact) for a,contact in zip(chosen,contacts)]
    row.update(actualEmissionCount=len(contacts),enemyHitEmissionCount=sum(bool(c) for c in contacts),
        completeEmissionCountAllMatch=len(fired),cancelledBeforeEmissionCountAllMatch=cancelled_without_emission,
        aimTargetUsedAsHit=False,normalBulletsCombined=False,projectileObjectCountUsed=False,
        interpretation='실제 레일건 발사 기준; 발사 전 취소는 탄환 분모와 별도, 일반탄은 제외')
    if mixed_damage:
        from .skill_lifecycle_result_policy import finalize_lifecycle_result
        row=finalize_lifecycle_result(row,list(range(len(observed))),unknown)
        row.update(actualEmissionCount=len(observed),classifiedEmissionCount=len(chosen),
            unresolvedEmissionCount=len(unknown),
            mixedDamageEmissionEvidence=mixed_evidence,
            unresolvedCastTicks=[observed[i]['tick'] for i in unknown],
            unresolvedUseEvidence=[dict(startTick=observed[i]['tick'],
                sourceObjectId=observed[i]['sourceObjectId'],
                reasons=[unknown[i]],hasRecordedEmission=True) for i in unknown])
        if unknown:
            row.update(hitRateScope='verified-uses-only',wholeCombatHitRate=None,
                fullRequestedMetricComplete=False)
    return row
