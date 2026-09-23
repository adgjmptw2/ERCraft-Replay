"""Use recorded command order only to resolve equal-tick cast boundaries."""
from collections import defaultdict


class IndexedFinishEvents(list):
    """Calculation-local index; callers must not mutate this event sequence."""
    def __init__(self, events):
        super().__init__(events)
        self.by_player=defaultdict(list)
        for event in self:self.by_player[event.get('playerObjectId')].append(event)


def player_finishes(finishes, player):
    return finishes.by_player.get(player,[]) if isinstance(finishes,IndexedFinishEvents) else finishes


def competing_manual_starts(starts,catalog,selected_groups,player):
    """Keep all variants together for the caller's wire-identity pairing.

    CmdFinishSkill carries skillId, not skillGroup. Splitting a shared wire
    identity by base/reinforced group creates fictitious orphan finishes.
    """
    return [s for s in starts if s.get('playerObjectId',player)==player
        and s['skillGroup'] not in selected_groups
        and catalog['skillGroups'].get(str(s['skillGroup']),{}).get('family')
            in {'Active1','Active2','Active3','Active4'}]


def command_order(event):
    order=event.get('wireOrder')
    if (event.get('wireCategory')=='commands' and isinstance(order,list) and len(order)==2
            and type(order[0]) is int and order[0]>=0
            and type(order[1]) is int and order[1]>=0):
        return tuple(order)
    return None


def finish_lookup(finishes,player):
    output=defaultdict(list)
    for event in player_finishes(finishes,player):
        if event.get('playerObjectId')==player:
            output[event.get('skillIdCode'),event['tick']].append(event)
    return output


def event_within_cast(start,end_tick,event,finishes):
    if not start['tick']<=event['tick']<=end_tick:return False
    if start['tick']<event['tick']<end_tick:return True
    candidates=finishes.get((start['skillIdCode'],end_tick),[])
    if len(candidates)!=1:return True  # Preserve the conservative tick overlap.
    left,right,at=command_order(start),command_order(candidates[0]),command_order(event)
    if left is None or right is None or at is None or left>right:return True
    # Never extend the numeric lifetime. Equal-tick order can only rule out a
    # start that has not happened yet or a cast that has already finished.
    return left<=at<=right
