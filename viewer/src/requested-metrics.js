/* Consume core-produced rates; never infer skill ownership in the UI. */
import { prepareOpeningUsage } from './requested-opening-usage.js';
export function markRequestedUnavailable(data) {
  for (const player of data.players) {
    player.skillOperation.requestedMetrics = { status: 'unavailable', metrics: [] };
    for (const episode of player.skillOperation.episodes || []) {
      episode.requestedMetricRows = [];
      episode.requestedMetricsUnavailable = true;
    }
  }
}

export function bindRequestedMetrics(data, sidecar, fixtureSha256) {
  if (sidecar.format !== 'er-requested-map-metrics.v1' || sidecar.fallbackUsed !== false ||
      sidecar.sourceFixtureSha256 !== fixtureSha256 || sidecar.reportId !== data.meta.reportId ||
      sidecar.clientVersion !== data.meta.clientVersion) throw new Error('적중률 데이터의 경기 식별이 다릅니다.');
  const keyed = new Map(sidecar.players.map(p => [p.publicPlayerId, p]));
  if (keyed.size !== sidecar.players.length || keyed.size !== data.players.length) throw new Error('플레이어 연결이 불완전합니다.');
  const prepared = data.players.map(player => {
    const source = keyed.get(player.publicPlayerId);
    if (!source || source.characterCode !== player.characterCode) throw new Error('플레이어 연결이 다릅니다.');
    const sourceMetrics = source.requestedMetrics.metrics.filter(row => !isExcludedMetric(source.characterCode, row));
    const labels = metricDisplayLabels(sourceMetrics, source.characterCode);
    const operation = { ...source.requestedMetrics, metrics: sourceMetrics.map(row => ({ ...row, displayLabel: labels.get(row.metricId) })) };
    if (operation.status !== 'requested-metrics-with-explicit-timeline-coverage' || operation.fallbackUsed !== false) throw new Error('적중률 계약이 유효하지 않습니다.');
    const definitions = new Map(operation.metrics.map(m => [m.metricId, m]));
    if (definitions.size !== operation.metrics.length) throw new Error('지표 정의가 중복됩니다.');
    const episodes = new Map(operation.episodes.map(e => [`${e.startTick}:${e.endTick}`, e.metrics.filter(row => !isExcludedMetric(source.characterCode, row)).map(row => {
      const definition = definitions.get(row.metricId);
      if (!definition || typeof definition.label !== 'string') throw new Error('교전 지표의 정의가 없습니다.');
      return { ...row, label: definition.label, displayLabel: definition.displayLabel };
    })]));
    if (episodes.size !== operation.episodes.length) throw new Error('교전 구간이 중복됩니다.');
    const targets = player.skillOperation.episodes || [];
    if (targets.length !== episodes.size || targets.some(e => !episodes.has(`${e.startTick}:${e.endTick}`))) throw new Error('교전 구간이 다릅니다.');
    const usage = prepareOpeningUsage(player, operation);
    return { player, operation, targets, episodes, usage };
  });
  for (const { player, operation, targets, episodes, usage } of prepared) {
    player.skillOperation.requestedMetrics = operation;
    if (usage) player.skillOperation.requestedSkillUsage = usage.total;
    else delete player.skillOperation.requestedSkillUsage;
    for (const target of targets) {
      target.requestedMetricRows = episodes.get(`${target.startTick}:${target.endTick}`);
      if (usage) target.requestedSkillUsage = usage.episodes.get(`${target.startTick}:${target.endTick}`);
      else delete target.requestedSkillUsage;
      delete target.requestedMetricsUnavailable;
    }
  }
}

const PHASE_LABELS = {
  innerFrozen: '안쪽 빙결', 'wall-stun': '벽 충돌 기절',
  'forced-rope-collision': '로프 충돌', path: '경로 타격', arrival: '도착 타격',
  'outer-or-reinforced': '외곽·강화',
  blockPath: '블록 경로',
  center: '중앙', bookmarkStun: '책갈피 기절', bookmarkDamage: '책갈피 피해',
  firstRange: '첫 타격', actualPull: '끌기 성공', initial: '최초 타격', fall: '낙하 타격',
  'first-landing': '첫 착지', 'second-landing': '두 번째 착지',
  'rook-path': '룩 경로', 'triggered-pieces': '연쇄 기물', 'scroll-explosion': '두루마리 폭발'
};

export function metricDisplayLabel(row) {
  if (/:23:1023300:any$/.test(row.metricId || '')) return 'W';
  if (/:42:1042500:creation-hit$/.test(row.metricId || '')) return 'R 생성 타격';
  if (/:42:1042500:end-hit$/.test(row.metricId || '')) return 'R 종료 타격';
  if (row.characterCode === 28 && SUA_METRIC_LABELS[row.metricId]) return SUA_METRIC_LABELS[row.metricId];
  const ardaLabels = {1066200:'Q',1066210:'강화Q',1066300:'W',1066310:'강화W',1066400:'E',1066410:'강화E',1066420:'강화E',1066430:'강화E'};
  const arda = (row.metricId || '').match(/:66:(\d+):any$/);
  if (arda && ardaLabels[arda[1]]) return ardaLabels[arda[1]];
  if (/:52:1052410:any$/.test(row.metricId || '')) return '강화E';
  if (row.displayLabel) return row.displayLabel;
  const key = (row.label || '').match(/^(강화)?([QWER])/);
  const mode = row.mode || (row.metricId || '').split(':').at(-1);
  if (key) {
    const base = `${key[1] || ''}${key[2]}`;
    if (mode === 'any' && !(row.label || '').includes('[')) return base;
    if (mode === 'both-hit') return `${base} · 동일 대상 2타`;
    if (mode === 'fetter') return `${base} · 속박`;
  }
  const label = row.label || '';
  const camilo = label.match(/^(Q|R)\s*\[CamiloActive(?:1|4)_([123])\]/);
  if (camilo) {
    const name = camilo[1] === 'Q'
      ? { '1': 'Q', '2': '강화Q', '3': 'EQ' }[camilo[2]]
      : 'R';
    return label.replace(camilo[0], name).trim();
  }
  const clean = label.replace(/\s*\[[^\]]*\]/g, '').trim();
  return /\[[^\]]*Reinforce/.test(label) && !clean.includes('강화') ? `${clean} 강화` : clean;
}

const SUA_METRIC_LABELS = {
  'user-request-20260905-all90-v1:28:1028510:any': 'RQ',
  'user-request-20260905-all90-v1:28:1028530:any': 'RE',
};

export function isExcludedSuaMetric(characterCode, row) {
  return characterCode === 28 && (row?.metricId?.endsWith(':1028500:any') || row?.metricId?.endsWith(':1028520:blind'));
}

function isExcludedMetric(characterCode, row) {
  if (characterCode === 55 && row?.metricId?.endsWith(':1055430:any')) return true;
  // Owned child effects already contribute to the player's parent-use result.
  if (characterCode === 66 && /:1066(?:600|610|700|710):any$/.test(row?.metricId || '')) return true;
  return row?.status === 'not-applicable-static-non-executable' || isExcludedSuaMetric(characterCode, row);
}

export function metricDisplayLabels(rows = [], characterCode = null) {
  const groups = new Map();
  for (const row of rows) {
    const label = metricDisplayLabel({ ...row, characterCode });
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(row);
  }
  const result = new Map();
  for (const [label, group] of groups) {
    // Number distinct definitions; never merge their counts or invent skill names.
    group.forEach((row, i) => {
      const camiloUltimate = /^R\s*\[CamiloActive4_[123]\]/.test(row.label || '');
      result.set(row.metricId, group.length > 1 && !camiloUltimate && !/^.*:66:10664[123]0:any$/.test(row.metricId || '') ? `${label}${i + 1}` : label);
    });
  }
  return result;
}

function* displayMetrics(rows, parentLabel = '', parentPath = '') {
  const labels = metricDisplayLabels(rows);
  for (const row of rows) {
    const label = parentLabel || labels.get(row.metricId);
    yield { row, label, phasePath: parentPath };
    if (/:52:10524[01]0:any$/.test(row.metricId || '')) continue;
    for (const [name, phase] of Object.entries(row.phaseMetrics || {})) {
      const phasePath = parentPath ? `${parentPath}.${name}` : name;
      yield* displayMetrics([phase], `${label} · ${PHASE_LABELS[name] || name}`, phasePath);
    }
  }
}

export function multiTargetLabels(rows = [], cursor = null) {
  const counts = new Set();
  for (const row of rows) {
    if (row.multiTargetStatus !== 'verified-per-attempt-target-counts') continue;
    let targets = row.distinctEnemyTargetsPerAttempt || [];
    if (Number.isFinite(cursor)) {
      if (row.multiTargetTimelineStatus !== 'verified-first-contact-ticks') continue;
      targets = (row.distinctEnemyTargetFirstHitTicksPerAttempt || []).map(ticks => ticks.filter(t => t <= cursor).length);
    }
    for (const count of targets) if (Number.isInteger(count) && count >= 2) counts.add(count);
  }
  return [...counts].sort((a,b) => a-b).map(count => `${count}인`).join(' ');
}

export function requestedRateRows(rows = [], cursor = null) {
  const result = [];
  for (const { row, label, phasePath } of displayMetrics(rows)) {
    if (!['calculable-observed', 'calculable-experimental', 'verified-timed-outcomes', 'experimental-timed-outcomes'].includes(row.status) ||
        !['skill-cast', 'projectile-shot'].includes(row.unit)) continue;
    let attempts = row.attemptCount, hits = row.hitCount;
    if (Number.isFinite(cursor)) {
      if (!Array.isArray(row.outcomes) || !row.outcomes.every(o => Array.isArray(o) && o.length === 4 &&
          Number.isInteger(o[0]) && Number.isInteger(o[2]) && o[2] >= o[0] &&
          (o[1] === 0 ? o[3] === null : o[1] === 1 && Number.isInteger(o[3]) && o[3] >= o[2]))) continue;
      const visible = row.outcomes.filter(o => o[2] <= cursor);
      attempts = visible.length;
      hits = visible.filter(o => o[1] === 1 && o[3] <= cursor).length;
    }
    if (!Number.isInteger(attempts) || attempts <= 0 || !Number.isInteger(hits) || hits < 0 || hits > attempts) continue;
    const bound = Number.isFinite(cursor) ? null : row.unresolvedHitRateBounds;
    const range = bound && Number.isFinite(bound.minimum) && Number.isFinite(bound.maximum) &&
      bound.minimum >= 0 && bound.maximum <= 1 && bound.minimum <= bound.maximum &&
      bound.unknownOutcomesFilled === false && bound.classifiedOutcomesAssumedCorrect === true
      ? ` · 미확인 반영 범위 ${(bound.minimum * 100).toFixed(1)}~${(bound.maximum * 100).toFixed(1)}%` : '';
    result.push({ multiTargets: multiTargetLabels([row], cursor), range, regionEstimates: Number.isFinite(cursor) ? 0 : (row.estimatedRegionCastCount || 0), metricId: row.metricId, phasePath, selectionKey: `${row.metricId}::${phasePath || 'root'}`, label, unit: row.unit, attempts, hits,
      experimental: row.calculationConfidence === 'experimental' || ['calculable-experimental', 'experimental-timed-outcomes'].includes(row.status), unknown: row.unresolvedCombatCastCount || 0 });
  }
  return result;
}

export function formatRequestedRates(rows = [], cursor = null, showPercent = true) {
  const rates = groupSkillDisplayRows(requestedRateRows(rows, cursor));
  const text = rates.map(r => `${r.label} ${r.hits}/${r.attempts}${r.unit === 'projectile-shot' ? '발' : '회'}${r.multiTargets ? ` ${r.multiTargets}` : ''}${showPercent ? ` · ${Math.round(r.hits / r.attempts * 100)}%` : ''}${r.unknown ? ` · 미확인 ${r.unknown}회` : ''}${r.range}${r.regionEstimates ? ` · 위치·지역 추정 ${r.regionEstimates}회` : ''}`).join(' · ');
  const displayed = [...displayMetrics(rows)];
  const unavailable = displayed.filter(({ row }) => ['unresolved-evidence', 'unavailable-no-metric-result'].includes(row.status)).length;
  const absent = displayed.filter(({ row }) => row.status === 'checked-sources-no-cast').length;
  const noCombatSample = displayed.filter(({ row }) => row.status === 'no-combat-sample').length;
  // Cancellation totals have no public event timestamps; show only in aggregate views.
  const cancelled = Number.isFinite(cursor) ? [] : displayed.filter(({ row }) =>
    Number.isInteger(row.provisionallyExcludedCancelledCastCount) && row.provisionallyExcludedCancelledCastCount > 0)
    .map(({ row, label }) => `${label} 취소 집계 제외 ${row.provisionallyExcludedCancelledCastCount}회`);
  // The exporter supplies confirmed tip totals, not tip event timestamps.
  // Keep them in aggregate views rather than revealing future successes live.
  const tips = Number.isFinite(cursor) ? [] : displayed.filter(({row}) => Number.isInteger(row.confirmedTipCastCount) && row.confirmedTipCastCount >= 0)
    .map(({row,label}) => `${label} 끝부분 확정 ${row.confirmedTipCastCount}회`);
  return (text || '현재 계산 가능한 적중 기록 없음') + (unavailable ? ` · 계산 미확인 ${unavailable}항목` : '') + (absent ? ` · 사용 기록 없음 ${absent}항목` : '') + (noCombatSample ? ` · 교전 표본 없음 ${noCombatSample}항목` : '') + (cancelled.length ? ` · ${cancelled.join(' · ')}` : '') + (tips.length ? ` · ${tips.join(' · ')}` : '');
}

export function formatRecordedFreezeCounts(counts) {
  if (!counts) return '';
  if (!['complete','partial-source-attribution'].includes(counts.status) || counts.fallbackUsed !== false) return '빙결 성공 횟수 확인 불가';
  const c=counts.combat;
  const keys=['totalSuccessCount','passiveSuccessCount','ultimateInnerSuccessCount','unresolvedSourceSuccessCount'];
  if (!c || keys.some(k=>!Number.isInteger(c[k]) || c[k]<0) || c.totalSuccessCount !== c.passiveSuccessCount+c.ultimateInnerSuccessCount+c.unresolvedSourceSuccessCount) return '빙결 성공 횟수 확인 불가';
  return `교전 빙결 성공 ${c.totalSuccessCount}회 · 패시브 ${c.passiveSuccessCount}회 · 궁 중앙 ${c.ultimateInnerSuccessCount}회 · 원인 미확인 ${c.unresolvedSourceSuccessCount}회`;
}

// Group only the three distinct Camilo R cast definitions for presentation.
export function groupSkillDisplayRows(rows, characterCode = null) {
  let result = rows;
  const definitions = [
    {character:39, pattern:/:39:10395[012]0:any$/, label:'R', key:'camilo-r::root'},
    {character:66, pattern:/:66:10664[123]0:any$/, label:'강화E', key:'arda-enhanced-e::root'},
  ];
  for (const definition of definitions) {
  if (characterCode !== null && characterCode !== definition.character) continue;
  const matches = row => !row.phasePath && definition.pattern.test(row.metricId || '') && row.unit === 'skill-cast';
  rows = result;
  const members = rows.filter(matches);
  if (!members.length) continue;
  const grouped = { ...members[0], label: definition.label, selectionKey: definition.key,
    memberMetricIds: members.map(row => row.metricId),
    multiTargets: [...new Set(members.flatMap(row => (row.multiTargets || '').split(' ').filter(Boolean)))].sort((a,b) => parseInt(a)-parseInt(b)).join(' '),
    attempts: members.reduce((n, row) => n + row.attempts, 0),
    hits: members.reduce((n, row) => n + row.hits, 0),
    unknown: members.reduce((n, row) => n + (row.unknown || 0), 0),
    experimental: members.some(row => row.experimental),
  };
  let inserted = false;
  result = rows.flatMap(row => {
    if (!matches(row)) return [row];
    if (inserted) return [];
    inserted = true;
    return [grouped];
  });
  }
  return result;
}
export const groupCamiloUltimateRows = groupSkillDisplayRows;

export function skillSelectorGroups(rows) {
  const groups = new Map();
  for (const row of rows) {
    const root = (row.label || '').split(' · ')[0];
    const match = root.match(/^(강화)?([QWER])(?:\s*강화)?\d*$/);
    const label = match ? match[2] : root;
    if (!groups.has(label)) groups.set(label, {label, rows: []});
    groups.get(label).rows.push(row);
  }
  return [...groups.values()].map(group => {
    const parents = group.rows.filter(row => !row.phasePath);
    const units = new Set(parents.map(row => row.unit).filter(Boolean));
    const ids = parents.flatMap(row => row.memberMetricIds || [row.metricId]);
    const sourceGroups = ids.map(id => id?.split(':').slice(0,-1).join(':'));
    const canSum = units.size === 1 && new Set(sourceGroups).size === sourceGroups.length;
    const primary = parents.find(row => (row.metricId || '').endsWith(':any') && row.label === group.label);
    const summary = !canSum && primary ? { ...primary, label: `${group.label} 전체` } : canSum ? { ...parents[0], label: `${group.label} 전체`,
      selectionKey: `skill-group-${group.label}::all`, memberMetricIds: ids,
      attempts: parents.reduce((n,row) => n+(row.attempts||0),0),
      hits: parents.reduce((n,row) => n+(row.hits||0),0),
      unknown: parents.reduce((n,row) => n+(row.unknown||0),0), phasePath: '',
    } : null;
    return {...group, summary};
  });
}
