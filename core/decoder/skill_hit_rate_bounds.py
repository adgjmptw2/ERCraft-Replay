"""Conditional outcome range over explicitly counted unresolved binary casts."""
def unresolved_hit_rate_bounds(row):
    if row.get('unit')!='skill-cast' or row.get('perUseCompletenessTracked') is not True:
        return None
    n,h,u=(row.get(k) for k in ('attemptCount','hitCount','unresolvedCombatCastCount'))
    if any(type(v)is not int for v in (n,h,u)) or n<0 or not 0<=h<=n or u<=0:
        return None
    declared=row.get('observedCombatCastCount')
    if declared is not None and declared!=n+u:return None
    return dict(minimum=h/(n+u),maximum=(h+u)/(n+u),countedCastCount=n+u,
        classifiedCastCount=n,unresolvedCastCount=u,
        interpretation='Range conditional on current classified outcomes; unresolved casts remain unknown.',
        includesDevelopmentalClassifications=row.get('calculationConfidence')=='experimental',
        classifiedOutcomesAssumedCorrect=True,statisticalConfidenceInterval=False,
        unknownOutcomesFilled=False)
