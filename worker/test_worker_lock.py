import tempfile,subprocess,sys,json,unittest
from pathlib import Path
class Test(unittest.TestCase):
 def test_new_install_creates_lock_directory(self):
  from worker_lock import single_worker
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder)/'new'/'worker'
   with single_worker(root):self.assertTrue((root/'worker.lock').is_file())
 def test_two_processes_do_not_overlap(self):
  with tempfile.TemporaryDirectory() as folder:
   code="from worker_lock import single_worker;from pathlib import Path;import time,json;\nwith single_worker(Path(%r)):\n print(time.monotonic(),flush=True);time.sleep(.3);print(time.monotonic(),flush=True)" % folder
   args=[sys.executable,'-c',code]
   a=subprocess.Popen(args,cwd=Path(__file__).parent,stdout=subprocess.PIPE,text=True);b=subprocess.Popen(args,cwd=Path(__file__).parent,stdout=subprocess.PIPE,text=True)
   spans=sorted([list(map(float,a.communicate()[0].split())),list(map(float,b.communicate()[0].split()))]);self.assertEqual(a.returncode,0);self.assertEqual(b.returncode,0);self.assertLessEqual(spans[0][1],spans[1][0])
if __name__=='__main__':unittest.main()
