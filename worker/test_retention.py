import tempfile,json,unittest
from pathlib import Path
from retention import expire_other_patches
class Test(unittest.TestCase):
 def test_only_old_verified_local_payload_is_removed(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)
   for name,patch,verified in [('1','12.2.0',True),('2','12.3.0',True),('3','12.2.0',False),('4','12.4.0',True)]:
    d=root/name;d.mkdir();(d/'analysis').mkdir();(d/'analysis/x').write_text('data');(d/'READY').touch();(d/'statistics.json.gz').touch();(d/'retention.json').write_text(json.dumps({'patch':patch,'verified':verified}))
   self.assertEqual(expire_other_patches(root,'12.3.0'),['1']);self.assertTrue((root/'1/statistics.json.gz').exists())
   for n in ['2','3','4']:self.assertTrue((root/n/'analysis/x').exists())
if __name__=='__main__':unittest.main()
