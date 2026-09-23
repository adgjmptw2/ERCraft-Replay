def classify(tail):
    # Successful timing output contains "sessionRetained": false. It must not
    # turn unrelated validation exceptions into authentication failures.
    trace=tail.rsplit(b'Traceback (most recent call last):',1)[-1]
    if b'REPLAY_VERSION_UNSUPPORTED' in trace or b'official replay version unsupported (' in trace:return 'REPLAY_VERSION_UNSUPPORTED'
    if b'official replay temporarily unavailable' in trace:return 'REPLAY_NOT_READY'
    if b'public movement ping is invalid' in trace:return 'MOVEMENT_PING_INVALID'
    if b'item asset status is undeclared' in trace:return 'ITEM_ASSET_UNDECLARED'
    if b'transport transition' in trace:return 'TRANSPORT_TRANSITION_INVALID'
    if any(marker in trace for marker in (b'Saved session',b'session expired',b'HTTP Error 401',b'HTTP Error 403')):
        return 'SESSION_UNAVAILABLE'
    return 'ANALYSIS_VALIDATION_FAILED'

def message_for(code):
    return {
        'REPLAY_VERSION_UNSUPPORTED':'현재 12.4 버전 리플레이만 분석·재생할 수 있어요.',
        'REPLAY_NOT_READY':'리플레이가 아직 준비되지 않았어요. 잠시 후 다시 신청해 주세요.',
    }.get(code,'분석 또는 결과 검증에 실패했어요. 관리자가 확인해야 해요.')
