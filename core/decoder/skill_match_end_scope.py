"""Limit eligible cast times to a recorded match end; retain all outcome facts."""
from .skill_ordered_match_end import ordered_winner_match_end
from .skill_wire_order import command_order


def match_end_combat_intervals(intervals, starts, terminals, gaps):
    end = ordered_winner_match_end(terminals, gaps)
    if end is None:
        return intervals, {'status': 'unresolved-end-evidence', 'applied': False}
    tick = end['tick']
    same = [s for s in starts if s.get('tick') == tick]
    sides = set()
    for start in same:
        order = command_order(start)
        if order is None or order == command_order(end):
            return intervals, {'status': 'unresolved-same-tick-order', 'applied': False, 'end': end}
        sides.add(order < command_order(end))
    if len(sides) > 1:
        return intervals, {'status': 'unresolved-mixed-same-tick-casts', 'applied': False, 'end': end}
    # Integer cast ticks permit this exact interval representation only when
    # every cast on the boundary tick belongs to the same side of the event.
    cutoff = tick + 1 if sides == {True} else tick
    result = {player: [[left, min(right, cutoff)] for left, right in spans
                       if left < min(right, cutoff)] for player, spans in intervals.items()}
    excluded = [s for s in starts if isinstance(s.get('tick'), int) and s['tick'] >= cutoff]
    return result, {'status': 'applied', 'applied': True, 'end': end,
                    'combatIntervalEndExclusive': cutoff, 'excludedStarts': excluded,
                    'authority': 'explicit-user-exclude-postmatch-casts',
                    'outcomeFactsTruncated': False, 'scopeChangeNotAccuracyFix': True}
