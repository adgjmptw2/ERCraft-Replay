"""Judgment kills at the recorded targeted attack, never nearby/later kills."""
from collections import Counter

try:
    from .skill_recorded_servant_target import preceding_servant_records
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS
except ImportError:
    from skill_recorded_servant_target import preceding_servant_records
    from requested_skill_hit_rates import _result, _unavailable
    from skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS


def judgment_kill_metric(spec, starts, finishes, actions, damages, deaths, player,
                         teams, intervals, catalog, skill_rows, effect_rows,
                         evidence_gaps, skill_ids=None,development=False,target_object_types=None,target_objects=None):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    fail=lambda reason:_unavailable(spec,reason)
    if (spec.get('characterCode'),spec.get('skillGroup'),spec.get('mode'),spec.get('unit'))!=(14,1014510,'enemy-kill','skill-cast'):
        return fail('unsupported judgment kill metric')
    definition=catalog['skillGroups'].get('1014510',{})
    effects=[e for e in effect_rows if e.get('code')==1014503]
    if (definition.get('characterCode')!=14 or definition.get('skillId')!='ChiaraActive4judgment' or
            len(effects)!=1 or effects[0].get('effectPrefabName')!='FX_BI_Chiara_Skill04_judge_atk_01'):
        return fail('exact judgment skill/effect definitions unavailable')
    required={'CmdStartSkill','CmdFinishSkill','CmdPlaySkillAction','CmdPlaySkillActionWithTargets','CmdDamage','CmdKill'}
    if evidence_gaps is None or any(g.get('count',0) and g.get('packetName') in required for g in evidence_gaps):
        return fail('judgment attack/kill stream completeness unavailable')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    wire=ids.get('ChiaraActive4judgment')
    codes={r['code'] for r in skill_rows if r.get('group')==1014510}
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1014510]
    if not selected or type(wire) is not int or any(s['skillIdCode']!=wire or s['skillCode'] not in codes for s in selected):
        return fail('explicit judgment cast and versioned skill identities unavailable')
    lives,reason=exact_cast_lifetimes(selected,finishes,player)
    if reason:return fail(reason)
    own_actions=[a for a in actions if a['sourceObjectId']==player and a['skillIdCode']==wire
                 and a.get('wireStatus') in ORDINARY_ACTIONS]
    own_damage=[d for d in damages if d['attackerObjectId']==player]
    kills={(d['tick'],d['deadObjectId']) for d in deaths
           if d.get('event')=='CmdKill' and d.get('killerObjectId')==player}
    contacts=[];ticks=[];unknown=Counter();observed=0;attack_ticks=[];estimated=[];excluded=[];non_player=[]
    servant_targets=[]
    for start,end in lives:
        if not any(l<=start['tick']<r for l,r in intervals):continue
        observed+=1;target=start.get('targetObjectId')
        if target not in teams or teams[target]==teams[player]:
            if target not in teams and (target_object_types or {}).get(target)=={3}:
                # Exact ObjectType.Monster is outside enemy-player kill rate.
                # Missing identity and contradictory object types stay unknown.
                non_player.append(start['tick']);continue
            servant = preceding_servant_records(target_objects, target, start, evidence_gaps) if target not in teams else None
            if servant:
                non_player.append(start['tick'])
                servant_targets.append(dict(start=dict(start),objectRecords=servant))
                continue
            unknown['target-is-not-an-enemy-player']+=1;continue
        # A kill percentage needs a recorded judgment attack and its actual
        # target, not a non-null numeric damage value. Null damage alone never
        # establishes a kill: only CmdKill with the same killer/target/tick does.
        action_ticks={a['tick'] for a in own_actions if start['tick']<=a['tick']<=end}
        evidence={d['tick'] for d in own_damage if d['targetObjectId']==target and
                  d.get('effectCode')==1014503 and d['tick'] in action_ticks}
        if len(evidence)!=1:
            ends=[f for f in finishes if f['playerObjectId']==player and f['skillIdCode']==wire and f['tick']==end]
            if development and len(ends)==1 and ends[0].get('reason')==0:
                matching={t for t,o in kills if o==target and start['tick']<=t<=end}
                contacts.append({(min(matching),target)} if matching else set());ticks.append(start['tick']);attack_ticks.append(None);estimated.append(start['tick']);continue
            if development and len(ends)==1 and ends[0].get('reason') in set(range(1,15))|{16,17} and not any(start['tick']<=a['tick']<=end for a in own_actions) and not any(start['tick']<=d['tick']<=end and d.get('effectCode')==1014503 for d in own_damage):
                excluded.append(start['tick']);continue
            unknown['no-single-recorded-judgment-attack-on-cast-target']+=1;continue
        tick=next(iter(evidence))
        if sum(s['tick']<=tick<=e and s.get('targetObjectId')==target for s,e in lives)!=1:
            unknown['judgment-attack-overlaps-another-use']+=1;continue
        killed=(tick,target) in kills
        if killed and any(d['tick']==tick and d['targetObjectId']==target and d.get('effectCode')!=1014503 for d in own_damage):
            unknown['other-own-damage-at-kill-tick-prevents-skill-attribution']+=1;continue
        contacts.append({(tick,target)} if killed else set());ticks.append(start['tick']);attack_ticks.append(tick)
    diagnostics=dict(observedCombatCastCount=observed,verifiedCombatCastCount=len(contacts),
        unresolvedCombatCastCount=sum(unknown.values()),unresolvedCastReasons=dict(unknown),
        perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,
        exactJudgmentAttackCount=len(contacts),killAttributionFallbackUsed=False,
        numericDamageRequired=False,measuredOutcome='same-attack-tick-target-CmdKill',
        outcomeScope='recorded judgment attacks on enemy players; other uses remain unknown')
    diagnostics.update(excludedNonPlayerTargetCastCount=len(non_player),excludedNonPlayerTargetCastTicks=non_player)
    diagnostics.update(excludedSummonServantTargetEvidence=servant_targets)
    if not contacts and observed>len(excluded)+len(non_player):
        return {**fail('observed judgment uses have no classifiable enemy attack'),**diagnostics}
    row=_result(spec,contacts,'exact-judgment-target-action-effect-and-same-tick-CmdKill',cast_ticks=ticks)
    row.update(diagnostics,exactJudgmentKillCount=sum(bool(c) for c in contacts),
               judgmentAttackTicks=attack_ticks,
               laterKillsCounted=False,knockdownsCountedAsKills=False)
    if estimated or excluded:
        from .skill_development_cancellation import annotate_provisional
        row.update(estimatedJudgmentCastTicks=estimated,exactJudgmentKillCount=None,provisionallyExcludedCancelledCastCount=len(excluded),provisionallyExcludedCancelledCastTicks=excluded,
                   verifiedCompletionCredit=False,calculationConfidence='experimental')
        annotate_provisional(row,'Without the exact attack marker, normally completed R uses the recorded killer and original target within its cast. Other damage can cause that kill; attribution is provisional. Canceled uses without judgment action or judgment effect are provisionally excluded; unrelated aura damage is not judgment emission. Knockdowns and later kills are excluded.')
    return row
