import unittest
from skill_required_channel_guard import guarded_metric

class GuardTests(unittest.TestCase):
    def test_required_gap_blocks_before_calculation(self):
        def forbidden(**kw):self.fail('calculator invoked despite known gap')
        for count in (1,None,-1,True):
            r=guarded_metric(forbidden,dict(spec={'metricId':'m'},gaps=[dict(packetName='CmdDamage',count=count)]),{'CmdDamage'})
            self.assertIsNone(r['attemptCount']);self.assertIsNone(r['hitCount'])
            self.assertNotIn('outcomes',r)
    def test_no_declared_gap_preserves_exact_result_without_proving_completeness(self):
        result={'hitCount':2,'hitRate':0.5}
        for gaps in (None,[],[dict(packetName='CmdDamage',count=0)],[dict(packetName='CmdChat',count=3)],[dict(packetName='CmdDamageUnrelated',count=1)]):
            self.assertIs(guarded_metric(lambda **kw:result,dict(gaps=gaps),{'CmdDamage'}),result)

if __name__=='__main__':unittest.main()
