"""Separate recorded hit-action stages from actual target CC applications.

State codes below are reviewed identities, not numeric Skill/State joins. A CC
must independently match an exact skill action, its target, and its damage tick.
Nathapon's created camera is named by action 1; its recorded retirement bounds
the follow-up actions, while final destruction is checked separately.
"""
from collections import Counter, defaultdict
from .skill_partial_cast_lifetimes import partial_cast_windows, partial_window_contains
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS

CONFIG = {
    1034300: dict(character=34, skill='NathaponActive2', actions={1,2,3},
                  hits={2,3}, ccAction=3, stateGroup=1034310, stateType='Fetter',
                  camera=(1150, 'SummonArtifact', 'Nathapon_Skill02_Camera')),
    1090500: dict(character=90, skill='LuciaActive4', actions={6,7,8,9},
                  hits={8,9}, ccAction=9, stateGroup=1090500, stateType='Stun'),
}
GAMEPLAY_CANCELS = set(range(1,15)) | {16,17}


def guard_action_outcome_metric(spec, starts, finishes, actions, player,
                               intervals, catalog, skill_rows, gaps,development=False):
    """Nicky's native guard outcome is independent of counterattack contact."""
    try:
        from .requested_skill_scope import exact_cast_lifetimes
        from .skill_wire_order import finish_lookup, event_within_cast, command_order
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
        from skill_wire_order import finish_lookup, event_within_cast, command_order
    fail=lambda reason:_unavailable(spec,reason)
    if (spec['characterCode'],spec['skillGroup'],spec['mode'],spec['unit'])!=(33,1033300,'guard-success','skill-cast'):
        return fail('native guard outcome contract not registered')
    definition=catalog['skillGroups'].get('1033300',{})
    if definition.get('skillId')!='NickyActive2_1' or definition.get('characterCode')!=33:
        return fail('native guard game data identity mismatch')
    if actions is None or gaps is None or any(g.get('count',0) and g.get('packetName') in {
            'CmdStartSkill','CmdFinishSkill','CmdPlaySkillAction','CmdPlaySkillActionWithTargets'} for g in gaps):
        return fail('native guard requires complete cast and action command streams')
    wire=load_exact_skill_ids()['NickyActive2_1']
    codes={r['code'] for r in skill_rows if r.get('group')==1033300}
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1033300]
    if not selected or any(s['skillIdCode']!=wire or s['skillCode'] not in codes for s in selected):
        return fail('native guard cast wire identity missing or mismatched')
    lives,unfinished,reason=partial_cast_windows(selected,finishes,player)
    if reason:return fail(reason)
    lookup=finish_lookup(finishes,player);markers=[{} for _ in lives]
    ordered_open_success=set();marker_events=[[] for _ in lives]
    for a in actions:
        if (a.get('sourceObjectId')!=player or a.get('skillIdCode')!=wire
                or a.get('actionNo') not in {31,32}):continue
        if a.get('wireStatus') not in ORDINARY_ACTIONS:
            return fail('guard outcome action is not an exact decoded command')
        owners=[i for i,(s,end) in enumerate(lives) if partial_window_contains(s,end,a,lookup)]
        if len(owners)!=1:return fail('guard outcome has no unique cast identity')
        row=markers[owners[0]];action=a['actionNo']
        marker_events[owners[0]].append(a)
        row[action]=min(row.get(action,a['tick']),a['tick'])
        i=owners[0];start=lives[i][0]
        if action==32 and unfinished.get(i)=='open-final-cast':
            left,at=command_order(start),command_order(a)
            # Native source + wire skill + unique parent prove this binary
            # outcome. Never turn an unordered/overlapping open window into
            # attribution, or use a later skill's marker for this parent.
            competing=[s for s in starts if s is not start
                and s.get('playerObjectId')==player and s.get('skillIdCode')==wire
                and (command_order(s) is None or (left is not None and at is not None
                    and left<=command_order(s)<=at))]
            if left is not None and at is not None and left<at and start['tick']<=a['tick'] and not competing:
                ordered_open_success.add(i)
    observed=[i for i,(s,_) in enumerate(lives) if any(l<=s['tick']<r for l,r in intervals)]
    valid=[i for i in observed if len(markers[i])==1 and i not in unfinished]
    # Repeated success markers have no reviewed multi-marker contract here.
    # Keep them unknown instead of choosing min(tick) across possibly
    # duplicated, reordered or conflicting commands.
    recovered=[i for i in observed if i in ordered_open_success
               and set(markers[i])=={32} and len(marker_events[i])==1]
    valid=sorted(set(valid)|set(recovered))
    estimated=[];excluded=[]
    if development:
        for i in observed:
            if i in valid:continue
            if i in unfinished:continue
            end=lookup.get((wire,lives[i][1]),[])
            if len(end)==1 and end[0].get('reason')==0:
                valid.append(i);estimated.append(i)
            elif len(end)==1 and end[0].get('reason') in GAMEPLAY_CANCELS and not markers[i]:
                excluded.append(i)
        valid.sort()
    diagnostics=dict(perUseCompletenessTracked=True,observedCombatCastCount=len(observed),
        verifiedCombatCastCount=len(valid),unresolvedCombatCastCount=len(observed)-len(valid)-len(excluded),
        unresolvedCastTicks=[lives[i][0]['tick'] for i in observed if i not in valid and i not in excluded],
        unresolvedCastReasons=dict(Counter(unfinished.get(i,'guard-outcome-missing-or-contradictory') for i in observed if i not in valid and i not in excluded)),
        incompleteUsesCountedAsMisses=False)
    if observed and not valid:return {**fail('guard outcome action missing or contradictory'),**diagnostics}
    n=len(valid);success=sum(32 in markers[i] for i in valid)
    row=dict(spec,status='calculable-observed' if n else 'no-combat-sample',
        method='native-Nicky-guard-success32-failure31-actions',attemptCount=n,
        hitCount=success,hitRate=round(success/n,6) if n else None,
        guardSuccessCount=success,guardFailureCount=n-success,measuredOutcome='guard-success',
        counterattackHitRequired=False,enemyDamageRequired=False,
        outcomes=[[lives[i][0]['tick'],int(32 in markers[i]),lives[i][0]['tick'],
                   markers[i].get(32)] for i in valid],
        multiTargetAttemptCount=None,multiTargetAttemptRate=None,
        distinctEnemyTargetsSummedAcrossAttempts=None,meanDistinctEnemyTargetsPerAttempt=None,
        deduplicatedEnemyContactEventCount=None,reason=None,fallbackUsed=False,
        evidenceReview='deliverables/native-nicky-guard-outcome-v1.json',**diagnostics)
    if diagnostics['unresolvedCombatCastCount']:
        row.update(hitRateScope='verified-uses-only',wholeCombatHitRate=None,fullRequestedMetricComplete=False)
    if recovered:
        row.update(openGuardSuccessCastCount=len(recovered),
            openGuardSuccessCastTicks=[lives[i][0]['tick'] for i in recovered],
            guardOutcomeDoesNotRequireCastFinish=True,finishInvented=False,
            openNegativeOutcomesRemainUnknown=True)
    if estimated or excluded:
        from .skill_development_cancellation import annotate_provisional
        row.update(provisionallyExcludedCancelledCastCount=len(excluded),provisionallyExcludedCancelledCastTicks=[lives[i][0]['tick'] for i in excluded])
        row['estimatedGuardCastTicks']=[lives[i][0]['tick'] for i in estimated]
        annotate_provisional(row,'On a normally finished guard, success action 32 wins over conflicting markers; missing success marker is provisionally failure. Canceled uses with no guard outcome marker are provisionally excluded. Counterattack damage is never required.')
    return row


def action_cc_outcome_metric(spec, starts, finishes, actions, states, damages,
                             summons, terminals, player, teams, intervals,
                             catalog, skill_rows, state_rows, state_groups,
                             summon_rows, skill_ids=None,game_terminals=None,gaps=None,raw_objects=None):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    group=spec['skillGroup']; cfg=CONFIG.get(group); mode=spec['mode']
    if group==1034300 and any(v is None for v in (starts,finishes,actions,states,damages,summons,terminals)):
        return _unavailable(spec,'Nathapon original action/contact streams missing')
    if group==1090500 and any(v is None for v in (starts,finishes,actions,states,damages)):
        return _unavailable(spec,'Lucia action/contact streams missing')
    if not cfg or spec['unit']!='skill-cast' or mode not in {'any','fetter','stun','marked-hit','unmarked-hit'}:
        return _unavailable(spec,'검증된 행동·대상·CC 분리 규칙 없음')
    definition=catalog['skillGroups'].get(str(group),{})
    if definition.get('skillId')!=cfg['skill'] or definition.get('characterCode')!=cfg['character']:
        return _unavailable(spec,'정확한 스킬 단계 정의 불일치')
    sg=[r for r in state_groups if r['group']==cfg['stateGroup']]
    if len(sg)!=1 or sg[0].get('stateType')!=cfg['stateType'] or sg[0].get('effectType')!='Debuff':
        return _unavailable(spec,'정확한 실제 CC 상태 정의 불일치')
    codes={r['code'] for r in state_rows if r['group']==cfg['stateGroup']}
    if not codes:return _unavailable(spec,'실제 CC 상태 코드 정의 없음')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    wire=ids.get(cfg['skill']); skill_codes={r['code']:r['group'] for r in skill_rows}
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==group]
    if not selected or any(s['skillIdCode']!=wire or skill_codes.get(s['skillCode'])!=group for s in selected):
        return _unavailable(spec,'시전 누락 또는 wire 스킬 정체성 불일치')
    casts,unfinished,reason=partial_cast_windows(selected,finishes,player)
    if reason:return _unavailable(spec,reason)
    own=[a for a in actions if a['sourceObjectId']==player and a['skillIdCode']==wire
         and a.get('wireStatus') in ORDINARY_ACTIONS]
    if not own or any(a.get('actionNo') not in cfg['actions'] for a in own):
        return _unavailable(spec,'타격 단계 행동 번호 누락 또는 미검토 행동 관측')
    windows={i:(s['tick'],end) for i,(s,end) in enumerate(casts)}
    from .skill_wire_order import finish_lookup
    lookup=finish_lookup(finishes,player)
    cancelled=set(); root_count=0; unknown=dict(unfinished)
    missing_camera_ends={}; bounded_camera_contacts=set()
    if 'camera' in cfg:
        code,kind,prefab=cfg['camera']; defs=[r for r in summon_rows if r['code']==code]
        if len(defs)!=1 or (defs[0].get('objectType'),defs[0].get('prefabPath'))!=(kind,prefab):
            return _unavailable(spec,'명시 생성 카메라 gameDb 정체성 불일치')
        objects=[s for s in summons if s['ownerObjectId']==player and s['summonCode']==code]
        by_id=defaultdict(list)
        for s in objects:by_id[s['objectId']].append(s)
        rooted=set(); windows={}
        from .skill_projectile_active_end import projectile_active_end_records
        active_ends=projectile_active_end_records(terminals)
        for i,(s,end) in enumerate(casts):
            creators=[a for a in own if a['actionNo']==1 and partial_window_contains(s,end,a,lookup)]
            if not creators and i in unfinished:
                windows[i]=(s['tick'],None)
                continue
            if len(creators)!=1 or len(creators[0]['targets'])!=1:
                ff=[f for f in finishes if f['playerObjectId']==player and f['skillIdCode']==wire and f['tick']==end]
                if not creators and len(ff)==1 and ff[0]['reason'] in GAMEPLAY_CANCELS:
                    cancelled.add(i);continue
                return _unavailable(spec,'정상 시전과 생성 객체 지정 행동이 일대일 아님')
            a=creators[0]; oid=a['targets'][0]['targetObjectId']; candidates=by_id[oid]
            if len(candidates)!=1 or oid in rooted:
                return _unavailable(spec,'행동이 지정한 소유 객체 생성 정체성 불완전')
            obj=candidates[0]
            if obj.get('identityVerifiedAgainstGameDb') is not True or obj['objectType']!=21 or obj['tick']!=a['tick']:
                return _unavailable(spec,'생성 행동·실제 객체·소유자·시각 불일치')
            retire=[t['tick'] for t in terminals if t['objectId']==oid and t['event']=='CmdDestroyDelayStart']
            active=active_ends.get(oid,{})
            if len(retire)!=1 or not active.get('complete') or not obj['tick']<=retire[0]==active['endTick']:
                unknown[i]='created-object-active-end-incomplete-or-conflicting'
                windows[i]=(obj['tick'],None)
                if not active and not any(t['objectId']==oid for t in terminals):
                    missing_camera_ends[i]=a
            else:
                windows[i]=(obj['tick'],retire[0])
            rooted.add(oid)
        if len(rooted)!=len(objects):return _unavailable(spec,'부모 사용 없이 남은 소유 카메라가 있음')
        root_count=len(rooted)
    else:
        for i,(s,end) in enumerate(casts):
            shots=[a for a in own if a['actionNo']==7 and partial_window_contains(s,end,a,lookup)]
            if len(shots)==1:continue
            if i in unfinished:continue
            ff=[f for f in finishes if f['playerObjectId']==player and f['skillIdCode']==wire and f['tick']==end]
            if not shots and len(ff)==1 and ff[0]['reason'] in GAMEPLAY_CANCELS:
                cancelled.add(i);continue
            return _unavailable(spec,'정상 R 사용과 명시 발사 행동이 일대일 아님')
    hits={n:[set() for _ in casts] for n in cfg['hits']}; applications=[set() for _ in casts]
    emitted=defaultdict(set); event_casts=defaultdict(set)
    damage_pairs={(d['tick'],d['targetObjectId']) for d in damages if d['attackerObjectId']==player}
    competing=defaultdict(set)
    for a in actions:
        if a['sourceObjectId']!=player or a['skillIdCode']==wire:continue
        for t in a.get('targets',[]):competing[a['tick'],t['targetObjectId']].add(a['skillIdCode'])
    for a in own:
        n=a['actionNo']
        if n not in cfg['hits']:continue
        owners=[i for i,(left,right) in windows.items() if
                (partial_window_contains(casts[i][0],right,a,lookup) if 'camera' not in cfg
                 else left<=a['tick'] and (right is None or a['tick']<=right))]
        if len(owners)!=1:
            return _unavailable(spec,'후속 타격 행동이 한 실제 시전/생성 객체의 활성 구간에 배타적으로 연결되지 않음')
        i=owners[0]; emitted[n].add((i,a['tick']))
        for t in a['targets']:
            target=t['targetObjectId']
            if target not in teams or teams[target]==teams[player]:continue
            key=(a['tick'],target)
            if key not in damage_pairs or competing[key]:
                return _unavailable(spec,'행동 대상의 같은 시각 실제 피해가 없거나 다른 행동 대상과 겹침')
            hits[n][i].add(key); event_casts[n,*key].add(i)
            if i in missing_camera_ends and n==2 and casts[i][1] is not None:
                from .skill_wire_order import command_order
                start=casts[i][0];creator=missing_camera_ends[i]
                so,co,ao=map(command_order,(start,creator,a))
                matched=[d for d in damages if d['attackerObjectId']==player
                         and (d['tick'],d['targetObjectId'])==key]
                # Initial contact stays within the real parent finish tick;
                # an open object lifetime never supplies its attribution.
                if (so is not None and co is not None and ao is not None and so<co<ao
                        and start['tick']<=creator['tick']<=a['tick']<=casts[i][1]
                        and matched and all(command_order(d) is not None and co<command_order(d) for d in matched)):
                    bounded_camera_contacts.add((i,key))
    for state in states:
        target=state['targetObjectId']
        if state['event']!='add' or state.get('stateCode') not in codes or target not in teams or teams[target]==teams[player]:continue
        if state['casterObjectId'] not in teams:return _unavailable(spec,'실제 CC 시전자 미확정')
        if state['casterObjectId']!=player:continue
        if state.get('stateGroup') not in (None,cfg['stateGroup']):return _unavailable(spec,'실제 상태 코드/그룹 불일치')
        owners=event_casts[cfg['ccAction'],state['tick'],target]
        if len(owners)!=1:return _unavailable(spec,'CC가 명시 타격 단계의 같은 시각·대상 하나에 연결되지 않음')
        applications[next(iter(owners))].add((state['tick'],target))
    # CC is a separate observed outcome. Missing a positive CC example must
    # not erase independently proven damage hits or complete zero-CC uses.
    chosen=[i for i,(s,_) in enumerate(casts) if any(left<=s['tick']<right for left,right in intervals)]
    observed=chosen
    chosen=[i for i in observed if i not in unknown]
    if mode in {'fetter','stun'}: contacts=applications
    elif mode in {'marked-hit','unmarked-hit'}:contacts=hits[9 if mode=='marked-hit' else 8]
    else:contacts=[set().union(*(hits[n][i] for n in hits)) for i in range(len(casts))]
    recovered=[i for i in observed if mode=='any'
        and unknown.get(i)=='created-object-active-end-incomplete-or-conflicting'
        and any(parent==i and key in contacts[i] for parent,key in bounded_camera_contacts)]
    for i in recovered:unknown.pop(i)
    policy_evidence=[];policy_blocked=[]
    if group==1034300 and mode=='fetter':
        import json
        from pathlib import Path
        from .skill_wire_order import command_order
        from .skill_ordered_match_end import ordered_winner_match_end
        enabled=json.loads((Path(__file__).resolve().parents[1]/'data/user-hit-rate-scope-20260912.json').read_text(encoding='utf-8'))['recordedUseMissPolicy'].get('enabled') is True
        winner=ordered_winner_match_end(game_terminals,gaps) if enabled else None
        for i in observed:
            if not enabled or i not in missing_camera_ends or contacts[i] or unknown.get(i)!='created-object-active-end-incomplete-or-conflicting':continue
            start,end=casts[i];creator=missing_camera_ends[i];oid=creator['targets'][0]['targetObjectId']
            left=command_order(start);co=command_order(creator);right=command_order(winner) if winner else None;reasons=[]
            births=[o for o in raw_objects or [] if o.get('objectId')==oid]
            ff=[f for f in finishes if f.get('playerObjectId')==player and f.get('skillIdCode')==wire and f.get('tick')==end]
            if raw_objects is None or gaps is None or any(g.get('count',0) for g in gaps):reasons.append('original-stream-completeness-unavailable')
            if len(ff)!=1 or ff[0].get('reason')!=0 or left is None or right is None or command_order(ff[0]) is None or not left<command_order(ff[0])<right or not start['tick']<=ff[0]['tick']<=winner['tick']:reasons.append('normal-parent-before-winner-unavailable')
            if len(births)!=1 or births[0].get('objectType')!=21 or births[0].get('tick')!=creator['tick'] or command_order(births[0]) is None or left is None or co is None or right is None or not left<command_order(births[0])<co<right or not start['tick']<=creator['tick']<=winner['tick']:reasons.append('ordered-camera-creation-unavailable')
            def after(e):
                at=command_order(e);tick=e.get('tick')
                return at is None or left is None or type(tick) is not int or tick>=start['tick'] or at>=left
            aa=[a for a in actions if a.get('skillIdCode')==wire and (a.get('sourceObjectId')==player or a.get('sourceObjectId') not in teams) and after(a)]
            for a in aa:
                at=command_order(a)
                if a.get('sourceObjectId')!=player or a.get('wireStatus') not in ORDINARY_ACTIONS or a.get('actionNo') not in {1,2} or at is None or right is None or at>=right or a['tick']>winner['tick']:reasons.append('later-unresolved-or-fetter-action')
                if a.get('actionNo')==1 and a is not creator:reasons.append('later-camera-parent-ambiguous')
            if any(after(e) and (e.get('stateCode') in codes or e.get('stateGroup')==cfg['stateGroup']) and (e.get('casterObjectId')==player or e.get('casterObjectId') not in teams) for e in states):reasons.append('later-fetter-state-unresolved')
            # CC requires distinct action 3 and exact Fetter state. Damage
            # effect codes (including zero) do not independently prove CC.
            plain=[]
            for d in damages:
                if not after(d) or d.get('attackerObjectId')!=player:continue
                matched=[a for a in aa if a.get('actionNo')==2 and a['tick']==d.get('tick') and any(t.get('targetObjectId')==d.get('targetObjectId') for t in a.get('targets',[]))]
                if len(matched)==1 and command_order(d) is not None and command_order(matched[0]) is not None and command_order(d)[0]==command_order(matched[0])[0] and command_order(d)!=command_order(matched[0]):
                    plain.append(dict(tick=d['tick'],damageOrder=command_order(d),actionOrder=command_order(matched[0]),targetObjectId=d.get('targetObjectId'),effectCode=d.get('effectCode')))
            for o in summons:
                if o.get('summonCode')!=1150 or o.get('objectId')==oid or (o.get('ownerObjectId') in teams and o.get('ownerObjectId')!=player):continue
                prior_births=[b for b in raw_objects or [] if b.get('objectId')==o.get('objectId')]
                # Compact summons have no wire order. Only the matching raw
                # birth can establish that another camera predates this use.
                prior=(len(prior_births)==1 and prior_births[0].get('objectType')==21
                    and prior_births[0].get('tick')==o.get('tick')
                    and type(o.get('tick')) is int and o['tick']<start['tick']
                    and command_order(prior_births[0]) is not None and left is not None
                    and command_order(prior_births[0])<left)
                if not prior:reasons.append('later-unlinked-camera')
            proof=dict(startTick=start['tick'],startOrder=left,parentFinish=ff[0] if len(ff)==1 else None,cameraObjectId=oid,cameraBirth=births[0] if len(births)==1 else None,creationAction=creator,winnerEnd=winner,plainHitDamageEvidence=plain,damageUsedAsFetterProof=False,cameraTerminalInvented=False)
            if reasons:policy_blocked.append(dict(proof,reasons=sorted(set(reasons))));continue
            unknown.pop(i);policy_evidence.append(proof)
    if group==1090500:
        import json
        from pathlib import Path
        from .skill_wire_order import command_order
        from .skill_ordered_match_end import ordered_winner_match_end
        enabled=json.loads((Path(__file__).resolve().parents[1]/'data/user-hit-rate-scope-20260912.json').read_text(encoding='utf-8'))['recordedUseMissPolicy'].get('enabled') is True
        winner=ordered_winner_match_end(game_terminals,gaps) if enabled else None
        for i in observed:
            if unknown.get(i)!='open-final-cast' or contacts[i]:continue
            start=casts[i][0];left=command_order(start);right=command_order(winner) if winner else None
            reasons=[]
            if not enabled:continue
            if gaps is None or any(g.get('count',0) for g in gaps):reasons.append('stream-completeness-unavailable')
            if left is None or right is None or left>=right or start['tick']>winner['tick']:reasons.append('actual-ordered-winner-unavailable')
            def after(e):return command_order(e) is None or left is None or command_order(e)>=left
            aa=[a for a in actions if a.get('sourceObjectId')==player and a.get('skillIdCode')==wire and after(a)]
            def direction_only(targets):
                import math
                if targets==[]:return True
                if not isinstance(targets,list) or len(targets)!=1:return False
                t=targets[0]
                if not isinstance(t,dict):return False
                xyz=t.get('targetPosition')
                return (type(t.get('targetObjectId')) is int and t['targetObjectId']==0
                    and t.get('hasTargetPosition') is True
                    and isinstance(xyz,(list,tuple)) and len(xyz)==3
                    and all(type(v) in (int,float) and math.isfinite(v) for v in xyz)
                    and not any(v is True for k,v in t.items() if 'damage' in k.lower() or 'collision' in k.lower() or 'corroborat' in k.lower()))
            if not aa or any(a.get('wireStatus') not in ORDINARY_ACTIONS or a.get('actionNo')!=6 or not direction_only(a.get('targets',[])) or command_order(a) is None or right is None or command_order(a)>=right or a['tick']>winner['tick'] for a in aa):reasons.append('preparation-only-actions-not-proven')
            if any(after(d) for d in damages if d.get('attackerObjectId') in {None,player}):reasons.append('later-owner-damage-not-excluded')
            if any(after(e) for e in states if (e.get('stateCode') in codes or e.get('stateGroup')==cfg['stateGroup']) and e.get('casterObjectId') in {None,player}):reasons.append('later-CC-state-not-excluded')
            proof=dict(startTick=start['tick'],startOrder=left,winnerEnd=winner,skillFinishInvented=False,
                actions=[{k:a[k] for k in ('tick','wireOrder','wireCategory','actionNo','sourceObjectId','skillIdCode','wireStatus','targets') if k in a} for a in aa])
            if reasons:policy_blocked.append(dict(proof,reasons=reasons));continue
            unknown.pop(i);policy_evidence.append(proof)
    chosen=[i for i in observed if i not in unknown]
    row=_result(spec,[contacts[i] for i in chosen],'exact-hit-action-target-damage-and-distinct-CC-outcome',
                cast_ticks=[casts[i][0]['tick'] for i in chosen])
    row.update(exactStateGroups=[cfg['stateGroup']],ccActionNumber=cfg['ccAction'],
        sourceActionNumbers=sorted(cfg['actions']),numericSkillStateCodeJoinUsed=False,
        damageUsedAsCCProof=False,cancelledBeforeAttackCount=sum(i in cancelled for i in chosen),
        actionStageCounts={str(n):{'observedTargetEventCount':sum(len(hits[n][i]) for i in chosen),
            'hitCastCount':sum(bool(hits[n][i]) for i in chosen),
            'distinctEnemyTargetsSummedAcrossAttempts':sum(len({t for _,t in hits[n][i]}) for i in chosen),
            'recordedHitActionTickCount':sum(i in chosen for i,tick in emitted[n])} for n in sorted(hits)},
        actualCCApplicationEventCount=sum(len(applications[i]) for i in chosen),
        rootedCreatedObjectCount=root_count,
        denominatorMeaning='all selected skill uses in observed combat; hit-stage categories are outcomes, not inferred pre-cast empowerment eligibility',
        interpretation='반복 타격 접촉과 사용별 서로 다른 대상, 실제 CC 적용을 분리. 명시 타격 행동만 세며 미기록 발사 수를 추정하지 않음.')
    from .skill_lifecycle_result_policy import finalize_lifecycle_result
    row['unresolvedCastTicks']=[casts[i][0]['tick'] for i in observed if i in unknown]
    if row['unresolvedCastTicks']:
        row.update(hitRateScope='verified-uses-only',wholeCombatHitRate=None,
                   fullRequestedMetricComplete=False)
    row=finalize_lifecycle_result(row,observed,unknown,incomplete_positive=recovered)
    if group in {1034300,1090500}:
        row.update(userPolicyMissEvidence=policy_evidence,userPolicyBlockedEvidence=policy_blocked,
            userPolicyMissCastCount=len(policy_evidence),userPolicyMissCastTicks=[e['startTick'] for e in policy_evidence],
            userPolicyMissAuthority='explicit-user-rule')
        if policy_evidence:row.update(verifiedCompletionCredit=False,fullRequestedMetricComplete=False,
            verifiedCombatCastCount=max(0,(row.get('verifiedCombatCastCount') or 0)-len(policy_evidence)))
    if group==1034300 and policy_evidence:
        from .skill_development_cancellation import annotate_provisional
        annotate_provisional(row,'Explicit user fetter miss before actual winner; plain hit actions do not prove Fetter and camera retirement is not invented.')
        row.update(verifiedCombatCastCount=0,verifiedCompletionCredit=False,fullRequestedMetricComplete=False)
    return row
