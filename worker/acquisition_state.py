"""Existing acquisition identity and DPAPI state, loaded only on demand."""
import base64
import ctypes
import json
from pathlib import Path


def load_service_account(shared_core):
    config = json.loads((Path(shared_core) / 'acquire/config.local').read_text(encoding='utf-8-sig'))
    if not isinstance(config, dict) or config.get('format') != 'ercraft-replay-service-account.v1':
        raise ValueError('fixed service-account config format is invalid')
    user = config.get('serviceAccountUserNum')
    if type(user) is not int or user <= 0:
        raise ValueError('fixed service-account identity is invalid')
    return user


def load_session(shared_core):
    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    encrypted = base64.b64decode((Path(shared_core) / 'acquire/.secrets/session.dpapi').read_bytes())
    buffer = (ctypes.c_ubyte * len(encrypted)).from_buffer_copy(encrypted)
    source = Blob(len(encrypted), buffer)
    plain = Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(plain)):
        raise RuntimeError('Saved session cannot be opened by this Windows account')
    try:
        state = json.loads(ctypes.string_at(plain.data, plain.size).decode('utf-8-sig'))
    finally:
        ctypes.memset(plain.data, 0, plain.size)
        ctypes.windll.kernel32.LocalFree(ctypes.cast(plain.data, ctypes.c_void_p))
    if not isinstance(state, dict) or not isinstance(state.get('sessionKey'), str) or not state['sessionKey'].startswith('Session:'):
        if isinstance(state, dict):
            state.clear()
        raise ValueError('Saved session invalid')
    return state
