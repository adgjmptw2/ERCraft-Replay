import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from . import skill_game_data_contract as contract
from . import skill_execution_plan as plan
from .skill_scope_evidence_cache import build_evidence_cache,validate_evidence_cache,FIELDS
from .requested_skill_scope import manifest
from .projectile_hit_catalog import build_projectile_skill_catalog
from .recalculate_skill_scope import recalculate

OLD='5ef9cb5459a2e908099655bf9ffc70ac16b134d1db282fb97b25fe065e7c8e4f'
NEW='82dc298a2047af0e23637a6a78fcbbb70680e70bcdb7d80ca96ebec2e0f02b7c'


class ExactRevisionTests(unittest.TestCase):
    def test_two_archives_keep_different_source_hashes_and_identical_rule_reference(self):
        for digest in (OLD,NEW):
            p=contract.archive_for_revision('12.3.0',digest)
            identity=contract.validate_archive(p,'12.3.0')
            self.assertEqual(identity['gameDataSha256'],digest)
            self.assertEqual(identity['ruleGameDataSha256'],OLD)

    def test_unknown_hash_or_client_does_not_use_old_revision(self):
        for version,digest in [('12.3.0','0'*64),('12.4.0',NEW)]:
            with self.assertRaisesRegex(ValueError,'no fallback'):
                contract.revision_identity(version,digest)

    def test_filename_alone_does_not_admit_modified_archive(self):
        source=contract.archive_for_revision('12.3.0',NEW)
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/source.name;p.write_bytes(source.read_bytes()+b'changed')
            with self.assertRaisesRegex(ValueError,'no fallback'):
                contract.validate_archive(p,'12.3.0')

    def test_reviewed_table_hash_mismatch_rejects_even_registered_zip(self):
        source=contract.archive_for_revision('12.3.0',NEW)
        data=json.loads(contract.CONTRACT_PATH.read_bytes())
        data['sharedTableSha256']['Skill.json']='0'*64
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'contract.json';p.write_text(json.dumps(data))
            with patch.object(contract,'CONTRACT_PATH',p):
                with self.assertRaisesRegex(ValueError,'skill-rule table changed: Skill.json'):
                    contract.validate_archive(source,'12.3.0')

    def test_new_revision_has_its_own_plan_and_missing_plan_is_not_old_plan(self):
        old,oldsha=plan.load_plan('12.3.0',OLD);new,newsha=plan.load_plan('12.3.0',NEW)
        self.assertNotEqual(oldsha,newsha)
        self.assertEqual((old['gameDbSha256'],new['gameDbSha256']),(OLD,NEW))
        self.assertEqual(old['entries'],new['entries'])
        with tempfile.TemporaryDirectory() as directory,patch.object(plan,'ROOT',Path(directory)):
            with self.assertRaises(FileNotFoundError):plan.load_plan('12.3.0',NEW)

    def test_cache_and_actual_calculation_preserve_new_source_identity(self):
        path=contract.archive_for_revision('12.3.0',NEW)
        catalog=build_projectile_skill_catalog(path)
        with zipfile.ZipFile(path) as archive:
            tables={n:json.loads(archive.read(n+'.json')) for n in ('Skill','CharacterState','CharacterStateGroup','EffectAndSound')}
        facts={k:[] for k in FIELDS}
        facts['starts']=[dict(tick=10,playerObjectId=1,skillIdCode=478,skillCode=1033402,skillGroup=1033400)]
        facts['finishes']=[dict(tick=20,playerObjectId=1,skillIdCode=478,reason=0)]
        cache=build_evidence_cache(client_version='12.3.0',game_db_sha256=NEW,match_key='a'*64,
            players={1:{'characterCode':33},2:{'characterCode':2}},teams={1:1,2:2},intervals={1:[[0,100]]},**facts)
        spec=next(s for s in manifest() if s['skillGroup']==1033400)
        result=recalculate(cache,catalog,tables,[spec])
        self.assertEqual(cache['gameDataSha256'],NEW)
        self.assertEqual(result['gameDataSha256'],NEW)
        self.assertEqual(result['gameDataRuleIdentity']['ruleGameDataSha256'],OLD)
        row=next(r for r in result['observations'] if r['skillGroup']==1033400)
        self.assertEqual((row['attemptCount'],row['hitCount']),(1,0))
        bad=copy.deepcopy(cache);bad['gameDataSha256']='0'*64
        with self.assertRaisesRegex(ValueError,'no fallback'):validate_evidence_cache(bad)


if __name__=='__main__':unittest.main()
