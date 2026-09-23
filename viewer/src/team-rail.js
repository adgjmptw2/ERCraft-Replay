/* ERCraft Replay Analytics - Team Loadout Rail Component */

export const equipmentSlotNames = ['무기', '옷', '머리', '팔', '다리'];
export const activeCooldownFamilies = ['Active1', 'Active2', 'Active3', 'Active4'];
export const utilityFamilies = ['WeaponSkill', 'TacticalSkill'];
export const railFamilies = ['Active1', 'Active2', 'Active3', 'Active4', 'Passive', 'WeaponSkill', 'TacticalSkill'];
export const skillKey = { Active1: 'Q', Active2: 'W', Active3: 'E', Active4: 'R' };
export const familyLabel = f => f === 'WeaponSkill' ? '무기' : f === 'TacticalSkill' ? '전술' : f === 'Passive' ? 'T' : (skillKey[f] || f);

export function itemArtScale(code, meta, asset) {
  if (Number.isFinite(asset?.displayScale)) return asset.displayScale;
  const name = meta?.itemName || '';
  if (name.includes('카메라') || name.includes('드론')) return 1.55;
  if (name.includes('스테이크')) return 1.35;
  if (meta?.subType === 'Material' || meta?.itemType === 'Misc') return 1.18;
  return 1;
}

export function skillVisual(data, p, family) {
  const character = data.skillAssets?.icons?.[String(p.characterCode)];
  const semantic = { Active1: 'q', Active2: 'w', Active3: 'e', Active4: 'r' }[family];
  if (!semantic) return '<span class="none">?</span>';
  const slots = Object.entries(character?.slots || {});
  const exactBase = character?.slots?.[semantic];
  const semanticCandidates = slots.filter(([, row]) => row.semanticSlot === semantic);
  const candidates = exactBase ? [[semantic, exactBase]] : semanticCandidates;
  if (!candidates.length) return '<span class="none">?</span>';
  const multiState = (p.characterCode === 90 && family === 'Active1') || (p.characterCode === 89 && family === 'Active2');
  const picked = multiState ? semanticCandidates.slice(0, 2) : candidates.slice(0, 2);
  if (picked.length === 1) return `<img src="${picked[0][1].imageDataUrl}" alt="">`;
  return `<span class="pair">${picked.map(([, row]) => `<img src="${row.imageDataUrl}" alt="">`).join('')}</span>`;
}

export function passiveVisual(data, p) {
  const character = data.skillAssets?.icons?.[String(p.characterCode)];
  const slots = Object.entries(character?.slots || {});
  const base = character?.slots?.passive;
  const candidates = base ? [['passive', base]] : slots.filter(([, row]) => row.semanticSlot === 'passive').slice(0, 2);
  if (!candidates.length) return '<span class="none">?</span>';
  if (candidates.length === 1) return `<img src="${candidates[0][1].imageDataUrl}" alt="">`;
  return `<span class="pair">${candidates.map(([, row]) => `<img src="${row.imageDataUrl}" alt="">`).join('')}</span>`;
}

export function utilityVisual(data, p, family) {
  let asset = null;
  if (family === 'WeaponSkill') {
    asset = data.utilitySkillAssets?.weapon?.characterOverrides?.[String(p.characterCode)] ||
            data.utilitySkillAssets?.weapon?.icons?.[String(p.result?.bestWeapon)];
  } else if (family === 'TacticalSkill') {
    asset = data.utilitySkillAssets?.tactical?.icons?.[String(p.result?.tacticalSkillGroup)];
  }
  return asset ? `<img src="${asset.imageDataUrl}" alt="${familyLabel(family)} 스킬">` : '<span class="none">?</span>';
}

function itemCellHtml() {
  return `<div class="cell"><span class="item-bg" aria-hidden="true" hidden></span><img class="art" alt="" hidden><span class="gap">…</span><span class="qty" hidden></span></div>`;
}

function skillCellHtml(data, p, family) {
  const label = familyLabel(family);
  if (utilityFamilies.includes(family)) {
    return `<div class="sk-util"><div class="ico">${utilityVisual(data, p, family)}</div><div class="sk-util-info"><div class="row"><b class="cd is-unknown">—</b></div><span class="n"></span></div></div>`;
  }
  return `<div class="sk"><div class="ico">${family === 'Passive' ? passiveVisual(data, p) : skillVisual(data, p, family)}</div><span class="k">${label}</span><b class="cd is-unknown">—</b><span class="n"></span></div>`;
}

function cellRefs(el) {
  return {
    el,
    bg: el.querySelector('.item-bg'),
    art: el.querySelector('.art'),
    gap: el.querySelector('.gap'),
    qty: el.querySelector('.qty'),
    code: undefined,
    grade: '',
    glyph: '',
    title: '',
  };
}

function skillRefs(el) {
  return {
    el,
    cd: el.querySelector('.cd'),
    n: el.querySelector('.n'),
    cls: 'is-unknown',
    title: '',
  };
}

export function trackAnchorAt(rows, t) {
  if (!rows || !rows.length || rows[0][0] > t) return null;
  let lo = 0, hi = rows.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (rows[mid][0] <= t) lo = mid + 1;
    else hi = mid - 1;
  }
  return hi >= 0 ? rows[hi] : null;
}

export function cooldownRemaining(row, t, targetFrameRate = 60) {
  if (!row || row.kind !== 'known') return null;
  if (row.held) return row.remaining;
  return Math.max(0, row.remaining - (t - row.tick) * 100 / targetFrameRate);
}

export function cooldownStatesAt(p, t, targetFrameRate = 60) {
  const cooldownFamilies = ['Active1', 'Active2', 'Active3', 'Active4', 'WeaponSkill', 'TacticalSkill'];
  const out = new Map(cooldownFamilies.map(f => [f, { kind: 'unobserved' }]));
  for (const event of p.skillCooldownTimeline || []) {
    if (event[0] > t) break;
    const [tick, action, family, remaining, max, stack, detail] = event;
    if (action === 'clear') {
      for (const f of activeCooldownFamilies) {
        out.set(f, { kind: 'known', tick, remaining: 0, max: 0, stack: null, source: 'character-clear' });
      }
      continue;
    }
    if (action === 'set') {
      if (Number.isInteger(remaining) && remaining >= 0 && (max === null || (Number.isInteger(max) && max >= 0))) {
        out.set(family, { kind: 'known', tick, remaining, max, stack: Number.isInteger(stack) ? stack : null, held: false, source: 'packet' });
      } else {
        out.set(family, { kind: 'unknown', reason: '값 미확인' });
      }
      continue;
    }
    if (action === 'copy') {
      const source = detail && out.get(detail);
      const copied = cooldownRemaining(source, tick, targetFrameRate);
      if (copied === null) {
        out.set(family, { kind: 'unknown', reason: '원본 미관측' });
      } else {
        out.set(family, { kind: 'known', tick, remaining: copied, max: source.max, stack: source.stack, held: source.held, source: 'copy' });
      }
      continue;
    }
    if (action === 'hold') {
      const source = out.get(family);
      const heldRemaining = cooldownRemaining(source, tick, targetFrameRate);
      if (heldRemaining !== null) {
        out.set(family, { ...source, tick, remaining: heldRemaining, held: Boolean(detail), source: 'hold' });
      }
    }
  }
  return out;
}

export function skillInfoAt(p, t, targetFrameRate = 60) {
  const levels = new Map();
  for (const [tick, family, level] of p.skillLevelTimeline || []) {
    if (tick > t) break;
    levels.set(family, level);
  }
  const counts = new Map();
  for (const row of p.skillStartTimeline || []) {
    if (row[0] > t) break;
    const family = Number.isInteger(row[3]) && row[3] >= 3000000 && row[3] < 4000000 ? 'WeaponSkill' : row[1];
    const prev = counts.get(family);
    counts.set(family, { count: (prev?.count || 0) + 1, lastTick: row[0], name: row[2] });
  }
  const states = cooldownStatesAt(p, t, targetFrameRate);
  return railFamilies.map(family => {
    const level = levels.get(family);
    const levelText = Number.isInteger(level) ? `Lv.${level}` : 'Lv.—';
    if (level === 0) return { family, status: '미습득', cls: 'is-unknown', count: 'Lv.0', title: `${familyLabel(family)} · 미습득` };
    if (family === 'Passive') {
      return { status: Number.isInteger(level) ? '패시브' : '미확인', cls: Number.isInteger(level) ? 'is-ready' : 'is-unknown', count: levelText, title: 'T · 패시브' };
    }
    const hit = counts.get(family);
    const current = states.get(family);
    const remaining = cooldownRemaining(current, t, targetFrameRate);
    let status, cls, reason = '';
    if (current?.kind === 'unobserved') {
      status = hit ? '—' : '미사용';
      cls = 'is-unknown';
      reason = hit ? '숫자 쿨다운 미관측' : '첫 사용 전';
    } else if (current?.kind === 'unknown') {
      status = '—';
      cls = 'is-unknown';
      reason = current.reason;
    } else if (remaining <= 0) {
      const levelUnknown = activeCooldownFamilies.includes(family) && !Number.isInteger(level);
      status = levelUnknown ? '미확인' : '준비';
      cls = levelUnknown ? 'is-unknown' : 'is-ready';
    } else {
      status = `${(remaining / 100).toFixed(1)}초`;
      cls = 'is-cooldown';
    }
    const stack = current?.kind === 'known' && Number.isInteger(current.stack) && current.stack > 0 ? ` · ${current.stack}스택` : '';
    return {
      family,
      status,
      cls,
      count: `${Number.isInteger(level) ? `${levelText} · ` : ''}${hit ? `${hit.count}회` : '0회'}`,
      title: `${hit?.name || familyLabel(family)}${reason ? ` · ${reason}` : ''}${stack}`
    };
  });
}

export function itemStateAt(timeline, t) {
  const slots = new Map();
  let seen = false;
  for (const row of timeline || []) {
    if (row[0] > t) break;
    seen = true;
    for (const update of row[1]) {
      const [slot, code, amount] = update;
      if (code === null || amount === 0) slots.delete(slot);
      else slots.set(slot, { code, amount });
    }
  }
  return { seen, slots };
}

export function liveStatusAt(p, t) {
  const kda = trackAnchorAt(p.kdaTimeline, t) || [t, 0, 0, 0];
  const observer = trackAnchorAt(p.observerStatusTimeline, t);
  const survivable = trackAnchorAt(p.survivableTimeTimeline, t);
  return {
    kda: `${kda[1]}/${kda[2]}/${kda[3]}`,
    credit: observer ? String(Math.floor(observer[1])) : '—',
    creditExact: observer ? observer[1] : null,
    gadget: observer ? String(observer[2]) : '—',
    survive: survivable ? String(survivable[1]) : '—',
  };
}

export class TeamRail {
  constructor(containerEl, data, onSelectPlayer) {
    this.container = containerEl;
    this.data = data;
    this.onSelectPlayer = onSelectPlayer;
    this.rail = null;
    this.currentTeamKey = '';
    this.lastUpdate = 0;
  }

  build(team, selectedPlayerId) {
    const kiosk = this.data.mapMarkerAssets?.icons?.kiosk?.imageDataUrl || '';
    const gadget = this.data.uiAssets?.gadgetPoint?.imageDataUrl || '';

    this.container.innerHTML = team.map(p => {
      const isSelected = p.publicPlayerId === selectedPlayerId;
      return `
        <article class="pcard${isSelected ? ' is-selected' : ''}" data-player-id="${p.publicPlayerId}" role="button" tabindex="0">
          <div class="pcard-head">
            <div class="pcard-head-title">
              <b>#${p.teamNumber} ${p.characterName}</b>
            </div>
            <span class="chip alive">생존</span>
          </div>
          <div class="live-status">
            <span class="kda">0/0/0</span>
            <div class="live-metrics">
              <span class="metric credit"><img src="${kiosk}" alt="크레딧"><b>—</b></span>
              <span class="metric survive"><span class="restricted" aria-hidden="true"></span><b>—</b></span>
              <span class="metric gadget"><img src="${gadget}" alt="가젯 포인트"><b>—</b></span>
            </div>
          </div>
          <div class="loadout-label">장비</div>
          <div class="gear">${equipmentSlotNames.map(itemCellHtml).join('')}</div>
          <div class="loadout-label inventory-label">소지품</div>
          <div class="bag">${Array.from({ length: 10 }, itemCellHtml).join('')}</div>
          <div class="skills">${['Active1', 'Active2', 'Active3', 'Active4', 'Passive'].map(f => skillCellHtml(this.data, p, f)).join('')}</div>
          <div class="util">${utilityFamilies.map(f => skillCellHtml(this.data, p, f)).join('')}</div>
        </article>
      `;
    }).join('');

    this.rail = {
      cards: Array.from(this.container.children).map((el, i) => ({
        el,
        player: team[i],
        chip: el.querySelector('.chip'),
        life: '',
        live: {
          el: el.querySelector('.live-status'),
          kda: el.querySelector('.kda'),
          credit: el.querySelector('.metric.credit b'),
          survive: el.querySelector('.metric.survive b'),
          gadget: el.querySelector('.metric.gadget b'),
          value: '',
        },
        gear: Array.from(el.querySelectorAll('.gear .cell')).map(cellRefs),
        bag: Array.from(el.querySelectorAll('.bag .cell')).map(cellRefs),
        skills: Array.from(el.querySelectorAll('.sk, .sk-util')).map(skillRefs),
      }))
    };

    for (const card of this.rail.cards) {
      card.el.addEventListener('click', () => this.onSelectPlayer(card.player.publicPlayerId));
      card.el.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          this.onSelectPlayer(card.player.publicPlayerId);
        }
      });
    }
  }

  update(focusPlayer, cursor, force = false) {
    if (!focusPlayer || !this.data) return;
    const team = this.data.players
      .filter(p => p.teamNumber === focusPlayer.teamNumber)
      .sort((a, b) => (a.publicPlayerId === focusPlayer.publicPlayerId ? -1 : b.publicPlayerId === focusPlayer.publicPlayerId ? 1 : a.publicPlayerId - b.publicPlayerId));

    const key = team.map(p => p.publicPlayerId).join(',');
    const teamChanged = key !== this.currentTeamKey;
    if (teamChanged) {
      this.currentTeamKey = key;
      this.build(team, focusPlayer.publicPlayerId);
    }

    const now = performance.now();
    if (!force && !teamChanged && now - this.lastUpdate < 180) return;
    this.lastUpdate = now;

    if (!this.rail) return;

    for (const card of this.rail.cards) {
      const p = card.player;
      const isSelected = p.publicPlayerId === focusPlayer.publicPlayerId;
      card.el.classList.toggle('is-selected', isSelected);
      card.el.classList.toggle('skills-hidden', !isSelected);

      // Life Status Chip
      let life = 'alive';
      for (const row of p.lifeTimeline || []) {
        if (row[0] > cursor) break;
        life = row[1];
      }
      if (life !== card.life) {
        card.life = life;
        card.chip.className = `chip ${life}`;
        card.chip.textContent = life === 'alive' ? '생존' : life === 'down' ? '다운' : '사망';
      }

      // Live KDA & Metrics
      const live = liveStatusAt(p, cursor);
      const liveVal = `${live.kda}|${live.credit}|${live.survive}|${live.gadget}`;
      if (liveVal !== card.live.value) {
        card.live.value = liveVal;
        card.live.kda.textContent = live.kda;
        card.live.credit.textContent = live.credit;
        card.live.survive.textContent = live.survive;
        card.live.gadget.textContent = live.gadget;
      }

      // Gear & Bag
      const gearState = itemStateAt(p.equipmentTimeline, cursor);
      const bagState = itemStateAt(p.inventoryTimeline, cursor);

      for (let s = 0; s < 5; s++) {
        this.paintItemCell(card.gear[s], gearState.slots.get(s), gearState.seen);
      }
      for (let s = 0; s < 10; s++) {
        this.paintItemCell(card.bag[s], bagState.slots.get(s), bagState.seen);
      }

      // Three team cards stay readable together; update at the shared 5 Hz rail cadence.
      if (!isSelected) continue;
      const skillsInfo = skillInfoAt(p, cursor, this.data.meta.targetFrameRate);
      for (let i = 0; i < skillsInfo.length && i < card.skills.length; i++) {
        const info = skillsInfo[i];
        const ref = card.skills[i];
        if (ref.cd.textContent !== info.status) {
          ref.cd.textContent = info.status;
          ref.cd.className = `cd ${info.cls}`;
        }
        if (ref.n && ref.n.textContent !== info.count) {
          ref.n.textContent = info.count;
        }
      }
    }
  }

  paintItemCell(ref, item, observed) {
    const code = item ? item.code : null;
    if (code !== ref.code) {
      ref.code = code;
      const asset = code === null ? null : this.data.itemAssets?.icons?.[String(code)];
      const meta = code === null ? null : this.data.itemCatalog?.[String(code)];
      ref.el.style.setProperty('--asset-art-scale', String(itemArtScale(code, meta, asset)));
      const geometry=asset?.displayGeometry;
      const categoryScale = meta?.subType === 'Arm' ? 0.85 : 1;
      ref.art.classList.toggle('is-measured',!!geometry);
      ref.art.classList.toggle('is-arm', meta?.subType === 'Arm');
      for (const key of ['width','height','x','y']) {
        if (geometry) ref.art.style.setProperty(`--art-${key}`,`${geometry[key] * categoryScale}px`);
        else ref.art.style.removeProperty(`--art-${key}`);
      }


      if (asset) {
        ref.art.src = asset.imageDataUrl;
        ref.art.alt = meta?.itemName || '';
        ref.art.hidden = false;
        ref.gap.hidden = true;
      } else {
        ref.art.removeAttribute('src');
        ref.art.alt = '';
        ref.art.hidden = true;
        ref.gap.hidden = false;
      }

      if (ref.bg) ref.bg.hidden = code === null;
      ref.el.classList.toggle('has-item', code !== null);
      ref.el.classList.toggle('unobserved', code === null && !observed);
      ref.el.classList.toggle('empty', code === null && observed);

      const grade = code === null ? '' : (meta?.itemGrade || '');
      if (grade !== ref.grade) {
        if (ref.grade) ref.el.classList.remove(`grade-${ref.grade}`);
        if (grade) ref.el.classList.add(`grade-${grade}`);
        ref.grade = grade;
      }

      ref.el.title = meta?.itemName || (code === null ? (observed ? '빈칸' : '관측 전') : `아이템 ${code}`);
    }

    const glyph = code === null ? (observed ? '—' : '…') : (this.data.itemAssets?.icons?.[String(code)] ? '' : '?');
    if (glyph && glyph !== ref.glyph) {
      ref.gap.textContent = glyph;
      ref.glyph = glyph;
    }

    const amount = item && item.amount > 1 ? String(item.amount) : '';
    if (amount) {
      if (ref.qty.textContent !== amount) ref.qty.textContent = amount;
      ref.qty.hidden = false;
    } else if (!ref.qty.hidden) {
      ref.qty.hidden = true;
    }
  }
}
