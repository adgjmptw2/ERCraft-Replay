import copy
import json
import unittest

from decoder.practical_encounter_damage import build_damage_sidecar


class EncounterDamageTest(unittest.TestCase):
    def inputs(self):
        episode = dict(teamEpisodeNumber=1, startTick=60, endTick=120)
        player = dict(objectId=987, characterCode=1, teamNumber=1, sceneCoaching=dict(episodes=[episode]))
        private = dict(meta=dict(clientVersion='12.4'), sources=[dict(file='input.er', sha256='source')], players=[player])
        public = dict(meta=dict(clientVersion='12.4', reportId='public'), players=[dict(publicPlayerId=1, characterCode=1, teamNumber=1, sceneCoaching=player['sceneCoaching'])])
        cell = dict(knownSubtotal=0, unknownEvents=2)
        total = dict(insideEpisodeKnownSubtotal=0, outsideEpisodeKnownSubtotal=0, knownEventSubtotal=0, insideEpisodeUnknownEvents=2, outsideEpisodeUnknownEvents=0, unresolvedEvents=2)
        row = dict(player=987, episodes=[dict(startTick=60, endTick=120, dealt=cell, taken=cell)], totals=dict(dealt=total, taken=total), outside=dict(dealt=dict(knownSubtotal=0, unknownEvents=0), taken=dict(knownSubtotal=0, unknownEvents=0)))
        practical = dict(format='ercraft-practical-encounters.v1', runs=[dict(sourceSha256='source', players=[row])])
        return json.dumps(public).encode(), private, practical

    def test_valid_and_private_fields_excluded(self):
        result = build_damage_sidecar(*self.inputs())
        self.assertEqual(result['players'][0]['episodes'][0]['dealt'], dict(knownSubtotal=0, unknownEvents=2))
        self.assertFalse(result['calibratedToMatchTotal'])
        self.assertNotIn('objectId', json.dumps(result))
        self.assertNotIn('sourceSha256', json.dumps(result))
        self.assertNotIn('987', json.dumps(result))

    def test_reject_source_roster_window_and_sum_mismatch(self):
        for kind in ('source', 'roster', 'window', 'sum'):
            raw, private, practical = copy.deepcopy(self.inputs())
            run = practical['runs'][0]
            if kind == 'source': run['sourceSha256'] = 'other'
            if kind == 'roster': run['players'][0]['player'] = 123
            if kind == 'window': run['players'][0]['episodes'][0]['endTick'] += 1
            if kind == 'sum': run['players'][0]['totals']['dealt']['knownEventSubtotal'] = 1
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                build_damage_sidecar(raw, private, practical)


if __name__ == '__main__':
    unittest.main()
