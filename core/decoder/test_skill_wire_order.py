import unittest
from .skill_wire_order import event_within_cast,finish_lookup


class WireOrderTests(unittest.TestCase):
    def sample(self):
        a={'tick':100,'skillIdCode':7,'wireCategory':'commands','wireOrder':[1,0]}
        b={'tick':200,'skillIdCode':7,'wireCategory':'commands','wireOrder':[2,3]}
        ends=[{'tick':200,'skillIdCode':7,'playerObjectId':1,'wireCategory':'commands','wireOrder':[2,1]},
              {'tick':300,'skillIdCode':7,'playerObjectId':1,'wireCategory':'commands','wireOrder':[3,1]}]
        return a,b,finish_lookup(ends,1)

    def test_boundary_damage_follows_recorded_command_order(self):
        a,b,ends=self.sample()
        for ordinal,expected in [(0,(True,False)),(2,(False,False)),(4,(False,True))]:
            event={'tick':200,'wireCategory':'commands','wireOrder':[2,ordinal]}
            self.assertEqual((event_within_cast(a,200,event,ends),event_within_cast(b,300,event,ends)),expected)

    def test_unknown_or_unordered_event_keeps_both_possible_owners(self):
        a,b,ends=self.sample()
        for event in [{'tick':200},{'tick':200,'wireCategory':'ignoreOrderPackets','wireOrder':[2,0]}]:
            self.assertTrue(event_within_cast(a,200,event,ends))
            self.assertTrue(event_within_cast(b,300,event,ends))

    def test_missing_finish_order_cannot_be_invented(self):
        a,b,ends=self.sample();ends[7,200][0].pop('wireOrder')
        self.assertTrue(event_within_cast(a,200,{'tick':200,'wireCategory':'commands','wireOrder':[2,4]},ends))

    def test_order_does_not_extend_numeric_lifetime(self):
        a,b,ends=self.sample()
        self.assertFalse(event_within_cast(a,200,{'tick':201,'wireCategory':'commands','wireOrder':[1,1]},ends))
        self.assertTrue(event_within_cast(a,200,{'tick':150},ends))


if __name__=='__main__':unittest.main()
