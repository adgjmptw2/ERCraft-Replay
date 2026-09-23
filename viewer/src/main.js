import { applyMissPolicy } from './miss-policy.js';
import { applyMeasuredItemGeometry } from './item-art-layout.js?v=2';
/* ERCraft Replay Analytics - Main Application Entry */

import { state, initTheme, toggleTheme } from './state.js?v=readable-review-v3';
import { advanceReplayCursor } from './playback-clock.js';
import { engagementIndexAt, phaseAt, phaseSecondsRemaining, keyboardSeekTick } from './replay-navigation.js?v=phase-clock-v2';
import { TeamRail } from './team-rail.js?v=skill-levels-v1';
import { MapRenderer, hasWildlifePortrait, wildlifeAssetUrl } from './map-renderer.js?v=rift-background-v6';
import { applyFrontendAssetOverrides, auditVisibleAssets } from './asset-overrides.js?v=team-items-v1';
import { combatLogViewModel, teamCombatRowsAt } from './team-combat-log.js?v=skill-levels-v1';
import { engagementReviewViewModel } from './death-review.js?v=team-items-v1&service-labels-v1&skill-ui-v2&opening-casts-v2&restrained-ui-v1&compact-devices-v1';
import { allEngagementSkillViewModel, currentEngagementSkillViewModel, skillFamilyLabel } from './engagement-skills.js?v=service-labels-v1&skill-ui-v2&opening-casts-v2&restrained-ui-v1';
import { bindRequestedMetrics, markRequestedUnavailable, requestedRateRows, multiTargetLabels, groupSkillDisplayRows, skillSelectorGroups } from './requested-metrics.js?v=service-labels-v1&skill-ui-v2&sua-r-labels-v1&skill-label-audit-v1&opening-casts-v2&restrained-ui-v1';
import { bindPersonalEngagementMetrics } from './personal-engagement.js?v=personal-metrics-v3';

const PLAYER_FOCUS_ZOOM = 3;

let appData = null;
let teamRail = null;
let mapRenderer = null;
let animationFrameId = null;
let lastFrameTime = 0;
let lastCombatLogKey = '';
let lastCurrentEngagementKey = '';
let lastEngagementReviewKey = '';
let selectedSkillMetricId = null;
let engagementExpanded = false;
let engagementSelection = null;
let lastAutoEngagement = '';

// Keep one log node: mobile puts it below the map, with disclosure state preserved.
const mobileLogMedia = window.matchMedia('(max-width:700px)');
const combatLogNode = document.getElementById('mapTeamCombatLog');
const desktopLogParent = combatLogNode?.parentElement;
function placeCombatLog() {
  const target = mobileLogMedia.matches ? document.getElementById('mobileCombatLog') : desktopLogParent;
  if (target && combatLogNode && combatLogNode.parentElement !== target) target.append(combatLogNode);
}
mobileLogMedia.addEventListener('change', placeCombatLog);
placeCombatLog();

function formatClock(ticks, fps = 60) {
  const totalSeconds = Math.max(0, Math.floor(ticks / fps));
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function formatPersonalMetric(metric, label, unit = '') {
  if (!metric) return '';
  if (metric.status !== 'available' || !Number.isFinite(metric.value)) return `${label} 미확인`;
  const value = Number.isInteger(metric.value) ? metric.value : metric.value.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
  return `${label} ${value}${unit}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function renderTeamCombatLog(focusPlayer) {
  const host = document.getElementById('mapTeamCombatLog');
  if (!host || !focusPlayer) return;
  const rows = teamCombatRowsAt(appData, focusPlayer, state.cursor);
  const key = `${focusPlayer.teamNumber}|${rows.map(row => row.publicEventId).join(',')}`;
  if (key === lastCombatLogKey) return;
  lastCombatLogKey = key;

  if (!rows.length) {
    host.hidden = true;
    host.replaceChildren();
    return;
  }

  host.hidden = false;
  host.innerHTML = rows.map(row => {
    const view = combatLogViewModel(appData, focusPlayer, row);
    const actors = view.attacker
      ? `${escapeHtml(view.attacker)}<span class="arrow">→</span>${escapeHtml(view.victim)}`
      : escapeHtml(view.victim);
    return `<div class="team-log-line ${view.className}" data-event-id="${view.eventId}">` +
      `<span class="kind">${view.kind}</span><span class="actors">${actors}</span></div>`;
  }).join('');
}

function renderHitText(text) {
  return escapeHtml(text).replace(/(\d+인)/g, '<small class="multi-target-badge">$1</small>');
}

function renderCurrentEngagementSkills(focusPlayer) {
  const host = document.getElementById('mapEngagementSkills');
  if (!host || !focusPlayer) return;
  const model = currentEngagementSkillViewModel(focusPlayer, state.cursor);
  const key = model
    ? `${focusPlayer.publicPlayerId}|${model.episodeNumber}|${model.usage}|${model.accuracy}`
    : `${focusPlayer.publicPlayerId}|none`;
  if (key === lastCurrentEngagementKey) return;
  lastCurrentEngagementKey = key;
  if (!model) {
    host.hidden = true;
    host.replaceChildren();
    return;
  }
  host.hidden = false;
  host.innerHTML = `
    <div class="engagement-skill-title">현재 교전 ${model.episodeNumber}</div>
    <div><span>사용</span><b>${escapeHtml(model.usage)}</b></div>
    <div><span>적중</span><b>${renderHitText(model.accuracy)}</b></div>
  `;
}

function renderEngagementReview(focusPlayer) {
  const host = document.getElementById('mapEngagementReview');
  const count = document.getElementById('engagementReviewCount');
  if (!host || !focusPlayer) return;
  // These records are immutable after load and do not depend on playback time.
  if (lastEngagementReviewKey === focusPlayer.publicPlayerId) return;
  const model = engagementReviewViewModel(focusPlayer, appData.meta?.targetFrameRate || 60);
  lastEngagementReviewKey = focusPlayer.publicPlayerId;
  if (count) count.textContent = `${model.length}회`;

  if (!model.length) {
    host.innerHTML = '<div class="death-review-empty">확인된 대인 교전이 없습니다.</div>';
    return;
  }

  host.innerHTML = model.map(row => `
    <article class="engagement-card">
      <button type="button" class="engagement-card-seek" aria-label="교전 ${row.episodeNumber}, ${escapeHtml(row.timeline)} 장면 보기" data-seek-tick="${row.seekTick}">
        <span class="engagement-card-title">교전 ${row.episodeNumber}</span><span class="engagement-card-time">${escapeHtml(row.timeline)}</span><span class="engagement-card-result ${row.result === '사망' ? 'is-death' : ''}">${escapeHtml(row.result)}</span><span class="engagement-scene-link" aria-hidden="true">장면 보기 ↗</span>
      </button>
      <div class="engagement-skill-overview">
        <div><span class="record-label">사용</span><span class="engagement-values">${renderRecordValues(row.skillUsage)}${row.normalAttackStartCount === null ? '' : `<span class="engagement-value" aria-label="평타 시도 ${row.normalAttackStartCount}회">평타 ${row.normalAttackStartCount}회</span>`}</span></div>
        <div><span class="record-label">적중</span><span class="engagement-values">${renderRecordValues(row.skillAccuracy)}</span></div>
      </div>
      ${row.personalMetrics ? `<div class="personal-metric-grid">${row.personalMetrics.cells.map(cell => `<div class="personal-metric-cell" title="${escapeHtml(cell.note)}"><span>${escapeHtml(cell.label)}</span><b class="${cell.value === '—' ? 'is-unknown' : /^0(?:초|회)$/.test(cell.value) ? 'is-zero' : ''}">${escapeHtml(cell.value)}</b></div>`).join('')}</div>` : ''}
      ${row.facts ? `<details><summary>진입 상황</summary><span class="engagement-card-facts">${escapeHtml(row.facts)}</span></details>` : ''}
    </article>
  `).join('');
}

async function loadData() {
  const loadingEl = document.getElementById('loadingIndicator');
  if (loadingEl) loadingEl.style.display = 'flex';

  try {
    if (window.__FIXTURE_DATA__) {
      appData = window.__FIXTURE_DATA__;
    } else {
      const resp = await fetch('./public/ui-assets/combat-analysis-personal-pvp-v1.json', { cache: 'no-store' });
      if (!resp.ok) throw new Error(`HTTP error ${resp.status}`);
      const bytes = await resp.arrayBuffer();
      appData = JSON.parse(new TextDecoder().decode(bytes));
      markRequestedUnavailable(appData);
      let fixtureSha;
      const digest = await crypto.subtle.digest('SHA-256', bytes);
      fixtureSha = [...new Uint8Array(digest)].map(x => x.toString(16).padStart(2, '0')).join('');
      try {
        const response = await fetch('./public/ui-assets/skill-levels-v1.json');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const levels = await response.json();
        if (levels.sourceFixtureSha256 !== fixtureSha) throw new Error('Skill level source mismatch');
        for (const row of levels.players) {
          const player = appData.players.find(p => p.publicPlayerId === row.publicPlayerId);
          if (player) player.skillLevelTimeline = row.skillLevelTimeline;
        }
      } catch (error) { console.error('Skill levels unavailable:', error); }
      const transportResponse = await fetch('./public/ui-assets/transport-visual-v1.json');
      if (transportResponse.ok) {
        const transport = await transportResponse.json();
        if (transport.sourceFixtureSha256 === fixtureSha) {
          for (const row of transport.objects) {
            const target = appData.worldMap.staticObjects.find(item => item.publicWorldObjectId === row.publicWorldObjectId);
            if (target) target.transportModeTimeline = row.transportModeTimeline;
          }
        }
      }
      try {
        const metrics = await fetch('./public/ui-assets/requested-map-metrics-v18.json', { cache: 'no-store' });
        if (!metrics.ok) throw new Error(`HTTP ${metrics.status}`);
        const scored = await metrics.json(); applyMissPolicy(scored); bindRequestedMetrics(appData, scored, fixtureSha);
      } catch (error) {
        console.error('Requested skill metrics unavailable:', error);
      }
      try {
        const personal = await fetch('./public/ui-assets/personal-engagement-metrics-v3.json', { cache: 'no-store' });
        if (!personal.ok) throw new Error(`HTTP ${personal.status}`);
        bindPersonalEngagementMetrics(appData, await personal.json(), fixtureSha);
      } catch (error) {
        console.error('Personal engagement metrics unavailable:', error);
      }
    }
  } catch (err) {
    console.error('Failed to load replay data:', err);
    if (loadingEl) {
      loadingEl.innerHTML = `<div class="notice">리플레이 데이터를 불러오지 못했습니다: ${err.message}</div>`;
    }
    return;
  }

  const beforeOverrides = auditVisibleAssets(appData);
  applyFrontendAssetOverrides(appData);
  await applyMeasuredItemGeometry(appData);
  const afterOverrides = auditVisibleAssets(appData);
  window.__UI_ASSET_AUDIT__ = { beforeOverrides, afterOverrides };

  if (loadingEl) loadingEl.style.display = 'none';
  initApp();
}

function initApp() {
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.has('theme')) {
    state.theme = urlParams.get('theme');
  }
  initTheme();
  setupThemeToggle();

  // Pick first player if not set
  if (!state.playerId && appData.players?.length) {
    state.playerId = appData.players[0].publicPlayerId;
  }
  if (urlParams.has('tick')) {
    state.cursor = Number(urlParams.get('tick'));
  } else if (!state.cursor && appData.meta?.firstTick) {
    state.cursor = appData.meta.firstTick;
  }

  setupPlayerSelect();
  setupTabs();
  setupTransport();
  setupMapControls();
  setupWildlifeGuide();
  setupEngagementReview();

  // Initialize Components
  const railContainer = document.getElementById('teamLoadout');
  if (railContainer) {
    teamRail = new TeamRail(railContainer, appData, onSelectPlayer);
  }

  const mapCanvas = document.getElementById('mapCanvas');
  if (mapCanvas) {
    mapRenderer = new MapRenderer(mapCanvas, appData, state, () => updateUI(false));
  }

  updateUI(true);
  startPlaybackLoop();
}

function onSelectPlayer(playerId) {
  state.playerId = playerId;
  selectedSkillMetricId = null;
  state.cameraLock = 'focus';
  state.mapView.zoom = PLAYER_FOCUS_ZOOM;
  const select = document.getElementById('playerSelect');
  if (select) select.value = String(playerId);
  updateUI(true);
  if (state.view !== 'map') renderOtherView(state.view);
}

function seekToMap(tick) {
  lastAutoEngagement = '';
  state.cursor = Number(tick);
  state.cameraLock = 'focus';
  state.mapView.zoom = PLAYER_FOCUS_ZOOM;
  state.view = 'map';
  document.querySelectorAll('.tabs button').forEach(button => button.classList.toggle('active', button.dataset.view === 'map'));
  const stageEl = document.querySelector('.stage');
  if (stageEl) {
    stageEl.style.display = 'grid';
    stageEl.scrollIntoView({ block: 'start', behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }
  const otherViewEl = document.getElementById('otherViews');
  if (otherViewEl) otherViewEl.style.display = 'none';
  updateUI(true);
}

function setupEngagementReview() {
  document.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', ' '].includes(event.key) || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || state.view !== 'map') return;
    if (event.target.closest('select, textarea, input:not([type="range"]):not([type="checkbox"]):not([type="radio"]), [contenteditable="true"], [role="dialog"], dialog[open]')) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.key === ' ') {
      if (!event.repeat) document.getElementById('play').click();
      return;
    }
    state.cursor = keyboardSeekTick(state.cursor, event.key === 'ArrowRight' ? 1 : -1, appData.meta.targetFrameRate || 60, appData.meta.firstTick, appData.meta.lastTick);
    lastAutoEngagement = '';
    updateUI(true);
  }, true);
  for (const [hostId, toggleId] of [
    ['mapEngagementReview', 'engagementReviewToggle'],
  ]) {
    const host = document.getElementById(hostId);
    const toggle = document.getElementById(toggleId);
    if (!host || !toggle) continue;
    toggle.addEventListener('click', () => {
      engagementExpanded = !engagementExpanded;
      toggle.setAttribute('aria-expanded', String(engagementExpanded));
      toggle.textContent = engagementExpanded ? '접기' : '전체 펼치기';
      updateUI();
    });
    host.addEventListener('click', event => {
      const card = event.target.closest('[data-seek-tick]');
      if (!card) return;
      seekToMap(card.dataset.seekTick);
    });
  }
  document.getElementById('engagementPicker').addEventListener('click', event => {
    const button = event.target.closest('[data-engagement-index]');
    if (!button) return;
    engagementSelection = Number(button.dataset.engagementIndex);
    updateUI();
  });
  const phases = appData.phaseClock?.restrictionUpdates || [];
  const select = document.getElementById('mapPhaseSelect');
  const seen = new Set();
  for (const row of phases) {
    const key = `${row.day}:${row.dayNight}`;
    if (seen.has(key)) continue;
    seen.add(key);
    select.add(new Option(`${row.day}일차 ${row.dayNightName === 'Night' ? '밤' : '낮'}`, String(row.tick)));
  }
  select.addEventListener('change', () => seekToMap(Number(select.value)));
}

function renderRecordValues(text) {
  const groups = [];
  for (const part of String(text).split(' · ')) {
    if (/^\d+(?:\.\d+)?%$/.test(part) && groups.length) groups[groups.length - 1] += ` · ${part}`;
    else groups.push(part);
  }
  return groups.map(value => `<span class="engagement-value">${renderHitText(value)}</span>`).join('');
}

function updateReplayNavigation(focusPlayer) {
  const episodes = focusPlayer?.sceneCoaching?.episodes || [];
  const fps = appData.meta.targetFrameRate || 60;
  const index = engagementIndexAt(episodes, state.cursor, fps);
  const key = `${focusPlayer.publicPlayerId}:${index}`;
  if (key !== lastAutoEngagement) { engagementSelection = index; lastAutoEngagement = key; }
  const host = document.getElementById('mapEngagementReview');
  const picker = document.getElementById('engagementPicker');
  if (picker.dataset.player !== String(focusPlayer.publicPlayerId)) {
    picker.dataset.player = String(focusPlayer.publicPlayerId);
    picker.innerHTML = episodes.map((e, i) => `<button type="button" data-engagement-index="${i}"><b>교전 ${e.teamEpisodeNumber || i + 1}</b><span>${formatClock(e.startTick, fps)}–${formatClock(e.endTick, fps)}</span><span>${e.survived === true ? '생존' : e.survived === false ? '사망' : '미확인'}</span></button>`).join('');
  }
  const displayKey = `${focusPlayer.publicPlayerId}:${engagementSelection}:${engagementExpanded}`;
  if (host.dataset.displayKey !== displayKey) {
    host.dataset.displayKey = displayKey;
    [...host.children].forEach((card, i) => { card.hidden = !engagementExpanded && i !== engagementSelection; });
    [...picker.children].forEach((button, i) => button.setAttribute('aria-pressed', String(i === engagementSelection)));
  }
  const phase = phaseAt(appData.phaseClock?.restrictionUpdates || [], state.cursor);
  if (phase) {
    const rows = appData.phaseClock.restrictionUpdates;
    const start = rows.find(r => r.day === phase.day && r.dayNight === phase.dayNight);
    const select = document.getElementById('mapPhaseSelect');
    if (select.value !== String(start.tick)) select.value = String(start.tick);
    const seconds = phaseSecondsRemaining(rows, state.cursor, fps);
    const label = seconds === null ? '—' : `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    const clock = document.getElementById('mapPhaseClock');
    if (clock.textContent !== label) clock.textContent = label;
  }
}

function setupThemeToggle() {
  const btn = document.getElementById('themeToggle');
  if (!btn) return;
  btn.addEventListener('click', () => {
    toggleTheme();
    btn.textContent = state.theme === 'dark' ? '☀️' : '🌙';
  });
  btn.textContent = state.theme === 'dark' ? '☀️' : '🌙';
}

function setupPlayerSelect() {
  const select = document.getElementById('playerSelect');
  if (!select || !appData.players) return;
  select.innerHTML = appData.players.map(p => {
    return `<option value="${p.publicPlayerId}" ${p.publicPlayerId === state.playerId ? 'selected' : ''}>#${p.teamNumber} ${p.characterName}</option>`;
  }).join('');

  select.addEventListener('change', e => {
    onSelectPlayer(Number(e.target.value));
  });
}

function setupTabs() {
  const tabs = document.querySelectorAll('.tabs button');
  tabs.forEach(btn => {
    btn.addEventListener('click', () => {
      if (btn.disabled) return;
      tabs.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.view = btn.dataset.view;
      const stageEl = document.querySelector('.stage');
      const otherViewEl = document.getElementById('otherViews');

      if (state.view === 'map') {
        if (stageEl) stageEl.style.display = 'grid';
        if (otherViewEl) otherViewEl.style.display = 'none';
        updateUI();
      } else {
        if (stageEl) stageEl.style.display = 'none';
        if (otherViewEl) {
          otherViewEl.style.display = 'block';
          renderOtherView(state.view);
        }
      }
    });
  });
}

function renderOtherView(view) {
  const container = document.getElementById('otherViews');
  if (!container) return;

  if (view === 'overview') {
    container.innerHTML = '<section class="panel"><h2>🔒 경기 요약</h2><div class="notice">경기 요약은 준비 중입니다.</div></section>';
    return;
  }

  if (view === 'skills') {
    const player = appData.players?.find(row => row.publicPlayerId === state.playerId) || appData.players?.[0];
    const model = allEngagementSkillViewModel(player);
    const requestedRows = player?.skillOperation?.requestedMetrics?.metrics || [];
    const statusOnlyRows = requestedRows
      .filter(row => ['unresolved-evidence', 'unavailable-no-metric-result', 'checked-sources-no-cast', 'no-combat-sample'].includes(row.status))
      .filter(row => !model.accuracyRows.some(exact => exact.metricId === row.metricId))
      .map(row => ({ ...row, label: row.displayLabel || row.label || skillFamilyLabel(row.family), attempts: 0, hits: 0, unknown: row.unresolvedCombatCastCount || 0, selectionKey: `${row.metricId}::root` }));
    const baseSkillRows = groupSkillDisplayRows([...model.accuracyRows, ...statusOnlyRows], player.characterCode);
    const selectorGroups = skillSelectorGroups(baseSkillRows);
    const skillRows = [...baseSkillRows, ...selectorGroups.filter(g => g.rows.length > 1 && g.summary).map(g => g.summary)];
    if (!selectedSkillMetricId && selectorGroups[0]?.summary) selectedSkillMetricId = selectorGroups[0].summary.selectionKey;
    if (!skillRows.some(row => (row.selectionKey || `${row.metricId}::root`) === selectedSkillMetricId)) selectedSkillMetricId = skillRows[0] ? (skillRows[0].selectionKey || `${skillRows[0].metricId}::root`) : null;
    const selectedRow = skillRows.find(row => (row.selectionKey || `${row.metricId}::root`) === selectedSkillMetricId);
    const selectedEmptyStatus = ['unresolved-evidence', 'unavailable-no-metric-result'].includes(selectedRow?.status) ? '계산 미확인' : selectedRow?.status === 'no-combat-sample' ? '교전 표본 없음' : '사용 기록 없음';
    const episodes = player?.skillOperation?.episodes || [];
    const fps = appData.meta?.targetFrameRate || 60;
    const sceneRows = selectedRow ? [...episodes].sort((a, b) => (a.startTick || 0) - (b.startTick || 0)).map(episode => {
      if (selectedRow.memberMetricIds) {
        const records = (episode.requestedMetricRows || []).filter(row => selectedRow.memberMetricIds.includes(row.metricId));
        const rows = requestedRateRows(records);
        const attempts = rows.reduce((n, row) => n + row.attempts, 0);
        const hits = rows.reduce((n, row) => n + row.hits, 0);
        const unknown = records.reduce((n, row) => n + (row.unresolvedCombatCastCount || 0), 0);
        if (!attempts && !unknown) return null;
        const result = attempts ? `${hits}/${attempts}회 · ${Math.round(hits / attempts * 100)}%${unknown ? ` · 미확인 ${unknown}회` : ''}` : `미확인 ${unknown}회`;
        return { episode, result, multiTargets: multiTargetLabels(records) };
      }
      const parent = (episode.requestedMetricRows || []).find(row => row.metricId === selectedRow.metricId);
      const record = (selectedRow.phasePath ? selectedRow.phasePath.split('.') : []).reduce((row, phase) => row?.phaseMetrics?.[phase], parent);
      const used = record && (Number.isInteger(record.attemptCount) && record.attemptCount > 0 || Number.isInteger(record.unresolvedCombatCastCount) && record.unresolvedCombatCastCount > 0);
      if (!used) return null;
      const exact = requestedRateRows([record])[0];
      const unit = record.unit === 'projectile-shot' ? '발' : '회';
      const unknown = record.unresolvedCombatCastCount ? ` · 미확인 ${record.unresolvedCombatCastCount}${unit}` : '';
      const result = exact ? `${exact.hits}/${exact.attempts}${unit} · ${Math.round(exact.hits / exact.attempts * 100)}%${unknown}` : `미확인 ${record.unresolvedCombatCastCount}${unit}`;
      return { episode, result, multiTargets: multiTargetLabels([record]) };
    }).filter(Boolean) : [];
    const renderSkillButton = row => {
      const unit = row.unit === 'projectile-shot' ? '발' : '회';
      const rate = row.attempts > 0 ? `${Math.round(row.hits / row.attempts * 100)}%` : '—';
      const status = row.status === 'unresolved-evidence' || row.status === 'unavailable-no-metric-result' ? '계산 미확인' : row.status === 'no-combat-sample' ? '교전 표본 없음' : '사용 기록 없음';
      const key = row.selectionKey || `${row.metricId}::root`;
      return `<button type="button" class="skill-select-button" data-skill-key="${escapeHtml(key)}" aria-pressed="${String(key === selectedSkillMetricId)}"><b>${escapeHtml(row.label || skillFamilyLabel(row.family))}</b><span class="tnum">${rate}</span><small class="tnum">${row.attempts > 0 ? `${row.hits}/${row.attempts}${unit}` : row.unknown ? `미확인 ${row.unknown}${unit}` : status}</small></button>`;
    };
    const activeGroup = selectorGroups.find(group => group.rows.includes(selectedRow) || group.summary === selectedRow);
    const skillButtons = selectorGroups.length ? selectorGroups.map(group => {
      if (group.rows.length === 1) return renderSkillButton({ ...group.rows[0], label: group.label });
      const selected = group === activeGroup;
      return `<button type="button" class="skill-select-button" data-skill-group="${escapeHtml(group.label)}" aria-pressed="${selected}" aria-expanded="${selected}"><b>${escapeHtml(group.label)}</b><span class="tnum">${group.summary?.attempts > 0 ? `${Math.round(group.summary.hits / group.summary.attempts * 100)}%` : '—'}</span><small>${group.summary?.attempts > 0 ? `${group.summary.hits}/${group.summary.attempts}${group.summary.unit === 'projectile-shot' ? '발' : '회'}` : '세부 기록'}</small></button>`;
    }).join('') : '<div class="skill-rate-empty">표시할 스킬 기록이 없습니다.</div>';
    const detailButtons = activeGroup?.rows.length > 1 ? `<div class="skill-variant-list" role="group" aria-label="${escapeHtml(activeGroup.label)} 세부 선택">${[...(activeGroup.summary ? [activeGroup.summary] : []), ...activeGroup.rows.filter(row => !row.phasePath)].map(renderSkillButton).join('')}</div>` : '';

    const sceneCards = sceneRows.length ? sceneRows.map(({ episode, result, multiTargets }) => `<article class="skill-scene-row"><div><b>교전 ${episode.teamEpisodeNumber}</b><span class="tnum">${formatClock(episode.startTick, fps)}–${formatClock(episode.endTick, fps)}</span><strong class="tnum" title="${escapeHtml(result)}">${escapeHtml(result.replace(/ · \d+%/, ''))}</strong><span class="skill-multi-target">${escapeHtml(multiTargets)}</span></div><button type="button" aria-label="교전 ${episode.teamEpisodeNumber} 장면 보기" data-seek-tick="${Math.max(0, episode.startTick - fps * 3)}">보기 ↗</button></article>`).join('') : `<div class="skill-rate-empty">이 스킬의 사용이 확인된 교전이 없습니다.</div>`;
    const unavailableCount = requestedRows.filter(row => ['unresolved-evidence', 'unavailable-no-metric-result'].includes(row.status)).length;
    const absentCount = requestedRows.filter(row => row.status === 'checked-sources-no-cast').length;
    const noCombatSampleCount = requestedRows.filter(row => row.status === 'no-combat-sample').length;
    const accuracyNotices = [
      player?.skillOperation?.requestedMetrics?.status === 'unavailable' ? '적중률 데이터를 불러오지 못했습니다' : '',
      unavailableCount ? `계산 미확인 ${unavailableCount}항목` : '',
      absentCount ? `사용 기록 없음 ${absentCount}항목` : '',
      noCombatSampleCount ? `교전 표본 없음 ${noCombatSampleCount}항목` : '',
    ].filter(Boolean);
    container.innerHTML = `
      <section class="panel skill-analysis-view">
        <h2>#${player.teamNumber} ${escapeHtml(player.characterName)} 스킬 분석</h2>
        <p class="skill-analysis-intro">스킬별 적중률을 확인하고, 기본·강화 기록과 교전 장면을 살펴보세요.</p>
        <div class="skill-analysis-layout">
          <div class="skill-select-list" style="--skill-columns: ${Math.min(4, selectorGroups.length)}" role="group" aria-label="스킬 선택">${skillButtons}</div>
          <div class="skill-selected-detail">${detailButtons}<h3>교전별 스킬 기록</h3><p class="skill-selected-summary">${escapeHtml(selectedRow?.label || '스킬 선택')} · ${selectedRow?.attempts > 0 ? `총 ${selectedRow.attempts}${selectedRow.unit === 'projectile-shot' ? '발' : '회'} 중 ${selectedRow.hits} 적중` : selectedRow?.unknown ? `미확인 ${selectedRow.unknown}${selectedRow.unit === 'projectile-shot' ? '발' : '회'}` : selectedEmptyStatus}</p><div class="skill-record-columns"><span>교전</span><span>시간</span><span>적중 / 시도</span><span>다중타격</span><span></span></div>${sceneCards}</div>
        </div>
        ${accuracyNotices.length ? `<div class="skill-analysis-notices">${accuracyNotices.map(notice => `<span>${escapeHtml(notice)}</span>`).join('')}</div>` : ''}
      </section>
    `;
    container.onclick = event => {
      const groupButton = event.target.closest('[data-skill-group]');
      if (groupButton) {
        const group = selectorGroups.find(row => row.label === groupButton.dataset.skillGroup);
        const row = group?.summary || group?.rows[0];
        if (row) {
          selectedSkillMetricId = row.selectionKey || `${row.metricId}::root`;
          renderOtherView('skills');
          queueMicrotask(() => container.querySelector(`[data-skill-key="${CSS.escape(selectedSkillMetricId)}"]`)?.focus());
        }
        return;
      }
      const skillButton = event.target.closest('[data-skill-key]');
      if (skillButton) {
        selectedSkillMetricId = skillButton.dataset.skillKey;
        renderOtherView('skills');
        queueMicrotask(() => container.querySelector(`[data-skill-key="${CSS.escape(selectedSkillMetricId)}"]`)?.focus());
        return;
      }
      const seekButton = event.target.closest('[data-seek-tick]');
      if (!seekButton) return;
      seekToMap(seekButton.dataset.seekTick);
    };
  } else {
    container.innerHTML = `
      <section class="panel">
        <h2>${view.toUpperCase()} 분석</h2>
        <div class="notice">상세 분석 데이터가 준비되어 있습니다. 상단 탭에서 이동 지도를 확인하세요.</div>
      </section>
    `;
  }
}

function setupTransport() {
  const playBtn = document.getElementById('play');
  const slider = document.getElementById('time');
  const clockLabel = document.getElementById('timeLabel');

  if (slider && appData.meta) {
    slider.min = appData.meta.firstTick || 0;
    slider.max = appData.meta.lastTick || 10000;
    slider.value = state.cursor;
  }

  if (playBtn) {
    playBtn.addEventListener('click', () => {
      state.playing = !state.playing;
      lastFrameTime = 0;
      playBtn.textContent = state.playing ? '일시정지' : '재생';
      playBtn.setAttribute('aria-pressed', String(state.playing));
    });
  }

  if (slider) {
    slider.addEventListener('input', e => {
      lastAutoEngagement = '';
      state.cursor = Number(e.target.value);
      if (clockLabel) clockLabel.textContent = formatClock(state.cursor, appData.meta?.targetFrameRate);
      updateUI(true);
    });
  }

  // Speed Buttons
  const speedBtns = document.querySelectorAll('.seg button[data-speed]');
  speedBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      speedBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.speed = Number(btn.dataset.speed);
    });
  });
}

function setupMapControls() {
  const zoomIn = document.getElementById('mapZoomIn');
  const zoomOut = document.getElementById('mapZoomOut');
  const zoomReset = document.getElementById('mapZoomReset');

  if (zoomIn) zoomIn.addEventListener('click', () => {
    state.mapView.zoom = Math.min(6, state.mapView.zoom * 1.25);
    updateUI(true);
  });
  if (zoomOut) zoomOut.addEventListener('click', () => {
    state.mapView.zoom = Math.max(0.8, state.mapView.zoom / 1.25);
    updateUI(true);
  });
  if (zoomReset) zoomReset.addEventListener('click', () => {
    state.cameraLock = 'free';
    state.mapView.zoom = 1;
    state.mapView.ox = 0;
    state.mapView.oy = 0;
    updateUI(true);
  });

  // Layer toggles
  document.querySelectorAll('.map-toggle input[data-layer]').forEach(input => {
    input.checked = Boolean(state.layers[input.dataset.layer]);
    input.addEventListener('change', e => {
      const layer = e.target.dataset.layer;
      state.layers[layer] = e.target.checked;
      updateUI(true);
    });
  });

  // Canvas Drag / Pan & Wheel Zoom
  const canvas = document.getElementById('mapCanvas');
  const shell = canvas?.parentElement;
  if (canvas && shell) {
    let isDragging = false;
    let startX = 0, startY = 0;
    let startOx = 0, startOy = 0;

    canvas.addEventListener('pointerdown', e => {
      if (e.button !== 0) return;
      isDragging = true;
      startX = e.clientX;
      startY = e.clientY;
      startOx = state.mapView.ox;
      startOy = state.mapView.oy;
      canvas.setPointerCapture(e.pointerId);
      shell.classList.add('is-dragging');
    });

    canvas.addEventListener('pointermove', e => {
      if (!isDragging) return;
      if (e.clientX !== startX || e.clientY !== startY) state.cameraLock = 'free';
      state.mapView.ox = startOx + (e.clientX - startX);
      state.mapView.oy = startOy + (e.clientY - startY);
      updateUI();
    });

    const endDrag = e => {
      if (!isDragging) return;
      isDragging = false;
      try { canvas.releasePointerCapture(e.pointerId); } catch (_) {}
      shell.classList.remove('is-dragging');
    };

    canvas.addEventListener('pointerup', endDrag);
    canvas.addEventListener('pointercancel', endDrag);
    canvas.addEventListener('lostpointercapture', endDrag);

    canvas.addEventListener('wheel', e => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const cx = e.clientX - rect.left;
      const cy = e.clientY - rect.top;
      const zoomFactor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      const prevZoom = state.mapView.zoom;
      const nextZoom = Math.max(0.8, Math.min(6.0, prevZoom * zoomFactor));

      if (nextZoom !== prevZoom) {
        state.cameraLock = 'free';
        state.mapView.ox = cx - (cx - state.mapView.ox) * (nextZoom / prevZoom);
        state.mapView.oy = cy - (cy - state.mapView.oy) * (nextZoom / prevZoom);
        state.mapView.zoom = nextZoom;
        updateUI();
      }
    }, { passive: false });
  }
}

function setupWildlifeGuide() {
  const guidePopover = document.getElementById('wildlifeGuidePopover');
  const instances = appData.wildlife?.instances || appData.wildlifeCandidates || [];
  if (!guidePopover || !instances.length) return;

  const seen = new Set();
  const rows = [];
  for (const animal of instances) {
    if (!animal.monsterName || seen.has(animal.monsterName) || animal.monsterName.startsWith('Monster')) continue;
    seen.add(animal.monsterName);
    rows.push({
      name: animal.monsterName,
      color: animal.mapMarkerColor || '#ffffff',
      style: animal.mapMarkerStyle || 'species-triangle',
      assetKey: animal.assetKey || '',
    });
  }

  const species = ['chicken', 'bat', 'wild-dog', 'boar', 'wolf', 'bear', 'raven'];
  const rank = row => {
    const base = row.assetKey.replace(/^mutant-/, '');
    const index = species.indexOf(base);
    return index < 0 ? 100 : index * 2 + Number(row.assetKey.startsWith('mutant-'));
  };
  rows.sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name, 'ko'));
  guidePopover.innerHTML = `
    <div class="wildlife-guide-grid">
      ${rows.filter(r => rank(r) < 100).map(r => `
        <span class="wildlife-guide-row">
          <span class="wildlife-guide-icon">${hasWildlifePortrait(r.assetKey)
            ? `<img class="wildlife-guide-image ${r.assetKey.endsWith('raven') ? 'is-raven' : ''}" src="${wildlifeAssetUrl(r.assetKey)}" alt="">`
            : `<i class="wildlife-guide-marker ${r.style === 'neutral-monster-red-dot' ? 'dot' : 'triangle'}" style="--guide-color:${r.color}"></i>`}</span>
          <span>${escapeHtml(r.name)}</span>
        </span>
      `).join('')}
    </div>
  `;
}

function updateUI(forceRail = false) {
  if (!appData) return;
  const focusPlayer = appData.players?.find(p => p.publicPlayerId === state.playerId) || appData.players?.[0];

  // Update Clock Label & Slider
  const clockLabel = document.getElementById('timeLabel');
  const slider = document.getElementById('time');
  const clock = formatClock(state.cursor, appData.meta?.targetFrameRate);
  if (clockLabel && clockLabel.textContent !== clock) clockLabel.textContent = clock;
  if (slider) slider.value = state.cursor;

  // Update Team Rail (Differential update)
  if (teamRail) {
    teamRail.update(focusPlayer, state.cursor, forceRail);
  }

  // Render Map
  if (mapRenderer && state.view === 'map') {
    mapRenderer.render(state.cursor, focusPlayer);
  }
  renderTeamCombatLog(focusPlayer);
  renderCurrentEngagementSkills(focusPlayer);
  renderEngagementReview(focusPlayer);
  updateReplayNavigation(focusPlayer);
}

// Single Animation Loop (Performance Rule #1)
function startPlaybackLoop() {
  function tick(timestamp) {
    if (state.playing && appData?.meta) {
      if (!lastFrameTime) lastFrameTime = timestamp;
      const deltaMs = timestamp - lastFrameTime;
      const targetFps = appData.meta.targetFrameRate || 60;
      const nextCursor = advanceReplayCursor(state.cursor, deltaMs, targetFps, state.speed, appData.meta.lastTick);
      lastFrameTime = timestamp;

      if (nextCursor > state.cursor) {
        state.cursor = nextCursor;

        if (state.cursor >= appData.meta.lastTick) {
          state.playing = false;
          const playBtn = document.getElementById('play');
          if (playBtn) {
            playBtn.textContent = '재생';
            playBtn.setAttribute('aria-pressed', 'false');
          }
        }
        updateUI();
      }
    } else {
      lastFrameTime = timestamp;
    }
    animationFrameId = requestAnimationFrame(tick);
  }

  animationFrameId = requestAnimationFrame(tick);
}

function setupPrivacyNotice() {
  const toggle = document.getElementById('privacyNoticeToggle');
  const panel = document.getElementById('privacyNoticePanel');
  if (!toggle || !panel) return;
  toggle.addEventListener('click', () => {
    const open = panel.hidden;
    panel.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
  });
}

// Kick off
window.addEventListener('DOMContentLoaded', () => {
  setupPrivacyNotice();
  loadData();
});
