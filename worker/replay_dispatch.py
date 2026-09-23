"""Decoder-free acquisition and exact-version worker dispatch. No core fallback."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile

ADAPTER = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[2]
SHARED_CORE = ROOT.parent / 'secret_replay-safe'
CORE_124 = ROOT.parent / 'ERCraft-replay-core-12.4-20260917'
FIELDS = {'format', 'gameId', 'bytes', 'firstSnapshotTick', 'path', 'clientVersion', 'sourceSha256'}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_version(path):
    with Path(path).open('rb') as stream:
        header = stream.read(32)
    if len(header) != 32 or header[:16] != b'EternalReturnV1\0' or Path(path).stat().st_size < 0x420:
        raise ValueError('Invalid or truncated replay header')
    try:
        version = header[16:32].split(b'\0', 1)[0].decode('ascii')
    except UnicodeDecodeError:
        raise ValueError('REPLAY_VERSION_UNSUPPORTED: current 12.4.0 only') from None
    if version != '12.4.0':
        raise ValueError('REPLAY_VERSION_UNSUPPORTED: current 12.4.0 only')
    return version


def select_core(version):
    if version != '12.4.0':
        raise ValueError('REPLAY_VERSION_UNSUPPORTED: current 12.4.0 only')
    manifest = json.loads((ADAPTER / 'replay-patches/core-12.4.json').read_text(encoding='utf-8'))
    if (not isinstance(manifest, dict) or manifest.get('format') != 'ercraft-replay-core-release.v1'
            or manifest.get('clientVersion') != version
            or manifest.get('coreDirectory') != 'ERCraft-replay-core-12.4-20260917'
            or not isinstance(manifest.get('files'), dict) or not manifest['files']):
        raise ValueError('Invalid exact core release manifest')
    core = CORE_124.resolve(strict=True)
    if core != CORE_124.absolute():
        raise ValueError('Core installation must not redirect')
    expected = set()
    for relative, digest in manifest['files'].items():
        rel = PurePosixPath(relative)
        if (not relative or '\\' in relative or ':' in relative or rel.is_absolute()
                or any(part in ('', '.', '..') for part in relative.split('/'))
                or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
            raise ValueError('Invalid core manifest file')
        target = core.joinpath(*rel.parts)
        if target.resolve(strict=True) != target or not target.is_file() or sha256(target) != digest:
            raise ValueError('Core release file mismatch')
        expected.add(relative)
    actual = set()
    for target in core.rglob('*'):
        if target.resolve(strict=True) != target:
            raise ValueError('Core release contains redirected paths')
        if target.is_file():
            actual.add(target.relative_to(core).as_posix())
    if actual != expected:
        raise ValueError('Core release file set mismatch')
    return core


def load_acquisition_helper():
    # Never import runner/archive/decoder or search another core for acquisition.
    path = SHARED_CORE / 'acquire/get_replay.py'
    spec = importlib.util.spec_from_file_location('_replay_dispatch_acquisition', path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def plain_directory(path):
    """Require an existing literal directory, never a symlink/junction alias."""
    try:
        return (path.is_dir() and not path.is_symlink()
                and not getattr(path.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                and path.resolve(strict=True) == path.absolute())
    except OSError:
        return False


def server_scratch(path, folder):
    return (path.is_absolute() and path.parent == folder
            and path.name.startswith('replay-job-') and plain_directory(path))


def dispatch_parent(folder):
    inherited = os.environ.get('TEMP')
    if inherited:
        candidate = Path(inherited)
        if server_scratch(candidate, folder):
            return candidate
    return folder


def validate_prepared(path, game, folder):
    if type(game) is not int or game <= 0:
        raise ValueError('Invalid game id')
    root = Path(folder).resolve(strict=True)
    path = Path(path).absolute()
    resolved = path.resolve(strict=True)
    scratch = resolved.parent
    if (path != resolved or resolved.name != 'prepared.json'
            or not (scratch.parent == root or server_scratch(scratch.parent, root))
            or not scratch.name.startswith('replay-dispatch-') or not plain_directory(scratch)):
        raise ValueError('Prepared replay must be inside this job dispatch directory')
    prepared = json.loads(resolved.read_text(encoding='utf-8'))
    if (not isinstance(prepared, dict) or set(prepared) != FIELDS
            or prepared['format'] != 'ercraft-prepared-replay.v1'
            or type(prepared['gameId']) is not int or prepared['gameId'] != game
            or type(prepared['bytes']) is not int or prepared['bytes'] <= 0
            or type(prepared['firstSnapshotTick']) is not int or prepared['firstSnapshotTick'] < 0
            or not isinstance(prepared['path'], str)
            or not isinstance(prepared['sourceSha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', prepared['sourceSha256'])):
        raise ValueError('Invalid prepared replay schema')
    raw = scratch / f'{game}.er'
    if Path(prepared['path']) != raw or raw.resolve(strict=True) != raw:
        raise ValueError('Prepared raw replay path mismatch')
    if (raw.stat().st_size != prepared['bytes'] or sha256(raw) != prepared['sourceSha256']
            or read_version(raw) != prepared['clientVersion']):
        raise ValueError('Prepared replay content mismatch')
    verified = load_acquisition_helper().validate_replay(str(raw), game)
    if any(type(verified.get(key)) is not int or verified[key] != prepared[key]
           for key in ('gameId', 'bytes', 'firstSnapshotTick')):
        raise ValueError('Prepared replay snapshot mismatch')
    return dict(prepared, core=select_core(prepared['clientVersion']))


def main():
    from patch_policy import current_retention
    from acquisition_state import load_service_account, load_session
    from worker_lock import single_worker
    from job_scratch import cleanup_abandoned
    from process_tree import run_process_tree

    if len(sys.argv) != 3:
        raise ValueError('Expected game id and job folder')
    game = int(sys.argv[1])
    if game <= 0:
        raise ValueError('Invalid game id')
    folder = Path(sys.argv[2]).resolve(strict=True)
    with ExitStack() as locks:
        for root in dict.fromkeys(p.resolve() for p in (ROOT / 'work', ROOT / 'work/replay-beta', folder.parent)):
            locks.enter_context(single_worker(root))
        cleanup_abandoned(folder)
        if (folder / 'READY').exists():
            if not current_retention(folder):
                raise ValueError('REPLAY_VERSION_UNSUPPORTED: current 12.4.0 only')
            return
        # Server timeout kills this process before finally; its outer TEMP owner
        # must therefore own the raw file and every child intermediate too.
        with tempfile.TemporaryDirectory(dir=dispatch_parent(folder), prefix='replay-dispatch-') as temporary:
            scratch = Path(temporary)
            helper = load_acquisition_helper()
            user = load_service_account(SHARED_CORE)
            state = load_session(SHARED_CORE)
            try:
                url, _, _resolution = helper.resolve_replay(game, user, state)
                info = helper.download_replay(url, str(scratch / f'{game}.er'), game)
            finally:
                state.clear()
            raw = scratch / f'{game}.er'
            version = read_version(raw)
            prepared = {key: info[key] for key in ('gameId', 'bytes', 'firstSnapshotTick', 'path')}
            prepared.update(format='ercraft-prepared-replay.v1', clientVersion=version, sourceSha256=sha256(raw))
            path = scratch / 'prepared.json'
            path.write_text(json.dumps(prepared), encoding='utf-8')
            checked = validate_prepared(path, game, folder)
            child_temp = scratch / 'worker-temp'
            child_temp.mkdir()
            env = {key: value for key, value in os.environ.items()
                   if key.upper() not in ('PYTHONPATH', 'PYTHONHOME', 'TEMP', 'TMP', 'TMPDIR')}
            # The dispatcher owns these files even when the child tree times out.
            env.update(TEMP=str(child_temp), TMP=str(child_temp), TMPDIR=str(child_temp),
                       PYTHONDONTWRITEBYTECODE='1')
            result = run_process_tree([sys.executable, '-B', '-X', 'utf8', str(ADAPTER / 'analyze.py'),
                str(game), str(folder), '--prepared', str(path)], cwd=str(checked['core']), env=env, timeout=840)
            result.check_returncode()


if __name__ == '__main__':
    main()
