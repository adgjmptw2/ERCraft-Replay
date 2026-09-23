/** Source-pixel rendering filters. Original asset bytes remain immutable. */
const GLYPH_EXTENSIONS = {
  'all-in': [[5, 10, 5, 8], [15, 20, 5, 8], [5, 10, 17, 19], [15, 20, 17, 19]],
  escape: [[10, 15, 3, 6], [4, 21, 13, 18], [7, 18, 19, 19]],
  help: [[12, 18, 5, 5], [9, 19, 6, 6], [5, 19, 7, 7], [4, 20, 8, 8], [5, 20, 9, 12], [10, 14, 20, 22]],
  'need-vision': [[5, 9, 4, 10], [11, 14, 20, 22]],
  'enemy-vision': [[11, 14, 20, 22]],
  run: [[5, 20, 17, 18], [8, 17, 19, 19]],
  'lets-join': [[5, 20, 17, 18], [7, 18, 19, 19], [9, 18, 20, 20], [14, 17, 21, 21]],
  warning: [],
  fallback: [],
};

export function isPreservedPingPixel(key, x, y, width, height) {
  if (key === 'tactical-ping-select') return true;
  // Target's circle is the crosshair glyph itself, not the shared outer border.
  if (key === 'tactical-ping-target') return true;
  const extensions = GLYPH_EXTENSIONS[key.replace('tactical-ping-', '')];
  if (!extensions || width !== 26 || height !== 26) return true;
  if (Math.hypot(x - 12.5, y - 12.5) < 9) return true;
  // Retain pixels where original glyphs meet the ring: those baked pixels cannot
  // be separated without repainting the glyph, which the user prohibited.
  return extensions.some(([x0, x1, y0, y1]) => x >= x0 && x <= x1 && y >= y0 && y <= y1);
}

export function maskPingPixels(data, width, height, key) {
  const out = new Uint8ClampedArray(data);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      if (!isPreservedPingPixel(key, x, y, width, height)) out[(y * width + x) * 4 + 3] = 0;
    }
  }
  return out;
}

export function blackenRiftPixels(data) {
  const out = new Uint8ClampedArray(data);
  for (let i = 0; i < out.length; i += 4) {
    const high = Math.max(data[i], data[i + 1], data[i + 2]);
    const low = Math.min(data[i], data[i + 1], data[i + 2]);
    if (data[i + 3] > 0 && high <= 80 && high - low <= 12) {
      out[i] = out[i + 1] = out[i + 2] = 0;
    }
  }
  return out;
}

function renderFilteredAsset(image, filter) {
  const canvas = document.createElement('canvas');
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(image, 0, 0);
  const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height);
  pixels.data.set(filter(pixels.data, canvas.width, canvas.height));
  ctx.putImageData(pixels, 0, 0);
  return canvas;
}

export function makeRinglessPingGlyph(image, key) {
  return renderFilteredAsset(image, (pixels, width, height) => maskPingPixels(pixels, width, height, key));
}

export function makeBlackRiftBackground(image) {
  return renderFilteredAsset(image, blackenRiftPixels);
}
