"""Actual named state application through a verified owned follow-up lifetime."""
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_owned_followup_metrics import owned_followup_windows
    from .skill_summon_ownership import live_summon_owner_resolver
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_owned_followup_metrics import owned_followup_windows
    from skill_summon_ownership import live_summon_owner_resolver


def owned_enemy_pull_metric(spec, starts, finishes, nonplayer_starts, summons, terminals,
                            states, player, teams, intervals, catalog, skill_rows,
                            state_rows, state_groups, skill_ids=None):
    if (spec['characterCode'],spec['skillGroup'],spec['mode'])!=(15,1015400,'enemy-pull') or spec['unit']!='skill-cast':
        return _unavailable(spec,'이 스킬의 실제 끌기 상태 적용은 검증되지 않음')
    definitions=[r for r in state_groups if r.get('group')==1015400]
    if (len(definitions)!=1 or definitions[0].get('skillId')!='SisselaActive3Pull' or
            definitions[0].get('stateType')!='Airborne'):
        return _unavailable(spec,'같은 gameDb의 SisselaActive3Pull 상태 정의 불일치')
    codes={r['code'] for r in state_rows if r.get('group')==definitions[0]['group']}
    if not codes:return _unavailable(spec,'정확한 끌기 상태 코드 없음')
    windows,reason=owned_followup_windows(spec['skillGroup'],starts,finishes,nonplayer_starts,
        summons,terminals,player,teams,catalog,skill_rows,skill_ids)
    if reason:return _unavailable(spec,reason)
    resolve=live_summon_owner_resolver(summons,terminals,set(teams))
    contacts=[set() for _ in windows]
    for state in states:
        target=state['targetObjectId']
        if (state.get('event')!='add' or state.get('stateCode') not in codes or
                target not in teams or teams[target]==teams[player]):continue
        # Add-state packets carry a concrete stateCode; the cache's optional
        # stateGroup slot is null. Resolve its group through CharacterState.
        if state.get('stateGroup') not in (None,1015400):
            return _unavailable(spec,'실제 끌기 상태 코드와 group 필드 불일치')
        owner,_,reason=resolve(state['casterObjectId'],state['tick'])
        if reason:return _unavailable(spec,'실제 적 끌기 상태의 시전자 귀속 미확정')
        if owner!=player:continue
        candidates=[i for i,w in enumerate(windows) if any(
            ch['start']['tick']<=state['tick']<=ch['end'] and
            (state['casterObjectId']==player or state['casterObjectId']==ch['start']['sourceObjectId'])
            for ch in w['children'])]
        if len(candidates)!=1:
            return _unavailable(spec,'실제 적 끌기 상태가 본체 E와 윌슨 후속 수명 하나에 연결되지 않음')
        contacts[candidates[0]].add((state['tick'],target))
    if not any(contacts):
        return _unavailable(spec,'실제 적 끌기 적용 검증 표본 없음; 자기 이동이나 일반 피해로 대체하지 않음')
    chosen=[i for i,w in enumerate(windows) if any(l<=w['start']['tick']<r for l,r in intervals)]
    row=_result(spec,[contacts[i] for i in chosen],
                'exact-Sissela-Pull-state-application-in-linked-player-Wilson-lifetimes',
                cast_ticks=[windows[i]['start']['tick'] for i in chosen])
    row.update(stateApplicationEventCount=sum(len(contacts[i]) for i in chosen),
        measuredOutcome='enemy-pull-state-application',movementCompletionMeasured=False,
        interpretation='교전 중 전체 E 사용 대비 적에게 끌기 상태가 실제 적용된 사용; 자기 이동과 일반 접촉은 별도이며 이동 완료·이동 거리는 측정하지 않음')
    return row
