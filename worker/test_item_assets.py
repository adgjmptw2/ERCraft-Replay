import io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from item_assets import prepare

class Tests(unittest.TestCase):
 def test_exact_version_and_cache(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);(root/'data/items').mkdir(parents=True)
   (root/'data/items.provenance.json').write_text(json.dumps({'assets':[],'unavailable':[]}))
   cat={'meta':{'clientVersion':'12.3.0'},'itemCatalog':{'123':{}}}
   with patch('item_assets.urllib.request.urlopen',return_value=io.BytesIO(b'\x89PNG\r\n\x1a\ntest')) as fetch:
    self.assertEqual(prepare(root,cat),1)
    self.assertIn('/12.3.0/ItemIcon_123.png',fetch.call_args.args[0])
   with patch('item_assets.urllib.request.urlopen',side_effect=AssertionError('unexpected network')):
    self.assertEqual(prepare(root,cat),0)
   cat['meta']['clientVersion']='12.4.0'
   with self.assertRaises(ValueError):prepare(root,cat)
 def test_invalid_image_does_not_register_success(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);(root/'data/items').mkdir(parents=True)
   p=root/'data/items.provenance.json';p.write_text(json.dumps({'assets':[],'unavailable':[]}))
   with patch('item_assets.urllib.request.urlopen',return_value=io.BytesIO(b'<html>error</html>')):
    with self.assertRaises(ValueError):prepare(root,{'meta':{'clientVersion':'12.3.0'},'itemCatalog':{'123':{}}})
   self.assertEqual(json.loads(p.read_text())['assets'],[])
