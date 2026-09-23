import { familyLabel, skillInfoAt } from './team-rail.js?v=team-items-v1';
import { fullEngagementSkillViewModel } from './engagement-skills.js?v=service-labels-v1&skill-ui-v2&opening-casts-v2&restrained-ui-v1';

function clock(tick, fps) {
  if (!Number.isFinite(tick)) return null;
  const seconds = Math.max(0, Math.floor(tick / fps));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function contextLine(row) {
  const parts = [];
  if (Number.isFinite(row.nearbyAliveAllyCount) && Number.isFinite(row.nearbyAliveEnemyCount)) {
    parts.push(`주변 아군 ${row.nearbyAliveAllyCount}명 · 적 ${row.nearbyAliveEnemyCount}명`);
  }
  if (Number.isFinite(row.closestAliveTeammateMeters)) {
    parts.push(`가장 가까운 팀원 ${row.closestAliveTeammateMeters.toFixed(1)}m`);
  }
  return parts.join(' · ');
}

function personalMetricText(metrics) {
  if (!metrics) return null;
  const cell = (metric, label, unit = '') => {
    const known = metric?.status === 'available' && Number.isFinite(metric.value);
    const value = known ? `${Number.isInteger(metric.value) ? metric.value : metric.value.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')}${unit}` : '—';
    const note = known ? '' : '미확인';
    return { label, value, note };
  };
  const cells = [
    cell(metrics.crowdControl?.applied, '넣은 CC', '초'),
    cell(metrics.crowdControl?.received, '받은 CC', '초'),
    cell(metrics.cameraAppearances, metrics.cameraAppearances?.eventMeaning === 'exact-owned-CmdSpawn' ? '카메라 설치' : '카메라 기록', '회'),
    cell(metrics.droneAppearances, metrics.droneAppearances?.eventMeaning === 'exact-owned-CmdSpawn' ? '드론 사용' : '드론 기록', '회'),
  ];
  const summary = cells.map(item => `${item.label} ${item.value}`).join(' · ');
  const details = '넣은 CC은 대상별 합계 · 받은 CC은 겹친 시간 제외';
  return { summary, cells, details };
}

function deathCooldownLine(player, review, fps) {
  const hasDownTick = Number.isFinite(review.downTick);
  const eventTick = hasDownTick ? review.downTick : review.deathTick;
  if (!Number.isFinite(eventTick)) return '';
  const visibleFamilies = new Set(['Active1', 'Active2', 'Active3', 'Active4', 'WeaponSkill', 'TacticalSkill']);
  const rows = skillInfoAt(player, eventTick, fps)
    .filter(row => visibleFamilies.has(row.family))
    .map(row => `${familyLabel(row.family)} ${row.cls === 'is-unknown' ? '미확인' : row.status}`);
  const label = hasDownTick ? '다운 당시 쿨다운' : '사망 당시 쿨다운';
  return rows.length ? `${label} · ${rows.join(' · ')}` : '';
}

export function deathReviewViewModel(player, fps = 60) {
  const reviews = player?.deathReview?.deaths || [];
  const scenes = new Map((player?.sceneCoaching?.deaths || []).map(row => [row.deathNumber, row]));
  return reviews.map(review => {
    const scene = scenes.get(review.deathNumber) || {};
    const startTick = Number.isFinite(scene.combatStartTick) ? scene.combatStartTick : review.focusTick;
    const flow = [`교전 시작 ${clock(startTick, fps)}`];
    if (Number.isFinite(review.downTick)) flow.push(`다운 ${clock(review.downTick, fps)}`);
    flow.push(`사망 ${clock(review.deathTick, fps)}`);
    const matchingMetrics = (player?.personalEngagementMetrics || []).filter(metric => Number.isFinite(review.deathTick) && metric.startTick <= review.deathTick && review.deathTick <= metric.endTick);
    return {
      deathNumber: review.deathNumber,
      seekTick: Math.max(0, startTick - fps * 3),
      timeline: flow.join(' → '),
      context: contextLine(review),
      cooldowns: deathCooldownLine(player, review, fps),
      personalMetrics: matchingMetrics.length === 1 ? personalMetricText(matchingMetrics[0]) : null,
    };
  });
}

function entryRoleLabel(role) {
  if (role === 'initiator') return '먼저 진입';
  if (role === 'joiner') return '뒤이어 진입';
  return null;
}

export function engagementReviewViewModel(player, fps = 60) {
  const episodes = player?.sceneCoaching?.episodes || [];
  const skillEpisodes = new Map(
    (player?.skillOperation?.episodes || []).map(row => [row.teamEpisodeNumber, row])
  );
  return episodes.map((episode, index) => {
    const startTick = Number.isFinite(episode.startTick) ? episode.startTick : episode.entryTick;
    const endTick = Number.isFinite(episode.endTick) ? episode.endTick : startTick;
    const facts = [];
    const role = entryRoleLabel(episode.entryRole);
    if (role) facts.push(role);
    if (Number.isFinite(episode.nearbyAliveAllyCount) && Number.isFinite(episode.nearbyAliveEnemyCount)) {
      facts.push(`진입 때 주변 아군 ${episode.nearbyAliveAllyCount}명 · 적 ${episode.nearbyAliveEnemyCount}명`);
    }
    const skills = fullEngagementSkillViewModel(
      skillEpisodes.get(episode.teamEpisodeNumber) || episode
    );
    return {
      episodeNumber: episode.teamEpisodeNumber || index + 1,
      seekTick: Math.max(0, startTick - fps * 3),
      timeline: `${clock(startTick, fps)}–${clock(endTick, fps)}`,
      result: episode.survived === true ? '생존' : episode.survived === false ? '사망' : '결과 미확인',
      facts: facts.join(' · '),
      skillUsage: skills.usage,
      skillAccuracy: skills.accuracy,
      normalAttackStartCount: Number.isInteger(skillEpisodes.get(episode.teamEpisodeNumber)?.normalAttackStartCount)
        ? skillEpisodes.get(episode.teamEpisodeNumber).normalAttackStartCount
        : null,
      personalMetrics: personalMetricText((player?.personalEngagementMetrics || []).find(metric => metric.startTick === startTick && metric.endTick === endTick) || null),
    };
  });
}
