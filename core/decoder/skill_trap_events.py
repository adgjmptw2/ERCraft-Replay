"""Exact trap activation/burst packets, preserving explicit target identities."""


def trap_event_fact(event, tick, category, wire_order, decoded):
    if event not in ('CmdActiveTrap', 'CmdBurstTrap'):
        raise ValueError('unsupported trap packet')
    if type(decoded.get('objectId')) is not int:
        raise ValueError('missing trap object identity')
    row = dict(event=event, tick=tick, objectId=decoded['objectId'],
               wireCategory=category, wireOrder=wire_order)
    if event == 'CmdBurstTrap':
        targets = decoded.get('targets')
        if not isinstance(targets, list) or any(type(t) is not int for t in targets):
            raise ValueError('missing explicit trap burst targets')
        # Nested identity fields use the same de-identification path as skill
        # action targets. Never store a raw integer target array in a cache.
        row['targets'] = [dict(targetObjectId=t) for t in targets]
    return row
