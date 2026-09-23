"""Aiden melee Q: ordered synchronous collision-loop damage and action pairs.

SpearHitEffectCode is not CmdDamage.effectCode. The pinned producer emits an
action without targets, then one DamageTo and one position action per queried
target. The position itself is never used to identify the damaged character.
"""
from collections import Counter
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order as order
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order as order
    from skill_action_stage_evidence import load_exact_skill_ids


def aiden_spear_execution(spec, starts, finishes, actions, damages, player, teams,
                          intervals, catalog, skill_rows, gaps, skill_ids=None, *, evasion_events=None, summons=None, development=False):
    selected = [s for s in starts if s['playerObjectId'] == player and s['skillGroup'] == 1046200]
    combat = lambda s: any(a <= s['tick'] < b for a, b in intervals)
    diag = dict(observedCastCount=len(selected), observedCombatCastCount=sum(map(combat, selected)))
    def fail(reason):
        return {**_unavailable(spec, reason), **diag}
    if (spec['characterCode'], spec['skillGroup'], spec['mode'], spec['unit']) != (46, 1046200, 'any', 'skill-cast'):
        return fail('unsupported Aiden spear execution')
    if any(x is None for x in (actions, damages, gaps)) or player not in teams:
        return fail('missing action/damage stream or caster team')
    required = {'CmdStartSkill', 'CmdFinishSkill', 'CmdDamage', 'CmdPlaySkillAction', 'CmdPlaySkillActionWithTargets'}
    if any(g.get('count', 0) and g.get('packetName') in required for g in gaps):
        return fail('incomplete Aiden spear command stream')
    ids = load_exact_skill_ids() if skill_ids is None else skill_ids
    codes = {s['code'] for s in skill_rows if s.get('group') == 1046200}
    if (ids.get('AidenActive1_Spear') != 654 or ids.get('AidenPassive') != 648 or not codes
            or catalog['skillGroups'].get('1046200', {}).get('skillId') != 'AidenActive1_Spear'
            or any(s['skillIdCode'] != 654 or s['skillCode'] not in codes for s in selected)):
        return fail('pinned Aiden spear identity mismatch')
    records, why = ordered_cast_records(selected, finishes, player, allow_same_tick_finishes=True)
    if why:
        return fail(why)
    aa = [a for a in actions if a['sourceObjectId'] == player and a['skillIdCode'] == 654]
    if any(order(a) is None for a in aa) or len({order(a) for a in aa}) != len(aa):
        return fail('missing or repeated spear action order')
    contacts, cast_ticks, attempt_ticks, details, evidence, evaded_details = [], [], [], [], [], []
    unknown = {}; assigned = set(); cancelled = []; recipient_groups=[]
    for i, rec in enumerate(records):
        s, end = rec['start'], rec['finish']
        owned = sorted((a for a in aa if order(s) < order(a) and (end is None or order(a) < order(end))), key=order)
        assigned.update(map(order, owned))
        if not rec['complete'] or end is None:
            unknown[i] = rec['reason']; continue
        markers = [a for a in owned if a.get('wireStatus') == 'decoded-exact-CmdPlaySkillAction'
                   and a.get('actionNo') == 1 and a.get('targets') == []]
        closes = [a for a in owned if a.get('wireStatus') == 'decoded-exact-CmdPlaySkillActionWithTargets'
                  and a.get('actionNo') == 1]
        # Process emits the marker before any collision query or DamageTo.
        # An actual cancellation with no Q action never executed this attack.
        if end.get('reason') == 3 and not owned:
            cancelled.append(i); continue
        if len(markers) != 1 or len(owned) != 1 + len(closes):
            unknown[i] = 'missing-or-ambiguous-spear-execution-marker'; continue
        marker = markers[0]
        # Process has no yield: all target iterations and normal finish are in
        # the marker frame. Recovery/cancel handling is not guessed here.
        if end.get('reason') != 0 or end['tick'] != marker['tick'] or any(a['tick'] != marker['tick'] or order(a) <= order(marker) for a in closes):
            unknown[i] = 'spear-loop-not-synchronously-closed'; continue
        if any(len(a.get('targets', [])) != 1 or a['targets'][0].get('targetObjectId') != 0
               or a['targets'][0].get('hasTargetPosition') is not True for a in closes):
            unknown[i] = 'unexpected-spear-loop-action-payload'; continue
        # Same-frame effects of other skills outside this exact marker/finish
        # range cannot enter the attribution. Unknown ordering is not ignored.
        ds = [d for d in damages if d['attackerObjectId'] == player and d['tick'] == marker['tick']]
        if any(order(d) is None for d in ds):
            unknown[i] = 'missing-spear-damage-order'; continue
        ds = [d for d in ds if order(marker) < order(d) < order(end)]
        # OnModifyExtraPointToPlayerCharacter emits passive action 1/2 from
        # the synchronous on-hit stack change. It is not a second attack.
        other_actions = [a for a in actions if a['sourceObjectId'] == player and a['skillIdCode'] != 654
                         and a['tick'] == marker['tick']
                         and not (a['skillIdCode'] == 648 and a.get('actionNo') in (1, 2)
                                  and a.get('wireStatus') == 'decoded-exact-CmdPlaySkillAction'
                                  and a.get('targets') == [])]
        if any(order(a) is None or order(marker) < order(a) < order(end) for a in other_actions):
            unknown[i] = 'competing-action-inside-spear-loop'; continue
        primary = [d for d in ds if d.get('damageType') == 1]
        evades = [e for e in evasion_events or [] if e.get('event') == 'CmdEvasion' and e['tick'] == marker['tick']]
        if any(order(e) is None for e in evades):
            unknown[i] = 'missing-evasion-command-order'; continue
        evades = [e for e in evades if order(marker) < order(e) < order(end)]
        previous = order(marker); used = set(); used_evades = set(); hits = []; avoided = []; why = None
        for closing in closes:
            segment = [d for d in primary if previous < order(d) < order(closing)]
            avoided_segment = [e for e in evades if previous < order(e) < order(closing)]
            if avoided_segment:
                if (segment or len(avoided_segment) != 1
                        or any(g.get('count', 0) and g.get('packetName') == 'CmdEvasion' for g in gaps)):
                    why = 'ambiguous-primary-damage-or-evasion'; break
                evasion = avoided_segment[0];target = evasion.get('objectId')
                if type(target) is not int or target <= 0:
                    why = 'missing-evasion-target-identity'; break
                used_evades.add(order(evasion))
                avoided.append(dict(targetObjectId=target, eventTick=evasion['tick'], outcome='evaded',
                                    enemyPlayer=target in teams and teams[target] != teams[player],
                                    evasionOrder=evasion['wireOrder'], targetLoopActionOrder=closing['wireOrder']))
                previous = order(closing); continue
            grouped=False
            if development and len(segment)==2 and summons is not None:
                direct,child=segment
                owners=[s for s in summons if s.get('objectId')==child['targetObjectId']
                    and s.get('ownerObjectId')==direct['targetObjectId'] and s.get('summonCode')==1191
                    and s.get('identityVerifiedAgainstGameDb') is True and s['tick']<=child['tick']]
                grouped=(direct['targetObjectId'] in teams and child['targetObjectId'] not in teams
                    and len(owners)==1 and all(d.get('effectCode')==0 and type(d.get('damageIsNull')) is bool for d in segment)
                    and order(direct)<order(child))
            if (len(segment) != 1 and not grouped) or segment[0].get('effectCode') != 0:
                why = 'missing-or-ambiguous-primary-spear-damage'; break
            d = segment[0]
            if type(d.get('damageIsNull')) is not bool:
                why = 'missing-damage-discriminator'; break
            used.update(order(x) for x in segment)
            if grouped:
                recipient_groups.append(dict(castTick=s['tick'],playerTargetId=d['targetObjectId'],
                    linkedServantId=segment[1]['targetObjectId'],damageOrders=[x['wireOrder'] for x in segment],
                    targetLoopActionOrder=closing['wireOrder']))
            target = d['targetObjectId']
            if target in teams and teams[target] != teams[player]:
                hits.append(dict(targetObjectId=target, hitTick=d['tick'], damageOrder=d['wireOrder'],
                                 targetLoopActionOrder=closing['wireOrder']))
            previous = order(closing)
        if why is None and (len(used) != len(primary) or len({order(d) for d in primary}) != len(primary)):
            why = 'primary-damage-outside-unique-spear-target-iteration'
        if why is None and (len(used_evades) != len(evades) or len({order(e) for e in evades}) != len(evades)):
            why = 'evasion-outside-unique-spear-target-iteration'
        if why:
            unknown[i] = why; continue
        if combat(s):
            contacts.append({(h['hitTick'], h['targetObjectId']) for h in hits})
            cast_ticks.append(s['tick']); attempt_ticks.append(marker['tick']); details.append(hits); evaded_details.append(avoided)
            evidence.append(dict(startOrder=s['wireOrder'], markerOrder=marker['wireOrder'], finishOrder=end['wireOrder'],
                                 targetIterationCount=len(closes), primaryDamageCount=len(primary), evasionCount=len(avoided)))
    if assigned != {order(a) for a in aa}:
        return fail('orphan spear execution or target-loop action')
    reasons = Counter(v for i, v in unknown.items() if combat(records[i]['start']))
    diag.update(unresolvedCombatCastCount=sum(reasons.values()), unresolvedCastReasons=dict(reasons),
                unresolvedNonCombatCastCount=sum(not combat(records[i]['start']) for i in unknown),
                unresolvedNonCombatCastReasons=dict(Counter(v for i, v in unknown.items() if not combat(records[i]['start']))),
                cancelledBeforeAttackCount=sum(combat(records[i]['start']) for i in cancelled),
                cancelledBeforeAttackCastTicks=[records[i]['start']['tick'] for i in cancelled if combat(records[i]['start'])],
                cancelledBeforeAttackNonCombatCount=sum(not combat(records[i]['start']) for i in cancelled),
                perUseCompletenessTracked=True, incompleteUsesCountedAsMisses=False)
    if diag['observedCombatCastCount'] and not contacts and not diag['cancelledBeforeAttackCount']:
        return fail('no complete Aiden spear execution outcomes')
    result = _result(spec, contacts, 'static-spear-synchronous-primary-damage-target-loop', cast_ticks=cast_ticks)
    for outcome, tick in zip(result.get('outcomes', []), attempt_ticks):
        outcome[2] = tick
    result.update(diag, contactDetailsByAttempt=details, executionEvidenceByAttempt=evidence,
                  evasionDetailsByAttempt=evaded_details, evadedTargetCount=sum(len(v) for v in evaded_details),
                  evadedEnemyTargetCount=sum(sum(e['enemyPlayer'] for e in v) for v in evaded_details),
                  evidenceReview='deliverables/aiden-spear-execution-static-proof-v1.json',
                  effectCodeAloneUsed=False, actionPositionUsedAsTarget=False, damageAmountInferred=False,
                  repeatedHitTicksCountedAsDistinctTargets=False)
    included=[g for g in recipient_groups if g['castTick'] in cast_ticks]
    if included:
        from .skill_development_cancellation import annotate_provisional
        annotate_provisional(result,'Within one synchronous target iteration, a player damage packet followed by its verified Nina servant packet is provisionally one linked-recipient hit. Damage redirection is not proved by ownership alone.')
        result['provisionalLinkedRecipientGroups']=included
    return result
