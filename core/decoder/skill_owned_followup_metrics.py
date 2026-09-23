"""One player use through exactly linked, live-owned follow-up skill lifetimes.

Attack enums were verified in Blis.Common CelineSkillAction, JustynaSkillAction
and NiahSkillAction from metadata SHA256
ede0935bbc00dcd93707d279f5a3f63570b8f09fcd9119d4582637f957afed61.
"""
from collections import Counter, defaultdict

try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_summon_ownership import live_summon_owner_resolver
    from .skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS
    from .skill_static_effect_families import candidate_effect_family
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_summon_ownership import live_summon_owner_resolver
    from skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS
    from skill_static_effect_families import candidate_effect_family

FOLLOWUPS = {
    1015400: dict(childGroup=1015900, summonCodes={1030}),
    1035500: dict(childGroup=1035600, summonCodes={1173}),
    1043500: dict(childGroup=1043520, summonCodes={1224,1225,1226,1227},
                  actionActor='player', hitActions={5,6,7,8}, normalEndActions='one-burst'),
    1079300: dict(childGroup=1079600, summonCodes={1561},
                  actionActor='child', hitActions={32,33}, normalEndActions='both-phases'),
    1081500: dict(childGroup=1081800, summonCodes={1585},
                  actionActor='child', hitActions={242}, normalEndActions='variable'),
}
GAMEPLAY_CANCELS=set(range(1,15))|{16,17}


def owned_followup_windows(group, starts, finishes, nonplayer_starts, summons,
                           terminals, player, teams, catalog, skill_rows, skill_ids=None):
    """Return one window per explicit parent start; no nearest-time association."""
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    config=FOLLOWUPS.get(group)
    if not config or nonplayer_starts is None:
        return None,'소환물의 실제 스킬 시작 기록이 보관되지 않음'
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    parent_definition=catalog['skillGroups'].get(str(group),{})
    child_definition=catalog['skillGroups'].get(str(config['childGroup']),{})
    if (not parent_definition or not child_definition or
            child_definition.get('characterCode')!=parent_definition.get('characterCode') or
            child_definition.get('family')!=parent_definition.get('family')):
        return None,'본체와 후속 스킬의 정확한 gameDb 정의 불일치'
    parent_wire=ids.get(parent_definition.get('skillId'));child_wire=ids.get(child_definition.get('skillId'))
    code_groups={r['code']:r['group'] for r in skill_rows}
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==group]
    if not selected or any(s['skillIdCode']!=parent_wire or code_groups.get(s['skillCode'])!=group for s in selected):
        return None,'본체 시전 누락 또는 wire 스킬 연결 불일치'
    lives,reason=exact_cast_lifetimes(selected,finishes,player)
    if reason:return None,reason
    parent_ends={(f['skillIdCode'],f['tick']):f for f in finishes if f['playerObjectId']==player}
    windows=[{'start':s,'end':end,'children':[],
              'finishReason':parent_ends[s['skillIdCode'],end]['reason']} for s,end in lives]
    by_tick=defaultdict(list)
    for i,w in enumerate(windows):by_tick[w['start']['tick']].append(i)
    resolve=live_summon_owner_resolver(summons,terminals,set(teams))
    by_source=defaultdict(list)
    for s in nonplayer_starts:
        raw_group=code_groups.get(s['skillCode'])
        if raw_group!=config['childGroup'] and s['skillIdCode']!=child_wire:continue
        if raw_group!=config['childGroup'] or s['skillIdCode']!=child_wire:
            return None,'후속 스킬의 실제 Skill.code와 wire enum 정체성 불일치'
        owner,path,reason=resolve(s['sourceObjectId'],s['tick'])
        if reason:return None,'후속 시전의 실제 시점 소환물 소유 미확정: '+reason
        if owner!=player:continue
        if not path or path[0] not in config['summonCodes']:
            return None,'후속 시전의 소환물 종류가 이 본체 스킬 정의와 다름'
        parents=by_tick[s['tick']]
        if len(parents)!=1:
            return None,'소환물 시전과 같은 tick의 본체 사용이 단 하나로 연결되지 않음'
        by_source[s['sourceObjectId']].append({**s,'playerObjectId':s['sourceObjectId'],
                                             'skillGroup':config['childGroup'],'parentIndex':parents[0]})
    for finish in finishes:
        if finish['skillIdCode']!=child_wire:continue
        owner,_,reason=resolve(finish['playerObjectId'],finish['tick'])
        if reason:return None,'후속 종료의 실제 소유 미확정: '+reason
        if owner==player and finish['playerObjectId'] not in by_source:
            return None,'시작 없이 남은 소유 소환물의 후속 종료가 있음'
    for source,ss in by_source.items():
        child_lives,reason=exact_cast_lifetimes(ss,finishes,source)
        if reason:return None,'후속 시전 수명 미확정: '+reason
        finish_rows={(f['skillIdCode'],f['tick']):f for f in finishes if f['playerObjectId']==source}
        for s,end in child_lives:
            owner,_,reason=resolve(source,end)
            if reason or owner!=player:return None,'후속 스킬 종료가 소환물의 실제 소유 수명 밖에 있음'
            child={'start':s,'end':end,'finishReason':finish_rows[s['skillIdCode'],end]['reason']}
            w=windows[s['parentIndex']];w['children'].append(child);w['end']=max(w['end'],end)
    for w in windows:
        if not w['children'] and w['finishReason'] not in GAMEPLAY_CANCELS:
            return None,'후속 스킬이 없는 본체 정상 종료를 미적중으로 확정할 수 없음'
    return windows,None


def owned_followup_damage_metric(spec, starts, finishes, nonplayer_starts, summons,
                                 terminals, actions, damages, player, teams, intervals,
                                 catalog, skill_rows, effect_rows, skill_ids=None):
    config=FOLLOWUPS.get(spec['skillGroup'],{})
    if spec['mode']!='any' or spec['unit']!='skill-cast' or 'hitActions' not in config:
        return _unavailable(spec,'이 후속 스킬은 적중 대상·끌기·로프 충돌의 별도 근거가 필요함')
    windows,reason=owned_followup_windows(spec['skillGroup'],starts,finishes,nonplayer_starts,
        summons,terminals,player,teams,catalog,skill_rows,skill_ids)
    if reason:return _unavailable(spec,reason)
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    action_group=spec['skillGroup'] if config['actionActor']=='player' else config['childGroup']
    wire=ids[catalog['skillGroups'][str(action_group)]['skillId']]
    child_sources={ch['start']['sourceObjectId'] for w in windows for ch in w['children']}
    actors={player} if config['actionActor']=='player' else child_sources
    if config['actionActor']=='child':
        resolve=live_summon_owner_resolver(summons,terminals,set(teams))
        for a in actions:
            if (a.get('wireStatus') not in ORDINARY_ACTIONS or a['skillIdCode']!=wire or
                    a['actionNo'] not in config['hitActions'] or a['sourceObjectId'] in actors):continue
            owner,_,reason=resolve(a['sourceObjectId'],a['tick'])
            if reason or owner==player:return _unavailable(spec,'본체 사용에 연결되지 않은 소환물의 실제 공격 행동이 남아 있음')
    emit_actions=[a for a in actions if a.get('wireStatus') in ORDINARY_ACTIONS and
                  a['skillIdCode']==wire and a['sourceObjectId'] in actors and a['actionNo'] in config['hitActions']]
    child_actions=defaultdict(list);emit_owners=defaultdict(set)
    identities=set()
    for a in emit_actions:
        identity=(a['sourceObjectId'],a['tick'],a['actionNo'])
        if identity in identities:return _unavailable(spec,'후속 공격 행동 중복으로 실제 방출 수 미확정')
        identities.add(identity)
        candidates=[(i,j) for i,w in enumerate(windows) for j,ch in enumerate(w['children'])
                    if ch['start']['tick']<=a['tick']<=ch['end'] and
                    (config['normalEndActions']!='one-burst' or ch['end']==a['tick']) and
                    (config['actionActor']=='player' or ch['start']['sourceObjectId']==a['sourceObjectId'])]
        if len(candidates)!=1:return _unavailable(spec,'실제 후속 공격 행동이 한 소환물 수명에 배타적으로 연결되지 않음')
        i,j=candidates[0];child_actions[i,j].append(a);emit_owners[a['tick']].add(i)
    for i,w in enumerate(windows):
        for j,ch in enumerate(w['children']):
            aa=child_actions[i,j];count=Counter(a['actionNo'] for a in aa)
            if ch['finishReason']==0:
                if config['normalEndActions']=='one-burst' and (len(aa)!=1 or aa[0]['tick']!=ch['end']):
                    return _unavailable(spec,'폭탄 정상 종료와 실제 폭발 행동의 일대일 연결 미확정')
                if config['normalEndActions']=='both-phases' and count!=Counter({32:1,33:1}):
                    return _unavailable(spec,'두 차례 포격의 실제 방출과 정상 종료 연결 미확정')
            elif ch['finishReason'] not in GAMEPLAY_CANCELS:
                return _unavailable(spec,'후속 시전 종료 사유가 정상 또는 검증된 게임 내 취소가 아님')
    _,effects=candidate_effect_family(spec,catalog,effect_rows)
    if not effects:return _unavailable(spec,'같은 gameDb의 후속 타격 FX 계열 없음')
    contacts=[set() for _ in windows];packets=[0 for _ in windows]
    for d in damages:
        target=d['targetObjectId']
        if (d['attackerObjectId'] not in {player,*child_sources} or target not in teams or
                teams[target]==teams[player] or d.get('effectCode') not in effects):continue
        candidates=[i for i,w in enumerate(windows) if i in emit_owners[d['tick']] and w['start']['tick']<=d['tick']<=w['end']
                    and (d['attackerObjectId']==player or any(ch['start']['sourceObjectId']==d['attackerObjectId']
                         and ch['start']['tick']<=d['tick']<=ch['end'] for ch in w['children']))]
        if len(candidates)!=1 or emit_owners[d['tick']]!={candidates[0]}:
            return _unavailable(spec,'후속 타격 FX가 한 본체 사용과 같은 tick의 실제 공격 행동에 배타적으로 연결되지 않음')
        i=candidates[0];contacts[i].add((d['tick'],target));packets[i]+=1
    if not any(contacts):return _unavailable(spec,'후속 공격의 실제 적 실험체 타격 검증 표본 없음')
    chosen=[i for i,w in enumerate(windows) if any(l<=w['start']['tick']<r for l,r in intervals)]
    row=_result(spec,[contacts[i] for i in chosen],
                'one-parent-use-exact-live-owned-child-lifetimes-and-same-tick-attack-FX',
                cast_ticks=[windows[i]['start']['tick'] for i in chosen])
    row.update(exactDamagePacketCount=sum(packets[i] for i in chosen),
        linkedOwnedChildCastCount=sum(len(w['children']) for w in windows),
        parentUsesWithOwnedChildren=sum(bool(w['children']) for w in windows),
        observedOwnedAttackActionCount=len(emit_actions),
        cancelledBeforeAttackCount=sum(not windows[i]['children'] for i in chosen),
        childCastsCountedAsIndependentUses=False,
        interpretation='본체 한 번 사용에 소환물의 후속 공격을 합산; 같은 적 반복 타격은 일반 성공·다인 인원을 중복 증가시키지 않음')
    if config['normalEndActions']=='one-burst':
        # Exact BurstCombinedBomb_1..4 enum members, not the initial summon level.
        stages={5:1,6:2,7:3,8:4};by_stage=defaultdict(list);without_burst=0
        for i in chosen:
            aa=[a for j in range(len(windows[i]['children'])) for a in child_actions[i,j]]
            if len(aa)>1:return _unavailable(spec,'본체 R 한 번에 여러 폭발이 있어 최종 융합 단계별 분모 미확정')
            if aa:by_stage[stages[aa[0]['actionNo']]].append(contacts[i])
            else:without_burst+=1
        row['detonationStageCounts']={}
        for stage in range(1,5):
            hits=by_stage[stage];attempts=len(hits);success=sum(bool(h) for h in hits)
            row['detonationStageCounts'][str(stage)]={
                'attemptCount':attempts,'hitCount':success,'hitRate':round(success/attempts,6) if attempts else None,
                'distinctEnemyTargetsSummedAcrossAttempts':sum(len({target for _,target in h}) for h in hits)}
        row['usesWithoutDetonationCount']=without_burst
    return row
