"""Observe cyclic-GC time without changing collection behavior or results."""
import gc
import json
import time
from collections import defaultdict
from contextlib import contextmanager


@contextmanager
def large_graph_threshold():
    """Keep cyclic GC enabled, but avoid repeatedly scanning live replay trees."""
    previous = gc.get_threshold()
    gc.set_threshold(max(previous[0], 50000), previous[1], previous[2])
    try:
        yield
    finally:
        gc.set_threshold(*previous)


@contextmanager
def observe(folder):
    phase = ['startup']
    starts = {}
    rows = defaultdict(lambda: {'seconds': 0.0, 'collections': [0, 0, 0]})
    def callback(event, info):
        generation = info['generation']
        if event == 'start':
            starts[generation] = time.perf_counter()
        elif generation in starts:
            row = rows[phase[0]]
            row['seconds'] += time.perf_counter() - starts.pop(generation)
            row['collections'][generation] += 1
    gc.callbacks.append(callback)
    try:
        yield phase
    finally:
        gc.callbacks.remove(callback)
        (folder / 'gc-timings.json').write_text(json.dumps({
            'thresholds': gc.get_threshold(), 'enabled': gc.isenabled(),
            'phases': {k: {**v, 'seconds': round(v['seconds'], 6)} for k, v in rows.items()}
        }), encoding='utf8')
