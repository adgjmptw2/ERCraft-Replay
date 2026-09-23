export function engagementIndexAt(episodes, tick, fps = 60) {
  if (!episodes.length) return -1;
  const active = episodes.findIndex(e => e.startTick <= tick && tick <= e.endTick);
  if (active >= 0) return active;
  const upcoming = episodes.findIndex(e => tick < e.startTick && e.startTick - tick <= fps * 5);
  if (upcoming >= 0) return upcoming;
  const next = episodes.findIndex(e => tick < e.startTick);
  return next < 0 ? episodes.length - 1 : Math.max(0, next - 1);
}

export function phaseAt(updates, tick) {
  let current = null;
  for (const row of updates) { if (row.tick > tick) break; current = row; }
  return current;
}

export function phaseSecondsRemaining(updates, tick, fps = 60) {
  const current = phaseAt(updates, tick);
  if (!current) return null;
  // Area-only refreshes repeat the phase with a zero duration; they do not reset its clock.
  const start = updates.find(row => row.tick <= tick && row.day === current.day && row.dayNight === current.dayNight && row.phase === current.phase);
  if (!Number.isFinite(start?.remainSeconds)) return null;
  return Math.max(0, Math.ceil(start.remainSeconds - (tick - start.tick) / fps));
}

export function keyboardSeekTick(tick, direction, fps, firstTick, lastTick) {
  return Math.max(firstTick, Math.min(lastTick, tick + direction * fps * 5));
}

export function visibleWorldEvents(events) {
  // A scratch marker is the same incoming drop when position and landing tick agree.
  return events.filter(row => !row.warningStatus?.includes('scratch-lifecycle') ||
    !events.some(other => other !== row && other.kind === row.kind &&
      other.activeTick === row.endTick && other.warningTick <= row.activeTick &&
      other.warningTick < other.activeTick &&
      other.position?.[0] === row.position?.[0] && other.position?.[1] === row.position?.[1]));
}
