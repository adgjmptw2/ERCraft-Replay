"""Shared candidate FX families, accepted only by complete runtime evidence.

Names select candidates, never a Skill/State foreign key. Every observed
candidate hit must belong exclusively to one completed cast, and the selected
stage must have its own enemy hit sample. Delayed or shared effects stay unknown.
"""
import re
from collections import Counter
try:
    from .skill_wire_order import finish_lookup,event_within_cast,competing_manual_starts
    from .requested_skill_scope import exact_cast_lifetimes
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_partial_cast_lifetimes import normal_completion_partition,retain_cancelled_hits,annotate_cancelled_hits
except ImportError:
    from skill_wire_order import finish_lookup,event_within_cast,competing_manual_starts
    from requested_skill_scope import exact_cast_lifetimes
    from requested_skill_hit_rates import _result,_unavailable
    from skill_partial_cast_lifetimes import normal_completion_partition,retain_cancelled_hits,annotate_cancelled_hits


def candidate_effect_family(spec,catalog,effect_rows):
    # During a match this must be a lookup in the exact-version plan. The
    # pure builder is used only outside evaluation (compilation and audits).
    try:
        from .skill_execution_plan import planned_family_candidates
        from .skill_rule_candidates import build_effect_candidates
    except ImportError:
        from skill_execution_plan import planned_family_candidates
        from skill_rule_candidates import build_effect_candidates
    planned=planned_family_candidates(spec,'effectCodes')
    if planned is not None:return planned
    return build_effect_candidates(spec,catalog,effect_rows)


def _player_damage_events(damages,player,teams,projectile_owners):
    try:
        from .skill_execution_plan import damage_events_for_player
    except ImportError:
        from skill_execution_plan import damage_events_for_player
    return damage_events_for_player(damages,player,teams,projectile_owners)


NATIVE_DIRECT_EFFECTS = {
    1007400: dict(character=7, skill='HyunwooActive3', wire=112, effects={1007401},
        proof='deliverables/native-hyunwoo-e-primary-followup-contract-v1.json',
        dependentEffects={1007402}),
    1071200: dict(character=71, skill='KennethActive1', wire=1045, effects={1071202},
        proof='deliverables/native-kenneth-q-disjoint-e-contract-v1.json', partialOrderedUses=True,
        atomicGameplayClosureProof='deliverables/native-kenneth-q-atomic-closure-v1.json',
        foreignProducerExclusions={1071400:dict(skill='KennethActive3',wire=1048,effects={1071202},
            proof='deliverables/native-kenneth-q-disjoint-e-contract-v1.json')}),
    1039220: dict(character=39, skill='CamiloActive1_3', wire=543, effects={1039204},
        proof='deliverables/native-camilo-q3-foreign-producer-contract-v1.json', partialOrderedUses=True,
        foreignProducerExclusions={
            1039400:dict(skill='CamiloActive3',wire=549,effects={1039204},proof='deliverables/native-camilo-e-two-steps-contract-v1.json'),
            1039300:dict(skill='CamiloActive2',wire=548,effects={1039204},proof='deliverables/native-camilo-q3-foreign-producer-contract-v1.json'),
            1039500:dict(skill='CamiloActive4_1',wire=551,effects={1039204},proof='deliverables/native-camilo-q3-foreign-producer-contract-v1.json'),
            1039510:dict(skill='CamiloActive4_2',wire=552,effects={1039204},proof='deliverables/native-camilo-q3-foreign-producer-contract-v1.json'),
            1039520:dict(skill='CamiloActive4_3',wire=553,effects={1039204},proof='deliverables/native-camilo-q3-foreign-producer-contract-v1.json')}),
    1039400: dict(character=39, skill='CamiloActive3', wire=549, effects={1039402,1039403},
        proof='deliverables/native-camilo-e-two-steps-contract-v1.json', partialOrderedUses=True,
        foreignProducerExclusions={1039220:dict(skill='CamiloActive1_3',wire=543,effects={1039402,1039403},
            proof='deliverables/native-camilo-e-two-steps-contract-v1.json')}),
    1050300: dict(character=50, skill='ElenaActive2', wire=734, effects={1050301},
        proof='deliverables/native-elena-w-both-forms-contract-v1.json', partialOrderedUses=True,
        foreignProducerExclusions={1050400:dict(skill='ElenaActive3',wire=738,effects={1050301},
            proof='deliverables/native-elena-w-both-forms-contract-v1.json')}),
    1061310: dict(character=61, skill='IremCatActive2', wire=910, effects={1061305},
        proof='deliverables/native-irem-cat-w-execution-contract-v1.json'),
    1058210: dict(character=58, skill='HazeActive1_2', wire=861, effects={1058401},
        proof='deliverables/native-haze-q-execution-contract-v1.json',partialOrderedUses=True),
    1033400: dict(character=33, skill='NickyActive3_1', wire=478, effects={1033401},
        proof='deliverables/native-nicky-e-direct-effect-contract-v1.json'),
    1055300: dict(character=55, skill='EstelleActive2_1', wire=797, effects={1055301},
        proof='deliverables/native-process-direct-effects-v1.json', partialOrderedUses=True,
        foreignProducerExclusions={1055220:dict(skill='EstelleActive1',wire=794,effects={1055301},
            proof='deliverables/native-estelle-q-disjoint-producer-v1.json')}),
    1055310: dict(character=55, skill='EstelleActive2_2', wire=798, effects={1055302},
        proof='deliverables/native-process-direct-effects-v1.json', partialOrderedUses=True),
    1055410: dict(character=55, skill='EstelleActive3_2', wire=804, effects={1055303},
        proof='deliverables/native-process-direct-effects-v1.json', partialOrderedUses=True,
        foreignProducerExclusions={1055400:dict(skill='EstelleActive3_1',wire=801,effects={1055303},
            proof='deliverables/native-estelle-shield-disjoint-producer-v1.json')}),
    1065300: dict(character=65, skill='DebiMarleneActive2_1', wire=977, effects={1065301},
        proof='deliverables/native-process-direct-effects-v1.json'),
    1089400: dict(character=89, skill='CraverActive3_1', wire=1303, effects={1089401},
        proof='deliverables/native-process-direct-effects-v1.json'),
}


def native_direct_effect_metric(spec,starts,finishes,damages,player,teams,intervals,
                                effect_rows,catalog,skill_rows,gaps=None):
    """Consume reviewed synchronous producers without a per-match hit sample.

    A constructor setter alone is insufficient: the contract must also bind
    its parameter consumer and prove that damage runs within the cast process.
    Unknown competing producers and out-of-life damage still fail closed.
    """
    cfg=NATIVE_DIRECT_EFFECTS.get(spec['skillGroup'])
    fail=lambda reason:_unavailable(spec,reason)
    if not cfg or (spec['characterCode'],spec['mode'],spec['unit'])!=(cfg['character'],'any','skill-cast'):
        return fail('native direct effect contract not registered')
    definition=catalog['skillGroups'].get(str(spec['skillGroup']),{})
    code_groups={s['code']:s['group'] for s in skill_rows}
    own=[s for s in starts if s.get('playerObjectId')==player]
    selected=[s for s in own if s['skillGroup']==spec['skillGroup']]
    if (definition.get('skillId')!=cfg['skill'] or definition.get('characterCode')!=cfg['character']
            or not selected or any(s['skillIdCode']!=cfg['wire'] or code_groups.get(s['skillCode'])!=spec['skillGroup'] for s in selected)
            or not cfg['effects']<={r['code'] for r in effect_rows}):
        return fail('native direct effect wire or game data identity mismatch')
    if gaps is None or any(g.get('count',0) and g.get('packetName') in {'CmdStartSkill','CmdFinishSkill','CmdDamage'} for g in gaps):
        return fail('native direct effect requires complete cast and damage streams')
    excluded=[]
    for start in own:
        foreign=cfg.get('foreignProducerExclusions',{}).get(start['skillGroup'])
        if foreign is None or not cfg['effects']<=foreign['effects']:continue
        definition=catalog['skillGroups'].get(str(start['skillGroup']),{})
        if (definition.get('characterCode')==cfg['character'] and definition.get('skillId')==foreign['skill']
                and start.get('skillIdCode')==foreign['wire'] and code_groups.get(start.get('skillCode'))==start['skillGroup']):
            excluded.append(start)
    # A reviewed foreign producer cannot own this effect merely because its
    # defensive state remains active. Preserve all unreviewed competitors.
    excluded_ids={id(start) for start in excluded}
    own=[start for start in own if id(start) not in excluded_ids]
    row=shared_effect_lifetime_metric(spec,own,finishes,damages,player,teams,intervals,
        effect_rows,{},catalog,allow_partial=True,native_contract=cfg)
    if excluded:
        row['nativeForeignProducerExclusions']=dict(
            excludedStartCount=len(excluded),skillGroups=sorted({s['skillGroup'] for s in excluded}),
            effectCodes=sorted(cfg['effects']),proofs=sorted({cfg['foreignProducerExclusions'][s['skillGroup']]['proof'] for s in excluded}))
    if row['status'] in {'calculable-observed','no-combat-sample'}:
        if cfg.get('dependentEffects'):
            row.update(dependentEffectCodesNotUsedForPrimaryRate=sorted(cfg['dependentEffects']),
                       primaryContactDoesNotRequireDependentEffect=True)
        if cfg.get('atomicGameplayClosureProof'):
            row.pop('finishReasonRequired',None)
            row.update(atomicGameplayClosureProof=cfg['atomicGameplayClosureProof'],
                normalFinishRequiredForNegativeOutcome=False,gameplayFinishClosesAtomicEffects=True)
        row.update(method='native-synchronous-damage-parameter-and-exclusive-cast-lifetimes',
            exactEffectCodes=sorted(cfg['effects']),positiveSampleRequired=False,
            effectMappingByPrefabName=False,castProcessClosesDamage=True,evidenceReview=cfg['proof'])
    return row


def _retain_atomic_gameplay_closures(unknown, lifetimes, finishes, player, native_contract):
    if not native_contract or not native_contract.get('atomicGameplayClosureProof'):
        return []
    closed=[]
    for i,reason in list(unknown.items()):
        if reason!='cast-not-normally-complete':continue
        start,end=lifetimes[i]
        matching=[f for f in finishes if f.get('playerObjectId')==player
                  and f.get('skillIdCode')==start['skillIdCode'] and f['tick']==end]
        if len(matching)==1 and type(matching[0].get('reason')) is int and matching[0]['reason'] in set(range(1,15))|{16,17}:
            del unknown[i];closed.append(i)
    return closed


def _strict_shared_effect_lifetime_metric(spec,all_starts,finishes,damages,player,teams,intervals,effect_rows,projectile_owners,catalog,*,native_contract=None):
    if spec['mode']!='any' or spec['unit']!='skill-cast':
        return _unavailable(spec,'기본 시전 적중 외의 조건은 전용 경로가 필요함')
    groups,effects=({spec['skillGroup']},native_contract['effects']) if native_contract else candidate_effect_family(spec,catalog,effect_rows)
    if not effects:return _unavailable(spec,'같은 gameDb에 스킬 단계와 타격을 함께 명시한 후보 FX 계열이 없음')
    starts=[s for s in all_starts if s['skillGroup'] in groups]
    # Repetition cannot establish a producer foreign key. Each use must pass
    # the ownership/lifetime checks below, including its own positive evidence.
    if not any(s['skillGroup']==spec['skillGroup'] for s in starts):
        return _unavailable(spec,'해당 단계 시전이 관측되지 않음',
                            category='observation-incomplete',reason_code='no-observed-stage-cast')
    lifetimes,reason=exact_cast_lifetimes(starts,finishes,player)
    if reason:return _unavailable(spec,reason)
    # Finish packets identify the wire skill, not its base/reinforced group.
    # Pair the whole competing stream before examining individual skill groups.
    other_starts=competing_manual_starts(all_starts,catalog,groups,player)
    competing,other_reason=exact_cast_lifetimes(other_starts,finishes,player)
    if other_reason:return _unavailable(spec,'같은 플레이어의 다른 수동 스킬 수명이 불완전하여 FX 중복 귀속 배제 불가')
    contacts=[set() for _ in lifetimes]
    finish_orders=finish_lookup(finishes,player)
    packet_counts=[0 for _ in lifetimes]
    for d in _player_damage_events(damages,player,teams,projectile_owners):
        owner=d['attackerObjectId'] if d['attackerObjectId'] in teams else projectile_owners.get(d['attackerObjectId'])
        target=d['targetObjectId']
        if owner!=player or target not in teams or teams[target]==teams[player] or d.get('effectCode') not in effects:continue
        owners=[i for i,(s,end) in enumerate(lifetimes) if event_within_cast(s,end,d,finish_orders)]
        if len(owners)!=1 or any(event_within_cast(s,end,d,finish_orders) for s,end in competing):
            return _unavailable(spec,'후보 타격 FX가 한 단계의 실제 시전 수명에 배타적으로 연결되지 않음')
        contacts[owners[0]].add((d['tick'],target));packet_counts[owners[0]]+=1
    selected=[i for i,(s,end) in enumerate(lifetimes) if s['skillGroup']==spec['skillGroup']]
    normal,unknown=normal_completion_partition(lifetimes,finishes,player,selected)
    atomic_closed=_retain_atomic_gameplay_closures(unknown,lifetimes,finishes,player,native_contract)
    normal=sorted(set(normal)|set(atomic_closed))
    recovered=retain_cancelled_hits(unknown,contacts,lifetimes,finishes,player)
    normal=sorted(set(normal)|set(recovered))
    if native_contract is None and not any(packet_counts[i] for i in normal):
        return _unavailable(spec,'해당 단계 자체의 적 타격 FX 표본 없음; 다른 단계로 미적중을 확정하지 않음')
    combat=[i for i in selected if any(l<=lifetimes[i][0]['tick']<r for l,r in intervals)]
    chosen=[i for i in combat if i not in unknown]
    diagnostics=dict(perUseCompletenessTracked=True,finishReasonRequired=0,
        observedCombatCastCount=len(combat),verifiedCombatCastCount=len(chosen),
        unresolvedCombatCastCount=len(combat)-len(chosen),
        unresolvedCastReasons=dict(Counter(unknown[i] for i in combat if i in unknown)),
        incompleteUsesCountedAsMisses=False)
    if combat and not chosen:
        return {**_unavailable(spec,'정상 완료된 교전 시전 없음; 취소를 미적중으로 계산하지 않음'),**diagnostics}
    row=_result(spec,[contacts[i] for i in chosen],'candidate-FX-family-verified-by-complete-exclusive-runtime-lifetimes',
                cast_ticks=[lifetimes[i][0]['tick'] for i in chosen])
    row.update(**diagnostics,effectNamespace='EffectAndSound',numericSkillStateCodeJoinUsed=False,
        candidateEffectCodes=sorted(effects),exactDamagePacketCount=sum(packet_counts[i] for i in chosen),
        interpretation='기본 단계의 시전당 실제 적 타격; 부위·벽·유지 조건을 대신하지 않음')
    return annotate_cancelled_hits(row,recovered,lifetimes,chosen)


def shared_effect_lifetime_metric(spec,all_starts,finishes,damages,player,teams,intervals,effect_rows,projectile_owners,catalog,*,allow_partial=False,native_contract=None,development=False,development_contract=None,development_foreign_events=(),development_phase_ends=None,unobserved_stage_policy=None,unowned_effect_policy=None,gaps=None,game_terminals=None,all_projectile_spawns=None,terminals=None,collisions=None):
    """Evaluate ordered uses once, retaining whole-family ambiguity barriers.

    Unowned family hits reject the sample by default. An explicit development
    policy can keep independent positives while every possible earlier
    negative remains unknown. Ambiguous bounded hits taint every possible
    family use, never pick one. Reviewed phase callers retain strict defaults.
    """
    if not allow_partial:
        return _strict_shared_effect_lifetime_metric(spec,all_starts,finishes,damages,player,teams,intervals,effect_rows,projectile_owners,catalog,native_contract=native_contract)
    if (spec['mode']!='any' and not (development and development_contract)) or spec['unit']!='skill-cast':
        return _unavailable(spec,'기본 시전 적중 외의 조건은 전용 경로가 필요함')
    try:
        from .skill_partial_cast_lifetimes import ordered_cast_records
        from .skill_wire_order import command_order,player_finishes
    except ImportError:
        from skill_partial_cast_lifetimes import ordered_cast_records
        from skill_wire_order import command_order,player_finishes
    groups,effects=({spec['skillGroup']},development_contract['effects']) if development and development_contract else (({spec['skillGroup']},native_contract['effects']) if native_contract else candidate_effect_family(spec,catalog,effect_rows))
    starts=[s for s in all_starts if s['skillGroup'] in groups]
    if not effects:return _unavailable(spec,'같은 gameDb에 스킬 단계와 타격을 함께 명시한 후보 FX 계열이 없음')
    if not any(s['skillGroup']==spec['skillGroup'] for s in starts):
        return _unavailable(spec,'해당 단계 시전이 관측되지 않음',
                            category='observation-incomplete',reason_code='no-observed-stage-cast')
    other_starts=competing_manual_starts(all_starts,catalog,groups,player)
    identities={s['skillIdCode'] for s in [*starts,*other_starts]}
    relevant_ends=[f for f in player_finishes(finishes,player) if f['skillIdCode'] in identities]
    # Choose the existing clock representation before evaluation. Tick-only
    # inputs cannot enter the ordered partial-use evaluator; no failure retry.
    if any(command_order(e) is None for e in [*starts,*other_starts,*relevant_ends]):
        if development:
            return _unavailable(spec,'development estimate requires actual ordered cast/finish records')
        return _strict_shared_effect_lifetime_metric(spec,all_starts,finishes,damages,player,teams,intervals,effect_rows,projectile_owners,catalog,native_contract=native_contract)
    records,reason=ordered_cast_records(starts,relevant_ends,player)
    if reason:return _unavailable(spec,reason)
    competing,reason=ordered_cast_records(other_starts,relevant_ends,player)
    if reason:return _unavailable(spec,'같은 플레이어의 다른 수동 스킬 수명이 불완전하여 FX 중복 귀속 배제 불가')
    strict_failure=None
    if any(not r['complete'] for r in records):
        strict_failure=('재생 종료 표식은 게임 내 시전 결과를 확정하는 종료가 아님'
                        if any(r['reason']=='replay-end-is-not-gameplay-finish' for r in records)
                        else '실제 명령 순서에서 종료 없는 시전')
    elif any(not r['complete'] for r in competing):
        strict_failure='같은 플레이어의 다른 수동 스킬 수명이 불완전하여 FX 중복 귀속 배제 불가'
    ambiguous_reason='후보 타격 FX가 한 단계의 실제 시전 수명에 배타적으로 연결되지 않음'
    finish_orders=finish_lookup(relevant_ends,player)
    def possible(record,event):
        start=record['start']
        phase_end=(development_phase_ends or {}).get(command_order(start)) if development else None
        if phase_end is not None and record['complete']:
            return command_order(event) is not None and command_order(start)<=command_order(event) and start['tick']<=event['tick']<=max(record['finish']['tick'],phase_end)
        if record['complete']:
            return event_within_cast(start,record['finish']['tick'],event,finish_orders)
        # ReplayEnd is not a gameplay end, and an open use has no fabricated
        # end at a later cast. It may own every subsequent family event.
        if event['tick']!=start['tick']:return event['tick']>start['tick']
        at=command_order(event)
        return at is None or at>=command_order(start)
    unknown={i:r['reason'] for i,r in enumerate(records) if not r['complete']}
    completed=[i for i,r in enumerate(records) if r['complete']]
    _,not_normal=normal_completion_partition(
        [(r['start'],r['finish']['tick'] if r['finish'] else None) for r in records],
        relevant_ends,player,completed)
    unknown.update(not_normal)
    phase_closed=[]
    if development:
        for i in list(unknown):
            if unknown[i]=='cast-not-normally-complete' and command_order(records[i]['start']) in (development_phase_ends or {}):
                unknown.pop(i);phase_closed.append(records[i]['start']['tick'])
    for i,r in enumerate(records):
        if r['complete'] and any(not c['complete'] and possible(c,r['finish']) for c in competing):
            unknown[i]='overlap-with-unfinished-competing-manual-cast'
    contacts=[set() for _ in records];counts=[0 for _ in records]
    delayed_possible=set();unowned_events=[];open_native_hits=set()
    open_candidate_hits=set();competing_candidate_hits=set()
    candidate_policy=unowned_effect_policy if native_contract is None else None
    for d in _player_damage_events(damages,player,teams,projectile_owners):
        owner=d['attackerObjectId'] if d['attackerObjectId'] in teams else projectile_owners.get(d['attackerObjectId'])
        target=d['targetObjectId']
        if owner!=player or target not in teams or teams[target]==teams[player] or d.get('effectCode') not in effects:continue
        owners=[i for i,r in enumerate(records) if possible(r,d)]
        if not owners or any(not records[i]['complete'] for i in owners):
            # Reviewed synchronous producers cannot defer damage beyond their
            # closed process. A unique still-open process may therefore prove
            # a positive contact now, even if recording ends before its finish.
            # Never apply this to candidate FX, orphan effects or competitors.
            candidate_open=bool(candidate_policy and candidate_policy.get('attributeUniqueOpenCandidateEffect'))
            if ((native_contract is not None or candidate_open) and len(owners)==1
                    and records[owners[0]]['reason'] in {'open-final-cast','replay-end-is-not-gameplay-finish'}
                    and command_order(d) is not None
                    and not any(possible(c,d) for c in competing)):
                i=owners[0];contacts[i].add((d['tick'],target));counts[i]+=1
                (open_native_hits if native_contract is not None else open_candidate_hits).add(i)
                continue
            if not development and unowned_effect_policy is None:
                return _unavailable(spec,strict_failure or ambiguous_reason)
            # An unowned delayed event can change any earlier no-contact use,
            # but cannot undo an independently observed hit or affect a later
            # cast. Keep that uncertainty local instead of rejecting the match.
            at=command_order(d)
            candidates=[i for i,r in enumerate(records) if r['start']['tick']<d['tick'] or
                (r['start']['tick']==d['tick'] and (at is None or command_order(r['start'])<=at))]
            delayed_possible.update(candidates)
            unowned_events.append(dict(tick=d['tick'],targetObjectId=target,effectCode=d['effectCode'],
                possibleCastTicks=[records[i]['start']['tick'] for i in candidates]))
            strict_failure=strict_failure or ambiguous_reason
            continue
        # An unfinished family use cannot prove that a later hit is its own:
        # it may instead hide a delayed hit from an earlier completed use.
        # Retain the whole-family rejection rather than absorb that orphan.
        has_competitor=any(possible(c,d) for c in competing)
        candidate_overlap=bool(candidate_policy and candidate_policy.get('attributeUniqueCandidateEffectDespiteManualOverlap')
            and len(owners)==1 and records[owners[0]]['complete'] and command_order(d) is not None)
        if len(owners)!=1 or (not development and has_competitor and not candidate_overlap):
            strict_failure=strict_failure or ambiguous_reason
            for i in owners:unknown[i]='ambiguous-family-hit-all-possible-uses-excluded'
            continue
        i=owners[0]
        contacts[i].add((d['tick'],target));counts[i]+=1
        if has_competitor and candidate_overlap:competing_candidate_hits.add(i)
    for i in delayed_possible:
        if not contacts[i] and i not in unknown:
            unknown[i]='unowned-delayed-effect-may-belong-to-this-use'
    for event in development_foreign_events:
        for i,r in enumerate(records):
            if not contacts[i] and possible(r,event):
                unknown[i]='shared-effect-overlaps-recorded-foreign-piece-producer'
    independent_positive_uses={}
    if unowned_effect_policy is not None:
        for i,why in list(unknown.items()):
            if contacts[i] and why in {'ambiguous-family-hit-all-possible-uses-excluded',
                                      'overlap-with-unfinished-competing-manual-cast'}:
                # Additional unassigned contacts cannot reverse a separately
                # attributed hit. Binary success is known; target totals aren't.
                independent_positive_uses[i]=why
                del unknown[i]
    lifetimes=[(r['start'],r['finish']['tick'] if r['finish'] else None) for r in records]
    for i in open_native_hits|open_candidate_hits:
        if unknown.get(i) in {'open-final-cast','replay-end-is-not-gameplay-finish'}:
            del unknown[i]
    _retain_atomic_gameplay_closures(unknown,lifetimes,relevant_ends,player,native_contract)
    recovered=retain_cancelled_hits(unknown,contacts,lifetimes,relevant_ends,player)
    winner_misses=[];winner_blocked=[]
    if (spec.get('characterCode'),spec['skillGroup'],spec['mode'])==(82,1082400,'any'):
        import json
        from pathlib import Path
        from .skill_ordered_match_end import ordered_winner_match_end
        enabled=json.loads((Path(__file__).resolve().parents[1]/'data/user-hit-rate-scope-20260912.json').read_text(encoding='utf-8'))['recordedUseMissPolicy'].get('enabled') is True
        winner=ordered_winner_match_end(game_terminals,gaps) if enabled else None
        for i,r in enumerate(records):
            if not enabled or unknown.get(i)!='open-final-cast' or contacts[i]:continue
            left=command_order(r['start']);right=command_order(winner) if winner else None;reasons=[]
            if gaps is None or any(g.get('count',0) for g in gaps):reasons.append('stream-gap-or-missing')
            if all_projectile_spawns is None or terminals is None or collisions is None or projectile_owners is None:reasons.append('original-object-streams-missing')
            if left is None or right is None or left>=right or r['start']['tick']>winner['tick']:reasons.append('ordered-actual-winner-unavailable')
            if i in delayed_possible:reasons.append('unowned-delayed-effect')
            def after(e):
                if e.get('tick') is not None and e['tick']<r['start']['tick']:return False
                return command_order(e) is None or left is None or e.get('tick',r['start']['tick'])>r['start']['tick'] or command_order(e)>=left
            raw=[s for s in all_projectile_spawns or [] if s.get('projectileCode')==108241 and after(s)]
            def owner(s):
                oid=s.get('ownerObjectId')
                return oid if oid in teams else (projectile_owners or {}).get(oid)
            if any(owner(s) is None for s in raw):reasons.append('phase-projectile-owner-unresolved')
            shots=[s for s in raw if owner(s)==player];arrivals=[]
            if len(shots)!=1:reasons.append('phase-projectile-not-unique')
            else:
                shot=shots[0];at=command_order(shot)
                parents=[j for j,z in enumerate(records) if command_order(z['start']) is not None and at is not None and command_order(z['start'])<=at and z['start']['tick']<=shot['tick'] and (z.get('finish') is None or (at<=command_order(z['finish']) and shot['tick']<=z['finish']['tick']))]
                ts=[t for t in terminals or [] if t.get('objectId')==shot.get('projectileObjectId')]
                if any(c.get('projectileObjectId')==shot.get('projectileObjectId') for c in collisions or []):reasons.append('phase-projectile-collision-needs-classification')
                arrivals=[t for t in ts if t.get('event')=='CmdProjectileArrived']
                if parents!=[i] or at is None or right is None or not left<=at<right or shot['tick']>winner['tick']:reasons.append('spawn-parent-order-not-exact')
                if len(arrivals)!=1 or len(ts)!=1:reasons.append('arrival-missing-or-conflicting-terminal')
                else:
                    arrival=arrivals[0];ao=command_order(arrival)
                    if arrival.get('isCollision') is not False or ao is None or at is None or right is None or not at<ao<right or not shot['tick']<=arrival['tick']<=winner['tick']:reasons.append('ordered-noncollision-arrival-not-proven')
            if any(after(d) for d in damages if d.get('effectCode') in effects and (d.get('attackerObjectId') in {None,player} or (d.get('attackerObjectId') not in teams and (projectile_owners or {}).get(d.get('attackerObjectId')) in {None,player}))):reasons.append('later-phase-damage')
            if any(not z['complete'] and j!=i for j,z in enumerate(records)):reasons.append('another-open-family-use')
            if any(not z['complete'] for z in competing):reasons.append('unfinished-competing-manual-use')
            proof=dict(startTick=r['start']['tick'],startOrder=left,winnerEnd=winner,projectiles=shots,arrivals=arrivals,skillFinishInvented=False,projectileRemovalInvented=False)
            if reasons:winner_blocked.append(dict(proof,reasons=reasons));continue
            unknown.pop(i);winner_misses.append(proof)
    from .skill_partial_cast_lifetimes import user_cancelled_no_recorded_contact_indices
    user_misses=user_cancelled_no_recorded_contact_indices(records,contacts,unknown,
        cancellation_reason='cast-not-normally-complete',pending_continuations=delayed_possible,gaps=gaps)
    for i in user_misses:unknown.pop(i)
    selected=[i for i,r in enumerate(records) if r['start']['skillGroup']==spec['skillGroup']]
    stage_positive=any(counts[i] for i in selected if i not in unknown)
    if not development and native_contract is None and not stage_positive and unobserved_stage_policy is None:
        return _unavailable(spec,strict_failure or '해당 단계 자체의 적 타격 FX 표본 없음; 다른 단계로 미적중을 확정하지 않음')
    combat=[i for i in selected if any(l<=records[i]['start']['tick']<r for l,r in intervals)]
    valid=[i for i in combat if i not in unknown]
    def annotate_open_hits(row):
        if spec['skillGroup']==1082400:
            included=[e for e in winner_misses if any(l<=e['startTick']<h for l,h in intervals)]
            row.update(userPolicyWinnerMissEvidence=included,userPolicyWinnerBlockedEvidence=winner_blocked)
            if included:row.update(userPolicyWinnerMissCastCount=len(included),verifiedCompletionCredit=False,fullRequestedMetricComplete=False,
                verifiedCombatCastCount=max(0,(row.get('verifiedCombatCastCount') or 0)-len(included)),userPolicyMissAuthority='explicit-user-rule')
        def cancelled_evidence(i):
            r=records[i];f=r['finish']
            return dict(startTick=r['start']['tick'],startOrder=list(command_order(r['start'])),
                finishTick=f['tick'],finishOrder=list(command_order(f)),finishReason=f['reason'])
        masked=sorted(i for i in combat if i in delayed_possible and unknown.get(i)=='cast-not-normally-complete')
        if masked:
            row['userPolicyCancellationPendingEvidence']=[dict(cancelled_evidence(i),
                pendingReason='unowned-delayed-effect-may-belong-to-this-use') for i in masked]
        included=sorted(set(user_misses).intersection(valid))
        if included:
            row.update(userPolicyCancelledUseEvidence=[cancelled_evidence(i) for i in included],
                userPolicyMissCastCount=len(included),
                userPolicyMissCastTicks=[records[i]['start']['tick'] for i in included],
                userPolicyMissAuthority='explicit-user-rule',
                userPolicyMissMeaning='Actual cancelled use with no recorded contact; user denominator rule, not native completion or geometric absence proof.',
                verifiedCombatCastCount=sum(i not in user_misses for i in valid),
                verifiedCompletionCredit=False,fullRequestedMetricComplete=False,
                normalFinishRequiredForNegativeOutcome=False)
        provisional=sorted((open_candidate_hits|competing_candidate_hits).intersection(valid))
        if provisional:
            from .skill_development_effect_metrics import annotate_provisional_rate
            row=annotate_provisional_rate(row,candidate_policy)
            row.update(provisionalUniqueCandidateCastTicks=[records[i]['start']['tick'] for i in provisional],
                provisionalOpenPositiveCastTicks=[records[i]['start']['tick'] for i in sorted(open_candidate_hits.intersection(valid))],
                provisionalManualOverlapCastTicks=[records[i]['start']['tick'] for i in sorted(competing_candidate_hits.intersection(valid))],
                candidateEffectAttributionAssumption='The candidate effect is produced by its unique active same-family cast, including an unfinished cast with a positive contact; overlapping other manual skills do not invalidate that candidate attribution.',
                candidateEffectAttributionVerified=False,finishInvented=False,
                normalFinishRequiredForNegativeOutcome=True,
                targetCountsAreLowerBounds=True,completeTargetCountsAvailable=False)
        if any(i in valid for i in independent_positive_uses):
            from .skill_development_effect_metrics import annotate_provisional_rate
            row=annotate_provisional_rate(row,unowned_effect_policy)
            row.update(independentPositiveCastCount=sum(i in valid for i in independent_positive_uses),
                targetCountsAreLowerBounds=True,completeTargetCountsAvailable=False,
                ambiguousExtraContactsAssigned=False)
        if unowned_events and unowned_effect_policy is not None:
            from .skill_development_effect_metrics import annotate_provisional_rate
            row=annotate_provisional_rate(row,unowned_effect_policy)
            row.update(unownedEffectEventsPreserved=len(unowned_events),
                unownedEffectPolicy='earlier-negative-uses-unknown-independent-positives-retained',
                orphanEffectsAssignedToCasts=False)
        if unobserved_stage_policy is not None and not stage_positive and native_contract is None:
            from .skill_development_effect_metrics import annotate_provisional_rate
            row=annotate_provisional_rate(row,unobserved_stage_policy)
            row.update(reusedReviewedCandidateRule=True,positiveSampleAbsent=True,
                negativeOutcomeBasis='normal unique cast lifetime and complete streams; candidate producer mapping remains provisional')
        if phase_closed:
            row.pop('finishReasonRequired',None)
            row.update(provisionalEmittedPhaseClosedCastTicks=phase_closed,phaseActivityEndUsed=True,
                phaseOutcomeClosure='recorded-explosion-and-activity-retirement',
                phaseMappingStatus='experimental-explicit-object-profile')
        included=sorted(open_native_hits.intersection(valid))
        if included:
            row.pop('finishReasonRequired',None)
            row.update(nativeOpenHitCastCount=len(included),
                nativeOpenHitCastTicks=[records[i]['start']['tick'] for i in included],
                normalFinishRequiredForNegativeOutcome=True,
                targetCountsAreLowerBounds=True,completeTargetCountsAvailable=False,
                fullRequestedMetricComplete=False,
                positiveContactRequiresFinish=False,
                openUseAttribution='unique active reviewed synchronous producer; no competing producer')
        return row
    if combat and not valid:
        return annotate_open_hits({**_unavailable(spec,strict_failure or '정상 완료된 교전 시전 없음; 취소를 미적중으로 계산하지 않음'),
                'unresolvedCombatCastCount':len(combat),
                'unresolvedCastReasons':dict(Counter(unknown[i] for i in combat if i in unknown))})
    if strict_failure is None:
        # Retain the established output order (wire identity, then command
        # order), including streams with multiple skill identities in a family.
        identity_order={key:i for i,key in enumerate(dict.fromkeys(s['skillIdCode'] for s in starts))}
        valid.sort(key=lambda i:(identity_order[records[i]['start']['skillIdCode']],command_order(records[i]['start'])))
        row=_result(spec,[contacts[i] for i in valid],'candidate-FX-family-verified-by-complete-exclusive-runtime-lifetimes',
                    cast_ticks=[records[i]['start']['tick'] for i in valid])
        row.update(perUseCompletenessTracked=True,finishReasonRequired=0,
            observedCombatCastCount=len(combat),verifiedCombatCastCount=len(valid),
            unresolvedCombatCastCount=len(combat)-len(valid),
            unresolvedCastReasons=dict(Counter(unknown[i] for i in combat if i in unknown)),
            incompleteUsesCountedAsMisses=False,effectNamespace='EffectAndSound',numericSkillStateCodeJoinUsed=False,
            candidateEffectCodes=sorted(effects),exactDamagePacketCount=sum(counts[i] for i in valid),
            interpretation='기본 단계의 시전당 실제 적 타격; 부위·벽·유지 조건을 대신하지 않음')
        return annotate_open_hits(annotate_cancelled_hits(row,recovered,lifetimes,valid))
    row=_result(spec,[contacts[i] for i in valid],'candidate-FX-family-exclusive-ordered-uses-with-explicit-unknowns',
                cast_ticks=[records[i]['start']['tick'] for i in valid])
    row.update(effectNamespace='EffectAndSound',numericSkillStateCodeJoinUsed=False,
        candidateEffectCodes=sorted(effects),exactDamagePacketCount=sum(counts[i] for i in valid),
        perUseCompletenessTracked=True,finishReasonRequired=0,observedCombatCastCount=len(combat),
        verifiedCombatCastCount=len(valid),unresolvedCombatCastCount=len(combat)-len(valid),
        unresolvedAllCastCount=sum(i in unknown for i in selected),incompleteUsesCountedAsMisses=False,
        unresolvedCastReasons=dict(Counter(unknown[i] for i in combat if i in unknown)),
        unownedFamilyHitsDiscarded=False,strictWholeSampleReason=strict_failure,
        interpretation='검증된 사용만의 관측 적중률. 미확정 사용은 분모와 실패에서 제외하며 별도 집계; 부위·벽·단계 조건의 완료를 뜻하지 않음')
    if unowned_events:
        row.update(unownedFamilyEffectEvents=unowned_events,
            unownedFamilyEffectCount=len(unowned_events),
            unknownEffectsAssignedToCast=False,
            targetCountsAreLowerBounds=True,completeTargetCountsAvailable=False,
            retainedPositiveUsesDespiteUnownedEffects=sum(bool(contacts[i]) for i in valid if i in delayed_possible))
    return annotate_open_hits(annotate_cancelled_hits(row,recovered,lifetimes,valid))
