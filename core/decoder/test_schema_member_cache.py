import json,struct,tempfile,unittest
from pathlib import Path
from .delta_payloads import SchemaDecoder,DecodeError
class SchemaMemberCacheTests(unittest.TestCase):
 def test_replay_definitions_and_returned_layouts_are_isolated(self):
  schema={'classes':{'BaseA':{'members':[[0,'int','a']]},'BaseB':{'members':[[0,'int','b']]},'Child':{'base':'BaseA','members':[[1,'int','value']]}},'enums':{}}
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'schema.json';path.write_text(json.dumps(schema),encoding='utf8')
   base={'Child':'BaseA'};a=SchemaDecoder(base,path);b=SchemaDecoder({'Child':'BaseB'},path)
   layout=a.wire_members_of('Child');layout.clear();base['Child']='BaseB'
   payload=bytes([2])+struct.pack('<ii',19,27)
   self.assertEqual(a.decode_exact(payload,'Child'),{'__type':'Child','a':19,'value':27})
   self.assertEqual(b.decode_exact(payload,'Child'),{'__type':'Child','b':19,'value':27})
   self.assertEqual(a.decode_exact(payload,'Child'),{'__type':'Child','a':19,'value':27})
   self.assertEqual(a.type_reads['Child'],2)
   with self.assertRaises(DecodeError):a.decode_exact(payload+b'\x00','Child')
