// Preserve sub-tick time for smooth movement at any display refresh/playback rate.
export function advanceReplayCursor(cursor, elapsedMs, fps, speed, endTick) {
  return Math.min(endTick, cursor + Math.max(0, elapsedMs) * fps * speed / 1000);
}
