"""Content hashes of local source dependencies without importing/executing them."""
import ast
import hashlib
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=512)
def _imports(data):
    rows=[]
    for node in ast.walk(ast.parse(data.decode('utf-8-sig'))):
        if isinstance(node,ast.Import):
            rows.extend((0,name.name,()) for name in node.names)
        elif isinstance(node,ast.ImportFrom):
            rows.append((node.level,node.module or '',tuple(n.name for n in node.names)))
    return tuple(rows)


def implementation_dependency_hashes(root, seeds, *, resolution_trace=None):
    """Conservative static closure, including optional local import branches.

    Only AST parsing is cached. Files are read and imports resolved each call,
    so edits and newly created/deleted optional modules cannot reuse old hashes.
    Dynamic imports and external dependencies are outside this source manifest.
    """
    root=Path(root).resolve();pending=set(seeds);hashes={}
    while pending:
        name=pending.pop()
        if name in hashes:continue
        path=(root/name).resolve()
        if not path.is_relative_to(root):raise ValueError('source dependency escapes workspace')
        data=path.read_bytes();hashes[name]=hashlib.sha256(data).hexdigest()
        if path.suffix!='.py':continue
        for level,module,aliases in _imports(data):
            if level:
                base=path.parent
                for _ in range(level-1):base=base.parent
                bases=[base]
            else:
                bases=[root,root/'acquire',root/'decoder']
            names=[module] if module else []
            names.extend((module+'.' if module else '')+a for a in aliases if a!='*')
            for imported in names:
                for base in bases:
                    stem=base.joinpath(*imported.split('.'))
                    found=False
                    for candidate in (stem.with_suffix('.py'),stem/'__init__.py'):
                        is_file=candidate.is_file()
                        if resolution_trace is not None and candidate.is_relative_to(root):
                            resolution_trace[candidate.relative_to(root).as_posix()]={
                                'isFile':is_file,'resolvedPath':candidate.resolve().relative_to(root).as_posix()
                                if is_file and candidate.resolve().is_relative_to(root) else None}
                        if is_file and candidate.resolve().is_relative_to(root):
                            pending.add(candidate.resolve().relative_to(root).as_posix());found=True
                    if found:break
    return dict(sorted(hashes.items()))


def write_dependency_manifest(root,seeds,path):
    """Offline-only import discovery. Runtime verifies bytes and file presence."""
    root=Path(root).resolve();trace={}
    hashes=implementation_dependency_hashes(root,seeds,resolution_trace=trace)
    value=dict(format='er-source-dependency-closure.v1',seeds=sorted(seeds),sourceHashes=hashes,
               resolutionTrace=trace,generatorSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    Path(path).write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n',encoding='utf8')
    return value


def prepared_dependency_hashes(root,seeds,path):
    """Use the explicit compiled closure, never retry AST discovery at runtime.

    Validate every source's actual bytes and every positive/negative import
    resolution probe. A new optional module or changed import invalidates it.
    """
    root=Path(root).resolve();raw=Path(path).read_bytes();value=json.loads(raw)
    if (value.get('format')!='er-source-dependency-closure.v1' or value.get('seeds')!=sorted(seeds)
            or value.get('generatorSha256')!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()):
        raise ValueError('stale source closure; rebuild execution plans offline')
    hashes=value['sourceHashes']
    if not set(seeds)<=hashes.keys():raise ValueError('source closure lacks required seeds')
    for name,digest in hashes.items():
        file=(root/name).resolve()
        if not file.is_relative_to(root) or hashlib.sha256(file.read_bytes()).hexdigest()!=digest:
            raise ValueError('stale source closure: '+name+'; rebuild execution plans offline')
    for name,expected in value['resolutionTrace'].items():
        candidate=root/name
        if not candidate.is_relative_to(root):raise ValueError('import probe escapes workspace')
        is_file=candidate.is_file();resolved=candidate.resolve() if is_file else None
        actual=dict(isFile=is_file,resolvedPath=resolved.relative_to(root).as_posix()
                    if resolved is not None and resolved.is_relative_to(root) else None)
        if actual!=expected:raise ValueError('import resolution changed: '+name+'; rebuild execution plans offline')
    return dict(sorted(hashes.items()))
