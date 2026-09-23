"""Disjoint end-area damage through cast -> self-state -> follow-up transitions.

The originating Skill.code on a state script is deliberately ignored: it may
be stale. A follow-up requires paired, complete predecessor lifetimes rooted
in a self-targeted manual cast and an exact end/start transition.
"""
from collections import Counter
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids
    from .skill_persistent_cc_metrics import named_state_lifetimes, root_state_windows
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_action_stage_evidence import load_exact_skill_ids
    from skill_persistent_cc_metrics import named_state_lifetimes, root_state_windows


def state_end_damage_metric(spec, starts, finishes, states, scripts, damages,
                            player, teams, intervals, catalog, skill_rows,
                            state_rows, state_groups, effect_rows,gaps=None):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    from .skill_lifecycle_result_policy import reviewed_rule_reuse_policy,annotate_reviewed_rule_reuse
    reuse_policy=reviewed_rule_reuse_policy(gaps,{'CmdStartSkill','CmdFinishSkill','CmdStartStateSkill','CmdFinishStateSkill','CmdAddState','CmdRemoveState','CmdDamage'},starts,finishes,states,scripts,damages)
    selected_group=spec['skillGroup']
    if (spec['characterCode']!=45 or selected_group not in {1045300,1045500}
            or spec['mode']!='any' or spec['unit']!='skill-cast'):
        return _unavailable(spec,'검토된 자기 상태 종료 피해 규칙 없음')
    ids=load_exact_skill_ids(); code_groups={r['code']:r['group'] for r in skill_rows}
    names={1045300:'MaiActive2',1045500:'MaiActive4'}
    definitions={r['group']:r for r in state_groups}
    expected={1045300:('MaiActive2BuffState','Common'),1045560:('Suppressed','Suppressed'),
              1045570:('Invulnerability','Invulnerability'),1045580:('MaiActive4BuffState','Common')}
    effects=[e for e in effect_rows if e['code']==1045304]
    if len(effects)!=1 or (effects[0].get('effectPrefabName'),effects[0].get('soundName'))!=('FX_BI_Mai_Skill02_Hit','Mai_Skill02_Hit'):
        return _unavailable(spec,'정확한 종료 타격 EffectAndSound 정의 불일치')
    for group,(name,kind) in expected.items():
        d=definitions.get(group,{})
        if (d.get('skillId'),d.get('stateType'),d.get('effectType'))!=(name,kind,'Buff'):
            return _unavailable(spec,'정확한 자기 상태 및 전이 정의 불일치')
    casts={}; cancelled={}; windows={}; graphs={}
    for group,name in names.items():
        d=catalog['skillGroups'].get(str(group),{})
        selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==group]
        if ((d.get('characterCode'),d.get('skillId'))!=(45,name) or
            any(s['skillIdCode']!=ids.get(name) or code_groups.get(s['skillCode'])!=group for s in selected)):
            return _unavailable(spec,'정확한 W/R 시전 스킬 정체성 불일치')
        casts[group],why=exact_cast_lifetimes(selected,finishes,player)
        if why:return _unavailable(spec,why)
        if group==1045500:
            casts[group]=[(s,e) for s,e in casts[group] if s.get('targetObjectId')==player]
    if not casts[selected_group]:
        return _unavailable(spec,'요청한 자기 대상 또는 W 시전이 관측되지 않음')
    for group,(name,_) in expected.items():
        codes={s['code'] for s in state_rows if s['group']==group}
        if not codes:return _unavailable(spec,'전용 상태 코드 정의 없음')
        windows[group],why=named_state_lifetimes(scripts,states,player,group,name,codes,ids,self_anchor=True)
        if why:return _unavailable(spec,why)
    for state_group,cast_group in [(1045300,1045300),(1045560,1045500),(1045570,1045500)]:
        graph,why=root_state_windows(casts[cast_group],windows[state_group],finishes,player)
        if why:return _unavailable(spec,why)
        graphs[state_group]=graph
        cancelled[cast_group]=graph[2]
    # Both actual precursor states must agree on their originating self R and
    # their complete start/end boundary. No prior/nearest cast is selected.
    predecessors={}; invuln=windows[1045570]
    for j,(start,end) in enumerate(windows[1045560]):
        matches=[k for k,(s,e) in enumerate(invuln) if s['tick']==start['tick'] and e==end
                 and graphs[1045570][0][k]==graphs[1045560][0][j]]
        if len(matches)!=1:return _unavailable(spec,'R 제압·무적 전이의 실제 수명 짝 불일치')
        predecessors[j]=graphs[1045560][0][j]
    if len(predecessors)!=len(invuln):return _unavailable(spec,'R 무적 상태의 배타적 전이 누락')
    r_roots={};used=set()
    for j,(state,_) in enumerate(windows[1045580]):
        matches=[k for k,(_,end) in enumerate(windows[1045560]) if end==state['tick']]
        if len(matches)!=1 or matches[0] in used:
            return _unavailable(spec,'R 선행 상태 종료와 후속 자기 상태 시작이 일대일로 연결되지 않음')
        k=matches[0];used.add(k);r_roots[j]=predecessors[k]
    if used!=set(predecessors):return _unavailable(spec,'R 후속 자기 상태의 시작 누락')
    roots={1045300:graphs[1045300][0],1045580:r_roots}
    contacts={g:[set() for _ in c] for g,c in casts.items()}
    packets={g:Counter() for g in casts}
    for damage in damages:
        target=damage.get('targetObjectId')
        if (damage.get('attackerObjectId')!=player or damage.get('effectCode')!=1045304
                or target not in teams or teams[target]==teams[player]):continue
        links=[(g,j) for g in roots for j,(_,end) in enumerate(windows[g]) if end==damage['tick']]
        if len(links)!=1:
            return _unavailable(spec,'종료 피해가 한 W/R 상태 종료에 배타적으로 연결되지 않음')
        g,j=links[0]; cast_group=1045300 if g==1045300 else 1045500;i=roots[g][j]
        contacts[cast_group][i].add((damage['tick'],target));packets[cast_group][i]+=1
    if not sum(packets[selected_group].values()) and reuse_policy is None:
        return _unavailable(spec,'요청한 W 또는 자기 R 종료 타격의 실제 적 피해 표본 없음')
    chosen=[i for i,(s,_) in enumerate(casts[selected_group]) if any(l<=s['tick']<r for l,r in intervals)]
    row=_result(spec,[contacts[selected_group][i] for i in chosen],'exact-cast-self-state-transition-exclusive-end-damage',
                cast_ticks=[casts[selected_group][i][0]['tick'] for i in chosen])
    row.update(exactDamagePacketCount=sum(packets[selected_group][i] for i in chosen),
        cancelledBeforeAttackCount=len(cancelled[selected_group].intersection(chosen)),
        verifiedStateLifetimeCount=sum(map(len,windows.values())),
        verifiedSelfRFollowupCount=len(r_roots),stateScriptSkillCodeUsed=False,
        fixedDurationWindowUsed=False,nearestCastUsed=False,
        measuredOutcome='self-targeted-R-followup-end-damage' if selected_group==1045500 else 'W-end-area-damage',
        interpretation='일반 W 종료 피해와 자기 대상 R의 실제 상태 전이 뒤 종료 피해를 분리. 같은 종료 피해를 양쪽에 중복 집계하지 않음. 자기 R의 공격 전 게임 취소는 시도·실패에 포함.')
    return annotate_reviewed_rule_reuse(row,reuse_policy,positive_gate_unsatisfied=not sum(packets[selected_group].values()))
