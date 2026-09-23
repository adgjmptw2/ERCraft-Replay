"""Keep verified event counts distinct from the official result snapshot."""
EXACT = 'derived-exact-CmdKill-CmdDead-cumulative-crosschecked-finish-result'
DISCREPANCY = 'derived-CmdKill-CmdDead-official-assist-discrepancy'


def validate_summary(timeline, expected, proof):
    if (not isinstance(timeline, list) or not timeline
            or any(not isinstance(r, list) or len(r) != 4
                   or any(type(v) is not int or v < 0 for v in r) for r in timeline)
            or timeline != sorted(timeline, key=lambda r: r[0])
            or any(any(b[i] < a[i] for i in (1, 2, 3)) for a, b in zip(timeline, timeline[1:]))):
        raise ValueError('invalid event KDA timeline')
    actual = timeline[-1][1:]
    if actual == expected:
        if proof is not None:
            raise ValueError('unexpected KDA discrepancy proof')
        return True
    if (actual[:2] != expected[:2] or actual[2] != expected[2] + 1
            or not isinstance(proof, dict)
            or proof.get('format') != 'er-kda-source-discrepancy.v1'
            or proof.get('status') != 'unresolved-assist-source-discrepancy'
            or proof.get('officialKda') != expected or proof.get('eventKda') != actual
            or proof.get('countsReconciled') is not False
            or proof.get('fallbackUsed') is not False
            or proof.get('officialSource') != 'CmdFinishGameResult'
            or proof.get('eventSource') != 'CmdKill/CmdDead'
            or type(proof.get('finishTick')) is not int
            or proof['finishTick'] < timeline[-1][0]):
        raise ValueError('unsupported KDA discrepancy')
    anchors = proof.get('assistAnchors')
    if (type(proof.get('finishCommandIndex')) is not int or proof['finishCommandIndex'] < 0
            or not isinstance(anchors, list) or len(anchors) != actual[2]
            or any(not isinstance(a, list) or len(a) != 3
                   or any(type(v) is not int or v < 0 for v in a)
                   or a[1] != 1 or a[0] > proof['finishTick'] for a in anchors)):
        raise ValueError('invalid assist source anchors')
    increments = {}
    for a,b in zip(timeline,timeline[1:]):
        if b[3] > a[3]: increments[b[0]] = increments.get(b[0],0) + b[3]-a[3]
    observed = {}
    for tick,_,_ in anchors: observed[tick] = observed.get(tick,0)+1
    if observed != increments:
        raise ValueError('assist anchors differ from event timeline')
    return True


def crosscheck(timeline, expected, pid, player_ids, events, event_defs, finish):
    """Independently rebuild the timeline before admitting the bounded discrepancy."""
    if timeline[-1][1:] == expected:
        validate_summary(timeline, expected, None)
        return None
    player_ids = set(player_ids)
    state = [0, 0, 0]
    rebuilt = [timeline[0]]
    anchors = []
    def field(event, name):
        definition = event_defs.get(event[2], event_defs.get(str(event[2])))
        return event[5 + definition['fields'].index(name)]
    for event in events:
        changed = False
        if event[2] == 14:
            victim = field(event, 'deadCharacterObjectId')
            if victim not in player_ids:
                continue
            killer = field(event, 'objectId')
            assists = field(event, 'assistCharacterObjectIds') or []
            if len(assists) != len(set(assists)) or any(a not in player_ids for a in assists):
                raise ValueError('ambiguous assist identity')
            if killer == pid:
                state[0] += 1
                changed = True
            if pid in assists and pid != killer:
                state[2] += 1
                changed = True
                anchors.append([event[0], event[3], event[4]])
        elif event[2] == 15 and field(event, 'objectId') == pid:
            state[1] += 1
            changed = True
        if changed:
            rebuilt.append([event[0], *state])
    if rebuilt != timeline:
        raise ValueError('KDA event timeline does not reproduce')
    if (finish.get('packetName') != 'CmdFinishGameResult'
            or finish.get('status') != 'decoded-exact-wire'
            or finish.get('wrapperCategory') != 'commands'):
        raise ValueError('missing exact official KDA source')
    proof = dict(format='er-kda-source-discrepancy.v1',
                 status='unresolved-assist-source-discrepancy',
                 officialSource='CmdFinishGameResult', eventSource='CmdKill/CmdDead',
                 officialKda=list(expected), eventKda=state,
                 finishTick=finish['tick'], finishCommandIndex=finish['wrapperIndex'],
                 assistAnchors=anchors, countsReconciled=False, fallbackUsed=False)
    validate_summary(timeline, expected, proof)
    return proof
