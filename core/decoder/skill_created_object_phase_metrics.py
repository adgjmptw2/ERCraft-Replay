"""Creation/end hits joined through explicit phase actions naming one object."""
from collections import Counter, defaultdict
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS
    from .skill_wire_order import event_within_cast, finish_lookup
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS
    from skill_wire_order import event_within_cast, finish_lookup

CONFIG={1042500:dict(character=42,skill='BiancaActive4',summon=1201,
    prefab='Bianca_Skill04_Circle',rootAction=6006,
    phaseActions={'creation-hit':(6004,6001),'end-hit':(6005,6002)},ignoredActions={6003})}
GAMEPLAY_CANCELS=set(range(1,15))|{16,17}


def created_object_phase_metric(spec,starts,finishes,actions,summons,terminals,
                                damages,player,teams,intervals,catalog,skill_rows,
                                summon_rows,skill_ids=None,development=False):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    group=spec['skillGroup'];cfg=CONFIG.get(group)
    if not cfg or spec['mode'] not in {'any',*cfg['phaseActions']} or spec['unit']!='skill-cast':
        return _unavailable(spec,'검토된 생성 객체 단계별 타격 규칙 없음')
    definition=catalog['skillGroups'].get(str(group),{})
    definitions=[r for r in summon_rows if r['code']==cfg['summon']]
    if (definition.get('characterCode'),definition.get('skillId'))!=(cfg['character'],cfg['skill']) or len(definitions)!=1 or (
            definitions[0].get('objectType'),definitions[0].get('prefabPath'))!=('SummonTrap',cfg['prefab']):
        return _unavailable(spec,'정확한 스킬·생성 객체 정의 불일치')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids;wire=ids.get(cfg['skill'])
    codes={r['code']:r['group'] for r in skill_rows}
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==group]
    if not selected or any(s['skillIdCode']!=wire or codes.get(s['skillCode'])!=group for s in selected):
        return _unavailable(spec,'시전 누락 또는 wire 정체성 불일치',
                            category='observation-conflict', reason_code='cast-wire-identity')
    casts,reason=exact_cast_lifetimes(selected,finishes,player)
    if reason:return _unavailable(spec,reason)
    lookup=finish_lookup(finishes,player)
    own=[a for a in actions if a['sourceObjectId']==player and a['skillIdCode']==wire and a.get('wireStatus') in ORDINARY_ACTIONS]
    reviewed={cfg['rootAction'],*cfg['ignoredActions'],*(n for ns in cfg['phaseActions'].values() for n in ns)}
    if any(a['actionNo'] not in reviewed for a in own):return _unavailable(spec,'미검토 객체 단계 행동이 있음')
    objects=defaultdict(list);ends=defaultdict(set);finals=defaultdict(set)
    for s in summons:
        if s['ownerObjectId']==player and s['summonCode']==cfg['summon']:objects[s['objectId']].append(s)
    for t in terminals:
        if t['event']=='CmdDestroyDelayStart':ends[t['objectId']].add(t['tick'])
        elif t['event']=='CmdDestroy':finals[t['objectId']].add(t['tick'])
    roots={};unknown={};cancelled=set();seen_creators=set()
    for i,(s,end) in enumerate(casts):
        creators=[(j,a) for j,a in enumerate(own) if a['actionNo']==cfg['rootAction'] and event_within_cast(s,end,a,lookup)]
        if not creators:
            ff=lookup.get((wire,end),[])
            if len(ff)==1 and ff[0]['reason'] in GAMEPLAY_CANCELS:cancelled.add(i)
            else:unknown[i]='normal-use-without-explicit-root'
            continue
        if len(creators)!=1 or len(creators[0][1]['targets'])!=1:return _unavailable(spec,'사용과 생성 객체가 일대일 아님')
        j,a=creators[0];oid=a['targets'][0]['targetObjectId'];ss=objects[oid]
        if j in seen_creators or oid in roots or len(ss)!=1:return _unavailable(spec,'사용/생성 객체 연결 중복 또는 누락')
        seen_creators.add(j);obj=ss[0]
        if obj['tick']!=a['tick'] or obj['objectType']!=10 or obj.get('identityVerifiedAgainstGameDb') is not True:
            return _unavailable(spec,'생성 객체의 시각·소유자·종류 근거 부족')
        roots[oid]=(i,obj['tick'])
    if set(objects)!=set(roots) or len(seen_creators)!=sum(a['actionNo']==cfg['rootAction'] for a in own):
        return _unavailable(spec,'사용에 연결되지 않은 생성 객체 또는 행동')
    markers={phase:defaultdict(set) for phase in cfg['phaseActions']}
    phase_times={oid:{} for oid in roots}
    for phase,(marker,_) in cfg['phaseActions'].items():
        for a in own:
            if a['actionNo']!=marker:continue
            targets={t['targetObjectId'] for t in a['targets']}
            if len(targets)!=1 or not targets<=set(roots):return _unavailable(spec,'단계 행동이 하나의 생성 객체를 직접 지정하지 않음')
            oid=next(iter(targets));i,left=roots[oid]
            if phase in phase_times[oid]:unknown[i]='duplicate-object-phase-marker'
            phase_times[oid][phase]=a['tick'];markers[phase][a['tick']].add(oid)
            if a['tick']<left:unknown[i]='phase-before-object-creation'
    estimated=[]
    for oid,(i,left) in roots.items():
        times=phase_times[oid]
        if set(times)!=set(cfg['phaseActions']):
            if development and ((spec['mode'] in times) or ends[oid] or finals[oid]):
                estimated.append(i);continue
            unknown[i]='recorded-object-phase-missing';continue
        if not left<=times['creation-hit']<times['end-hit']:unknown[i]='object-phase-order-invalid'
        if ends[oid]!={times['end-hit']} or len(finals[oid])>1 or any(t<times['end-hit'] for t in finals[oid]):
            unknown[i]='object-end-phase-retirement-mismatch'
    damage={(d['tick'],d['targetObjectId']) for d in damages if d['attackerObjectId']==player and d['targetObjectId'] in teams and teams[d['targetObjectId']]!=teams[player]}
    hits={phase:[set() for _ in casts] for phase in cfg['phaseActions']}
    corroborated=0
    for phase,(_,hit) in cfg['phaseActions'].items():
        for a in own:
            if a['actionNo']!=hit:continue
            owners=markers[phase][a['tick']]
            enemy={t['targetObjectId'] for t in a['targets'] if t['targetObjectId'] in teams and teams[t['targetObjectId']]!=teams[player]}
            if not enemy:continue
            if not owners:return _unavailable(spec,'대상 타격에 같은 시각의 명시 단계 객체가 없음')
            if len(owners)!=1:
                for oid in owners:unknown[roots[oid][0]]='ambiguous-same-tick-phase-objects'
                continue
            i,_=roots[next(iter(owners))]
            if any((a['tick'],target) not in damage for target in enemy):
                unknown[i]='explicit-target-hit-without-corroborating-damage';continue
            hits[phase][i].update((a['tick'],target) for target in enemy);corroborated+=len(enemy)
    # Each recorded enemy hit is checked above. An unrelated positive example
    # is not needed to interpret complete object phases with zero enemy hits.
    combat=[i for i,(s,_) in enumerate(casts) if any(l<=s['tick']<r for l,r in intervals)]
    valid=[i for i in combat if i not in unknown]
    contacts=hits[spec['mode']] if spec['mode']!='any' else [set().union(*(rows[i] for rows in hits.values())) for i in range(len(casts))]
    if development:
        for i in combat:
            if i in unknown and unknown[i]=='recorded-object-phase-missing' and contacts[i]:
                del unknown[i];estimated.append(i)
        valid=[i for i in combat if i not in unknown]
    row=_result(spec,[contacts[i] for i in valid],'explicit-created-object-phase-and-enemy-hit-target',
                cast_ticks=[casts[i][0]['tick'] for i in valid])
    if combat and not valid:
        row=_unavailable(spec,'교전 시전은 있지만 객체 단계의 완결된 결과가 없음',
                         category='observation-incomplete', reason_code='no-complete-object-phase-use')
    row.update(perUseCompletenessTracked=True,observedCombatCastCount=len(combat),verifiedCombatCastCount=len(valid),
        unresolvedCombatCastCount=len(combat)-len(valid),unresolvedAllCastCount=len(unknown),
        unresolvedCastReasons=dict(Counter(unknown[i] for i in combat if i in unknown)),
        incompleteUsesCountedAsMisses=False,cancelledBeforeAttackCount=sum(i in cancelled for i in valid),
        knownCreatedObjectCount=len(roots),numericSkillStateCodeJoinUsed=False,
        interpretation='마법진 생성/종료 행동이 직접 지정한 객체에 같은 시각의 적 대상 타격을 연결. 단계별 적중과 합집합을 별도 계산. 두 단계가 완결된 사용 및 공격 전 게임 취소가 분모이며 미완결은 실패와 구분.')
    if any(i in combat for i in estimated):
        from .skill_development_cancellation import annotate_provisional
        annotate_provisional(row,'Requested phase or recorded positive contact is usable independently of the other phase. Retired object with missing phase marker is a provisional negative for that phase.')
    return row
