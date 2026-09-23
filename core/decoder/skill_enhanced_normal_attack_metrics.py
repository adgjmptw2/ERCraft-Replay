"""Cathy Q enhanced attacks from their explicit normal-attack start command.

Only same-target decoded damage command at the exact finish establishes a hit.
Explicit gameplay cancellations can establish failed uses. Everything else is
an unknown use, not a missing-damage miss or a guessed basic-attack attribution.
"""
from collections import Counter
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_action_stage_evidence import load_exact_skill_ids


def enhanced_normal_attack_metric(spec,starts,finishes,damages,player,teams,
                                  intervals,catalog,skill_rows,effect_rows,skill_ids=None,gaps=None,development=False):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    if (spec.get('characterCode'),spec.get('skillGroup'),spec.get('mode'),spec.get('unit'))!=(23,1023210,'any','skill-cast'):
        return _unavailable(spec,'unsupported enhanced normal attack')
    definitions={r['code']:r for r in effect_rows}
    effects={1023201:'FX_BI_Cathy_Skill01_Hit_S',1023211:'FX_BI_Cathy_Skill01_Hit'}
    definition=catalog['skillGroups'].get('1023210',{})
    if (definition.get('skillId')!='CathyActive1NormalAttack' or definition.get('characterCode')!=23 or
            any(definitions.get(c,{}).get('effectPrefabName')!=p for c,p in effects.items())):
        return _unavailable(spec,'exact Cathy enhanced attack and candidate effect definitions unavailable')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    codes={r['code'] for r in skill_rows if r.get('group')==1023210}
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1023210]
    if not selected or any(s.get('sourceEvent')!='CmdStartNormalAttackSkill' or s['skillCode'] not in codes or
            s['skillIdCode']!=ids.get('CathyActive1NormalAttack') for s in selected):
        return _unavailable(spec,'enhanced attacks require explicit normal-attack command identities')
    lives,reason=exact_cast_lifetimes(selected,finishes,player)
    if reason:return _unavailable(spec,reason)
    reasons={(f['skillIdCode'],f['tick']):f.get('reason') for f in finishes if f['playerObjectId']==player}
    all_target_damage=[d for d in damages if d['attackerObjectId']==player and d.get('effectCode') in effects]
    own=[d for d in all_target_damage if d['targetObjectId'] in teams and teams[d['targetObjectId']]!=teams[player]]
    complete_streams=gaps is not None and not any(g.get('count',0) and g.get('packetName') in
        {'CmdStartNormalAttackSkill','CmdStartSkill','CmdFinishSkill','CmdDamage'} for g in gaps)
    non_enemy_contacts=0
    contacts=[];ticks=[];unknown=Counter();observed=0;cancelled=0;estimated=[]
    for start,end in lives:
        if not any(l<=start['tick']<r for l,r in intervals):continue
        observed+=1;target=start.get('targetObjectId');stop=reasons.get((start['skillIdCode'],end))
        if development and stop==0 and complete_streams and target in teams:
            actual=[d for d in damages if d['attackerObjectId']==player and d['targetObjectId']==target and start['tick']<=d['tick']<=end and sum(s['tick']<=d['tick']<=e and s.get('targetObjectId')==target for s,e in lives)==1]
            contacts.append({(d['tick'],target) for d in actual} if teams[target]!=teams[player] else set());ticks.append(start['tick']);estimated.append(start['tick']);continue
        packets=[d for d in own if start['tick']<=d['tick']<=end]
        exact=[d for d in packets if d['tick']==end and d['targetObjectId']==target and type(d.get('damageIsNull')) is bool]
        # Equal-tick finishes cannot let one damage packet confirm two uses.
        unique=all(sum(s['tick']<=d['tick']<=e and s.get('targetObjectId')==target for s,e in lives)==1 for d in exact)
        if stop==0 and exact and unique and all(d['targetObjectId']==target and d['tick']==end for d in packets):
            contacts.append({(end,target)});ticks.append(start['tick'])
        elif stop==0 and not packets and complete_streams and any(
                d['tick']==end and d['targetObjectId']==target and type(d.get('damageIsNull')) is bool
                and sum(s['tick']<=d['tick']<=e and s.get('targetObjectId')==target for s,e in lives)==1
                for d in all_target_damage):
            # The closed attack has a real hit on its specified non-enemy
            # target. Enemy-only prefiltering must not erase that completion.
            contacts.append(set());ticks.append(start['tick']);non_enemy_contacts+=1
        elif stop in set(range(1,15))|{16,17} and not packets:
            contacts.append(set());ticks.append(start['tick']);cancelled+=1
        else:
            unknown['missing-damage-or-unresolved-finish-target']+=1
    diagnostics=dict(observedCombatCastCount=observed,verifiedCombatCastCount=len(contacts),
        unresolvedCombatCastCount=sum(unknown.values()),unresolvedCastReasons=dict(unknown),
        exactEnhancedAttackStartCount=len(selected),cancelledBeforeAttackCount=cancelled,
        completedNonEnemyTargetAttackCount=non_enemy_contacts,positiveSampleRequired=False,
        perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False)
    if not contacts:
        return {**_unavailable(spec,'explicit enhanced attack starts found; no exact enemy finish-target damage command'),**diagnostics}
    result=_result(spec,contacts,'explicit-enhanced-normal-attack-exact-finish-target-damage-command',cast_ticks=ticks)
    result.update(diagnostics,candidateEffectCodes=sorted(effects),
        outcomeScope='verified selected enhanced attacks; other observed uses remain unknown',
        finishTickUsedAsHitTickOnlyWithExactDamagePacket=True,
        damageAmountRequiredForContact=False,damageAmountInferred=False,
        evidenceReview='deliverables/cathy-q-completed-target-contract-v1.json')
    if estimated:
        from .skill_development_cancellation import annotate_provisional
        result['estimatedTargetAttackCastTicks']=estimated
        annotate_provisional(result,'Completed enhanced attacks use actual damage to the original target within that attack; absence is provisionally a miss. Damage to other targets is excluded. Shared effects may reduce attribution accuracy.')
    return result
