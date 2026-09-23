"""Recorded winner result followed by its finish notification, never EOF."""
try:
    from .skill_wire_order import command_order as order
except ImportError:
    from skill_wire_order import command_order as order

def game_terminal_fact(event,tick,category,wire_order,decoded):
    if event not in ('CmdFinishGame','CmdFinishGameResult'):
        raise ValueError('not a game terminal command')
    row=dict(event=event,tick=tick,wireCategory=category,wireOrder=wire_order)
    if event=='CmdFinishGame':
        if type(decoded.get('finishGame')) is not bool or type(decoded.get('rank')) is not int:
            raise ValueError('missing exact game finish fields')
        row.update(finishGame=decoded['finishGame'],rank=decoded['rank'])
    # Account identities and the result payload are deliberately omitted.
    return row

def ordered_winner_match_end(events,gaps):
    if events is None or gaps is None:return None
    if any(g.get('count',0) and g.get('packetName') in ('CmdFinishGame','CmdFinishGameResult') for g in gaps):return None
    pairs=[]
    for end in events:
        if end.get('event')!='CmdFinishGame' or end.get('finishGame') is not True or type(end.get('rank')) is not int or end['rank']!=1:continue
        key=order(end)
        if key is None:continue
        preceding=[r for r in events if r.get('event')=='CmdFinishGameResult' and r.get('tick')==end.get('tick') and order(r)==(key[0],key[1]-1)]
        if len(preceding)==1:pairs.append(end)
    # Conflicting end frames are not collapsed to the last observed frame.
    if not pairs or len({p['tick'] for p in pairs})!=1:return None
    return max(pairs,key=order)


def precedes_recorded_match_end(event, game_end):
    """Tick chronology is sufficient across frames; same-frame needs wire order.

    The boundary must already have passed ordered_winner_match_end validation.
    This never treats EOF, a delay timer, or an inferred finish as a match end.
    """
    tick=event.get('tick');end_tick=game_end.get('tick')
    if type(tick) is not int or type(end_tick) is not int or tick>end_tick:
        return False
    if tick<end_tick:return True
    start_order=order(event);end_order=order(game_end)
    return start_order is not None and end_order is not None and start_order<end_order
