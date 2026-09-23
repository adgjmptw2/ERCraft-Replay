"""Job-local exact input version. Explicit binding; never retry another version."""
from contextlib import contextmanager
from contextvars import ContextVar

_VERSION = ContextVar('replay_exact_input_version', default=None)

def current_input_version():
    return _VERSION.get()

@contextmanager
def exact_input_version(version):
    if version not in ('12.3.0','12.4.0'):
        raise ValueError('Unsupported exact analysis input version')
    current = _VERSION.get()
    if current is not None and current != version:
        raise ValueError('Nested replay input version mismatch')
    token = _VERSION.set(version)
    try:
        yield
    finally:
        _VERSION.reset(token)
