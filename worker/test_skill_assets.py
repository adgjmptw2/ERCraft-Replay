import unittest
from skill_assets import validate_skill_icons
class SkillAssetsTest(unittest.TestCase):
    def test_empty_character_rejected(self):
        with self.assertRaisesRegex(ValueError, 'character=13: empty slots'):
            validate_skill_icons({'icons': {'13': {'slots': {}}}})
    def test_missing_image_rejected(self):
        with self.assertRaisesRegex(ValueError, 'image missing'):
            validate_skill_icons({'icons': {'13': {'slots': {'q': {'semanticSlot':'q'}}}}})
    def test_valid_variant_and_typed_gap(self):
        validate_skill_icons({'icons': {'13': {'slots': {'e2': {'imageDataUrl':'data:image/webp;base64,x','semanticSlot':'e'}}, 'unavailableSemanticSlots':['r']}}})
if __name__ == '__main__': unittest.main()
