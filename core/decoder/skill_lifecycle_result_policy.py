"""Shared per-use completeness and user-authorized zero-positive policy.

Call after validating identity, ownership, event attribution and negative
closure. A positive contact can settle binary success before visual retirement;
it does not establish complete contact/target totals.
"""
from collections import Counter
from .skill_development_effect_metrics import development_policy, annotate_provisional_rate


def settle_cancelled_bounded_phase(unresolved, index, *, launch_completed,
                                  survives_parent_finish, upper_bound, observed_through):
    """Remove only the parent-cancellation veto after independent phase closure.

    Callers prove the native bound and complete event channel; overlapping,
    truncated or otherwise ambiguous producers must retain their own reason.
    This does not turn absent damage into a miss or synthesize a finish.
    """
    if (unresolved.get(index) == 'cast-not-normally-complete'
            and launch_completed and survives_parent_finish
            and upper_bound is not None and observed_through >= upper_bound):
        del unresolved[index]
        return True
    return False


def provisional_single_root_contact(roots,has_foreign_producer):
    """Opt-in estimate, never a verified attribution or a nearest-cast match."""
    policy=development_policy()
    if policy.get('enabled') and policy.get('lifecycleEstimatesEnabled') and len(roots)==1 and has_foreign_producer:
        return next(iter(roots))
    return None


def finalize_lifecycle_result(row, selected, unknown, *, incomplete_positive=(),
                              observed_positive=True):
    missing=[i for i in selected if i in unknown]
    row.update(perUseCompletenessTracked=True, observedCombatCastCount=len(selected),
               unresolvedCombatCastCount=len(missing),
               unresolvedCastReasons=dict(Counter(unknown[i] for i in missing)),
               incompleteUsesCountedAsMisses=False)
    if missing and not row['attemptCount']:
        row.update(status='unresolved-evidence',reason='no classified lifecycle use',
                   attemptCount=None,hitCount=None,hitRate=None)
    partial_positive=set(selected).intersection(incomplete_positive)
    if partial_positive:
        row.update(positiveBeforeRetirementCount=len(partial_positive),
                   targetCountsAreLowerBounds=True, completeTargetCountsAvailable=False)
    if not observed_positive or partial_positive:
        policy=development_policy()
        if not policy['enabled']:
            row.update(status='unresolved-evidence',reason='provisional lifecycle policy disabled',
                       attemptCount=None,hitCount=None,hitRate=None)
            return row
        row=annotate_provisional_rate(row,policy)
        row['perMatchPositiveSampleRequired']=False
    return row


def reviewed_rule_reuse_policy(gaps, required_packets, *streams):
    """Preselect zero-positive reuse from complete inputs, never after failure."""
    policy=development_policy()
    if (not policy['enabled'] or not policy.get('reuseReviewedCandidateRulesWithoutPositiveSamples')
        or gaps is None or any(stream is None for stream in streams)):
        return None
    if any(g.get('count',0) and any(str(g.get('packetName','')).startswith(name) for name in required_packets) for g in gaps):
        return None
    return policy


def annotate_reviewed_rule_reuse(row, policy, *, positive_gate_unsatisfied):
    if policy is None:return row
    row['perMatchPositiveSampleRequired']=False
    if not positive_gate_unsatisfied:return row
    row.setdefault('perUseCompletenessTracked',True)
    row.setdefault('unresolvedCombatCastCount',sum(row.get('unresolvedCastReasons',{}).values()))
    if row['unresolvedCombatCastCount'] and not row.get('attemptCount'):
        row.update(status='unresolved-evidence',reason='no classified lifecycle use',attemptCount=None,hitCount=None,hitRate=None)
    row=annotate_provisional_rate(row,policy)
    row.update(reviewedRuleReusedWithoutPositiveSample=True,verifiedCompletionCredit=False)
    return row
