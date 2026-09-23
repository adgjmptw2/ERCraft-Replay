"""Dedicated on-hit callbacks corroborated by an exclusive damage family.

Fiora Active3_Hit=11 is from exact 12.3 metadata ede0935bbc00... . A common
effect code alone is never enough: every callback, damage and completed cast
in the player-match must agree, including hits on non-player targets.
"""
from collections import defaultdict
try:
    from .requested_skill_scope import exact_cast_lifetimes
    from .requested_skill_hit_rates import _result,_unavailable
except ImportError:
    from requested_skill_scope import exact_cast_lifetimes
    from requested_skill_hit_rates import _result,_unavailable


def fiora_e_hit_action_metric(spec,starts,finishes,actions,damages,player,teams,intervals,effect_rows,projectile_owners):
    if spec['skillGroup']!=1003400:
        return _unavailable(spec,'이 스킬의 전용 타격 행동 경로는 검증되지 않음')
    effects=[r for r in effect_rows if r['code']==1000000]
    if len(effects)!=1 or (effects[0].get('effectPrefabName'),effects[0].get('soundName'))!=('FX_BI_Common_Normal_Hit','hitOneHandSword_r1'):
        return _unavailable(spec,'피오라 E 후보 이펙트의 정확한 gameDb 정의 불일치')
    starts=[s for s in starts if s.get('playerObjectId',player)==player]
    if not any(s.get('skillGroup')==1003400 for s in starts):
        return _unavailable(spec,'피오라 E 시전이 관측되지 않음')
    lifetimes,reason=exact_cast_lifetimes(starts,finishes,player)
    if reason: return _unavailable(spec,reason)
    # Keep E lifetimes separate from other player casts (especially weapon D).
    e_lifetimes=[x for x in lifetimes if x[0].get('skillGroup')==1003400]
    other_lifetimes=[x for x in lifetimes if x[0].get('skillGroup')!=1003400]
    if not e_lifetimes:
        return _unavailable(spec,'피오라 E 시전 수명이 없음')
    lifetimes=e_lifetimes
    ids={s['skillIdCode'] for s in starts if s.get('skillGroup')==1003400}
    callbacks=[a for a in actions if a.get('sourceObjectId')==player and a.get('skillIdCode') in ids
               and a.get('actionNo')==11 and a.get('wireStatus')=='decoded-exact-CmdPlaySkillAction']
    callback_by_cast=defaultdict(list)
    for a in callbacks:
        owners=[i for i,(s,end) in enumerate(lifetimes) if s['skillIdCode']==a['skillIdCode'] and s['tick']<=a['tick']<=end]
        if len(owners)!=1:
            return _unavailable(spec,'피오라 E 전용 타격 행동이 실제 시전 수명에 단독 연결되지 않음')
        callback_by_cast[owners[0]].append(a)
    if not callbacks or any(len(rows)!=1 for rows in callback_by_cast.values()):
        return _unavailable(spec,'한 E 시전의 전용 타격 행동을 단 하나로 확인하지 못함')
    contacts=[set() for _ in lifetimes]
    damage_counts=defaultdict(int)
    excluded_competing_packets=0
    for d in damages:
        attacker=d.get('attackerObjectId')
        if attacker!=player and projectile_owners.get(attacker)!=player: continue
        if d.get('effectCode')!=1000000: continue
        if attacker!=player:
            return _unavailable(spec,'공통 타격 코드가 소유 투사체에서도 발생하여 E 전용 귀속 아님')
        owners=[i for i,(s,end) in enumerate(lifetimes) if callback_by_cast[i]
                and callback_by_cast[i][0]['tick']<=d['tick']<=end]
        competing=[i for i,(s,end) in enumerate(other_lifetimes)
                   if s['tick']<=d['tick']<=end]
        e_windows=[i for i,(s,end) in enumerate(lifetimes) if s['tick']<=d['tick']<=end]
        if len(owners)>1 or (owners and competing):
            row=_unavailable(spec,'공통 피해가 E와 다른 시전 수명에 중복 귀속됨')
            row['hitActionConflict']={'tick':d['tick'],'wireOrder':d.get('wireOrder'),
                'eCandidates':len(owners),'competingCandidates':len(competing)}
            return row
        if len(owners)!=1:
            # A common hit outside E is acceptable only when the exact same
            # packet falls inside one competing player cast lifetime (weapon D).
            if not e_windows and len(competing)==1:
                excluded_competing_packets+=1
                continue
            row=_unavailable(spec,'공통 타격 코드 일부가 E 전용 타격 행동 이후의 실제 시전 수명 밖에서 발생')
            row['hitActionConflict']={'tick':d['tick'],'wireOrder':d.get('wireOrder'),
                'eCandidates':len(owners),'eWindows':len(e_windows),'competingCandidates':len(competing)}
            return row
        i=owners[0];damage_counts[i]+=1
        target=d.get('targetObjectId')
        if target in teams and teams[target]!=teams[player]: contacts[i].add((d['tick'],target))
    if any(rows and not damage_counts[i] for i,rows in callback_by_cast.items()):
        return _unavailable(spec,'일부 E 타격 행동에 대응하는 실제 공통 피해가 없어 타격 결과 완전성 미확정')
    if not any(contacts):
        return _unavailable(spec,'피오라 E 자체의 적 플레이어 타격 검증 표본 없음')
    chosen=[i for i,(s,end) in enumerate(lifetimes) if any(l<=s['tick']<r for l,r in intervals)]
    row=_result(spec,[contacts[i] for i in chosen],'dedicated-Fiora-Active3-Hit-callback-exclusive-complete-damage-lifetime')
    row.update(actionIdentity='FioraSkillAction.Active3_Hit=11',
        actionIdentityMetadataSha256='ede0935bbc00dcd93707d279f5a3f63570b8f09fcd9119d4582637f957afed61',
        effectCodeAloneUsed=False,fixedActionDelayUsed=False,
        allTargetHitCallbacksChecked=len(callbacks),
        excludedCompetingDamagePacketCount=excluded_competing_packets)
    return row
