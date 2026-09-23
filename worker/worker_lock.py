import contextlib,msvcrt,time
from pathlib import Path
@contextlib.contextmanager
def single_worker(root:Path):
 root.mkdir(parents=True,exist_ok=True)
 path=root/'worker.lock'
 with path.open('a+b') as stream:
  if path.stat().st_size==0:stream.write(b'0');stream.flush()
  while True:
   stream.seek(0)
   try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1);break
   except OSError:time.sleep(.2)
  try:yield
  finally:stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
