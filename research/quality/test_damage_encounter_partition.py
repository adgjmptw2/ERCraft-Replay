import unittest
from damage_accounting_ledger import DamageFact,DamageLedger
from damage_encounter_partition import partition
class PartitionTests(unittest.TestCase):
    def test_boundaries_overlap_unknown_and_outside(self):
        ledger=DamageLedger([1,2]);ticks={}
        for i,tick,amount in [(1,10,20),(2,15,None),(3,30,7)]:
            fact=DamageFact((i,),1,2,amount,True,True);ledger.add(fact);ticks[(i,)]=tick
        result=partition(ledger,ticks,{1:[[10,20],[15,20]],2:[]})['players']
        self.assertEqual(result[1]['dealt'],dict(insideKnown=20,outsideKnown=7,insideUnknown=1,outsideUnknown=0,multipleWindowEvents=1))
        self.assertEqual(result[2]['taken']['outsideKnown'],27)
    def test_end_tick_is_outside(self):
        ledger=DamageLedger([1,2]);ledger.add(DamageFact((1,),1,2,7,True,True))
        result=partition(ledger,{(1,):20},{1:[[10,20]]})['players']
        self.assertEqual(result[1]['dealt']['outsideKnown'],7)
    def test_owner_window_not_object_window(self):
        ledger=DamageLedger([1,2]);ledger.add(DamageFact((1,),99,2,30,True,False,1,True))
        result=partition(ledger,{(1,):10},{1:[[10,11]]})['players']
        self.assertEqual(result[1]['dealt']['insideKnown'],30)
        self.assertEqual(result[2]['taken']['outsideKnown'],0)
if __name__=='__main__':unittest.main()
