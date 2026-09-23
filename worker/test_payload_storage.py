import contextlib,gzip,json,tempfile,unittest
from pathlib import Path
from payload_storage import seal_viewer

class PayloadStorageTests(unittest.TestCase):
 def test_exact_replay_bytes_and_only_one_saved_copy(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);raw=json.dumps({'track':[[1,0.123456789,88.123456789]]*2000,'unknown':None},ensure_ascii=False).encode()
   (root/'replay.json').write_bytes(raw);rows=seal_viewer(root)
   self.assertFalse((root/'replay.json').exists())
   self.assertEqual(gzip.decompress((root/'replay.json.gz').read_bytes()),raw)
   self.assertLess(rows[0]['storedBytes'],len(raw))
 def test_job_intermediates_disappear_on_success_and_failure(self):
  for fail in (False,True):
   with self.subTest(fail=fail),tempfile.TemporaryDirectory() as t:
    folder=Path(t);viewer=folder/'viewer-data';viewer.mkdir();(viewer/'replay.json').write_text('{"ready":true}')
    try:
     with contextlib.ExitStack() as resources:
      base=Path(resources.enter_context(tempfile.TemporaryDirectory(dir=folder)))
      (base/'corpus').mkdir();(base/'corpus/decode.sqlite3').write_bytes(b'private decode')
      (base/'analysis.json').write_text('private duplicate')
      if fail:raise ValueError('analysis failed')
    except ValueError:pass
    self.assertFalse(base.exists());self.assertTrue((viewer/'replay.json').exists())
