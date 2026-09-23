"""Current-patch viewer admission; never deletes data or changes queue state."""
import json

CURRENT_VERSION = '12.4.0'
UNSUPPORTED = 'REPLAY_VERSION_UNSUPPORTED'
MESSAGE = '현재 12.4 버전 리플레이만 분석·재생할 수 있어요.'


def current_retention(folder):
    try:
        data = json.loads((folder / 'retention.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get('patch') == CURRENT_VERSION and data.get('verified') is True


def blocked_cached_patch(folder):
    # A new game has no known patch until acquisition checks its raw header.
    # Cached viewers, however, require explicit verified current-patch provenance.
    if any((folder / name).exists() for name in ('retention.json', 'READY', 'viewer-data', 'EXPIRED')):
        return not current_retention(folder)
    stage = folder / 'stage.json'
    if stage.exists():
        try:
            data = json.loads(stage.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return True
        if not isinstance(data, dict):
            return True
        patch = data.get('patch')
        if patch is not None:
            return patch != CURRENT_VERSION
    return False
