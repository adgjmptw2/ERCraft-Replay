"""Opt-in exact ordered lifetimes, retaining an unfinished final use separately.

No overlap/orphan recovery and no invented end at the next cast. Existing
calculators keep their strict all-or-nothing contract until explicitly adapted.
"""
from collections import defaultdict
try:
    from .skill_wire_order import command_order
except ImportError:
    from skill_wire_order import command_order


def normal_completion_partition(lifetimes, finishes, player, selected):
    """Partition exact uses for routes whose denominator requires NotCancel.

    A paired finish establishes a boundary, not successful skill execution.
    Keep cancelled/missing-reason uses in attribution, but out of both counts.
    """
    ends=defaultdict(list)
    for event in finishes:
        if event.get('playerObjectId')==player:
            ends[event.get('skillIdCode'),event['tick']].append(event)
    valid=[];unknown={}
    for i in selected:
        start,end=lifetimes[i]
        matches=ends[start['skillIdCode'],end]
        if len(matches)!=1 or type(matches[0].get('reason')) is not int:
            unknown[i]='normal-finish-reason-unavailable'
        elif matches[0]['reason']!=0:
            unknown[i]='cast-not-normally-complete'
        else:
            valid.append(i)
    return valid,unknown


def retain_cancelled_hits(unknown, contacts, lifetimes, finishes, player):
    """A later gameplay cancellation cannot undo an exclusively proven hit.

    Call only after producer, owner, target and unique-parent validation.
    Do not admit ambiguous hits, missing finishes, replay termination, or
    negative outcomes. A recovered binary hit does not prove target totals.
    """
    ends=defaultdict(list)
    for event in finishes:
        if event.get('playerObjectId')==player:
            ends[event.get('skillIdCode'),event['tick']].append(event)
    recovered=[]
    for i,reason in list(unknown.items()):
        if reason!='cast-not-normally-complete' or not contacts[i]:continue
        start,end=lifetimes[i];matches=ends[start['skillIdCode'],end]
        if len(matches)==1 and type(matches[0].get('reason')) is int and matches[0]['reason'] in set(range(1,15))|{16,17}:
            del unknown[i];recovered.append(i)
    return recovered


def annotate_cancelled_hits(row, recovered, lifetimes, selected):
    """Keep binary success separate from unclosed target-count completeness."""
    included=sorted(set(recovered)&set(selected))
    if included:
        row.pop('finishReasonRequired',None)
        row.update(cancelledHitCastCount=len(included),
            cancelledHitCastTicks=[lifetimes[i][0]['tick'] for i in included],
            normalFinishRequiredForNegativeOutcome=True,
            cancelledWithoutConfirmedHitCountedAsMiss=False,
            targetCountsAreLowerBounds=True,completeTargetCountsAvailable=False,
            recordedCastOutcomesComplete=not bool(row.get('unresolvedCombatCastCount')),
            fullRequestedMetricComplete=False)
    return row


def ordered_cast_records(starts,finishes,player,*,allow_same_tick_finishes=False):
    # Opt-in callers must retain the actual finish event. Tick-only consumers
    # cannot distinguish two finishes at the same tick and keep the old guard.
    identities={s['skillIdCode'] for s in starts}
    ends=[f for f in finishes if f['playerObjectId']==player and f['skillIdCode'] in identities]
    if any(command_order(x) is None for x in [*starts,*ends]):
        return None,'사용별 부분 완결성에는 정확한 시전/종료 명령 순서가 필요함'
    streams=defaultdict(list)
    for kind,rows in [('start',starts),('finish',ends)]:
        for row in rows:streams[row['skillIdCode']].append((command_order(row),kind,row))
    records=[]
    for stream in streams.values():
        if len({at for at,_,_ in stream})!=len(stream):return None,'중복 명령 순서'
        if not allow_same_tick_finishes and len({f['tick'] for _,kind,f in stream if kind=='finish'})!=sum(kind=='finish' for _,kind,_ in stream):
            return None,'같은 tick의 복수 종료는 현재 수명 표현으로 구분 불가'
        active=None;last_tick=None
        for _,kind,event in sorted(stream,key=lambda x:x[0]):
            if last_tick is not None and event['tick']<last_tick:return None,'시각과 명령 순서 불일치'
            last_tick=event['tick']
            if kind=='start':
                if active is not None:return None,'종료 전에 같은 스킬의 다음 시전이 시작됨'
                active=event
            else:
                if active is None:return None,'시작 없는 종료'
                records.append(dict(start=active,finish=event,
                    complete=event['reason']!=15,reason='replay-end-is-not-gameplay-finish' if event['reason']==15 else None))
                active=None
        if active is not None:records.append(dict(start=active,finish=None,complete=False,reason='open-final-cast'))
    return sorted(records,key=lambda r:command_order(r['start'])),None


def partial_cast_windows(starts, finishes, player):
    """Preserve open ordered uses; None is an unknown end, never a closure.

    Select the clock representation before evaluation. Legacy tick inputs
    retain their strict contract; no failed calculation is retried.
    """
    identities={s['skillIdCode'] for s in starts}
    relevant=[f for f in finishes if f.get('playerObjectId')==player and f.get('skillIdCode') in identities]
    if any(command_order(e) is None for e in [*starts,*relevant]):
        from .requested_skill_scope import exact_cast_lifetimes
        lives,reason=exact_cast_lifetimes(starts,finishes,player)
        return lives,{},reason
    records,reason=ordered_cast_records(starts,finishes,player)
    if reason:return None,{},reason
    return ([(r['start'],r['finish']['tick'] if r['complete'] else None) for r in records],
            {i:r['reason'] for i,r in enumerate(records) if not r['complete']},None)


def partial_window_contains(start, end, event, lookup):
    from .skill_wire_order import event_within_cast
    if end is not None:return event_within_cast(start,end,event,lookup)
    if event['tick']!=start['tick']:return event['tick']>start['tick']
    left,at=command_order(start),command_order(event)
    return left is None or at is None or left<=at


def closed_cancelled_no_contact_indices(records, contacts, unknown, *, cancellation_reason,
                                         pending_continuations, gaps, required_packets, enabled):
    """Opt-in developmental negatives for a synchronous, non-lingering producer.

    The caller must prove no delayed primary producer exists and supply all
    continuations. This does not apply to projectiles, summons, replay-end
    truncation, missing recordings, or other unresolved attribution reasons.
    """
    if not enabled or gaps is None or not required_packets:
        return []
    if any(g.get('count',0) and g.get('packetName') in required_packets for g in gaps):
        return []
    admitted=[]
    for i,reason in unknown.items():
        r=records[i];finish=r.get('finish')
        if reason!=cancellation_reason or not r.get('complete') or not finish:
            continue
        if finish.get('reason') not in set(range(1,15))|{16,17}:
            continue
        start_order=command_order(r['start']);end_order=command_order(finish)
        if start_order is None or end_order is None or end_order<=start_order:
            continue
        if contacts[i] or i in pending_continuations:
            continue
        admitted.append(i)
    return admitted


def user_cancelled_no_recorded_contact_indices(records, contacts, unknown, *, cancellation_reason,
                                               pending_continuations, gaps):
    """Explicit user denominator rule, NOT a proof of native completion or absence.

    Only the sole cancellation gate can be removed. Missing or damaged streams,
    ambiguous attribution and unfinished continuations remain unresolved.
    """
    import json
    from pathlib import Path
    policy=json.loads((Path(__file__).resolve().parents[1]/'data/user-hit-rate-scope-20260912.json').read_text(encoding='utf-8'))['recordedUseMissPolicy']
    if policy.get('enabled') is not True or gaps is None or any(g.get('count',0) for g in gaps):return []
    allowed=set(range(1,15))|{16,17}
    admitted=[]
    for i,reason in unknown.items():
        r=records[i];start=r['start'];finish=r.get('finish')
        if reason!=cancellation_reason or not r.get('complete') or not finish:continue
        if finish.get('reason') not in allowed or contacts[i] or i in pending_continuations:continue
        left,right=command_order(start),command_order(finish)
        if left is None or right is None or left>=right or start['tick']>finish['tick']:continue
        if start.get('playerObjectId') is None or start.get('playerObjectId')!=finish.get('playerObjectId'):continue
        if start.get('skillIdCode') is None or start.get('skillIdCode')!=finish.get('skillIdCode'):continue
        admitted.append(i)
    return admitted
