/* Small, presentation-only selector for the selected team's combat feed. */

function lowerBound(rows, value) {
  let lo = 0;
  let hi = rows.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (rows[mid].tick < value) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

const combinedRowsCache = new WeakMap();
const majorObjectiveNames = new Set(['위클라인', '감마', '오메가', '알파']);

export function majorObjectiveKillRows(data) {
  return (data.wildlife?.instances || [])
    .filter(row => {
      const name = String(row.monsterName || '').split(' · ')[0];
      return majorObjectiveNames.has(name)
        && Number.isInteger(row.deathTick)
        && Number.isInteger(row.killerPublicPlayerId);
    })
    .map(row => ({
      publicEventId: `objective-${row.publicWildlifeId}`,
      tick: row.deathTick,
      kind: 'objective-kill',
      attackerPublicPlayerId: row.killerPublicPlayerId,
      objectiveName: String(row.monsterName).split(' · ')[0],
    }));
}

function combinedRows(data) {
  const cached = combinedRowsCache.get(data);
  if (cached) return cached;
  const rows = [
    ...(data.teamCombatLog?.items || []),
    ...majorObjectiveKillRows(data),
  ].sort((left, right) => left.tick - right.tick || String(left.publicEventId).localeCompare(String(right.publicEventId)));
  combinedRowsCache.set(data, rows);
  return rows;
}

export function teamCombatRowsAt(data, focusPlayer, cursor, limit = 3) {
  if (!focusPlayer) return [];
  const rows = combinedRows(data);
  const fps = data.meta?.targetFrameRate || 60;
  const windowTicks = (data.teamCombatLog?.displaySeconds || 8) * fps;
  const playerById = new Map((data.players || []).map(player => [player.publicPlayerId, player]));
  const start = lowerBound(rows, cursor - windowTicks);
  const visible = [];

  for (let i = start; i < rows.length && rows[i].tick <= cursor; i++) {
    const row = rows[i];
    const actorIds = [row.attackerPublicPlayerId, row.victimPublicPlayerId, row.downAttackerPublicPlayerId]
      .filter(Number.isInteger);
    if (actorIds.some(id => playerById.get(id)?.teamNumber === focusPlayer.teamNumber)) {
      visible.push(row);
    }
  }
  return visible.slice(-limit);
}

export function combatLogViewModel(data, focusPlayer, row) {
  const playerById = new Map((data.players || []).map(player => [player.publicPlayerId, player]));
  const actor = id => {
    const player = playerById.get(id);
    return player ? `#${player.teamNumber} ${player.characterName}` : '공격자 미확인';
  };
  const attackerId = row.attackerPublicPlayerId ?? row.downAttackerPublicPlayerId;
  const attacker = playerById.get(attackerId);
  const victim = playerById.get(row.victimPublicPlayerId);
  let kind = '다운';
  let className = 'down';

  if (row.kind === 'objective-kill') {
    return {
      eventId: row.publicEventId,
      kind: '처치',
      className: 'kill objective-kill',
      attacker: Number.isInteger(attackerId) ? actor(attackerId) : null,
      victim: row.objectiveName,
    };
  }

  if (row.kind === 'death') {
    if (victim?.teamNumber === focusPlayer.teamNumber) {
      kind = '사망';
      className = 'death';
    } else if (attacker?.teamNumber === focusPlayer.teamNumber) {
      kind = '처치';
      className = 'kill';
    } else {
      kind = '사망';
      className = 'death';
    }
  }

  return {
    eventId: row.publicEventId,
    kind,
    className,
    attacker: Number.isInteger(attackerId) ? actor(attackerId) : null,
    victim: actor(row.victimPublicPlayerId),
  };
}
