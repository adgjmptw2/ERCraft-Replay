import unittest
from damage_source_coverage_candidate import require_damage_source_coverage


class CoverageTests(unittest.TestCase):
    def check(self, hp_ids, wire_ids):
        require_damage_source_coverage(
            {'players': [{'objectId': 1}]},
            {'observations': [{'sourceSequence': p} for p in hp_ids]},
            {'events': [{'packetId': p} for p in wire_ids]},
            {'events': {10: {'victim': 1}, 11: {'victim': 1}, 12: {'victim': 2}}})

    def test_player_target_scope_and_order_independence(self):
        self.check([10, 11], [11, 10])

    def test_common_omission(self):
        with self.assertRaisesRegex(ValueError, 'missing=1, extra=0'):
            self.check([10], [10])

    def test_same_count_substitution(self):
        with self.assertRaisesRegex(ValueError, 'missing=1, extra=1'):
            self.check([10, 99], [10, 99])

    def test_nonplayer_extra_rejected(self):
        with self.assertRaisesRegex(ValueError, 'missing=0, extra=1'):
            self.check([10, 11, 12], [10, 11, 12])

    def test_duplicate_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.check([10, 11, 11], [10, 11])

    def test_empty_player_damage_scope(self):
        require_damage_source_coverage({'players': [{'objectId': 1}]},
                                      {'observations': []}, {'events': []},
                                      {'events': {12: {'victim': 2}}})


if __name__ == '__main__':
    unittest.main()
