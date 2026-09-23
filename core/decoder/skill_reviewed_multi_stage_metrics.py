"""Reviewed multi-hit stages; action identity or the existing exclusive FX gate.

Effect codes may be shared by both attack stages (Hisui). They are never a
stage number or a Skill foreign key. Both-hit means the SAME enemy at both
stages, not two different enemies, and repeat contacts remain separate.
"""
from collections import Counter, defaultdict
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order, event_within_cast, finish_lookup
    from .skill_static_effect_families import shared_effect_lifetime_metric, candidate_effect_family
    from .skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order, event_within_cast, finish_lookup
    from skill_static_effect_families import shared_effect_lifetime_metric, candidate_effect_family
    from skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS

CONFIG = {
    1017200: dict(character=17, skill='AdrianaActive1',
        phases={'any': (None, (1017201,))},
        effects={1017201: ('FX_BI_Adriana_Skill01_Hit', 'adriana_Skill01_Hit')}),
    1020500: dict(character=20, skill='LenoxActive4', sequence=(5001, 5002),
        phases={'first-hit': (5001, (1020501,)), 'end-hit': (5002, (1020502,))},
        effects={1020501: ('FX_BI_Lenox_Skill04_Hit', 'Lenox_Skill04_01_hit'),
                 1020502: ('FX_BI_Lenox_Skill04_Hit_P', 'Lenox_Skill04_02_hit')}),
    1078200: dict(character=78, skill='HisuiActive1', sequence=(1, 2, 3),
        phases={'first-hit': (1, (1078201, 1078202)), 'end-hit': (3, (1078201, 1078202))},
        effects={1078201: ('FX_BI_Hisui_Skill01_Swrod02_Hit', 'Hisui_Skill01_Hit'),
                 1078202: ('FX_BI_Hisui_Skill01_Swrod03_Hit', 'Hisui_Skill01_Hit')}),
    1067200: dict(character=67, skill='AbigailActive1',
        phases={'first-hit': (None, (1067211,)), 'end-hit': (None, (1067212,))},
        effects={1067211: ('FX_BI_Abigail_Skill01_02_hit', 'Abigail_Skill01_FirstSpin_Hit'),
                 1067212: ('FX_BI_Abigail_Skill01_02_hit_Second', 'Abigail_Skill01_SecondSpin_Hit')}),
    1039300: dict(character=39, skill='CamiloActive2', maxContactsPerEnemy=4,
        phases={'any': (None, (1039301,))},
        effects={1039301: ('FX_BI_Camilo_Skill02_Hit', 'Camilo_Skill02_Hit')}),
}


def reviewed_multi_stage_metric(spec, starts, finishes, actions, damages, player,
                                teams, intervals, catalog, skill_rows, effect_rows):
    group=spec['skillGroup']; cfg=CONFIG.get(group); mode=spec['mode']
    if not cfg or mode not in {'any', *cfg['phases'], *({'both-hit'} if len(cfg['phases'])==2 else set())} or spec['unit']!='skill-cast':
        return _unavailable(spec, '검토된 연속 타격 단계 규칙 없음')
    definition=catalog['skillGroups'].get(str(group), {})
    if (definition.get('characterCode'), definition.get('skillId'))!=(cfg['character'], cfg['skill']):
        return _unavailable(spec, '정확한 연속 타격 스킬 정체성 불일치')
    for code, identity in cfg['effects'].items():
        rows=[r for r in effect_rows if r['code']==code]
        if len(rows)!=1 or (rows[0].get('effectPrefabName'), rows[0].get('soundName'))!=identity:
            return _unavailable(spec, '정확한 연속 타격 EffectAndSound 정의 불일치')
    wire=load_exact_skill_ids()[cfg['skill']]; codes={r['code']:r['group'] for r in skill_rows}
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==group]
    # Recorded action sequences validate each use directly. Candidate-only
    # FX routes keep their separate runtime mapping gate below.
    if not selected or any(s['skillIdCode']!=wire or codes.get(s['skillCode'])!=group for s in selected):
        return _unavailable(spec, '시전 누락 또는 wire 정체성 불일치')
    records, reason=ordered_cast_records(selected, finishes, player)
    if reason: return _unavailable(spec, reason)
    unknown={i:r['reason'] for i,r in enumerate(records) if not r['complete']}
    lookup=finish_lookup(finishes, player)
    action_based='sequence' in cfg
    if not action_based:
        # Keep the established all-family/all-competing-casts exclusivity gate.
        # No open-use relaxation or newly assumed effect-code ownership here.
        groups, effects=candidate_effect_family(spec, catalog, effect_rows)
        if groups!={group} or effects!=set(cfg['effects']):
            return _unavailable(spec, '후보 타격 계열이 검토한 단계 전체와 다름')
        gate=shared_effect_lifetime_metric({**spec,'mode':'any'}, starts, finishes,
            damages, player, teams, intervals, effect_rows, {}, catalog)
        if gate['status']!='calculable-observed': return {**gate, **spec}
    own=[a for a in actions if a['sourceObjectId']==player and a['skillIdCode']==wire]
    by_use=[[] for _ in records]; markers=defaultdict(set)
    if action_based:
        for a in own:
            if a.get('wireStatus') not in ORDINARY_ACTIONS or a['actionNo'] not in cfg['sequence']:
                return _unavailable(spec, '연속 타격 행동 종류·정확한 순서 미확정')
            owners=[i for i,r in enumerate(records) if (event_within_cast(r['start'],r['finish']['tick'],a,lookup) if r['finish'] else a['tick']>r['start']['tick'])]
            if len(owners)!=1: return _unavailable(spec, '연속 타격 행동의 실제 시전 귀속 미확정')
            i=owners[0]; by_use[i].append(a); markers[a['tick'],a['actionNo']].add(i)
            r=records[i]
            if command_order(a) is None and a['tick'] in {r['start']['tick'],r['finish']['tick'] if r['finish'] else None}:
                unknown[i]='action-at-cast-boundary-without-command-order'
        for i,r in enumerate(records):
            if not r['complete']: continue
            if len({a['tick'] for a in by_use[i]})!=len(by_use[i]):
                unknown[i]='simultaneous-stage-order-unresolved'
            seq=tuple(a['actionNo'] for a in sorted(by_use[i], key=lambda a:a['tick']))
            cancelled=r['finish']['reason'] in (set(range(1,15))|{16,17})
            if seq!=cfg['sequence'] and not (cancelled and seq==cfg['sequence'][:len(seq)]):
                unknown[i]='missing-duplicate-or-out-of-order-attack-stage'
    hits={phase:[set() for _ in records] for phase in cfg['phases']}
    packet_counts={phase:Counter() for phase in cfg['phases']}
    for d in damages:
        target=d['targetObjectId']; effect=d.get('effectCode')
        if d['attackerObjectId']!=player or target not in teams or teams[target]==teams[player] or effect not in cfg['effects']: continue
        links=[]
        for phase,(action,effects) in cfg['phases'].items():
            if effect not in effects: continue
            if action_based: owners=markers[d['tick'],action]
            else: owners={i for i,r in enumerate(records) if r['finish'] and event_within_cast(r['start'],r['finish']['tick'],d,lookup)}
            links.extend((phase,i) for i in owners)
        if len(links)!=1: return _unavailable(spec, '실제 피해가 한 타격 단계에 배타적으로 연결되지 않음')
        phase,i=links[0]; r=records[i]
        if r['finish'] and not event_within_cast(r['start'],r['finish']['tick'],d,lookup):
            return _unavailable(spec, '실제 시전 종료 뒤의 피해를 단계에 붙일 수 없음')
        hits[phase][i].add((d['tick'],target)); packet_counts[phase][i]+=1
    if not action_based and any(not sum(counts.values()) for counts in packet_counts.values()):
        return _unavailable(spec, '후보 효과의 단계별 귀속을 독립 규칙으로 확정하는 작업이 남음',
                            category='rule-mapping-incomplete', reason_code='candidate-effect-phase-mapping')
    # Phase-specific identities must agree with recorded event chronology.
    if len(hits)==2:
        first,end=hits.values()
        for a,b in zip(first,end):
            if a and b and max(t for t,_ in a)>=min(t for t,_ in b):
                return _unavailable(spec, '첫 타격과 후속 타격의 관측 순서 불일치')
    union=[set().union(*(h[i] for h in hits.values())) for i in range(len(records))]
    if cfg.get('maxContactsPerEnemy'):
        if any(max(Counter(target for _,target in h).values(),default=0)>cfg['maxContactsPerEnemy'] for h in union):
            return _unavailable(spec, '관측 반복 타격 수가 검토한 스킬 상한 초과')
    if mode=='both-hit':
        first,end=hits.values(); contacts=[{(min(t for t,x in b if x==target),target) for target in {x for _,x in a}&{x for _,x in b}} for a,b in zip(first,end)]
    else: contacts=union if mode=='any' else hits[mode]
    combat=[i for i,r in enumerate(records) if any(l<=r['start']['tick']<end for l,end in intervals)]
    valid=[i for i in combat if i not in unknown]
    method='reviewed-multi-stage-'+('exact-action-order' if action_based else 'exclusive-complete-effect-lifetimes')
    row=_result(spec,[contacts[i] for i in valid],method,
                cast_ticks=[records[i]['start']['tick'] for i in valid])
    if combat and not valid:
        row=_unavailable(spec,'교전 시전은 있지만 완결된 타격 행동 순서가 없음',
                         category='observation-incomplete', reason_code='no-complete-attack-sequence')
    phases=list(hits) if mode in {'any','both-hit'} else [mode]
    row.update(perUseCompletenessTracked=True, observedCombatCastCount=len(combat),
        verifiedCombatCastCount=len(valid), unresolvedCombatCastCount=len(combat)-len(valid),
        unresolvedAllCastCount=len(unknown), incompleteUsesCountedAsMisses=False,
        unresolvedCastReasons=dict(Counter(unknown[i] for i in combat if i in unknown)),
        exactDamagePacketCount=sum(packet_counts[p][i] for p in phases for i in valid),
        bothHitRequiresSameEnemy=mode=='both-hit', numericSkillStateCodeJoinUsed=False,
        repeatContactsPerEnemyHistogram=dict(Counter(str(n) for i in valid for n in Counter(x for _,x in union[i]).values())),
        interpretation='사용당 한 명 이상·사용별 서로 다른 적 합·같은 적 반복 접촉을 분리. 두 타 모두 적중은 같은 적에게 두 단계를 맞힌 경우만 계산.')
    return row
