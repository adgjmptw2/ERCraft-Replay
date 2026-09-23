import test from 'node:test';
import assert from 'node:assert/strict';
import { engagementReviewViewModel } from './death-review.js';

const episode = { teamEpisodeNumber: 1, startTick: 60, endTick: 120, survived: true };
const skillEpisode = { teamEpisodeNumber: 1, normalAttackStartCount: 10, hitRates: [] };

test('engagement view model exposes normal attack attempts separately', () => {
  const player = { sceneCoaching: { episodes: [episode] }, skillOperation: { episodes: [skillEpisode] } };
  assert.equal(engagementReviewViewModel(player)[0].normalAttackStartCount, 10);
  assert.equal(engagementReviewViewModel({ sceneCoaching: { episodes: [episode] }, skillOperation: { episodes: [{ ...skillEpisode, normalAttackStartCount: undefined }] } })[0].normalAttackStartCount, null);
});
