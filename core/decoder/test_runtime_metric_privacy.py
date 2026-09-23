import unittest

from .audit_projectile_runtime_replay import public_requested_metric


class RuntimeMetricPrivacyTests(unittest.TestCase):
    def test_contact_and_execution_ids_stay_private_without_losing_counts(self):
        row = {
            'metricId': 'test', 'status': 'calculable-experimental',
            'attemptCount': 2, 'hitCount': 1, 'hitRate': 0.5,
            'outcomes': [[10, 1, 10, 12], [20, 0, 20, None]],
            'verifiedCompletionCredit': False,
            'contactDetailsByAttempt': [[{'targetObjectId': 123}]],
            'executionEvidenceByAttempt': [{'start': {'playerObjectId': 456}}],
        }
        result = public_requested_metric(row)
        for key in ('metricId', 'status', 'attemptCount', 'hitCount', 'hitRate',
                    'outcomes', 'verifiedCompletionCredit'):
            self.assertEqual(result[key], row[key])
        self.assertNotIn('contactDetailsByAttempt', result)
        self.assertNotIn('executionEvidenceByAttempt', result)
        self.assertIn('contactDetailsByAttempt', row)
