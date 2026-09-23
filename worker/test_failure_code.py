import unittest
from failure_code import classify
class Tests(unittest.TestCase):
 def test_successful_session_field_does_not_mask_transport_error(self):
  self.assertEqual(classify(b'{"sessionRetained":false}\nTraceback (most recent call last):\nValueError: ambiguous transport transition interval'),'TRANSPORT_TRANSITION_INVALID')
 def test_actual_session_failure(self):
  self.assertEqual(classify(b'RuntimeError: Saved session cannot be opened'),'SESSION_UNAVAILABLE')
