/* Validate and display core-assigned opening casts; never assign a cast here. */
const key = (tick, family, skillId) => `${tick}:${family}:${skillId}`;

function validateCounts(usage) {
  if (!usage || !Number.isInteger(usage.skillStartCount) || usage.skillStartCount < 0 ||
      !usage.familyCounts || typeof usage.familyCounts !== 'object' || Array.isArray(usage.familyCounts) ||
      Object.values(usage.familyCounts).some(n => !Number.isInteger(n) || n < 0) ||
      Object.values(usage.familyCounts).reduce((a, b) => a + b, 0) !== usage.skillStartCount) {
    throw new Error('선행 스킬 사용 횟수가 유효하지 않습니다.');
  }
}

export function prepareOpeningUsage(player, operation) {
  if (operation.skillUsage === undefined) {
    if (operation.episodes.some(e => e.skillUsage !== undefined)) throw new Error('선행 스킬 사용 집계가 불완전합니다.');
    return null;
  }
  validateCounts(operation.skillUsage);
  const used = new Set();
  const episodes = new Map();
  let total = 0;
  for (const episode of operation.episodes) {
    const usage = episode.skillUsage;
    validateCounts(usage);
    if (!Array.isArray(usage.openingSkillUses)) throw new Error('선행 시전 기록이 없습니다.');
    for (const use of usage.openingSkillUses) {
      const id = key(use.castTick, use.family, use.skillId);
      if (!Number.isInteger(use.castTick) || use.castTick >= episode.startTick ||
          typeof use.family !== 'string' || !Number.isInteger(use.skillId) || used.has(id) ||
          operation.episodes.some(e => e.startTick <= use.castTick && use.castTick < e.endTick) ||
          (player.skillStartTimeline || []).filter(r => key(r[0], r[1], r[3]) === id).length !== 1) {
        throw new Error('선행 시전의 원본 연결 또는 교전 배정이 유효하지 않습니다.');
      }
      used.add(id);
    }
    total += usage.skillStartCount;
    episodes.set(`${episode.startTick}:${episode.endTick}`, usage);
  }
  if (total !== operation.skillUsage.skillStartCount) throw new Error('교전별 사용 횟수와 전체 집계가 다릅니다.');
  return { total: operation.skillUsage, episodes };
}

export function isAssignedOpeningUse(episode, row) {
  return (episode.requestedSkillUsage?.openingSkillUses || []).some(use =>
    use.castTick === row[0] && use.family === row[1] && use.skillId === row[3]);
}
