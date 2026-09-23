/* Bind the exact personal engagement sidecar without changing replay data. */
export function bindPersonalEngagementMetrics(data, sidecar, fixtureSha256) {
  if (!sidecar || sidecar.format !== 'er-personal-engagement-metrics.v1' || sidecar.fallbackUsed !== false ||
      sidecar.sourceFixtureSha256 !== fixtureSha256 || sidecar.reportId !== data.meta?.reportId ||
      sidecar.clientVersion !== data.meta?.clientVersion) throw new Error('개인 교전 지표의 경기 식별이 다릅니다.');
  const sources = new Map(sidecar.players.map(player => [player.publicPlayerId, player]));
  if (sources.size !== sidecar.players.length || sources.size !== data.players.length) throw new Error('개인 교전 지표 연결이 불완전합니다.');
  const prepared = data.players.map(player => {
    const source = sources.get(player.publicPlayerId);
    const expected = player.combatJudgment?.personalEpisodes;
    if (!source || source.characterCode !== player.characterCode || !Array.isArray(expected) || expected.length !== source.episodes.length || expected.some((episode, index) => episode.personalEpisodeNumber !== source.episodes[index]?.personalEpisodeNumber || episode.startTick !== source.episodes[index]?.startTick || episode.endTick !== source.episodes[index]?.endTick) || source.episodes.some((episode, index, rows) => !Number.isInteger(episode.personalEpisodeNumber) ||
        !Number.isInteger(episode.startTick) || !Number.isInteger(episode.endTick) || episode.endTick <= episode.startTick ||
        rows.findIndex(other => other.personalEpisodeNumber === episode.personalEpisodeNumber) !== index)) {
      throw new Error('개인 교전 지표 구간이 유효하지 않습니다.');
    }
    return [player, source.episodes];
  });
  for (const [player, episodes] of prepared) player.personalEngagementMetrics = episodes;
}
