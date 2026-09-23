"""Clock constraints from a surviving CommonState's changed creation time.

Inputs must be ordered, exact, same-version state events. This does not claim
the timestamp belongs to the update event. A changed timestamp brackets a
reset between two updates; creation/replacement/removal breaks that continuity.
"""
from bisect import bisect_left, bisect_right
try:
    from .skill_server_frame_time import frame_time_preimage
except ImportError:
    from skill_server_frame_time import frame_time_preimage


def state_clock_constraints(events, groups, state_codes):
    # Group by object so a projectile wrapper invalidates only its own state
    # history, rather than copying every character's history on every spawn.
    previous = {}
    lower, upper = [], []
    for event in events:
        name = event['name']
        if name == 'barrier':
            affected = event.get('objectIds')
            if affected is None:
                previous.clear()
            else:
                for actor in affected:
                    previous.pop(actor, None)
            continue
        value = event['value']
        actor = value.get('objectId')
        if name in ('CmdAddState','CmdAddStateExtended') and value.get('code') not in state_codes:
            previous.pop(actor, None)
            continue
        group = value.get('group', state_codes.get(value.get('code')))
        definition = groups.get(group, {})
        if definition.get('stateBehaviourType') != 'Common':
            continue
        caster = value.get('casterId') or actor
        key = actor, group, 0 if definition['notCheckCasterId'] else caster
        history = previous.setdefault(actor, {})
        if name == 'CmdRemoveState':
            history.pop(key, None)
            continue
        if name in ('CmdAddState','CmdAddStateExtended'):
            # New Add is sent after synchronous CommonState.Start. This starts
            # a new instance; no prior-update relationship crosses this point.
            history[key] = dict(packetId=event['packetId'],wireOrder=event['wireOrder'],
                                 stateIdentity=list(key),creationPacket=True)
            continue
        if name == 'CmdResetCreateTimeState':
            history[key] = dict(packetId=event['packetId'],wireOrder=event['wireOrder'],
                                 stateIdentity=list(key),resetPacket=True)
            continue
        if name != 'CmdUpdateState':
            continue
        bounds = frame_time_preimage(value['createdTime']['internalValue'])
        if bounds is None:
            history.pop(key, None)
            continue
        witness = dict(packetId=event['packetId'], wireOrder=event['wireOrder'],
                       createdFramePreimage=list(bounds), stateIdentity=list(key))
        lower.append(dict(**witness, lower=bounds[0]))
        prior = history.get(key)
        if (prior is not None and prior['wireOrder'][0] < event['wireOrder'][0]
                and (prior.get('resetPacket') is True or prior.get('creationPacket') is True
                     or prior['createdFramePreimage'][1] < bounds[0])):
            # The same surviving instance's reset must follow the old update.
            # Use the full possible timestamp interval, not a rounded point.
            upper.append(dict(**prior, upper=bounds[1], laterUpdate=witness))
        history[key] = witness
    return dict(lower=lower, upper=upper)


def event_clock_bounds(constraints, events):
    """Use strictly earlier/later records; never order unrelated callbacks
    within a single batched record. Keep the source witnesses for each bound.
    """
    lower = sorted(constraints['lower'], key=lambda x: x['wireOrder'])
    upper = sorted(constraints['upper'], key=lambda x: x['wireOrder'])
    lower_records = [x['wireOrder'][0] for x in lower]
    upper_records = [x['wireOrder'][0] for x in upper]
    prefix, suffix = [], [None] * len(upper)
    best = None
    for row in lower:
        if best is None or row['lower'] > best['lower']:
            best = row
        prefix.append(best)
    best = None
    for i in range(len(upper) - 1, -1, -1):
        if best is None or upper[i]['upper'] < best['upper']:
            best = upper[i]
        suffix[i] = best
    result = []
    for event in events:
        record = event['wireOrder'][0]
        li, ui = bisect_left(lower_records, record) - 1, bisect_right(upper_records, record)
        lo = prefix[li] if li >= 0 else None
        hi = suffix[ui] if ui < len(upper) else None
        status = ('missing-clock-boundary' if lo is None or hi is None else
                  'clock-conflict' if lo['lower'] > hi['upper'] else 'bounded')
        result.append(dict(event=event, status=status, lower=lo['lower'] if lo else None,
                           upper=hi['upper'] if hi else None,
                           lowerWitness=lo, upperWitness=hi))
    return result
