import unittest
from copy import deepcopy
from replay_damage_quality_result import build_damage_quality_result

class QualityResultTests(unittest.TestCase):
    def fixture(self):
        side=dict(recordedFinish=10,knownEventSubtotal=10,unresolvedEvents=0,knownSubtotalMinusFinish=0)
        ledger=dict(players=[dict(player=1,dealt=deepcopy(side),taken=deepcopy(side))],unattributedDealtEvents=0)
        episode=dict(insideKnown=7,outsideKnown=3,insideUnknown=0,outsideUnknown=0)
        part=dict(ledgerBasis='ownerDealtHypothesis',partitionConserved=True,intervalBasis='[start,end)',players={1:dict(dealt=deepcopy(episode),taken=deepcopy(episode))})
        return ledger,part
    def test_matching_total_never_proves_exactness(self):
        row=build_damage_quality_result(*self.fixture())['players'][0]['dealt']
        self.assertEqual(row['status'],'matches_finish_unverified')
        self.assertEqual(row['insideEpisodeKnownSubtotal'],7)
        self.assertEqual(row['recordedMatchTotal'],10)
        self.assertIsNone(row['exactEventTotal'])
    def test_unknown_with_matching_finish_remains_unresolved(self):
        l,p=self.fixture();l['players'][0]['dealt']['unresolvedEvents']=1;p['players'][1]['dealt']['outsideUnknown']=1
        row=build_damage_quality_result(l,p)['players'][0]['dealt']
        self.assertEqual(row['status'],'unresolved_events')
        self.assertEqual(row['knownSubtotalMinusFinish'],0)
    def test_missing_finish_preserved(self):
        l,p=self.fixture();l['players'][0]['dealt'].update(recordedFinish=None,knownSubtotalMinusFinish=None)
        row=build_damage_quality_result(l,p)['players'][0]['dealt']
        self.assertIsNone(row['recordedMatchTotal']);self.assertEqual(row['status'],'finish_unavailable')
    def test_partition_mismatch_rejected(self):
        l,p=self.fixture();p['players'][1]['dealt']['outsideKnown']=4
        with self.assertRaises(ValueError):build_damage_quality_result(l,p)
    def test_unattributed_is_dealt_only(self):
        l,p=self.fixture();l['unattributedDealtEvents']=1
        row=build_damage_quality_result(l,p)['players'][0]
        self.assertEqual(row['dealt']['status'],'unresolved_events')
        self.assertEqual(row['taken']['status'],'matches_finish_unverified')
    def test_known_mismatch_keeps_finish(self):
        l,p=self.fixture();l['players'][0]['dealt'].update(recordedFinish=12,knownSubtotalMinusFinish=-2)
        row=build_damage_quality_result(l,p)['players'][0]['dealt']
        self.assertEqual(row['status'],'differs_from_finish');self.assertEqual(row['recordedMatchTotal'],12)

if __name__=='__main__':unittest.main()
