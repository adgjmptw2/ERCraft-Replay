"""Project the user's Sua Q/RQ hit-only scope without changing parent evidence."""


def project_sua_any_hit_scope(row):
    if row.get('skillGroup') not in (1028200, 1028510) or row.get('mode') != 'any':
        return row
    result = dict(row)
    result.update(phaseScope='any-hit-only', phaseMetrics={},
                  excludedPhaseMetrics=['bookmarkDamage', 'center', 'bookmarkStun'],
                  phaseExclusionAuthority='explicit-user-sua-hit-only-rule',
                  phaseExclusionIsAccuracyFix=False,
                  centerHitRateStatus='not-requested', bookmarkStunStatus='not-requested')
    for key in ('detailReason', 'detailedRequirementsComplete', 'regionEvidence'):
        result.pop(key, None)
    return result
