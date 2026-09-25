"""Research guard for declared packet gaps; absence of gaps is not completeness."""
def guarded_metric(calculator,arguments,required_packets):
    gaps=arguments.get('gaps')
    blocking=[]
    for gap in gaps or []:
        if gap.get('packetName') not in required_packets:
            continue
        count=gap.get('count')
        if type(count) is not int or count<0 or count>0:
            blocking.append(dict(gap))
    if blocking:
        return dict(arguments['spec'],status='unresolved-evidence',
                    reason='declared-required-packet-channel-gap',
                    attemptCount=None,hitCount=None,hitRate=None,fallbackUsed=False,
                    blockingPacketGaps=blocking,independentHitAccuracyProven=False)
    return calculator(**arguments)
