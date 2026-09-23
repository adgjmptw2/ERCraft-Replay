"""Remove abandoned job scratch only while the analysis worker lock is held."""
import os
import shutil
import stat
from pathlib import Path


def cleanup_abandoned(folder: Path, current_temp=None):
    root = folder.resolve()
    current = Path(current_temp or os.environ.get('TEMP', str(root))).resolve()
    removed = []
    candidates = list(root.glob('replay-job-*')) + list(root.glob('replay-dispatch-*'))
    for candidate in candidates:
        # Also reject in-root junctions: containment alone does not prove ownership.
        attributes = getattr(candidate.lstat(), 'st_file_attributes', 0)
        if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT or candidate.is_symlink():
            continue
        target = candidate.resolve()
        # Never follow junctions/symlinks out of the job directory, or erase
        # the live server-owned TEMP directory of this worker.
        if not target.is_relative_to(root) or target == current or current.is_relative_to(target):
            continue
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        shutil.rmtree(target)
        removed.append(candidate.name)
    return removed
