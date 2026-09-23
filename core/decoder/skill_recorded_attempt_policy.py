"""Apply an explicit use-denominator policy to already excluded recorded casts.

This does not classify unknown uses, discover casts, infer phase execution, or
change any positive evidence. A tick-only exclusion cannot identify multiple
casts at one tick: ambiguous exclusion identities fail closed.
"""
from copy import deepcopy
from collections import Counter


POLICY_ID = 'recorded-excluded-uses-count-as-misses-v1'
_EXCLUSIONS = (
    ('provisionallyExcludedCancelledCastTicks', 'provisionallyExcludedCancelledCastCount'),
    ('provisionallyExcludedWinnerInterruptedCastTicks', 'provisionallyExcludedWinnerInterruptedCastCount'),
)
_EXTRA_EMPTY = {
    'contactDetailsByAttempt': [],
    'confirmedRegionContactsByAttempt': [],
    'tipEvidenceByAttempt': {'confirmedTip': False, 'regionUnresolved': False},
    'policySuccessByAttempt': None,
}


def _target_keys(neutral):
    if neutral:
        return ('distinctTargetsPerAttempt', 'distinctTargetFirstHitTicksPerAttempt',
                'distinctTargetsSummedAcrossAttempts', 'meanDistinctTargetsPerAttempt',
                'deduplicatedTargetContactEventCount')
    return ('distinctEnemyTargetsPerAttempt', 'distinctEnemyTargetFirstHitTicksPerAttempt',
            'distinctEnemyTargetsSummedAcrossAttempts', 'meanDistinctEnemyTargetsPerAttempt',
            'deduplicatedEnemyContactEventCount')


def _valid_outcomes(outcomes, attempts, hits):
    if not isinstance(outcomes, list) or len(outcomes) != attempts:
        raise ValueError('recorded attempt policy requires complete aligned outcomes')
    for o in outcomes:
        if (not isinstance(o, list) or len(o) != 4 or type(o[0]) is not int
                or type(o[1]) is not int or o[1] not in (0, 1)
                or type(o[2]) is not int or o[2] < o[0]
                or (o[1] == 0 and o[3] is not None)
                or (o[1] == 1 and (type(o[3]) is not int or o[3] < o[2]))):
            raise ValueError('recorded attempt policy received invalid outcome timing')
    if sum(o[1] for o in outcomes) != hits:
        raise ValueError('recorded attempt policy outcome hit count mismatch')


def _reconcile_stale_unknown_ticks(row, excluded, before):
    ticks = row.get('unresolvedCastTicks', []) or []
    if not any(t in excluded for t in ticks):
        return
    receipt = row.get('beforeDevelopmentCancellation')
    current = row.get('unresolvedCombatCastCount')
    previous = receipt.get('unresolvedCombatCastCount') if isinstance(receipt, dict) else None
    remaining = [t for t in ticks if t not in excluded]
    receipt_source = 'beforeDevelopmentCancellation'
    # Winner interruption has its own persisted receipt, not a cancellation
    # count receipt. Accept only its complete, zero-unknown stale tick set.
    # No equal-frame chronology is inferred from missing cast wire identities.
    if receipt is None:
        end = row.get('winnerEndEvidence')
        winner_ticks = row.get('provisionallyExcludedWinnerInterruptedCastTicks')
        order = end.get('wireOrder') if isinstance(end, dict) else None
        valid_winner = (
            type(current) is int and current == 0 and row.get('unresolvedCastReasons') == {}
            and isinstance(ticks, list) and all(type(t) is int for t in ticks)
            and len(ticks) == len(set(ticks)) and set(ticks) == set(excluded)
            and isinstance(winner_ticks, list) and sorted(winner_ticks) == sorted(excluded)
            and row.get('provisionallyExcludedWinnerInterruptedCastCount') == len(excluded)
            and not row.get('provisionallyExcludedCancelledCastTicks')
            and row.get('provisionallyExcludedCancelledCastCount', 0) == 0
            and isinstance(end, dict) and end.get('event') == 'CmdFinishGame'
            and end.get('finishGame') is True and type(end.get('rank')) is int and end['rank'] == 1
            and type(end.get('tick')) is int and all(t < end['tick'] for t in excluded)
            and end.get('wireCategory') == 'commands' and isinstance(order, list)
            and len(order) == 2 and all(type(n) is int and n >= 0 for n in order)
            and row.get('syntheticSkillFinishCreated') is False)
        if valid_winner:
            previous = len(ticks)
            receipt_source = 'winnerEndEvidence'
    if (not isinstance(ticks, list) or any(type(t) is not int for t in ticks)
            or len(set(ticks)) != len(ticks)
            or type(previous) is not int or type(current) is not int or current < 0
            or previous != len(ticks) or previous - current != len(excluded)
            or not set(excluded).issubset(ticks) or len(remaining) != current):
        raise ValueError('excluded tick is also unresolved without an exact cancellation receipt')
    evidence = row.get('unresolvedUseEvidence')
    if evidence is not None:
        if not isinstance(evidence, list):
            raise ValueError('unknown use evidence cannot validate stale tick cleanup')
        for use in evidence:
            if not isinstance(use, dict):
                raise ValueError('unknown use evidence cannot validate stale tick cleanup')
            at = use.get('startTick')
            nested = use.get('start')
            nested_tick = nested.get('tick') if isinstance(nested, dict) else None
            if at is None:
                at = nested_tick
            if (type(at) is not int or at not in remaining
                    or (nested_tick is not None and nested_tick != at)):
                raise ValueError('current unknown use evidence contradicts cancellation tick cleanup')
    # Only obsolete diagnostic ticks are repaired. The post-cancellation unknown
    # count/reasons/use evidence already carries the true remainder and stays so.
    before['unresolvedCastTicks'] = deepcopy(ticks)
    row['unresolvedCastTicks'] = remaining
    row['userPolicyStaleUnresolvedTickCleanup'] = dict(
        receiptSource=receipt_source,
        previousUnresolvedCombatCastCount=previous,
        unchangedUnresolvedCombatCastCount=current,
        removedTicks=sorted(excluded), remainingTicks=deepcopy(remaining))


def _finalize_local(row):
    excluded = []
    for ticks_key, count_key in _EXCLUSIONS:
        if ticks_key not in row and count_key not in row:
            continue
        ticks = row.get(ticks_key)
        if (not isinstance(ticks, list) or any(type(t) is not int for t in ticks)
                or type(row.get(count_key)) is not int or row[count_key] != len(ticks)):
            raise ValueError('recorded attempt policy exclusion ticks/count mismatch')
        excluded.extend(ticks)
    if not excluded:
        return row
    if row.get('unit') != 'skill-cast':
        raise ValueError('recorded attempt policy requires a skill-cast denominator')
    if row.get('fallbackUsed') is not False:
        raise ValueError('recorded attempt policy requires an explicit non-fallback source row')
    if len(set(excluded)) != len(excluded):
        raise ValueError('ambiguous same-tick exclusions require exact cast identities; never deduplicate')
    if row.get('userPolicyCountedMissCount') is not None:
        raise ValueError('new exclusions attached to an already finalized policy row')

    before = {k: deepcopy(v) for k, v in row.items() if k in {
        'status', 'reason', 'attemptCount', 'hitCount', 'hitRate', 'denominatorStatus',
        'verifiedCombatCastCount', 'provisionalCombatCastCount', 'hitRateScope',
        'wholeCombatHitRate', 'interpretation', 'cancelledWithoutConfirmedHitCountedAsMiss',
        'cancelledExclusionAssumption', 'cancellationPolicyAssumption', 'winnerInterruptionAssumption',
    } or any(k in pair for pair in _EXCLUSIONS)}
    attempts, hits = row.get('attemptCount'), row.get('hitCount')
    outcomes = row.get('outcomes')
    unavailable = attempts is None
    if unavailable:
        if (hits is not None or row.get('hitRate') is not None or outcomes not in (None, [])
                or type(row.get('unresolvedCombatCastCount')) is not int
                or row['unresolvedCombatCastCount'] < 0):
            raise ValueError('unresolved row lacks an exact empty classified denominator and unknown count')
        attempts, hits, outcomes = 0, 0, []
    elif (type(attempts) is not int or attempts < 0 or type(hits) is not int
            or not 0 <= hits <= attempts
            or row.get('hitRate') != (round(hits / attempts, 6) if attempts else None)):
        raise ValueError('recorded attempt policy received inconsistent numerical denominator')
    if outcomes is None and attempts == 0:
        outcomes = []
    _valid_outcomes(outcomes, attempts, hits)
    old_ticks = Counter(o[0] for o in outcomes)
    if any(old_ticks[t] > 1 for t in excluded):
        raise ValueError('excluded tick overlaps multiple actual outcomes; exact identity required')
    restored = sorted(t for t in excluded if not old_ticks[t])
    already_counted = sorted(t for t in excluded if old_ticks[t])
    _reconcile_stale_unknown_ticks(row, excluded, before)

    neutral = bool(row.get('targetCohort'))
    counts_key, ticks_key, sum_key, mean_key, contact_key = _target_keys(neutral)
    if neutral and any(row.get(k) is not None for k in _target_keys(False)):
        raise ValueError('neutral target cohort cannot use enemy-labelled target fields')
    target_arrays = {counts_key, ticks_key}
    for key, value in row.items():
        if (key.endswith(('ByAttempt', 'PerAttempt')) and value is not None
                and not key.startswith('mean')
                and key not in target_arrays and key not in _EXTRA_EMPTY):
            raise ValueError('unsupported aligned attempt evidence: ' + key)
    counts, target_ticks = row.get(counts_key), row.get(ticks_key)
    target_available = counts is not None
    if unavailable and row.get('reportMultiTarget') is not False:
        # Existing _result([], ...) fields may survive a route's unavailable
        # status. Require those to be empty, never turn hidden positives to zero.
        if counts not in (None, []) or target_ticks not in (None, []):
            raise ValueError('unresolved row contains classified target evidence')
        for key in (sum_key, mean_key, contact_key, 'multiTargetAttemptCount', 'multiTargetAttemptRate'):
            if row.get(key) not in (None, 0):
                raise ValueError('unresolved row contains nonzero target aggregates')
        counts, target_ticks, target_available = [], [], True
    if target_available:
        if (not isinstance(counts, list) or len(counts) != attempts
                or any(type(n) is not int or n < 0 or bool(n) != bool(o[1]) for n, o in zip(counts, outcomes))):
            raise ValueError('per-attempt targets disagree with existing outcomes')
        if not unavailable and (row.get(sum_key) != sum(counts)
                or row.get('multiTargetAttemptCount') != sum(n >= 2 for n in counts)
                or row.get(mean_key) != (round(sum(counts) / attempts, 6) if attempts else None)
                or row.get('multiTargetAttemptRate') != (round(sum(n >= 2 for n in counts) / attempts, 6) if attempts else None)):
            raise ValueError('target aggregate mismatch before recorded attempt policy')
        if target_ticks is not None and (not isinstance(target_ticks, list) or len(target_ticks) != attempts
                or any(not isinstance(ts, list) or len(ts) != n or ts != sorted(ts)
                    or any(type(t) is not int or t < o[2] for t in ts)
                    or (ts[0] if ts else None) != o[3]
                    for ts, n, o in zip(target_ticks, counts, outcomes))):
            raise ValueError('per-target hit timing mismatch before recorded attempt policy')
    elif any(row.get(k) is not None for k in (ticks_key, sum_key, mean_key, 'multiTargetAttemptCount', 'multiTargetAttemptRate')):
        raise ValueError('aggregate targets without aligned per-attempt target evidence')

    aligned = {}
    if target_available:
        aligned[counts_key] = (counts, 0)
        if target_ticks is not None:
            aligned[ticks_key] = (target_ticks, [])
    for key, empty in _EXTRA_EMPTY.items():
        if key not in row or row[key] is None:
            continue
        if not isinstance(row[key], list) or len(row[key]) != attempts:
            raise ValueError('unaligned auxiliary attempt evidence: ' + key)
        aligned[key] = (row[key], empty)
    merged = [(o, index) for index, o in enumerate(outcomes)]
    merged.extend(([tick, 0, tick, None], None) for tick in restored)
    # Stable sorting retains multiple genuinely recorded casts at a shared tick
    # and reorders every parallel array using the same original ordinal.
    merged.sort(key=lambda entry: (entry[0][0], entry[0][2]))
    row['outcomes'] = [o for o, _ in merged]
    for key, (values, empty) in aligned.items():
        row[key] = [deepcopy(empty if index is None else values[index]) for _, index in merged]
    new_attempts = attempts + len(restored)
    row.update(attemptCount=new_attempts, hitCount=hits,
               hitRate=round(hits / new_attempts, 6) if new_attempts else None)
    if target_available:
        total = sum(counts)
        multi = sum(n >= 2 for n in counts)
        row.update({sum_key: total, mean_key: round(total / new_attempts, 6) if new_attempts else None,
                    'multiTargetAttemptCount': multi,
                    'multiTargetAttemptRate': round(multi / new_attempts, 6) if new_attempts else None})
        if unavailable:
            row[contact_key] = 0
            row.setdefault('contactCountMeaning', 'unique evidence tick/target per attempt; not inferred damage ticks')
    elif unavailable:
        for key in (*_target_keys(neutral), 'multiTargetAttemptCount', 'multiTargetAttemptRate'):
            row[key] = None
    for pair in _EXCLUSIONS:
        for key in pair:
            row.pop(key, None)
    for key in ('cancelledExclusionAssumption', 'cancellationPolicyAssumption', 'winnerInterruptionAssumption'):
        row.pop(key, None)
    row.update(beforeRecordedAttemptPolicy=before,
        userRecordedAttemptPolicy=POLICY_ID,
        userPolicyCountedMissTicks=restored, userPolicyCountedMissCount=len(restored),
        userPolicyAlreadyCountedOutcomeTicks=already_counted,
        userPolicyAlreadyCountedOutcomeCount=len(already_counted),
        userPolicyUnknownUsesReclassified=False,
        cancelledWithoutConfirmedHitCountedAsMiss=True,
        status='calculable-experimental' if new_attempts else 'no-combat-sample',
        calculationConfidence='experimental', verifiedCompletionCredit=False,
        fullRequestedMetricComplete=False, verifiedCombatCastCount=0,
        provisionalCombatCastCount=new_attempts, binaryCastSuccessComplete=False,
        recordedCastOutcomesComplete=False, wholeCombatHitRate=None,
        hitRateScope='classified-uses-including-user-policy-recorded-misses',
        denominatorStatus='recorded-excluded-uses-included-by-explicit-user-policy',
        reason=None, fallbackUsed=False,
        interpretation='명시된 사용자 정책으로 기존 제외 사용을 미적중 분모에 포함. 기존 적중과 미확인은 보존하며 새 사용이나 결과를 추정하지 않음.')
    row.setdefault('developmentLabel', '개발 중 · 사용자 사용 분모 정책 적용')
    return row


def finalize_recorded_attempt_policy(row, *, enabled=False):
    """Return a separate finalized row; disabled policy preserves the input.

    Each nested phase may use only its own exclusion lists. A parent's list is
    never a phase-use list. Unknown counters/reasons/evidence are untouched.
    """
    if enabled is not True:
        return row
    result = _finalize_local(deepcopy(row))
    phases = row.get('phaseMetrics')
    if phases is not None:
        if not isinstance(phases, dict) or any(not isinstance(child, dict) for child in phases.values()):
            raise ValueError('phaseMetrics must map phase names to their own metric rows')
        result['phaseMetrics'] = {name: finalize_recorded_attempt_policy(child, enabled=True)
                                  for name, child in phases.items()}
    return result
