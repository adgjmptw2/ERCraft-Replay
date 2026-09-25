"""Common accounting regressions: identity, ownership, independent sides, nulls."""
import unittest
from damage_accounting_ledger import DamageFact,DamageLedger

class LedgerTests(unittest.TestCase):
    def test_stable_identity_not_time_or_amount(self):
        l=DamageLedger([1,2]);a=DamageFact(('game',4,1),1,2,10,True,True)
        l.add(a);l.add(a);l.add(DamageFact(('game',4,2),1,2,10,True,True))
        r=l.report({});self.assertEqual(r['rawEvents'],2)
        self.assertEqual(r['players'][0]['dealt']['knownEventSubtotal'],20)
        self.assertEqual(r['duplicateReferencesIgnored'],1)
    def test_conflict_cannot_silently_overwrite(self):
        l=DamageLedger([1,2]);l.add(DamageFact((1,),1,2,10,True,True))
        with self.assertRaises(ValueError):l.add(DamageFact((1,),1,2,11,True,True))
        self.assertFalse(l.report({})['players'][0]['dealt']['completeUnderSuppliedPolicy'])
    def test_owned_damage_sides_independent(self):
        l=DamageLedger([1,2]);l.add(DamageFact((1,),99,2,40,True,False,1,True))
        r=l.report({});self.assertEqual(r['players'][0]['dealt']['knownEventSubtotal'],40)
        self.assertEqual(r['players'][1]['taken']['knownEventSubtotal'],0)
    def test_unknown_never_becomes_zero_or_balanced(self):
        l=DamageLedger([1,2]);l.add(DamageFact((1,),1,2,None,True,True))
        r=l.report({1:{'dealt':100}})['players'][0]['dealt']
        self.assertEqual(r['recordedFinish'],100);self.assertEqual(r['knownEventSubtotal'],0)
        self.assertEqual(r['unresolvedEvents'],1);self.assertFalse(r['completeUnderSuppliedPolicy'])
    def test_policy_unknown_blocks_only_affected_side(self):
        l=DamageLedger([1,2]);l.add(DamageFact((1,),1,2,20,None,True))
        r=l.report({});self.assertEqual(r['players'][0]['dealt']['unresolvedEvents'],1)
        self.assertEqual(r['players'][1]['taken']['knownEventSubtotal'],20)
    def test_unverified_owner_not_credited(self):
        l=DamageLedger([1,2]);l.add(DamageFact((1,),99,2,40,True,False,1,False))
        r=l.report({});self.assertEqual(r['unattributedDealtEvents'],1)
        self.assertEqual(r['players'][0]['dealt']['knownEventSubtotal'],0)
    def test_nonplayer_target_not_player_damage(self):
        l=DamageLedger([1]);l.add(DamageFact((1,),1,99,40,True,True))
        self.assertEqual(l.report({})['players'][0]['dealt']['knownEventSubtotal'],0)

    def test_owned_self_and_exclusion_reason(self):
        l=DamageLedger([1]);l.add(DamageFact((1,),99,1,40,True,False,1,True,taken_reason='policy:owned'))
        r=l.report({});self.assertEqual(r['classes']['owned_object_self'],1)
        self.assertEqual(r['excludedByPolicyOrScope'][0]['knownAmount'],40)
        self.assertEqual(r['excludedByPolicyOrScope'][0]['reason'],'policy:owned')
    def test_verified_nonplayer_owner_is_unattributed_global(self):
        l=DamageLedger([1,2]);l.add(DamageFact((1,),99,2,40,True,False,98,True))
        r=l.report({});self.assertEqual(r['unattributedDealtEvents'],1)
        self.assertTrue(all(not p['dealt']['completeUnderSuppliedPolicy'] for p in r['players']))
    def test_excluded_unknown_is_not_eligible_missing(self):
        l=DamageLedger([1,2]);l.add(DamageFact((1,),1,2,None,False,False))
        r=l.report({});self.assertEqual(r['players'][0]['dealt']['unresolvedEvents'],0)
        self.assertEqual(r['excludedByPolicyOrScope'][0]['unknownAmountEvents'],1)
    def test_negative_amount_rejected(self):
        l=DamageLedger([1,2])
        with self.assertRaises(ValueError):l.add(DamageFact((1,),1,2,-1,True,True))
        self.assertEqual(l.report({})['rawEvents'],0)

if __name__=='__main__':unittest.main()
