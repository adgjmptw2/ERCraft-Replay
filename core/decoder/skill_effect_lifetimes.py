"""Explicit exact-version effect families with complete, exclusive cast lifetimes.

EffectAndSound is the effect-code namespace. These reviewed identities are
candidate *families*, not a Skill-table foreign key. Every observed family hit
must fit exactly one completed cast in that family; delayed/shared/overlapping
outcomes remain unresolved instead of becoming misses. The normal pipeline
opts into per-use completeness and explicit development zero-hit reuse.
"""
from collections import defaultdict, Counter
try:
    from .skill_wire_order import finish_lookup,event_within_cast
    from .skill_partial_cast_lifetimes import normal_completion_partition,retain_cancelled_hits,annotate_cancelled_hits
except ImportError:
    from skill_wire_order import finish_lookup,event_within_cast
    from skill_partial_cast_lifetimes import normal_completion_partition,retain_cancelled_hits,annotate_cancelled_hits

# Exact EffectAndSound identities from the policy's SHA256-pinned gameDb.
# Generic prefab names are additionally distinguished by their exact hit sound.
EFFECT_FAMILIES = {
    'jackie-manual': {
        'groups': (1001200,1001210,1001400,1001510),
        'effects': {
            1001201: ('FX_BI_Jackie_NormalHit','hitSkillOneHandSword_r2'),
            1001202: ('FX_BI_Jackie_NormalHit','hitSkillTwoHandSword_r1'),
            1001203: ('FX_BI_Jackie_NormalHit','hitSkillAxe_r1'),
            1001204: ('FX_BI_Jackie_NormalHit','hitSkillDualSword_r1'),
        }},
    'fiora-w': {
        'groups': (1003300,),
        'effects': {1003004:('FX_BI_Fiora_Skill02_Hit','hitRapier_r2'),
                    1003005:('FX_BI_Fiora_Skill02_Hit','hitTwoHandSword_r1'),
                    1003006:('FX_BI_Fiora_Skill02_Hit','hitSpear_r2')}},
    'fiora-e': {
        'groups': (1003400,),
        'effects': {1003011:('FX_BI_Fiora_Skill03_Hit','hitRapier_r2'),
                    1003012:('FX_BI_Fiora_Skill03_Hit','hitTwoHandSword_r1'),
                    1003013:('FX_BI_Fiora_Skill03_Hit','hitSpear_r2')}},
    'fiora-r': {
        'groups': (1003500,1003510,1003520),
        'effects': {1003021:('FX_BI_Fiora_Skill04_Hit_New1','fiora_Skill04_Attack01_Hit'),
                    1003022:('FX_BI_Fiora_Skill04_Hit_New2','fiora_Skill04_Attack02_Hit')}},
    'magnus-e': {
        'groups': (1004400,),
        'effects': {1004401:('FX_BI_Magnus_Skill03_Hit','magnus_Skill03_Hit'),
                    1004402:('FX_BI_Magnus_Skill03_Hit','magnus_Skill03_Hit')}},
}


def effect_lifetime_metric(spec, all_starts, finishes, damages, player, teams,
                           intervals, effect_rows, projectile_owners, *, allow_partial=False, zero_hit_policy=None):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
        from .requested_skill_hit_rates import _result, _unavailable
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
        from requested_skill_hit_rates import _result, _unavailable
    family_id=next((key for key,value in EFFECT_FAMILIES.items() if spec['skillGroup'] in value['groups']),None)
    if family_id is None:
        return _unavailable(spec,'검증할 명시적 타격 이펙트 계열과 시전 수명 연결 없음')
    family=EFFECT_FAMILIES[family_id]
    definitions=defaultdict(list)
    for row in effect_rows: definitions[row['code']].append(row)
    for code,(prefab,sound) in family['effects'].items():
        matches=definitions[code]
        if len(matches)!=1 or (matches[0].get('effectPrefabName'),matches[0].get('soundName'))!=(prefab,sound):
            return _unavailable(spec,'정확한 EffectAndSound 코드·프리팹·효과음 정의 불일치')
    starts=[s for s in all_starts if s.get('playerObjectId',player)==player and s['skillGroup'] in family['groups']]
    selected=[s for s in starts if s['skillGroup']==spec['skillGroup']]
    if not selected:
        return _unavailable(spec,'해당 단계 시전이 관측되지 않음')
    from .skill_wire_order import command_order
    incomplete={}
    if allow_partial and all(command_order(s) is not None for s in starts):
        from .skill_partial_cast_lifetimes import ordered_cast_records
        records,reason=ordered_cast_records(starts,finishes,player,allow_same_tick_finishes=True)
        if reason:return _unavailable(spec,reason)
        lifetimes=[(r['start'],r['finish']['tick'] if r['finish'] else None) for r in records]
        incomplete={i:r['reason'] for i,r in enumerate(records) if not r['complete']}
    else:
        lifetimes,reason=exact_cast_lifetimes(starts,finishes,player)
        if reason:return _unavailable(spec,reason)
    contacts=[set() for _ in lifetimes]
    finish_orders=finish_lookup(finishes,player)
    damage_counts=[0 for _ in lifetimes]
    observed=0
    for damage in damages:
        owner=damage.get('attackerObjectId')
        if owner not in teams: owner=projectile_owners.get(owner)
        target=damage.get('targetObjectId')
        if (owner!=player or target not in teams or teams[target]==teams[player] or
            damage.get('effectCode') not in family['effects']): continue
        observed+=1
        owners=[i for i,(start,end) in enumerate(lifetimes) if (event_within_cast(start,end,damage,finish_orders) if end is not None else
            command_order(damage) is not None and command_order(start)<=command_order(damage) and start['tick']<=damage['tick'])]
        if len(owners)!=1:
            return {**_unavailable(spec,'타격 이펙트가 시전 수명 밖에 있거나 여러 단계와 겹침'),
                    'effectFamily':family_id,'effectFamilyRuntimeLinkComplete':False}
        i=owners[0]
        if family_id=='magnus-e' and lifetimes[i][0].get('targetObjectId')!=target:
            return _unavailable(spec,'매그너스 E 지정 대상과 실제 타격 대상 불일치')
        contacts[i].add((damage['tick'],target))
        damage_counts[i]+=1
    if not observed and zero_hit_policy is None:
        return _unavailable(spec,'타격 계열의 적 플레이어 실제 피해 패킷 표본 없음')
    selected=[i for i,(start,_) in enumerate(lifetimes) if start['skillGroup']==spec['skillGroup']]
    normal,unknown=normal_completion_partition(lifetimes,finishes,player,selected)
    unknown.update(incomplete)
    recovered=retain_cancelled_hits(unknown,contacts,lifetimes,finishes,player)
    partial_positive={i for i in selected if contacts[i] and unknown.get(i) in
        {'open-final-cast','replay-end-is-not-gameplay-finish'} and zero_hit_policy is not None}
    for i in partial_positive:unknown.pop(i)
    normal=sorted(set(normal)|partial_positive|set(recovered))
    if not any(damage_counts[i] for i in normal) and zero_hit_policy is None:
        return _unavailable(spec,'이 단계 자체의 적 타격 연결 표본 없음; 다른 단계 적중으로 미적중을 확정하지 않음')
    combat=[i for i in selected if any(l<=lifetimes[i][0]['tick']<r for l,r in intervals)]
    chosen=[i for i in combat if i not in unknown]
    diagnostics=dict(perUseCompletenessTracked=True,finishReasonRequired=0,
                     observedCombatCastCount=len(combat),verifiedCombatCastCount=len(chosen),
                     unresolvedCombatCastCount=len(combat)-len(chosen),
                     unresolvedCastReasons=dict(Counter(unknown[i] for i in combat if i in unknown)),
                     incompleteUsesCountedAsMisses=False)
    if combat and not chosen:
        return {**_unavailable(spec,'정상 완료된 교전 시전 없음; 취소를 미적중으로 계산하지 않음'),**diagnostics}
    row=_result(spec,[contacts[i] for i in chosen],'explicit-effect-family-exclusive-complete-cast-lifetime',
                cast_ticks=[lifetimes[i][0]['tick'] for i in chosen])
    row.update(**diagnostics,effectFamily=family_id,effectFamilyRuntimeLinkComplete=True,
               effectNamespace='EffectAndSound',numericSkillStateCodeJoinUsed=False,
               exactDamagePacketCount=sum(damage_counts[i] for i in chosen),
               exactDamagePacketCountMeaning='decoded family damage packets; distinct-target and cast-success counts are separate')
    row=annotate_cancelled_hits(row,recovered,lifetimes,chosen)
    if allow_partial or zero_hit_policy is not None:
        from .skill_lifecycle_result_policy import finalize_lifecycle_result
        row=finalize_lifecycle_result(row,combat,unknown,incomplete_positive=partial_positive,
            observed_positive=any(damage_counts[i] for i in chosen))
        row['unresolvedUseEvidence']=[dict(startTick=lifetimes[i][0]['tick'],startOrder=command_order(lifetimes[i][0]),
            reasons=[unknown[i]],emissionKind='direct-effect',hasRecordedEffectEvidence=bool(damage_counts[i])) for i in combat if i in unknown]
        row.update(perMatchPositiveSampleRequired=zero_hit_policy is None,positiveSampleRequiredForRuleDiscovery=False,
            zeroHitPolicy='explicit-development-complete-ordered-use' if zero_hit_policy is not None else None)
    return row
