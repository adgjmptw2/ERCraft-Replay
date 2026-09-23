/* Presentation-only asset mappings for the frozen 12.3 public fixture. */

const ITEM_ASSET_BASE = 'https://cdn.dak.gg/assets/er/game-assets/12.3.0';

export const itemAssetOverrides = Object.freeze({
  301111: { itemName: '고기', forceExactCdn: true },
  102504: { displayScale: 1.00, itemName: '이퀄리브리엄' },
  103201: { displayScale: 1.08 },
  130504: { displayScale: 1.10, itemName: '더 행맨' },
  130702: { displayScale: 1.04 },
  202531: { displayScale: 1.04, itemName: '서리바람 흉갑' },
  301701: { displayScale: 0.92, itemName: '사과', forceExactCdn: true },
  303501: { displayScale: 1.00 },
  303503: { displayScale: 1.00 },
  303506: { displayScale: 1.04 },
  307001: { displayScale: 1.00 },
  307002: { displayScale: 1.00 },
});

function missingSemanticSlots(data) {
  const missing = [];
  const seen = new Set();
  for (const player of data.players || []) {
    const characterCode = String(player.characterCode);
    const character = data.skillAssets?.icons?.[characterCode];
    for (const slot of ['q', 'w', 'e', 'r', 'passive']) {
      const key = `${characterCode}:${slot}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const found = Object.entries(character?.slots || {})
        .some(([name, row]) => name === slot || row?.semanticSlot === slot);
      if (!found) missing.push({ characterCode, characterName: player.characterName, slot });
    }
  }
  return missing;
}

export function auditVisibleAssets(data) {
  const usedItemCodes = new Set();
  for (const player of data.players || []) {
    for (const timelineName of ['equipmentTimeline', 'inventoryTimeline']) {
      for (const row of player[timelineName] || []) {
        for (const update of row[1] || []) {
          if (Number.isFinite(update[1])) usedItemCodes.add(String(update[1]));
        }
      }
    }
  }

  const usedMapAssetKeys = new Set();
  for (const ping of data.tacticalPings?.items || []) {
    if (ping.assetKey) usedMapAssetKeys.add(ping.assetKey);
  }
  for (const row of data.worldMap?.staticObjects || []) {
    usedMapAssetKeys.add(row.assetKey || row.category);
  }
  for (const row of data.worldMap?.timeline || []) {
    usedMapAssetKeys.add(row.kind);
    if (Number.isFinite(row.warningTick)) usedMapAssetKeys.add(`${row.kind}-warning`);
  }
  for (const animal of data.wildlife?.instances || []) {
    if (animal.mapMarkerAssetKey) usedMapAssetKeys.add(animal.mapMarkerAssetKey);
  }

  return {
    missingItemCodes: [...usedItemCodes]
      .filter(code => !data.itemAssets?.icons?.[code])
      .sort((a, b) => Number(a) - Number(b)),
    missingSkillSlots: missingSemanticSlots(data),
    missingMapAssetKeys: [...usedMapAssetKeys]
      .filter(key => !data.mapMarkerAssets?.icons?.[key])
      .sort(),
  };
}

export function applyFrontendAssetOverrides(data) {
  data.itemAssets ||= {};
  data.itemAssets.icons ||= {};
  for (const [code, options] of Object.entries(itemAssetOverrides)) {
    if (!data.itemAssets.icons[code] || options.forceExactCdn) {
      data.itemAssets.icons[code] = {
        imageDataUrl: `${ITEM_ASSET_BASE}/ItemIcon_${code}.png`,
        displayScale: options.displayScale,
        status: 'exact-12.3-official-cdn-frontend-mapping',
      };
    }
    if (options.itemName && data.itemCatalog?.[code] && !data.itemCatalog[code].itemName) {
      data.itemCatalog[code].itemName = options.itemName;
      data.itemCatalog[code].itemNameStatus = 'verified-korean-data-frontend-mapping';
    }
  }

  const adela = data.skillAssets?.icons?.['24'];
  if (adela && !adela.slots?.passive) {
    adela.slots ||= {};
    adela.slots.passive = {
      semanticSlot: 'passive',
      imageDataUrl: './public/ui-assets/skills/Adela_T.png',
      status: 'official-fankit-frontend-mapping',
    };
    if (Array.isArray(adela.unavailableSemanticSlots)) {
      adela.unavailableSemanticSlots = adela.unavailableSemanticSlots.filter(slot => slot !== 'passive');
    }
  }

  return data;
}
