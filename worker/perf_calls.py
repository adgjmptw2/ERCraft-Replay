"""Small aggregate timers, not a per-packet profiler; no payloads retained."""
from contextlib import contextmanager
from functools import wraps
import importlib
import json
import os
import time


@contextmanager
def observe(folder):
    targets = {
        'decoder.recalculate_skill_scope': ['calculate_scope_metrics', 'calculate_projectile_hit_rates'],
        'decoder.skill_owned_stage_casts': ['owned_stage_cast_evidence'],
        'decoder.skill_action_stage_evidence': ['observe_action_stages'],
        'decoder.skill_projectile_active_end': ['_build_projectile_active_end_records'],
        'decoder.skill_summon_ownership': ['live_summon_owner_resolver'],
    }
    rows = {}
    installed = []
    def wrapper(function, key):
        row = rows[key] = {'calls': 0, 'seconds': 0.0, 'maxSeconds': 0.0, 'errors': 0}
        @wraps(function)
        def measured(*args, **kwargs):
            start = time.perf_counter()
            try:
                return function(*args, **kwargs)
            except BaseException:
                row['errors'] += 1
                raise
            finally:
                seconds = time.perf_counter() - start
                row['calls'] += 1
                row['seconds'] += seconds
                row['maxSeconds'] = max(row['maxSeconds'], seconds)
        return measured
    try:
        if os.environ.get('ERCRAFT_PROFILE_CATALOG') == '1':
            import cProfile
            import pstats
            module = importlib.import_module('build_combat_analysis')
            original_catalog = module.build_catalog
            @wraps(original_catalog)
            def profiled_catalog(*args, **kwargs):
                profile = cProfile.Profile()
                try:
                    return profile.runcall(original_catalog, *args, **kwargs)
                finally:
                    profile.dump_stats(str(folder / 'catalog.cprofile'))
                    stats = pstats.Stats(profile)
                    values = [dict(file=f, line=line, function=name, primitiveCalls=pc,
                                   calls=nc, ownSeconds=tt, cumulativeSeconds=ct)
                              for (f, line, name), (pc, nc, tt, ct, callers) in stats.stats.items()]
                    values.sort(key=lambda v: v['cumulativeSeconds'], reverse=True)
                    (folder / 'catalog-profile.json').write_text(json.dumps(values), encoding='utf8')
            module.build_catalog = profiled_catalog
            installed.append((module, 'build_catalog', original_catalog))
        for module_name, names in targets.items():
            module = importlib.import_module(module_name)
            for name in names:
                function = getattr(module, name)
                setattr(module, name, wrapper(function, module_name + '.' + name))
                installed.append((module, name, function))
        yield
    finally:
        for module, name, function in reversed(installed):
            setattr(module, name, function)
        (folder / 'function-timings.json').write_text(json.dumps(rows), encoding='utf8')
