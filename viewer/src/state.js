/* ERCraft Replay Analytics - Application State */

export const state = {
  theme: localStorage.getItem('ercraft-theme') || 'dark',
  view: 'map',
  playerId: null,
  cursor: 0,
  speed: 1,
  playing: false,
  cameraLock: 'focus',
  mapView: { zoom: 1, ox: 0, oy: 0 },
  layers: {
    wildlife: false,
    pings: true,
    cameras: true,
    controlLens: true,
  },
  wildlifeGuideOpen: false,
  eventScope: 'player',
  eventType: 'all',
  eventQuery: '',
  eventPage: 1,
};

export function toggleTheme() {
  state.theme = state.theme === 'dark' ? 'light' : 'dark';
  localStorage.setItem('ercraft-theme', state.theme);
  document.documentElement.setAttribute('data-theme', state.theme);
}

export function initTheme() {
  document.documentElement.setAttribute('data-theme', state.theme);
}
