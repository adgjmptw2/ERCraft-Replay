"""Recorded evasion targets; attacker and skill attribution are route-specific."""


def evasion_event_fact(tick, category, wire_order, decoded):
    if type(decoded.get('objectId')) is not int:
        raise ValueError('missing exact CmdEvasion target')
    return dict(event='CmdEvasion', tick=tick, objectId=decoded['objectId'],
                wireCategory=category, wireOrder=wire_order)
