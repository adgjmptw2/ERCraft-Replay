"""Recognize a recorded non-player servant without inferring its team."""
try:
    from .skill_wire_order import command_order
except ImportError:
    from skill_wire_order import command_order


def preceding_servant_records(objects, target, event, gaps):
    # Exact ObjectType 11 is SummonServant (see skill_summon_ownership).
    # Other summon types are deliberately outside this rule.
    order = command_order(event)
    if type(target) is not int or target <= 0 or order is None or objects is None or gaps is None:
        return None
    if any(g.get('count', 0) and g.get('packetName') in {'CmdSpawn', 'CmdDestroy'} for g in gaps):
        return None
    selected = [o for o in objects if o.get('objectId') == target]
    if any(command_order(o) is None or command_order(o) == order for o in selected):
        return None
    preceding = [o for o in selected if command_order(o) < order]
    if not preceding or {o.get('objectType') for o in preceding} != {11}:
        return None
    if any(type(o.get('tick')) is not int or o['tick'] > event['tick'] for o in preceding):
        return None
    return [dict(o) for o in preceding]
