"""Preserve timings from already attributed attempts, without new attribution."""


def exact_outcome(scope_tick, attempt_tick, contacts):
    first_hit = min((tick for tick, _ in contacts), default=None)
    if (type(scope_tick) is not int or type(attempt_tick) is not int or
            attempt_tick < scope_tick or
            (first_hit is not None and first_hit < attempt_tick)):
        raise ValueError('attributed attempt has inconsistent recorded timing')
    return [scope_tick, int(bool(contacts)), attempt_tick, first_hit]


def projectile_outcomes(unit, uses):
    """uses: (scope tick, [(spawn tick, exact contacts)], explicit cancellation).

    A cancelled cast is a use at its recorded start, with no projectile emitted.
    A cast with emitted objects begins its attack at the first actual emission.
    """
    if unit not in {'skill-cast', 'projectile-shot'}:
        raise ValueError('projectile timing requires a cast or shot denominator')
    result = []
    for cast_tick, shots, cancelled in uses:
        if unit == 'projectile-shot':
            result.extend(exact_outcome(cast_tick, tick, hits) for tick, hits in shots)
        elif shots:
            hits = set().union(*(hits for _, hits in shots))
            result.append(exact_outcome(cast_tick, min(t for t, _ in shots), hits))
        elif cancelled:
            result.append(exact_outcome(cast_tick, cast_tick, set()))
        else:
            raise ValueError('unemitted cast has no explicit cancellation')
    return result
