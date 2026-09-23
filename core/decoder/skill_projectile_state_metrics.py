"""Named state outcomes attributed through complete emitted object lifetimes."""
from collections import Counter, defaultdict
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_action_stage_evidence import load_exact_skill_ids


# Explicit reviewed identities, not numeric Skill/State/FX joins.
CONFIG = {
    (20,1020300,'pull-hit'): ('LenoxActive2',1020320,'KnockUp','Airborne',
        102020,'Projectile_FX_BI_Lenox_Skill02','collision'),
    (14,1014400,'fetter'): ('ChiaraActive3',1014300,'ChiaraActive3FetterState','Fetter',
        101411,'Projectile_FX_BI_Chiara','destroy-delay-start'),
    (9,1009200,'attach'): ('IsolActive1',1009210,'IsolActive1Attach','Common',
        100902,'Projectile_FX_BI_Isol_Skill01','ground-summon'),
    (21,1021500,'attach'): ('RozziActive4',1021510,'RozziActive4AttachStackState','Common',
        102150,'Projectile_FX_BI_Rozzi_Skill04_Bomb','collision'),
    (28,1028300,'blind'): ('SuaActive2',1028340,'SuaActive2Blind','Blind',
        1028301,'Projectile_FX_BI_Sua_Skill02_Bird','collision'),
    (28,1028520,'blind'): ('SuaActive4_3',1028350,'SuaActive4_3Blind','Blind',
        1028302,'Projectile_FX_BI_Sua_Skill02_Bird_R','collision'),
}


def projectile_state_metric(spec, starts, finishes, spawns, collisions, terminals, states,
                            player, teams, intervals, catalog, skill_rows, state_rows,
                            state_groups, skill_ids=None, summons=None, summon_rows=None, gaps=None,development=False,
                            game_terminals=None,route_inputs=None):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    cfg = CONFIG.get((spec['characterCode'],spec['skillGroup'],spec['mode']))
    if not cfg or spec['unit'] != 'skill-cast':
        return _unavailable(spec,'이 스킬의 투사체와 전용 상태 연결은 검증되지 않음')
    name, state_group, state_name, state_type, code, prefab, route = cfg
    group = spec['skillGroup']
    definition = catalog['skillGroups'].get(str(group),{})
    state_def = [s for s in state_groups if s.get('group')==state_group]
    if (definition.get('skillId')!=name or definition.get('characterCode')!=spec['characterCode'] or
            catalog['projectileDefinitions'].get(str(code),{}).get('prefabName')!=prefab or
            len(state_def)!=1 or state_def[0].get('skillId')!=state_name or state_def[0].get('stateType')!=state_type):
        return _unavailable(spec,'같은 gameDb의 명시 스킬·투사체·전용 상태 정의 불일치')
    codes = {s['code'] for s in state_rows if s.get('group')==state_group}
    ids = load_exact_skill_ids() if skill_ids is None else skill_ids
    code_groups = {s['code']:s['group'] for s in skill_rows}
    selected = [s for s in starts if s['playerObjectId']==player and s['skillGroup']==group]
    if (not codes or not selected or any(s['skillIdCode']!=ids.get(name) or
            code_groups.get(s['skillCode'])!=group for s in selected)):
        return _unavailable(spec,'전용 상태 코드 또는 정확한 반복 사용 표본 부족')
    lives, reason = exact_cast_lifetimes(selected,finishes,player)
    if reason:return _unavailable(spec,reason)
    own = [s for s in spawns if s['ownerPlayerObjectId']==player and s['projectileCode']==code]
    ends = defaultdict(set)
    delay_starts = defaultdict(set)
    active_end_events = defaultdict(list)
    contacts = defaultdict(set)
    for t in terminals:
        if t['event']==('CmdDestroyDelayStart' if route=='ground-summon' or development and group==1021500 else 'CmdDestroy'):
            ends[t['objectId']].add(t['tick'])
        if t['event']=='CmdDestroyDelayStart':
            delay_starts[t['objectId']].add(t['tick'])
            active_end_events[t['objectId']].append(t)
    from .skill_projectile_active_end import projectile_active_end_records, collision_only_outcome
    if route=='destroy-delay-start' or (route=='collision' and
            collision_only_outcome(catalog['projectileDefinitions'].get(str(code),{}))):
        # The state is emitted at collision removal. Later visual retirement
        # is not required to close an object that can no longer apply it.
        ends=defaultdict(set,{oid:{r['endTick']} for oid,r in projectile_active_end_records(terminals).items()
                              if r.get('complete')})
    for c in collisions:contacts[c['projectileObjectId']].add((c['tick'],c['targetObjectId']))
    by_cast = defaultdict(list)
    objects = []
    unknown = defaultdict(set)
    invalid_end_casts = set()
    seen_objects = set()
    for spawn in own:
        owners = [i for i,(s,end) in enumerate(lives) if s['tick']<=spawn['tick']<=end]
        if len(owners)!=1:
            return _unavailable(spec,'실제 투사체 생성이 정확히 한 시전 수명에 연결되지 않음')
        oid = spawn['projectileObjectId']
        if oid in seen_objects:
            return _unavailable(spec,'duplicate projectile identity in named-state stream')
        seen_objects.add(oid)
        if len(ends[oid])!=1 or next(iter(ends[oid]))<spawn['tick']:
            unknown[owners[0]].add('projectile-final-end-incomplete-or-conflicting')
            if ends[oid]:invalid_end_casts.add(owners[0])
            # Retain every later event as a possible owner. A subsequent cast
            # or replay boundary must not fabricate this object's end.
            end = None
        else:
            end = next(iter(ends[oid]))
        if any(tick<spawn['tick'] or (end is not None and tick>end) for tick,_ in contacts[oid]):
            return _unavailable(spec,'실제 충돌이 투사체 수명 밖에 존재함')
        by_cast[owners[0]].append(oid)
        objects.append((owners[0],spawn,end))
    state_objects = objects
    summon_ends = {}
    ground_observation_ends = {}
    if route=='ground-summon':
        # The flight object creates a ground bomb. Its delayed visual destruction
        # is unrelated to which bomb applied the attachment state.
        if summons is None or summon_rows is None or gaps is None or any(
                g.get('count',0) and g.get('packetName') in
                {'CmdSpawn','CmdDestroyDelayStart','CmdAddState','CmdAddStateExtended','CmdStartSkill','CmdFinishSkill'} for g in gaps):
            return _unavailable(spec,'ground attachment requires complete summon, state and active-end streams')
        definitions=[s for s in summon_rows if s.get('code')==1011]
        if len(definitions)!=1 or definitions[0].get('prefabPath')!='Isol_Skill01_Timebomb' or definitions[0].get('objectType')!='SummonTrap':
            return _unavailable(spec,'native Isol ground bomb definition mismatch')
        ground=[s for s in summons if s.get('ownerObjectId')==player and s.get('summonCode')==1011]
        if len({s['objectId'] for s in ground})!=len(ground):
            return _unavailable(spec,'duplicate ground bomb identity')
        linked=defaultdict(list)
        from .skill_ordered_match_end import ordered_winner_match_end, precedes_recorded_match_end
        if game_terminals is None:
            game_terminals=((route_inputs or {}).get('wall_inputs') or {}).get('gameTerminals')
        game_end=ordered_winner_match_end(game_terminals,gaps) if development else None
        if any(g.get('count',0) and g.get('packetName') in
               {'CmdDestroy','SummonSnapshot','SummonSnapshot:10'} for g in gaps):game_end=None

        def observed_without_attachment(child):
            # This closes only observation of an unconsumed bomb. Never put
            # the match end into summon_ends or invent a removal/skill finish.
            if (game_end is None or child.get('identityVerifiedAgainstGameDb') is not True
                    or not precedes_recorded_match_end(child,game_end)):
                return False
            if any(t.get('objectId')==child['objectId'] for t in terminals):return False
            # A dedicated state with unknown caster, missing order, or after
            # the winner boundary cannot be silently discarded as unrelated.
            for state in states:
                if state.get('stateCode') not in codes and state.get('stateGroup')!=state_group:continue
                if state.get('casterObjectId') in teams and state.get('casterObjectId')!=player:continue
                if state.get('tick',-1)>=child['tick']:return False
            return True
        for child in ground:
            parents=[i for i,spawn,end in objects if end==child['tick'] or
                     (end is None and spawn['tick']<=child['tick'])]
            if len(parents)!=1 or not any(i==parents[0] and end==child['tick'] for i,_,end in objects):
                return _unavailable(spec,'ground bomb creation has no unique exact projectile active-end parent')
            linked[parents[0]].append(child)
        state_objects=[]
        for i,spawn,end in objects:
            children=linked[i]
            if len(children)!=1:
                unknown[i].add('ground-bomb-emission-incomplete-or-ambiguous')
                continue
            child=children[0];oid=child['objectId']
            terminal=active_end_events[oid]
            if len(terminal)!=1 or terminal[0]['tick']<child['tick']:
                unknown[i].add('ground-bomb-active-end-incomplete')
                child_end=None
                if observed_without_attachment(child):
                    ground_observation_ends[i]=dict(closureKind='actual-winner-match-end',
                        groundObjectId=oid,groundSpawnTick=child['tick'],endTick=game_end['tick'],
                        gameEndWireOrder=game_end['wireOrder'],syntheticRemovalCreated=False,
                        syntheticSkillFinishCreated=False,observedAttachmentCount=0,
                        scope='no attachment observed before the recorded winner match end')
            else:
                child_end=terminal[0]['tick'];summon_ends[oid]=terminal[0]
            state_objects.append((i,dict(tick=child['tick'],projectileObjectId=oid),child_end))
    targeted = spec['mode']=='blind'
    enemy = lambda target: target in teams and teams[target]!=teams[player]
    cancelled = set()
    finish_reason = {(f['skillIdCode'],f['tick']):f['reason'] for f in finishes if f['playerObjectId']==player}
    for i,(s,end) in enumerate(lives):
        if targeted and s.get('targetObjectId') in (None,0,-1):
            return _unavailable(spec,'수아 지정 대상이 없어 적 대상 사용과 자기 대상 사용을 구분하지 못함')
        if targeted and s.get('targetObjectId') in teams and teams[s['targetObjectId']]==teams[player] and s['targetObjectId']!=player:
            return _unavailable(spec,'다른 아군 지정 기록은 현재 수아 W 규칙과 불일치하므로 추정하지 않음')
        # Only enemy-player targeted uses belong to Sua's requested blindness rate.
        if targeted and not enemy(s.get('targetObjectId')):continue
        if len(by_cast[i])==1:continue
        if not by_cast[i] and finish_reason[(s['skillIdCode'],end)] in set(range(1,15))|{16,17}:
            cancelled.add(i)
        else:
            # No emission is not a miss. Unknown ownership can contaminate
            # later state events, so keep this strict until an emission exists.
            return _unavailable(spec,'측정 대상의 정상 사용에 한 발 연결이 없어 누락과 실패를 구분하지 못함')
    applied = [set() for _ in lives]
    for state in states:
        target = state['targetObjectId']
        if state['event']!='add' or state.get('stateCode') not in codes or not enemy(target):continue
        if state.get('stateGroup') not in (None,state_group):
            return _unavailable(spec,'전용 상태 코드와 명시 group 불일치')
        if state['casterObjectId'] not in teams:
            return _unavailable(spec,'전용 상태 적용의 실제 시전자 미확정')
        if state['casterObjectId']!=player:continue
        candidates = [(i,spawn) for i,spawn,end in state_objects
            if spawn['tick']<=state['tick'] and (end is None or state['tick']<=end) and ((route=='ground-summon' and (end is None or end==state['tick'])) or
                (route=='destroy-delay-start' and state['tick'] in delay_starts[spawn['projectileObjectId']]) or
                (route=='collision' and (state['tick'],target) in contacts[spawn['projectileObjectId']]))]
        if not candidates:
            return _unavailable(spec,'전용 상태가 실제 투사체 수명·충돌 하나에 연결되지 않음; 겹친 폭탄이나 최근 시전으로 추정하지 않음')
        if len(candidates)>1:
            for i,_ in candidates:unknown[i].add('state-application-has-multiple-projectile-parents')
            continue
        i,emitted = candidates[0]
        if route=='ground-summon':
            terminal=summon_ends.get(emitted['projectileObjectId'])
            if (terminal is None or state.get('wireCategory')!='commands' or terminal.get('wireCategory')!='commands'
                    or not state.get('wireOrder') or not terminal.get('wireOrder')
                    or tuple(state['wireOrder'])>=tuple(terminal['wireOrder'])):
                unknown[i].add('attachment-to-ground-destruction-order-unproven')
                continue
        if targeted and lives[i][0].get('targetObjectId')!=target:
            return _unavailable(spec,'실명 적용 대상과 그 투사체를 발사한 사용의 지정 대상 불일치')
        applied[i].add((state['tick'],target))
    for i in ground_observation_ends:
        if not applied[i]:unknown[i].discard('ground-bomb-active-end-incomplete')
    # Exact state-script identity and per-object completeness are checked
    # above. A miss does not require another use to have applied the state.
    chosen = [i for i,(s,_) in enumerate(lives) if (not targeted or enemy(s.get('targetObjectId')))
              and any(left<=s['tick']<right for left,right in intervals)]
    observed = chosen
    # Collision-linked state applications above already passed exact identity,
    # caster/target and unique object-parent checks. Missing retirement or a
    # different ambiguous application cannot undo that binary positive. Keep
    # conflicting end evidence and the ground-terminal order gate strict.
    recovered = {i for i in observed if route=='collision' and applied[i] and unknown[i]
                 and i not in invalid_end_casts}
    chosen = [i for i in observed if not unknown[i] or i in recovered]
    completeness = dict(perUseCompletenessTracked=True,
        observedCombatCastCount=len(observed),verifiedCombatCastCount=len(chosen),
        unresolvedCombatCastCount=len(observed)-len(chosen),
        unresolvedCastReasons=dict(Counter(reason for i in observed if i not in recovered for reason in unknown[i])),
        incompleteUsesCountedAsMisses=False,
        unresolvedCastTicks=[lives[i][0]['tick'] for i in observed if unknown[i] and i not in recovered])
    if observed and not chosen:
        return {**_unavailable(spec,'no complete unambiguous named-state uses'),**completeness}
    row = _result(spec,[applied[i] for i in chosen],
        'exact-named-state-and-exclusive-complete-projectile-'+route)
    if completeness['unresolvedCombatCastCount']:
        row.update(hitRateScope='verified-uses-only',wholeCombatHitRate=None,
                   fullRequestedMetricComplete=False)
    try:
        from .skill_attempt_timing import exact_outcome
    except ImportError:
        from skill_attempt_timing import exact_outcome
    emissions={i:spawn['tick'] for i,spawn,_ in objects}
    row['outcomes']=[exact_outcome(lives[i][0]['tick'],
                     lives[i][0]['tick'] if i in cancelled else emissions[i],applied[i]) for i in chosen]
    row.update(measuredOutcome='enemy-blind-application' if targeted else 'enemy-fetter-application' if spec['mode']=='fetter' else 'enemy-attachment',
        **completeness,
        attemptsRestrictedToEnemyPlayerTarget=targeted,
        cancelledBeforeAttackCount=len(cancelled.intersection(chosen)),
        actualLinkedProjectileCount=sum(len(by_cast[i]) for i in chosen),
        stateApplicationEventCount=sum(len(applied[i]) for i in chosen),
        fixedDurationWindowUsed=False,nearestCastUsed=False,explosionDamageMeasured=False,
        interpretation='적 실험체에게 지정 사용한 W/R-W 중 실제 실명 적용; 자기 대상 사용 제외' if targeted else
                       '교전 중 E 사용 대비 실제 적 속박; 같은 투사체의 소멸 지연 시작과 상태 적용 시각을 연결' if spec['mode']=='fetter' else
                       '교전 중 전체 사용 대비 실제 적 부착 상태 적용; 이후 폭발 피해 적중과 별도')
    if recovered:
        row.update(targetCountsAreLowerBounds=True,completeTargetCountsAvailable=False,
            multiTargetStatus='confirmed-contact-lower-bounds',fullRequestedMetricComplete=False,
            binaryPositiveWithIncompleteDetailsCount=len(recovered),
            incompleteContactEvidence=[dict(castTick=lives[i][0]['tick'],reasons=sorted(unknown[i]))
                                       for i in sorted(recovered)])
    if route=='ground-summon':
        row.update(evidenceReview='deliverables/native-isol-q-ground-attachment-v1.json',
                   attachmentLinkedThroughGroundSummon=True,visualProjectileEndUsed=False)
        closed=[dict(castTick=lives[i][0]['tick'],**ground_observation_ends[i])
                for i in chosen if i in ground_observation_ends]
        if closed:
            from .skill_development_cancellation import annotate_provisional
            row.update(groundAttachmentObservationClosures=closed,
                       recordedWinnerObservationClosedCastCount=len(closed))
            annotate_provisional(row,'No attachment was observed in the complete dedicated state stream before the recorded winner match end; ground removal and skill finish are not inferred.')
    if spec['mode']=='pull-hit':
        row.update(measuredOutcome='enemy-pull-state-application', firstSpinDamageCounted=False,
            damageUsedAsPullProof=False, evidenceReview='deliverables/lenox-w-pull-native-v1.json',
            interpretation='W 후속 투사체 충돌 대상에게 실제 끌기 상태가 적용된 사용만 집계; 회전 피해 제외')
    if development and group==1021500:
        from .skill_development_cancellation import annotate_provisional
        row['activeEndCommand']='CmdDestroyDelayStart'
        annotate_provisional(row,'Rozzi attachment ends at recorded projectile retirement, without waiting for delayed visual destruction. Only collision-linked own attachment states count as successes.')
    return row
