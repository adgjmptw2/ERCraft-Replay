"""Separate an arrival-created summon phase from its parent projectile hit."""
from .skill_wire_order import command_order
from .skill_attempt_timing import exact_outcome
from .skill_projectile_active_end import projectile_active_end_records
from .requested_skill_hit_rates import _result,_unavailable
from .skill_lifecycle_result_policy import finalize_lifecycle_result
from .skill_development_cancellation import annotate_provisional


def arrival_summon_phase(spec,records,assigned,*,summons,terminals,actions,damages,
                         player,teams,intervals,gaps,effect_rows,summon_rows,profile,parent_unknown=None,
                         game_terminals=None):
    fail=lambda why:_unavailable(spec,why)
    required={'CmdSpawn','CmdProjectileArrived','CmdDestroyDelayStart','CmdDestroy','CmdDamage','CmdPlaySkillAction'}
    if any(x is None for x in [summons,terminals,actions,damages,gaps,effect_rows,summon_rows]):return fail('followup phase streams missing')
    if any(g.get('count',0) and g.get('packetName') in required for g in gaps):return fail('followup phase stream gap')
    if not any(r.get('code')==profile['effect'] and r.get('effectPrefabName')==profile['effectPrefab'] for r in effect_rows):return fail('followup effect identity mismatch')
    if not any(r.get('code')==profile['summon'] and r.get('prefabPath')==profile['summonPrefab'] and r.get('useAttackerType')=='Owner' for r in summon_rows):return fail('followup summon identity mismatch')
    owned=[s for s in summons if s.get('ownerObjectId')==player and s.get('summonCode')==profile['summon']]
    arrivals=[t for t in terminals if t.get('event')=='CmdProjectileArrived']
    ends=projectile_active_end_records(terminals)
    markers=[a for a in actions if a.get('sourceObjectId')==player and a.get('skillIdCode')==profile['wire'] and a.get('actionNo')==profile['action']]
    parents={};unknown={};hits={};timings={};links=[];linked_events=set()
    for j,s in enumerate(owned):
        possible=[i for i,shot in assigned.items() if any(t['objectId']==shot['projectileObjectId'] and t['tick']==s['tick'] for t in arrivals)]
        if len(possible)!=1:return fail('arrival-created summon parent is not unique')
        i=possible[0]
        if i in parents:return fail('multiple followup summons for one parent require a different phase unit')
        parents[i]=s;hits[i]=set();end=ends.get(s['objectId'],{})
        if not end.get('complete') or end['endTick']<s['tick']:
            unknown[i]='followup-summon-activity-end-missing';continue
        mm=[m for m in markers if m['tick']==end['endTick']]
        same_end=[z for z in owned if ends.get(z['objectId'],{}).get('endTick')==end['endTick']]
        if len(mm)!=1 or len(same_end)!=1 or command_order(mm[0]) is None:
            unknown[i]='followup-explosion-action-not-unique';continue
        marker=mm[0];timings[i]=marker['tick'];phase_events={id(marker)}
        for d in damages:
            if d.get('attackerObjectId')!=player or d.get('effectCode')!=profile['effect'] or d['tick']!=marker['tick']:continue
            if command_order(d) is None or command_order(d)<=command_order(marker):
                unknown[i]='followup-damage-command-order-conflict';continue
            phase_events.add(id(d))
            target=d['targetObjectId']
            if target in teams and teams[target]!=teams[player]:hits[i].add((d['tick'],target))
        links.append(dict(castTick=records[i]['start']['tick'],projectileObjectId=assigned[i]['projectileObjectId'],
            summonObjectId=s['objectId'],summonTick=s['tick'],explosionTick=marker['tick'],actionWireOrder=marker['wireOrder']))
        if i not in unknown:linked_events.update(phase_events)
    import json
    from pathlib import Path
    policy=json.loads((Path(__file__).resolve().parents[1]/'data/user-hit-rate-scope-20260912.json').read_text(encoding='utf8'))['recordedUseMissPolicy']['enabled'] is True
    selected=[i for i in (range(len(records)) if policy else parents)
              if any(a<=records[i]['start']['tick']<b for a,b in intervals)]
    policy_evidence=[]
    from .skill_ordered_match_end import ordered_winner_match_end
    match_end=ordered_winner_match_end(game_terminals,gaps)
    unlinked_events=[e for e in [*markers,*[d for d in damages
        if d.get('attackerObjectId')==player and d.get('effectCode')==profile['effect']]]
        if id(e) not in linked_events]
    for i in selected:
        if i in parents:continue
        r=records[i];start=r['start'];finish=r.get('finish');shot=assigned.get(i)
        end=ends.get(shot['projectileObjectId'],{}) if shot else {}
        winner_closed=bool(not end and shot and finish and match_end
            and command_order(shot) is not None and command_order(finish) is not None
            and command_order(shot)<command_order(match_end) and command_order(finish)<=command_order(match_end)
            and shot['tick']<=match_end['tick'] and finish['tick']<=match_end['tick'])
        why=None
        if parent_unknown is None:why='parent-closure-ledger-missing'
        elif i in parent_unknown:why='parent-'+parent_unknown[i]
        elif any(g.get('count',0) for g in gaps):why='parent-use-stream-gap'
        elif (not r.get('complete') or not finish or finish.get('reason') not in set(range(15))|{16,17}
              or command_order(start) is None or command_order(finish) is None
              or command_order(start)>=command_order(finish) or start['tick']>finish['tick']):
            why='parent-use-finish-unresolved'
        elif not shot:why='parent-projectile-emission-missing'
        elif not winner_closed and (not end.get('complete') or end['endTick']<shot['tick']):why='parent-projectile-activity-end-missing'
        elif any(e['tick']>=start['tick'] for e in unlinked_events):why='unlinked-followup-event-may-belong-to-parent'
        if why:
            unknown[i]=why;continue
        hits[i]=set()
        policy_evidence.append(dict(startTick=start['tick'],startOrder=list(command_order(start)),
            finishTick=finish['tick'],finishOrder=list(command_order(finish)),finishReason=finish['reason'],
            projectileObjectId=shot['projectileObjectId'],projectileEndTick=end.get('endTick'),
            closureKind='actual-winner-match-end' if winner_closed else 'recorded-projectile-end',
            winnerMatchEndEvidence=dict(match_end) if winner_closed else None,
            phaseExecutionObserved=False,denominatorTimeSource='recorded-parent-use'))
    valid=[i for i in selected if i not in unknown]
    row=_result(spec,[hits[i] for i in valid],'arrival-owned-summon-and-recorded-explosion-phase',cast_ticks=[records[i]['start']['tick'] for i in valid])
    row['outcomes']=[exact_outcome(records[i]['start']['tick'],
        parents[i]['tick'] if i in parents else records[i]['start']['tick'],hits[i]) for i in valid]
    row=finalize_lifecycle_result(row,selected,unknown)
    row.update(phase='scroll-explosion',label='강화 Q 두루마리 폭발',
        denominatorMeaning=('실제 부모 스킬 사용 기준; 기록이 완결된 미발동은 사용자 정의 실패' if policy else
            '실제로 생성된 두루마리의 부모 시전; 생성되지 않은 두루마리는 폭발 실패가 아님'),
        userPolicyUnactivatedPhaseMissEvidence=policy_evidence,
        userPolicyUnactivatedPhaseMissCount=len(policy_evidence),
        unresolvedCastTicks=[records[i]['start']['tick'] for i in selected if i in unknown],
        sourceLinks=links,actualFollowupSummonCount=len(owned),projectileCollisionCountedAsExplosion=False,
        effectCodes=[profile['effect']],summonCodes=[profile['summon']],fixedDelayUsed=False,
        evidenceReview='deliverables/arda-q-scroll-phase-v1.json')
    return annotate_provisional(row,'정적 생성 경로와 같은 소유자의 유일한 도착·소환·종료·폭발 행동을 연결. 명시적 부모 ID가 없는 소환 연결은 잠정 귀속.')
