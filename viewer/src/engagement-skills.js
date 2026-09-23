/* Human-readable, exact engagement skill summaries. No inferred hit results. */
import { isAssignedOpeningUse } from './requested-opening-usage.js';
import { requestedRateRows, formatRequestedRates, formatRecordedFreezeCounts } from './requested-metrics.js?v=service-labels-v1&skill-ui-v2&sua-r-labels-v1&skill-label-audit-v1&opening-casts-v2&restrained-ui-v1';

export const skillFamilyOrder = [
  'Active1',
  'Active2',
  'Active3',
  'Active4',
  'WeaponSkill',
  'TacticalSkill',
  'GadgetSkill',
  'Other',
];

export function skillFamilyLabel(family) {
  return ({
    Active1: 'Q',
    Active2: 'W',
    Active3: 'E',
    Active4: 'R',
    WeaponSkill: '무기',
    TacticalSkill: '전술',
    GadgetSkill: '가젯',
    Other: '기타',
  })[family] || family;
}

function orderedCounts(counts = {}) {
  return Object.entries(counts)
    .filter(([, count]) => Number.isInteger(count) && count > 0)
    .sort(([left], [right]) => {
      const a = skillFamilyOrder.indexOf(left);
      const b = skillFamilyOrder.indexOf(right);
      return (a < 0 ? 99 : a) - (b < 0 ? 99 : b) || left.localeCompare(right);
    });
}

export function formatSkillUsage(counts = {}) {
  const rows = orderedCounts(counts);
  return rows.length
    ? rows.map(([family, count]) => `${skillFamilyLabel(family)} ${count}회`).join(' · ')
    : '사용 기록 없음';
}

export function aggregateHitRates(rows = [], cursor = null) {
  const grouped = new Map();
  for (const row of rows) {
    if (!row || !['projectile-shot', 'skill-cast'].includes(row.unit)) continue;
    let attempts = row.attemptCount;
    let hits = row.hitCount;
    if (Number.isFinite(cursor)) {
      // [castTick, finalHitFlag, attemptTick, firstEnemyHitTick].
      // Old two-field rows have no impact time and cannot drive a live rate.
      if (!Array.isArray(row.outcomes) || !row.outcomes.every(outcome =>
        Array.isArray(outcome) && outcome.length === 4 &&
        Number.isInteger(outcome[0]) && [0, 1].includes(outcome[1]) &&
        Number.isInteger(outcome[2]) && outcome[2] >= outcome[0] &&
        (outcome[1] === 1
          ? Number.isInteger(outcome[3]) && outcome[3] >= outcome[2]
          : outcome[3] === null))) continue;
      const visible = row.outcomes.filter(outcome => outcome[2] <= cursor);
      attempts = visible.length;
      hits = visible.reduce((sum, outcome) => sum + (outcome[1] === 1 && outcome[3] <= cursor ? 1 : 0), 0);
    }
    if (!Number.isInteger(attempts) || attempts <= 0 || !Number.isInteger(hits)) continue;
    const key = `${row.family}|${row.unit}`;
    const current = grouped.get(key) || { family: row.family, unit: row.unit, attempts: 0, hits: 0 };
    current.attempts += attempts;
    current.hits += hits;
    grouped.set(key, current);
  }
  return [...grouped.values()].sort((left, right) => {
    const a = skillFamilyOrder.indexOf(left.family);
    const b = skillFamilyOrder.indexOf(right.family);
    return (a < 0 ? 99 : a) - (b < 0 ? 99 : b) || left.unit.localeCompare(right.unit);
  });
}

export function formatHitRates(rows = [], cursor = null, showPercent = true) {
  const exact = aggregateHitRates(rows, cursor);
  if (!exact.length) return '정확히 셀 수 있는 적중 기록 없음';
  return exact.map(row => {
    const unit = row.unit === 'projectile-shot' ? '발' : '회';
    const percent = Math.round((row.hits / row.attempts) * 100);
    return `${skillFamilyLabel(row.family)} ${row.hits}/${row.attempts}${unit}${showPercent ? ` · ${percent}%` : ''}`;
  }).join(' · ');
}

export function engagementAt(player, cursor) {
  return (player?.skillOperation?.episodes || []).find(
    row => row.startTick <= cursor && cursor < row.endTick
  ) || null;
}

export function currentEngagementSkillViewModel(player, cursor) {
  const episode = engagementAt(player, cursor);
  if (!episode) return null;
  const counts = {};
  for (const row of player?.skillStartTimeline || []) {
    if (row[0] < episode.startTick && !isAssignedOpeningUse(episode, row)) continue;
    if (row[0] > cursor || row[0] >= episode.endTick) break;
    counts[row[1]] = (counts[row[1]] || 0) + 1;
  }
  return {
    episodeNumber: episode.teamEpisodeNumber,
    startTick: episode.startTick,
    endTick: episode.endTick,
    usage: formatSkillUsage(counts),
    accuracy: episode.requestedMetricsUnavailable ? '적중률 데이터를 불러오지 못했습니다' : episode.requestedMetricRows !== undefined ? formatRequestedRates(episode.requestedMetricRows, cursor, false) : formatHitRates(episode.hitRates, cursor, false),
  };
}

export function fullEngagementSkillViewModel(episode) {
  return {
    usage: formatSkillUsage(episode?.requestedSkillUsage?.familyCounts ?? episode?.familyCounts),
    accuracy: episode?.requestedMetricsUnavailable ? '적중률 데이터를 불러오지 못했습니다' : episode?.requestedMetricRows !== undefined ? formatRequestedRates(episode.requestedMetricRows, null, false) : formatHitRates(episode?.hitRates, null, false),
  };
}

export function allEngagementSkillViewModel(player) {
  const operation = player?.skillOperation || {};
  const freezeText = formatRecordedFreezeCounts(operation.requestedMetrics?.elenaFreezeCounts);
  return {
    engagementCount: (operation.episodes || []).length,
    skillStartCount: operation.requestedSkillUsage?.skillStartCount ?? operation.pvpSkillStartCount ?? 0,
    normalAttackStartCount: operation.pvpNormalAttackStartCount || 0,
    usage: formatSkillUsage(operation.requestedSkillUsage?.familyCounts ?? operation.familyCounts),
    accuracyRows: operation.requestedMetrics !== undefined ? requestedRateRows(operation.requestedMetrics.metrics) : aggregateHitRates(operation.hitRates),
    accuracy: (operation.requestedMetrics?.status === 'unavailable' ? '적중률 데이터를 불러오지 못했습니다' : operation.requestedMetrics !== undefined ? formatRequestedRates(operation.requestedMetrics.metrics) : formatHitRates(operation.hitRates)) + (freezeText ? ` · ${freezeText}` : ''),
  };
}
