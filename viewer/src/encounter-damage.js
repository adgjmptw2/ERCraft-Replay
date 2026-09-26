/* Read precomputed estimates only; never infer damage from final match totals. */
export function bindEncounterDamage(data, sidecar, fixtureSha256) {
  if (!sidecar || sidecar.format !== 'ercraft-encounter-damage.v1' ||
      sidecar.basis !== 'recorded-events-estimate' || sidecar.calibratedToMatchTotal !== false ||
      sidecar.sourceFixtureSha256 !== fixtureSha256 || sidecar.reportId !== data.meta?.reportId ||
      sidecar.clientVersion !== data.meta?.clientVersion || !Array.isArray(sidecar.players)) {
    throw new Error('교전 피해량의 경기 식별이 다릅니다.');
  }
  const sources = new Map(sidecar.players.map(p => [p.publicPlayerId, p]));
  if (sources.size !== sidecar.players.length || sources.size !== data.players.length) throw new Error('교전 피해량 연결이 불완전합니다.');
  const prepared = data.players.map(player => {
    const source = sources.get(player.publicPlayerId), expected = player.sceneCoaching?.episodes;
    if (!source || source.characterCode !== player.characterCode || !Array.isArray(source.episodes) ||
        !Array.isArray(expected) || expected.length !== source.episodes.length ||
        new Set(source.episodes.map(e => e.episodeNumber)).size !== source.episodes.length ||
        source.episodes.some((e, i) => !Number.isInteger(e.startTick) || !Number.isInteger(e.endTick) || e.endTick <= e.startTick ||
          e.episodeNumber !== expected[i].teamEpisodeNumber || e.startTick !== expected[i].startTick || e.endTick !== expected[i].endTick ||
          ['dealt', 'taken'].some(s => !e[s] || ['knownSubtotal', 'unknownEvents'].some(k => !Number.isSafeInteger(e[s][k]) || e[s][k] < 0)))) {
      throw new Error('교전 피해량의 구간 또는 수치가 유효하지 않습니다.');
    }
    return [player, structuredClone(source.episodes)];
  });
  for (const [player, episodes] of prepared) player.encounterDamage = episodes;
}

export function encounterDamageCells(player, episode) {
  const row = player.encounterDamage?.find(e => e.episodeNumber === episode.teamEpisodeNumber && e.startTick === episode.startTick && e.endTick === episode.endTick);
  return [['dealt', '가한 피해'], ['taken', '받은 피해']].map(([side, label]) => ({
    label,
    value: row && (row[side].knownSubtotal > 0 || row[side].unknownEvents === 0) ? row[side].knownSubtotal.toLocaleString('ko-KR') : '—',
    note: row ? `추정${row[side].unknownEvents ? ` · 미확정 ${row[side].unknownEvents}건 제외` : ''}` : '미확인',
  }));
}
