import test from 'node:test';
import assert from 'node:assert/strict';
import { bindEncounterDamage, encounterDamageCells } from './encounter-damage.js';
import { engagementReviewViewModel } from './death-review.js';

const episode = { teamEpisodeNumber: 1, startTick: 60, endTick: 120 };
function fixture() {
  const data = { meta: { reportId: 'r', clientVersion: '12.4.0' }, players: [1, 2].map(id => ({ publicPlayerId: id, characterCode: id, sceneCoaching: { episodes: [episode] } })) };
  const sidecar = { format: 'ercraft-encounter-damage.v1', sourceFixtureSha256: 'hash', reportId: 'r', clientVersion: '12.4.0', basis: 'recorded-events-estimate', calibratedToMatchTotal: false,
    players: data.players.map(p => ({ publicPlayerId: p.publicPlayerId, characterCode: p.characterCode, episodes: [{ episodeNumber: 1, startTick: 60, endTick: 120, dealt: { knownSubtotal: 1234, unknownEvents: 2 }, taken: { knownSubtotal: 0, unknownEvents: 0 } }] })) };
  return { data, sidecar };
}
test('existing estimates reach every card; missing data is distinct from zero', () => {
  const { data, sidecar } = fixture();
  assert.equal(encounterDamageCells(data.players[0], episode)[0].value, '—');
  bindEncounterDamage(data, sidecar, 'hash');
  for (const player of data.players) {
    const cells = engagementReviewViewModel(player)[0].damage;
    assert.deepEqual(cells.map(c => c.value), ['1,234', '0']);
    assert.match(cells[0].note, /미확정 2건 제외/);
  }
});
test('foreign source and calibration are rejected', () => {
  for (const change of [s => s.sourceFixtureSha256 = 'other', s => s.reportId = 'other', s => s.calibratedToMatchTotal = true]) {
    const { data, sidecar } = fixture(); change(sidecar);
    assert.throws(() => bindEncounterDamage(data, sidecar, 'hash'));
    assert.equal(data.players[0].encounterDamage, undefined);
  }
});

test('unknown-only damage is unavailable rather than zero', () => {
  const { data, sidecar } = fixture();
  sidecar.players[0].episodes[0].dealt.knownSubtotal = 0;
  bindEncounterDamage(data, sidecar, 'hash');
  const cells = encounterDamageCells(data.players[0], episode);
  assert.equal(cells[0].value, '—');
  assert.match(cells[0].note, /미확정 2건/);
  assert.equal(cells[1].value, '0');
});
test('late invalid player cannot partially attach earlier players', () => {
  for (const change of [e => e.endTick++, e => e.dealt.knownSubtotal = null, e => e.taken.unknownEvents = -1]) {
    const { data, sidecar } = fixture(); change(sidecar.players[1].episodes[0]);
    assert.throws(() => bindEncounterDamage(data, sidecar, 'hash'));
    assert.ok(data.players.every(p => p.encounterDamage === undefined));
  }
});
