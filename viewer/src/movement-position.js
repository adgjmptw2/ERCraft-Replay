// Cache movement continuity once. Snapshot anchors do not cancel an active move.
const movingByTrack = new WeakMap();
function movementFlags(track) {
  if (movingByTrack.has(track)) return movingByTrack.get(track);
  let moving = false;
  const flags = track.map(row => {
    const source = String(row[3] || '');
    if (source === 'full-snapshot' || source === 'later-full-snapshot') return moving;
    moving = source.startsWith('CmdMove') && !source.endsWith(':destination') || source === 'CmdVLSHorizontalMove';
    return moving;
  });
  movingByTrack.set(track, flags);
  return flags;
}

export function movementPositionAt(track, cursor) {
  if (!track?.length || cursor < track[0][0]) return null;
  let lo = 0, hi = track.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (track[mid][0] <= cursor) lo = mid + 1;
    else hi = mid;
  }
  const index = lo - 1, current = track[index], next = track[index + 1];
  if (!next || !movementFlags(track)[index]) return [current[1], current[2]];
  const nextSource = String(next[3] || '');
  // A warp/transport exit is a discontinuity, never a path across the map.
  const continuousEnd = nextSource.startsWith('CmdMove') || nextSource === 'CmdStopMove' ||
    nextSource === 'CmdVLSHorizontalMove' || nextSource === 'full-snapshot' || nextSource === 'later-full-snapshot';
  if (!continuousEnd || next[0] <= current[0]) return [current[1], current[2]];
  const t = (cursor - current[0]) / (next[0] - current[0]);
  return [current[1] + (next[1] - current[1]) * t, current[2] + (next[2] - current[2]) * t];
}
