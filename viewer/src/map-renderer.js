import { movementPositionAt } from './movement-position.js';
import { visibleWorldEvents } from './replay-navigation.js';
import { makeRinglessPingGlyph, makeBlackRiftBackground } from './map-art.js?v=1';
/* ERCraft Replay Analytics - Canvas Map Renderer */

export function lowerBound(arr, val, keyFn) {
  let lo = 0, hi = arr.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (keyFn(arr[mid]) <= val) lo = mid + 1;
    else hi = mid - 1;
  }
  return lo;
}

export function teamColor(teamNumber) {
  const colors = [
    '#a855f7', '#3b82f6', '#10b981', '#f59e0b', '#ec4899', '#06b6d4', '#8b5cf6', '#14b8a6'
  ];
  return colors[(teamNumber - 1) % colors.length] || '#3b82f6';
}

const WILDLIFE_ICON_KEYS = [
  'chicken', 'bat', 'wild-dog', 'boar', 'wolf', 'bear', 'raven',
  'mutant-chicken', 'mutant-bat', 'mutant-wild-dog', 'mutant-boar',
  'mutant-wolf', 'mutant-bear', 'mutant-raven'
];

const WILDLIFE_FACE_CROPS = {
  chicken: [0.10, 0.00, 0.78],
  bat: [0.00, 0.14, 0.72],
  'wild-dog': [0.06, 0.05, 0.84],
  boar: [0.08, 0.00, 0.84],
  wolf: [0.00, 0.02, 0.84],
  bear: [0.10, 0.00, 0.80],
  raven: [0.08, 0.00, 0.82],
};

// Raven art is portrait-oriented. Cropping it to the same square as the
// quadrupeds cuts away the body and makes the marker look unnaturally flat.
const RAVEN_PORTRAIT_CROP = [0.02, 0.02, 0.96, 0.96];

export function wildlifeAssetUrl(key) {
  return new URL(`../public/ui-assets/wildlife/${key}.png`, import.meta.url).href;
}

export function hasWildlifePortrait(key) {
  return WILDLIFE_ICON_KEYS.includes(key);
}

export function worldMovementPositionAt(row, cursor) {
  return movementPositionAt(row?.movementTrack, cursor);
}

export function lumiMovingAt(row, cursor, firstTick = 0) {
  const now = worldMovementPositionAt(row, cursor);
  const before = worldMovementPositionAt(row, Math.max(row?.firstSeenTick || firstTick, cursor - 6));
  return Boolean(now && before && Math.hypot(now[0] - before[0], now[1] - before[1]) > 0.03);
}

export function playerLifeStateAt(player, cursor) {
  let state = 'alive';
  for (const row of player?.lifeTimeline || []) {
    if (row[0] > cursor) break;
    state = row[1];
  }
  return state;
}

export function worldObjectAssetAt(row, cursor, transitions = []) {
  const original = row.assetKey || row.category;
  const timeline = row.transportModeTimeline;
  if (timeline?.length) {
    const state = timeline[lowerBound(timeline, cursor, r => r[0]) - 1];
    return state ? (state[1] === null ? 'transport-unknown' : state[1]) : original;
  }
  // Apply only an explicitly scoped transition; portable VLS shares the same category.
  if (!['vls', 'hyperloop'].includes(row.category)) return original;
  for (let i = lowerBound(transitions, cursor, event => event.tick) - 1; i >= 0; i--) {
    const event = transitions[i];
    const applies = row.transportModeScope === 'global' ||
      event.publicWorldObjectIds?.includes(row.publicWorldObjectId);
    if (applies && ['vls', 'hyperloop'].includes(event.mode)) return event.mode;
  }
  return original;
}

// Background alignment in source-image pixels, using the annotated landmarks.
// World objects and player tracks retain their shared world projection.
export const RIFT_BACKGROUND_OFFSET = [10, 12];

export function riftLandmarkVisual(row, space) {
  if (row.category !== 'kiosk' || !row.position ||
      space?.projection?.type !== 'calibrated-rift-world-to-client-pixel-12.3.v1') return null;
  const [ox, oz] = space.projection.originWorld;
  const dx = row.position[0] - ox, dz = row.position[1] - oz;
  if (!(dx > 8 && dx < 13 && dz > -1 && dz < 1)) return null;
  // User-requested hyperloop replaces the removed purple display marker.
  return {key: 'hyperloop', pixel: [
    (156.4 + RIFT_BACKGROUND_OFFSET[0]) * space.image.w / 363,
    (125.8 + RIFT_BACKGROUND_OFFSET[1]) * space.image.h / 364
  ]};
}

export class MapRenderer {
  constructor(canvasEl, data, state, onRequestRender = null) {
    this.canvas = canvasEl;
    this.ctx = canvasEl.getContext('2d');
    this.data = data;
    this.state = state;
    this.onRequestRender = onRequestRender;
    this.layout = null;
    this.cachedBackground = null;
    this.cachedMarkerImages = {};
    this.cachedPingGlyphs = {};
    this.cachedCharacterImages = {};
    this.cachedWildlifeImages = {};
    this.activeSpace = null;
    this.lastSpaceId = 'main';
    this.worldEvents = visibleWorldEvents(data.worldMap?.timeline || []);
    this.layoutDirty = true;
    const invalidateLayout = () => { this.layoutDirty = true; };
    this.layoutObserver = new ResizeObserver(invalidateLayout);
    if (canvasEl.parentElement) this.layoutObserver.observe(canvasEl.parentElement);
    const controls = document.getElementById('mapControls');
    if (controls) this.layoutObserver.observe(controls);
    window.addEventListener('resize', invalidateLayout, { passive: true });

    // Pre-sort wildlife instances by spawnTick once for fast range queries
    this.wildlifeInstances = [...(this.data.wildlife?.instances || [])].sort((a, b) => a.spawnTick - b.spawnTick);

    this.preloadAssets();
  }

  preloadAssets() {
    const notify = () => {
      if (this.onRequestRender) this.onRequestRender();
    };

    // Preload satellite map background
    if (this.data.map?.imageDataUrl) {
      const img = new Image();
      img.onload = notify;
      img.src = this.data.map.imageDataUrl;
      this.cachedMarkerImages['mapBackground'] = img;
    }
    // Preload map marker icons
    if (this.data.mapMarkerAssets?.icons) {
      for (const [key, row] of Object.entries(this.data.mapMarkerAssets.icons)) {
        if (row.imageDataUrl) {
          const img = new Image();
          img.onload = () => {
            if (key.startsWith('tactical-ping-')) {
              this.cachedPingGlyphs[key] = this.makeRinglessPingGlyph(img, key);
            }
            notify();
          };
          img.src = row.imageDataUrl;
          this.cachedMarkerImages[key] = img;
        }
      }
    }
    for (const space of this.data.map?.secondarySpaces || []) {
      if (!space.backgroundDataUrl) continue;
      const img = new Image(); img.onload = notify; img.src = space.backgroundDataUrl;
      this.cachedMarkerImages[space.backgroundAssetKey] = img;
    }
    // Preload character portraits
    for (const p of this.data.players || []) {
      if (p.characterCode && !this.cachedCharacterImages[p.characterCode]) {
        const charIcon = this.data.characterAssets?.icons?.[String(p.characterCode)]?.imageDataUrl;
        if (charIcon) {
          const img = new Image();
          img.onload = notify;
          img.src = charIcon;
          this.cachedCharacterImages[p.characterCode] = img;
        }
      }
    }
    // Preload the previously verified fankit wildlife portraits once.
    for (const key of WILDLIFE_ICON_KEYS) {
      const img = new Image();
      img.onload = notify;
      img.src = wildlifeAssetUrl(key);
      this.cachedWildlifeImages[key] = img;
    }
  }

  makeRinglessPingGlyph(image, key = '') {
    return makeRinglessPingGlyph(image, key);
  }

  playerPositionAt(player, cursor) {
    return movementPositionAt(player?.movementTrack, cursor);
  }

  spaceContains(space, pos, padding = 2) {
    if (!space?.bounds || !pos) return false;
    const [x, z] = pos;
    const b = space.bounds;
    return x >= b.xMin - padding && x <= b.xMax + padding && z >= b.zMin - padding && z <= b.zMax + padding;
  }

  mapSpaceAt(cursor, selectedPlayer) {
    const pos = this.playerPositionAt(selectedPlayer, cursor);
    return (this.data.map?.secondarySpaces || []).find(space =>
      cursor >= space.firstTick && cursor <= space.lastTick && this.spaceContains(space, pos)
    ) || null;
  }

  belongsToActiveSpace(pos) {
    if (!pos) return false;
    if (this.activeSpace) return this.spaceContains(this.activeSpace, pos, 5);
    return !(this.data.map?.secondarySpaces || []).some(space => this.spaceContains(space, pos, 5));
  }

  project(pos, space = this.activeSpace) {
    if (!pos) return null;
    if (space?.projection?.type === "affine-secondary-world-xz-to-pixel.v1") {
      const p = space.projection;
      return [p.pixelX[0]*pos[0]+p.pixelX[1]*pos[1]+p.pixelX[2], p.pixelY[0]*pos[0]+p.pixelY[1]*pos[1]+p.pixelY[2]];
    }
    if (space?.projection?.type === 'calibrated-rift-world-to-client-pixel-12.3.v1') {
      const [worldX, worldZ] = pos;
      const [originX, originZ] = space.projection.originWorld;
      const [pixelX, pixelY] = space.projection.pixelOrigin;
      const scale = space.projection.pixelPerWorldUnit;
      const localX = worldX - originX;
      const localZ = worldZ - originZ;
      // Client reference: negative Z points NE; positive X points NW.
      // A 180-degree rotation also swapped the transverse kiosk/spawn sides.
      return [
        space.image.w - pixelX - scale * (localX + localZ),
        space.image.h - pixelY + scale * (-localX + localZ)
      ];
    }
    const proj = this.data.map?.projection;
    if (!proj || proj.type !== 'affine-world-xz-to-source-pixel') return pos;
    const [worldX, worldZ] = pos;
    const px = proj.pixelX[0] * (worldX + worldZ) + proj.pixelX[2];
    const py = proj.pixelY[0] * worldX + proj.pixelY[1] * worldZ + proj.pixelY[2];
    return [px, py];
  }

  updateLayout(coordSpace) {
    if (this.layout && !this.layoutDirty && this.layoutCoordSpace === coordSpace) return;
    this.layoutDirty = false;
    this.layoutCoordSpace = coordSpace;
    const shell = this.canvas.parentElement;
    if (!shell) return;

    const availW = Math.max(260, shell.clientWidth || 720);
    const controls = document.getElementById('mapControls');
    const controlsH = controls ? controls.getBoundingClientRect().height : 0;
    coordSpace ||= this.data.map?.coordinateSpace || { w: 772, h: 981 };
    const ratio = coordSpace.h / coordSpace.w; // 981 / 772 ≈ 1.2707

    const stacked = window.innerWidth <= 1050;
    // Page scrolling must not resize the map under the user's pointer.
    const shellTop = shell.getBoundingClientRect().top + window.scrollY || 180;
    const budget = stacked
      ? Math.max(360, window.innerHeight * 0.8)
      : Math.max(480, Math.min(840, window.innerHeight - shellTop - controlsH - 32));

    let h = Math.min(budget, availW * ratio);
    let w = h / ratio;
    if (w > availW) {
      w = availW;
      h = w * ratio;
    }
    w = Math.round(w);
    h = Math.round(h);

    const dpr = Math.min(2, window.devicePixelRatio || 1);
    if (!this.layout || this.layout.w !== w || this.layout.h !== h || this.layout.dpr !== dpr) {
      this.layout = { w, h, dpr };
      this.canvas.width = Math.round(w * dpr);
      this.canvas.height = Math.round(h * dpr);
      this.canvas.style.width = `${w}px`;
      this.canvas.style.height = `${h}px`;
      this.cachedBackground = null; // Invalidate cached background on dimension change
    }
  }

  markerScale() {
    return Math.max(0.85, Math.min(1.4, Math.sqrt(this.state.mapView.zoom)));
  }

  ensureMapBackground(layout, space = null) {
    const backgroundKey = space?.backgroundAssetKey || 'mapBackground';
    if (this.cachedBackground && this.cachedBackground.key === backgroundKey && this.cachedBackground.w === layout.w && this.cachedBackground.h === layout.h && this.cachedBackground.dpr === layout.dpr) {
      return this.cachedBackground.canvas;
    }

    const bgImg = this.cachedMarkerImages[backgroundKey];
    if (!bgImg?.complete || !bgImg.naturalWidth) return null;

    const bgCanvas = document.createElement('canvas');
    bgCanvas.width = Math.round(layout.w * layout.dpr);
    bgCanvas.height = Math.round(layout.h * layout.dpr);
    const bgCtx = bgCanvas.getContext('2d');
    bgCtx.setTransform(layout.dpr, 0, 0, layout.dpr, 0, 0);

    const r = space
      ? { x: 0, y: 0, w: bgImg.naturalWidth, h: bgImg.naturalHeight }
      : (this.data.map?.imageContentRect || { x: 0, y: 10, w: 772, h: 981 });
    let source = bgImg;
    if (space && !space.backgroundDataUrl) {
      this.riftBackgrounds ||= new Map();
      if (!this.riftBackgrounds.has(backgroundKey)) this.riftBackgrounds.set(backgroundKey, makeBlackRiftBackground(bgImg));
      source = this.riftBackgrounds.get(backgroundKey);
    }
    const offset = space?.projection?.type === 'calibrated-rift-world-to-client-pixel-12.3.v1'
      ? RIFT_BACKGROUND_OFFSET : [0, 0];
    bgCtx.drawImage(source, r.x, r.y, r.w, r.h,
      offset[0] * layout.w / 363, offset[1] * layout.h / 364, layout.w, layout.h);

    this.cachedBackground = {
      w: layout.w,
      h: layout.h,
      dpr: layout.dpr,
      key: backgroundKey,
      canvas: bgCanvas
    };
    return bgCanvas;
  }

  drawMarkerAsset(g, key, x, y, size, warning = false, alpha = 1, isPing = false) {
    const row = this.data.mapMarkerAssets?.icons?.[key];
    const image = isPing ? (this.cachedPingGlyphs[key] || this.cachedMarkerImages[key]) : this.cachedMarkerImages[key];
    const imageWidth = image?.naturalWidth || image?.width || 0;
    const imageHeight = image?.naturalHeight || image?.height || 0;
    if (!row || !imageWidth || !imageHeight || (image instanceof HTMLImageElement && !image.complete)) return false;

    g.save();
    g.globalAlpha = alpha;

    if (isPing) {
      const pixel = row.pixelSize || [image.width, image.height];
      const anchor = row.anchorPixel || [pixel[0] / 2, pixel[1] / 2];
      const scale = size / Math.max(pixel[0], pixel[1]);
      g.drawImage(image, x - anchor[0] * scale, y - anchor[1] * scale, pixel[0] * scale, pixel[1] * scale);
    } else {
      const crop = row.crop;
      if (crop) {
        const sw = image.naturalWidth * crop.sizeByWidth;
        const sx = image.naturalWidth * crop.x;
        const sy = image.naturalWidth * crop.yByWidth;
        g.drawImage(image, sx, sy, sw, sw, x - size / 2, y - size / 2, size, size);
      } else {
        const pixel = row.pixelSize || [image.naturalWidth, image.naturalHeight];
        const anchor = row.anchorPixel || [pixel[0] / 2, pixel[1] / 2];
        const scale = size / Math.max(pixel[0], pixel[1]);
        g.drawImage(image, x - anchor[0] * scale, y - anchor[1] * scale, pixel[0] * scale, pixel[1] * scale);
      }
    }

    g.restore();
    return true;
  }

  drawWildlifePortrait(g, animal, x, y, size, alpha) {
    const key = animal.assetKey;
    const image = this.cachedWildlifeImages[key];
    if (!key || !image?.complete || !image.naturalWidth) return false;

    const species = key.replace(/^mutant-/, '');
    g.save();
    g.globalAlpha = alpha;
    g.imageSmoothingEnabled = true;
    if (species === 'raven') {
      const [cropX, cropY, cropWidth, cropHeight] = RAVEN_PORTRAIT_CROP;
      const sx = image.naturalWidth * cropX;
      const sy = image.naturalHeight * cropY;
      const sw = image.naturalWidth * cropWidth;
      const sh = image.naturalHeight * cropHeight;
      const scale = size / sh;
      const drawWidth = sw * scale;
      g.drawImage(image, sx, sy, sw, sh, x - drawWidth / 2, y - size / 2, drawWidth, size);
    } else {
      const [cropX, cropY, cropSize] = WILDLIFE_FACE_CROPS[species] || [0, 0, 1];
      const sw = image.naturalWidth * cropSize;
      const sx = image.naturalWidth * cropX;
      const sy = image.naturalHeight * cropY;
      const maxSquare = Math.min(sw, image.naturalHeight - sy);
      g.drawImage(image, sx, sy, maxSquare, maxSquare, x - size / 2, y - size / 2, size, size);
    }
    g.restore();
    return true;
  }

  // Normal and mutated wildlife use their distinct fankit portraits.
  drawWildlifeMarker(g, animal, x, y, size, alpha) {
    if (animal.mapMarkerStyle === 'special-icon') {
      const key = animal.mapMarkerAssetKey || 'lumi-normal';
      return this.drawMarkerAsset(g, key, x, y, size, false, alpha);
    }

    const isDrone = animal.mapMarkerStyle === 'neutral-monster-red-dot';

    if (isDrone) {
      g.save();
      g.globalAlpha = alpha;
      // Neutral Monster Drone (clean subtle red dot)
      g.fillStyle = '#ef4444';
      g.beginPath();
      g.arc(x, y, Math.max(2.5, size * 0.32), 0, Math.PI * 2);
      g.fill();
      g.strokeStyle = 'rgba(15, 18, 28, 0.8)';
      g.lineWidth = 1;
      g.stroke();
      g.restore();
      return true;
    }
    const bossAssetKey = { '알파': 'alpha', '오메가': 'omega', '위클라인': 'wickeline' }[animal.monsterName];
    if (bossAssetKey) return this.drawMarkerAsset(g, bossAssetKey, x, y, size, false, alpha);
    return this.drawWildlifePortrait(g, animal, x, y, size, alpha);
  }

  // Wildlife lifecycle & positions
  wildlifeRenderEnd(animal) {
    const end = [animal.deathTick, animal.destroyTick, animal.despawnTick].filter(Number.isFinite).sort((a, b) => a - b)[0];
    return end === undefined ? Infinity : end;
  }

  mapPositionAt(animal, t) {
    const track = animal.mapPositionTrack || [];
    if (!track.length || track[0][0] > t) return null;
    const i = lowerBound(track, t, row => row[0]) - 1;
    const row = track[i];
    const next = track[i + 1];
    const mode = String(animal.mapMovementMode || '');
    const mobile = mode.startsWith('crow-') || mode.startsWith('special-mobile-');
    if (!mobile || !next) return [row[1], row[2]];
    const source = String(row[3] || '');
    const nextSource = String(next[3] || '');
    const dt = next[0] - row[0];
    const moving = source.startsWith('CmdMove') && (nextSource.startsWith('CmdMove') || nextSource === 'CmdStopMove');
    if (!moving || dt <= 0) return [row[1], row[2]];
    const q = Math.max(0, Math.min(1, (t - row[0]) / dt));
    return [row[1] + (next[1] - row[1]) * q, row[2] + (next[2] - row[2]) * q];
  }

  wildlifeScreenOffset(index, count, size) {
    if (count <= 1) return [0, 0];
    const radius = Math.min(size * 0.62, 4 + count * 0.55);
    const angle = index * Math.PI * 2 / count - Math.PI / 2;
    return [Math.cos(angle) * radius, Math.sin(angle) * radius];
  }

  // World static objects lifecycle
  worldObjectEndTick(row) {
    const ends = [row.visibleEndTick, row.deathTick, row.destroyTick].filter(Number.isFinite);
    return ends.length ? Math.min(...ends) : undefined;
  }

  worldObjectVisible(row, t) {
    const end = this.worldObjectEndTick(row);
    if (row.firstSeenTick > t || (Number.isFinite(end) && end <= t)) return false;
    return true;
  }

  lumiAssetKeyAt(row, cursor) {
    const timeline = row.guideRobotStateTimeline || [];
    const index = lowerBound(timeline, cursor, item => item[0]) - 1;
    const current = index >= 0 ? timeline[index] : null;
    if (current?.[2]) return 'lumi-credit-rich';
    if (current?.[1] === 1001) return 'lumi-battle';
    return row.assetKey || 'lumi-normal';
  }

  drawLumiMovementTrail(g, row, cursor, X, Y, onScreen, markerScale) {
    const track = row.movementTrack || [];
    const from = Math.max(row.firstSeenTick || this.data.meta?.firstTick || 0, cursor - 180);
    const points = [];
    const start = worldMovementPositionAt(row, from);
    const current = worldMovementPositionAt(row, cursor);
    if (start) points.push(start);
    for (let i = lowerBound(track, from, item => item[0]); i < track.length && track[i][0] <= cursor; i++) {
      points.push([track[i][1], track[i][2]]);
    }
    if (current) points.push(current);
    if (points.length < 2) return false;

    g.save();
    g.strokeStyle = 'rgba(99, 232, 255, 0.72)';
    g.lineWidth = 1.45 * Math.min(markerScale, 1.6);
    g.lineCap = 'round';
    g.lineJoin = 'round';
    g.beginPath();
    let started = false;
    for (const raw of points) {
      if (!this.belongsToActiveSpace(raw)) continue;
      const point = this.project(raw);
      if (!point) continue;
      const x = X(point[0]);
      const y = Y(point[1]);
      if (!onScreen(x, y)) continue;
      if (started) g.lineTo(x, y);
      else {
        g.moveTo(x, y);
        started = true;
      }
    }
    if (started) g.stroke();
    g.restore();
    return started;
  }

  render(cursor, selectedPlayer) {
    this.activeSpace = this.mapSpaceAt(cursor, selectedPlayer);
    const spaceId = this.activeSpace?.spaceId || 'main';
    if (spaceId !== this.lastSpaceId) {
      this.lastSpaceId = spaceId;
      this.state.mapView.zoom = 1;
      this.state.mapView.ox = 0;
      this.state.mapView.oy = 0;
      this.cachedBackground = null;
    }
    this.canvas.dataset.mapSpaceId = spaceId;
    const badge = document.getElementById('mapSpaceBadge');
    if (badge) {
      badge.hidden = !this.activeSpace;
      badge.textContent = this.activeSpace?.label || '';
    }

    const coordSpace = this.activeSpace?.image || this.data.map?.coordinateSpace || { w: 772, h: 981 };
    this.updateLayout(coordSpace);
    if (!this.layout) return;

    const { w, h, dpr } = this.layout;
    const g = this.ctx;
    const v = this.state.mapView;
    const ms = this.markerScale();

    if (this.state.cameraLock === 'focus' && selectedPlayer) {
      const rawFocus = this.playerPositionAt(selectedPlayer, cursor);
      const focus = this.belongsToActiveSpace(rawFocus) ? this.project(rawFocus) : null;
      if (focus) {
        v.ox = w / 2 - (focus[0] / coordSpace.w * w) * v.zoom;
        v.oy = h / 2 - (focus[1] / coordSpace.h * h) * v.zoom;
      }
    }
    this.canvas.dataset.cameraLock = this.state.cameraLock;
    this.canvas.dataset.mapZoom = String(v.zoom);

    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);

    const X = x => v.ox + (x / coordSpace.w * w) * v.zoom;
    const Y = y => v.oy + (y / coordSpace.h * h) * v.zoom;
    const onScreen = (x, y) => x > -60 && x < w + 60 && y > -60 && y < h + 60;

    // Draw background map with exact aspect ratio
    const bgImage = this.ensureMapBackground(this.layout, this.activeSpace);
    if (bgImage) {
      g.drawImage(bgImage, 0, 0, bgImage.width, bgImage.height, v.ox, v.oy, w * v.zoom, h * v.zoom);
    } else {
      g.fillStyle = '#0a0c12';
      g.fillRect(v.ox, v.oy, w * v.zoom, h * v.zoom);
    }

    // Exact restriction state at the cursor, underneath all map markers.
    const shapes = this.data.worldMap?.restrictionAreaShapes;
    const updates = this.data.phaseClock?.restrictionUpdates || [];
    const restriction = updates[lowerBound(updates, cursor, row => row.tick) - 1];
    if (!this.activeSpace && shapes?.viewBox && restriction) {
      this.restrictionPaths ||= new Map();
      const [vx, vy, vw, vh] = shapes.viewBox;
      g.save();
      g.translate(v.ox, v.oy);
      g.scale(w * v.zoom / vw, h * v.zoom / vh);
      g.translate(-vx, -vy);
      for (const area of restriction.areas || []) {
        const source = shapes.paths?.[area.areaCode];
        if (!source || !['Reserved', 'Clearing', 'Restricted'].includes(area.stateName)) continue;
        if (!this.restrictionPaths.has(source)) this.restrictionPaths.set(source, new Path2D(source));
        const path = this.restrictionPaths.get(source);
        const restricted = area.stateName === 'Restricted';
        const clearing = area.stateName === 'Clearing';
        const pixel = vw / (w * v.zoom);
        g.fillStyle = restricted ? 'rgba(220, 45, 50, .26)' : clearing ? 'rgba(45, 200, 170, .15)' : 'rgba(238, 165, 40, .14)';
        g.strokeStyle = restricted ? 'rgba(244, 80, 80, .65)' : clearing ? 'rgba(70, 230, 195, .9)' : 'rgba(238, 180, 65, .6)';
        g.setLineDash(clearing ? [5 * pixel, 3 * pixel] : []);
        g.lineWidth = (clearing ? 1.8 : 1.2) * pixel;
        g.fill(path);
        g.stroke(path);
      }
      g.restore();
    }

    // Selected Player Trail
    if (selectedPlayer?.movementTrack) {
      const track = selectedPlayer.movementTrack;
      const trailFrom = lowerBound(track, cursor - 900, row => row[0]);
      g.fillStyle = teamColor(selectedPlayer.teamNumber);
      g.globalAlpha = 0.45;
      for (let i = trailFrom; i < track.length && track[i][0] <= cursor; i++) {
        if (!this.belongsToActiveSpace([track[i][1], track[i][2]])) continue;
        const q = this.project([track[i][1], track[i][2]]);
        if (!q) continue;
        const x = X(q[0]);
        const y = Y(q[1]);
        if (!onScreen(x, y)) continue;
        g.beginPath();
        g.arc(x, y, 1.5 * Math.sqrt(v.zoom), 0, Math.PI * 2);
        g.fill();
      }
      g.globalAlpha = 1;
    }

    // LAYER 1: Wildlife (contract order: wildlife -> players -> world-objects -> tactical-pings)
    let visibleWildlifePortraitCount = 0;
    let visibleMutantPortraitCount = 0;
    const missingWildlifeAssetKeys = new Set();
    if (this.state.layers.wildlife && this.wildlifeInstances.length) {
      const visible = [];
      const groupCounts = new Map();
      const groupSeen = new Map();

      for (const animal of this.wildlifeInstances) {
        if (animal.spawnTick > cursor) break;
        const end = this.wildlifeRenderEnd(animal);
        if (end !== Infinity && cursor >= end) continue;
        if (!animal.initialAlive) continue;

        const pos = this.mapPositionAt(animal, cursor);
        if (!pos) continue;
        if (!this.belongsToActiveSpace(pos)) continue;

        const groupId = animal.publicWildlifeGroupId || animal.publicWildlifeId;
        groupCounts.set(groupId, (groupCounts.get(groupId) || 0) + 1);
        visible.push({ animal, pos, groupId, offsetIndex: 0, offsetCount: 1 });
      }

      for (const row of visible) {
        const used = groupSeen.get(row.groupId) || 0;
        groupSeen.set(row.groupId, used + 1);
        row.offsetIndex = used;
        row.offsetCount = groupCounts.get(row.groupId) || 1;

        const animal = row.animal;
        const projected = this.project(row.pos);
        if (!projected) continue;

        const baseX = X(projected[0]);
        const baseY = Y(projected[1]);
        const size = (animal.mapMarkerStyle === 'neutral-monster-red-dot' ? 7 : animal.mapMarkerStyle === 'special-icon' ? 18 : 13) * ms;
        const offset = this.wildlifeScreenOffset(row.offsetIndex, row.offsetCount, size);
        const x = baseX + offset[0];
        const y = baseY + offset[1];

        if (!onScreen(x, y)) continue;
        const wildlifeDrawn = this.drawWildlifeMarker(g, animal, x, y, size, 0.95);
        if (hasWildlifePortrait(animal.assetKey)) {
          if (wildlifeDrawn) {
            visibleWildlifePortraitCount += 1;
            if (animal.mutated) visibleMutantPortraitCount += 1;
          } else {
            missingWildlifeAssetKeys.add(animal.assetKey);
          }
        }
      }
    }

    // LAYER 2: Players
    let visiblePlayerMarkerCount = 0;
    let visiblePortraitCount = 0;
    const missingPortraitCodes = new Set();
    for (const p of this.data.players || []) {
      // A later exact alive row means the player revived and must reappear.
      if (playerLifeStateAt(p, cursor) === 'dead') continue;

      const currentPos = this.playerPositionAt(p, cursor);

      if (!currentPos) continue;
      if (!this.belongsToActiveSpace(currentPos)) continue;

      const pos = this.project(currentPos);
      if (!pos) continue;
      const x = X(pos[0]);
      const y = Y(pos[1]);
      if (!onScreen(x, y)) continue;
      visiblePlayerMarkerCount += 1;

      const isSelected = selectedPlayer && p.publicPlayerId === selectedPlayer.publicPlayerId;
      const size = (isSelected ? 26 : 22) * ms;

      // Portrait Circle
      g.save();
      g.beginPath();
      g.arc(x, y, size / 2, 0, Math.PI * 2);
      g.clip();

      const charImg = this.cachedCharacterImages[p.characterCode];
      if (charImg?.complete && charImg.naturalWidth) {
        g.drawImage(charImg, x - size / 2, y - size / 2, size, size);
        visiblePortraitCount += 1;
      } else {
        missingPortraitCodes.add(String(p.characterCode));
        g.fillStyle = teamColor(p.teamNumber);
        g.fillRect(x - size / 2, y - size / 2, size, size);
      }
      g.restore();

      // Team color border
      g.strokeStyle = teamColor(p.teamNumber);
      g.lineWidth = (isSelected ? 2.5 : 1.5) * Math.min(ms, 1.4);
      g.beginPath();
      g.arc(x, y, size / 2 + 0.5, 0, Math.PI * 2);
      g.stroke();

      if (isSelected) {
        g.strokeStyle = '#ffffff';
        g.lineWidth = 1.2;
        g.beginPath();
        g.arc(x, y, size / 2 + 2.5, 0, Math.PI * 2);
        g.stroke();
      }

      // Player Label
      g.fillStyle = '#ffffff';
      g.font = `${isSelected ? '750' : '650'} ${Math.round((isSelected ? 11 : 10) * Math.min(ms, 1.4))}px sans-serif`;
      g.textAlign = 'center';
      g.fillText(`#${p.teamNumber} ${p.characterName}`, x, y - size / 2 - 4);
    }

    // LAYER 3: World Static & World Events
    const lumiMarkers = [];
    const reconOrbMarkers = [];
    if (this.data.worldMap?.staticObjects) {
      for (const row of this.data.worldMap.staticObjects) {
        if (!this.worldObjectVisible(row, cursor)) continue;
        if (row.category === 'surveillance-camera' && (!this.state.layers.cameras || (selectedPlayer && row.ownerPublicPlayerId !== selectedPlayer.publicPlayerId))) continue;
        if (row.category === 'control-lens' && (!this.state.layers.controlLens || (selectedPlayer && row.ownerPublicPlayerId != null && row.ownerPublicPlayerId !== selectedPlayer.publicPlayerId))) continue;
        if (row.category === 'recon-orb' && (!this.state.layers.cameras || (selectedPlayer && row.ownerPublicPlayerId != null && row.ownerPublicPlayerId !== selectedPlayer.publicPlayerId))) continue;
        const isLumi = row.category === 'lumi';
        const isMovingWorldObject = isLumi || row.category === 'recon-orb';
        const rawPosition = isMovingWorldObject ? (worldMovementPositionAt(row, cursor) || row.position) : row.position;
        if (!this.belongsToActiveSpace(rawPosition)) continue;

        const landmark = riftLandmarkVisual(row, this.activeSpace);
        const pos = landmark?.pixel || this.project(rawPosition);
        if (!pos) continue;
        const x = X(pos[0]);
        const y = Y(pos[1]);
        if (!onScreen(x, y)) continue;

        const key = landmark?.key || (isLumi ? this.lumiAssetKeyAt(row, cursor) : worldObjectAssetAt(row, cursor, this.data.worldMap.transportModeTransitions));
        const size = (row.category === 'campfire' ? 14 : row.category === 'kiosk' ? 15 : isLumi ? 20 : row.category === 'recon-orb' ? 18 : 16) * ms;
        const moving = isLumi && lumiMovingAt(row, cursor, this.data.meta?.firstTick);
        if (isLumi) {
          this.drawLumiMovementTrail(g, row, cursor, X, Y, onScreen, ms);

        }
        let markerDrawn;
        if (key === 'transport-unknown') {
          g.save(); g.fillStyle = '#a0a5ad'; g.font = `600 ${14 * ms}px sans-serif`;
          g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText('?', x, y); g.restore();
          markerDrawn = true;
        } else if (row.category === 'recon-orb') {
          g.save(); g.fillStyle = '#399bff'; g.beginPath();
          g.arc(x, y, 3 * ms, 0, Math.PI * 2); g.fill(); g.restore();
          markerDrawn = true;
        } else {
          markerDrawn = this.drawMarkerAsset(g, key, x, y, size, false, 0.95);
        }
        if (markerDrawn && row.category === 'recon-orb') {
          reconOrbMarkers.push({ x, y, owner: row.ownerPublicPlayerId });
        }
        if (markerDrawn && isLumi) {
          lumiMarkers.push({ x, y, moving, movementAnchorCount: row.movementAnchorCount || 0 });
        }
      }
    }

    // World Events (Meteors, Trees of life, Air supplies, Bosses)
    if (this.data.worldMap?.timeline) {
      for (const row of this.worldEvents) {
        if (cursor < row.warningTick || cursor >= row.endTick) continue;
        const phase = cursor < row.activeTick ? 'warning' : 'active';
        if (!this.belongsToActiveSpace(row.position)) continue;
        const pos = this.project(row.position);
        if (!pos) continue;
        const x = X(pos[0]);
        const y = Y(pos[1]);
        if (!onScreen(x, y)) continue;

        const warningKey = `${row.kind}-warning`;
        const key = (phase === 'warning' && this.data.mapMarkerAssets?.icons?.[warningKey]) ? warningKey : row.kind;
        const enlargedNotice = row.kind === 'rift' || row.kind === 'rift-warning' ||
          (row.kind === 'air-supply-epic' && phase === 'warning');
        const size = (['wickeline', 'alpha', 'omega'].includes(row.kind) ? 22 : enlargedNotice ? 21 : 17) * ms;
        const alpha = phase === 'warning' ? 0.9 : 1.0;
        this.drawMarkerAsset(g, key, x, y, size, phase === 'warning', alpha);
      }
    }

    // LAYER 4: Tactical Pings (NO CIRCLES!)
    let visibleMovementPingCount = 0;
    const pingGroups = [
      [this.data.tacticalPings?.items || [], this.data.tacticalPings?.displaySeconds || 5, false],
      [this.data.worldMap?.movementPings || [], this.data.worldMap?.movementPingDisplaySeconds || 5, true]
    ];
    for (const [pings, displaySeconds, automatic] of pingGroups) {
      if (!this.state.layers.pings) continue;
      const pingSpan = (this.data.meta?.targetFrameRate || 60) * displaySeconds;
      const pingFrom = lowerBound(pings, cursor - pingSpan, r => r.tick);

      for (let i = pingFrom; i < pings.length && pings[i].tick <= cursor; i++) {
        const ping = pings[i];
        if (!ping.position || !ping.assetKey || ping.mapPositionStatus) continue;
        if (!this.belongsToActiveSpace(ping.position)) continue;
        const pos = this.project(ping.position);
        if (!pos) continue;
        const x = X(pos[0]);
        const y = Y(pos[1]);
        if (!onScreen(x, y)) continue;

        const age = (cursor - ping.tick) / pingSpan;
        // Finish the fade at zero instead of abruptly removing a 40%-opaque icon.
        const alpha = Math.min(1, Math.max(0, (1 - age) / 0.2));
        const size = (automatic ? 14 : 20) * ms;
        if (automatic) visibleMovementPingCount++;

        // Draw ping glyph directly without circular clip or outer circle!
        this.drawMarkerAsset(g, ping.assetKey, x, y, size, false, alpha, true);
      }
    }
    this.canvas.dataset.mapSpace = this.activeSpace?.spaceId || "lumia";
    this.canvas.dataset.visibleMovementPingCount = String(visibleMovementPingCount);
    this.canvas.dataset.lumiMarkerCount = String(lumiMarkers.length);
    this.canvas.dataset.lumiMarkerPositions = lumiMarkers
      .map(row => `${row.x.toFixed(2)},${row.y.toFixed(2)},${row.moving ? 'moving' : 'stopped'}`)
      .join(';');
    this.canvas.dataset.reconOrbMarkerCount = String(reconOrbMarkers.length);
    this.canvas.dataset.reconOrbMarkerPositions = reconOrbMarkers
      .map(row => `${row.x.toFixed(2)},${row.y.toFixed(2)},${row.owner}`)
      .join(';');
    this.canvas.dataset.visiblePlayerMarkerCount = String(visiblePlayerMarkerCount);
    this.canvas.dataset.visiblePortraitCount = String(visiblePortraitCount);
    this.canvas.dataset.missingPortraitCodes = [...missingPortraitCodes].join(',');
    this.canvas.dataset.visibleWildlifePortraitCount = String(visibleWildlifePortraitCount);
    this.canvas.dataset.visibleMutantPortraitCount = String(visibleMutantPortraitCount);
    this.canvas.dataset.missingWildlifeAssetKeys = [...missingWildlifeAssetKeys].join(',');
  }
}
