import tempfile
import unittest
from pathlib import Path
from job_scratch import cleanup_abandoned


class Tests(unittest.TestCase):
    def test_only_abandoned_same_job_scratch_is_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ('replay-job-old','replay-job-current','viewer-data'):
                (root/name).mkdir()
                (root/name/'keep.er').write_bytes(b'test')
            self.assertEqual(cleanup_abandoned(root,root/'replay-job-current'),['replay-job-old'])
            self.assertTrue((root/'replay-job-current/keep.er').exists())
            self.assertTrue((root/'viewer-data/keep.er').exists())


if __name__=='__main__':unittest.main()
