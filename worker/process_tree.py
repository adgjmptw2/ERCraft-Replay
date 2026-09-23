"""Windows process-tree lifetime guard for replay-beta worker jobs."""
import ctypes
import subprocess
from ctypes import wintypes

CREATE_SUSPENDED = 0x00000004
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9


class _BasicLimit(ctypes.Structure):
    _fields_ = [
        ('PerProcessUserTimeLimit', ctypes.c_longlong),
        ('PerJobUserTimeLimit', ctypes.c_longlong),
        ('LimitFlags', wintypes.DWORD),
        ('MinimumWorkingSetSize', ctypes.c_size_t),
        ('MaximumWorkingSetSize', ctypes.c_size_t),
        ('ActiveProcessLimit', wintypes.DWORD),
        ('Affinity', ctypes.c_size_t),
        ('PriorityClass', wintypes.DWORD),
        ('SchedulingClass', wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [('ReadOperationCount', ctypes.c_ulonglong),
                ('WriteOperationCount', ctypes.c_ulonglong),
                ('OtherOperationCount', ctypes.c_ulonglong),
                ('ReadTransferCount', ctypes.c_ulonglong),
                ('WriteTransferCount', ctypes.c_ulonglong),
                ('OtherTransferCount', ctypes.c_ulonglong)]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', _BasicLimit),
                ('IoInfo', _IoCounters),
                ('ProcessMemoryLimit', ctypes.c_size_t),
                ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t),
                ('PeakJobMemoryUsed', ctypes.c_size_t)]


def run_process_tree(args, *, timeout, **kwargs):
    """Run a subprocess whose descendants die with it on timeout."""
    if ctypes.sizeof(ctypes.c_void_p) == 0:
        raise RuntimeError('invalid Windows process handle ABI')
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, wintypes.INT, wintypes.LPVOID, wintypes.DWORD
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    ntdll = ctypes.WinDLL('ntdll', use_last_error=True)
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = wintypes.LONG
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        limits = _ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        child = subprocess.Popen(args, creationflags=CREATE_SUSPENDED, **kwargs)
        try:
            if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(child._handle)):
                child.kill()
                raise ctypes.WinError(ctypes.get_last_error())
            # Popen exposes the process handle, not the suspended thread
            # handle. NtResumeProcess is the matching process-handle API.
            if ntdll.NtResumeProcess(wintypes.HANDLE(child._handle)) != 0:
                kernel32.TerminateJobObject(job, 1)
                raise RuntimeError('NtResumeProcess failed')
            try:
                stdout, stderr = child.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                kernel32.TerminateJobObject(job, 1)
                child.wait(timeout=5)
                raise
            return subprocess.CompletedProcess(args, child.returncode, stdout, stderr)
        finally:
            if child.poll() is None:
                kernel32.TerminateJobObject(job, 1)
                child.wait(timeout=5)
    finally:
        kernel32.CloseHandle(job)
