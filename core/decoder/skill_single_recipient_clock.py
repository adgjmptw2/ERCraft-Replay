"""Narrow recorded event clocks only within one unmerged recipient stream."""
from collections import defaultdict

try:
    from .skill_state_clock_constraints import event_clock_bounds
except ImportError:
    from skill_state_clock_constraints import event_clock_bounds

RULE = 'schema/single-recipient-command-order-v1.json'


def refine_clock_bounds(constraints, events, routing, match_key):
    if routing.get('matchKey') != match_key or routing.get('privateRouting') is not True:
        raise ValueError('routing source binding missing or different')
    masks = {}
    for fact in routing['facts']:
        key = fact['packetId'], tuple(fact['wireOrder'])
        if key in masks:
            raise ValueError('duplicate routing identity')
        masks[key] = fact['targetMask']

    def mask(row):
        value = masks.get((row['packetId'], tuple(row['wireOrder'])))
        return value if type(value) is int and 0 < value <= 0xffffffff and value & (value - 1) == 0 else None

    indexed = {side: defaultdict(list) for side in ('lower', 'upper')}
    for side in indexed:
        for row in constraints[side]:
            indexed[side][row['wireOrder'][0]].append(row)
    results = event_clock_bounds(constraints, events)
    for result in results:
        event = result['event']
        recipient = mask(event)
        if recipient is None or event.get('wireCategory') != 'commands':
            continue
        changes = []
        for side in ('lower', 'upper'):
            candidates = [r for r in indexed[side][event['wireOrder'][0]]
                          if mask(r) == recipient and
                          (r['wireOrder'][1] < event['wireOrder'][1] if side == 'lower'
                           else r['wireOrder'][1] > event['wireOrder'][1])]
            if not candidates:
                continue
            best = (max if side == 'lower' else min)(candidates, key=lambda r: r[side])
            old = result[side]
            if old is None or (best[side] > old if side == 'lower' else best[side] < old):
                result[side] = best[side]
                result[side + 'Witness'] = best
                changes.append(dict(side=side, witnessPacketId=best['packetId']))
        if changes:
            result['singleRecipientRefinement'] = dict(rule=RULE, matchKey=match_key,
                                                       boundaries=changes)
            lo, hi = result['lower'], result['upper']
            result['status'] = ('missing-clock-boundary' if lo is None or hi is None
                                else 'clock-conflict' if lo > hi else 'bounded')
    return results


def retained_clock_bounds(source, constraints, events, match_key):
    """Recover only masks that could tighten one requested event boundary."""
    try:
        from .corpus_command_routing import retained_command_routing
    except ImportError:
        from corpus_command_routing import retained_command_routing
    base = event_clock_bounds(constraints, events)
    records = defaultdict(list)
    for bound in base:
        if bound['event'].get('wireCategory') == 'commands':
            records[bound['event']['wireOrder'][0]].append(bound)
    selected = set()
    for side in ('lower', 'upper'):
        for witness in constraints[side]:
            for bound in records.get(witness['wireOrder'][0], ()):
                event = bound['event']
                ordered = (witness['wireOrder'][1] < event['wireOrder'][1] if side == 'lower'
                           else witness['wireOrder'][1] > event['wireOrder'][1])
                tighter = (bound[side] is None or
                           (witness[side] > bound[side] if side == 'lower' else witness[side] < bound[side]))
                if ordered and tighter:
                    selected.update((event['packetId'], witness['packetId']))
    if not selected:
        return base
    ids = sorted(selected)
    routing = dict(matchKey=match_key, privateRouting=True, facts=[])
    for offset in range(0, len(ids), 1000):
        chunk = retained_command_routing(source, ids[offset:offset + 1000])
        if chunk['matchKey'] != match_key:
            raise ValueError('routing source binding mismatch')
        routing['facts'].extend(chunk['facts'])
    return refine_clock_bounds(constraints, events, routing, match_key)
