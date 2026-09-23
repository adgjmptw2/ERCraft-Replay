"""Read retained QuickTransit snapshots without changing replay/skill data."""
import struct


def decode_transport_snapshot(payload):
    # MemoryPack Dictionary<int,bool> serializes unmanaged KeyValuePair with
    # three padding bytes after its bool (8-byte stride), not packed 5 bytes.
    data = memoryview(payload)
    offset = 0

    def take(n):
        nonlocal offset
        if offset + n > len(data):
            raise ValueError('truncated QuickTransitDeviceSnapshot')
        value = data[offset:offset + n]
        offset += n
        return value

    def integer():
        return struct.unpack('<i', take(4))[0]

    def count():
        value = integer()
        if not 0 <= value <= 10000:
            raise ValueError('invalid QuickTransit collection count')
        return value

    if bytes(take(1)) != b'\x06':
        raise ValueError('unsupported QuickTransit snapshot layout')
    for _ in range(count()):
        integer()
        for _ in range(count()):
            integer()
            for _ in range(count()):
                integer()
    for _ in range(count()):
        integer()
        if take(4)[0] not in (0, 1):
            raise ValueError('invalid restriction bool')
    modes = {}
    for _ in range(count()):
        area, mode = integer(), integer()
        if area in modes or mode not in (0, 1):
            raise ValueError('invalid area mode')
        modes[area] = 'hyperloop' if mode == 0 else 'vls'
    for _ in range(2):
        for _ in range(count()):
            integer()
    if take(1)[0] not in (0, 1) or offset != len(data):
        raise ValueError('invalid QuickTransit snapshot tail')
    return modes


def correlate_transport_states(snapshots, notifications, changes, *, allow_redundant_device_ids=False, allow_snapshot_gaps=False):
    """Require one exact mode-change notification between differing snapshots.

    Device IDs identify the new active devices, not a global VLS-only mode.
    Refuse an interval with multiple notifications or mismatched change counts.
    """
    result = []
    previous = None
    for tick, payload in snapshots:
        modes = decode_transport_snapshot(payload)
        if previous is None:
            result.append({'tick': tick, 'areaModes': modes, 'source': 'initial-QuickTransitDeviceSnapshot'})
        elif modes != previous[1]:
            # Duplicate notices at one tick are one transition. A notice without
            # a state command is not itself evidence that any device changed.
            events = sorted({t for t in notifications if previous[0] < t <= tick and t in changes})
            if set(modes) != set(previous[1]):
                raise ValueError('ambiguous transport transition interval')
            changed = [a for a in modes if modes[a] != previous[1][a]]
            if len(events) != 1:
                if not allow_snapshot_gaps:raise ValueError('ambiguous transport transition interval')
                # Snapshots prove the endpoints, not the missing transition time.
                unknown=dict(previous[1]);unknown.update({a:None for a in changed})
                result.append({'tick':previous[0]+1,'areaModes':unknown,
                               'source':'unavailable-transition-between-snapshots'})
                result.append({'tick':tick,'areaModes':modes,'source':'observed-QuickTransitDeviceSnapshot'})
                previous=(tick,modes)
                continue
            ids = changes.get(events[0])
            if ids is None or (len(set(ids)) < len(changed) if allow_redundant_device_ids else len(set(ids)) != len(changed)):
                raise ValueError('transport command/snapshot change count mismatch')
            result.append({'tick': events[0], 'areaModes': modes,
                           'source': 'QuickTransitDeviceSnapshot+CmdModifyQuickTransitDeviceState+CmdChangeHyperLoopToVLS'})
        previous = (tick, modes)
    return result
