"""Exact recorded rotation locks, without converting them into server poses."""
def rotation_event_fact(tick, category, wire_order, decoded):
    rotation = decoded.get('rotationY')
    if (type(decoded.get('objectId')) is not int or type(decoded.get('isLock')) is not bool or
            not isinstance(rotation, dict) or type(rotation.get('internalValue')) is not int):
        raise ValueError('missing exact CmdLockRotation fields')
    return dict(event='CmdLockRotation', tick=tick, objectId=decoded['objectId'],
                isLock=decoded['isLock'], rotationYInternalValue=rotation['internalValue'],
                wireCategory=category, wireOrder=wire_order)
