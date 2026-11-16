// Themes: the built-in palettes, custom themes, and painting them onto the page.
//
// A theme is { id, name, kind, colors }.
// - kind is "light" or "dark"; it sets data-theme on <html>, which CSS keys off.
// - colors maps every token in THEME_TOKENS to a "#RRGGBB" value.
// - Each color becomes the CSS variable --<token> on <html>.
// The Graphite and Daylight values here must match css/tokens.css (tests/test_themes.py checks).
// Enabled mods add their own themes; they act like built-in ones.

export const THEME_GROUPS = ['Surfaces', 'Text', 'Accents', 'Header band', 'Graph lanes', 'Diffs and status'];

/** Every editable color, in the order the editor shows them. */
export const THEME_TOKENS = [
  { key: 'bg', label: 'Background', group: 'Surfaces', hint: 'Main surface: graph, diffs, lists' },
  { key: 'panel', label: 'Panel', group: 'Surfaces', hint: 'Sidebar, toolbar, section headers' },
  { key: 'panel-strong', label: 'Panel, stronger', group: 'Surfaces', hint: 'Badges and neutral fills' },
  { key: 'raised', label: 'Raised surface', group: 'Surfaces', hint: 'Dialogs, menus, inputs' },
  { key: 'line', label: 'Border', group: 'Surfaces' },
  { key: 'line-soft', label: 'Border, soft', group: 'Surfaces' },
  { key: 'hover', label: 'Hover', group: 'Surfaces' },
  { key: 'selected', label: 'Selected row', group: 'Surfaces' },
  { key: 'highlight', label: 'Highlight', group: 'Surfaces', hint: 'Tag labels and info banners' },

  { key: 'text', label: 'Text', group: 'Text' },
  { key: 'text-strong', label: 'Text, strong', group: 'Text', hint: 'Headings and names' },
  { key: 'muted', label: 'Text, muted', group: 'Text', hint: 'Captions and secondary labels' },
  { key: 'title', label: 'Titles', group: 'Text', hint: 'The ALL CAPS page and dialog titles' },
  { key: 'link', label: 'Links', group: 'Text' },

  { key: 'accent', label: 'Accent', group: 'Accents', hint: 'Primary buttons and the current branch' },
  { key: 'accent-hover', label: 'Accent, hover', group: 'Accents' },
  { key: 'on-accent', label: 'Text on accent', group: 'Accents' },
  { key: 'accent-2', label: 'Second accent', group: 'Accents', hint: 'Icons and markers; text on dark themes' },
  { key: 'on-accent-2', label: 'Text on second accent', group: 'Accents' },

  { key: 'brand-1', label: 'Band, start', group: 'Header band', hint: 'Gradient of the top bar line and the repository list header' },
  { key: 'brand-2', label: 'Band, end', group: 'Header band' },
  { key: 'on-brand', label: 'Text on band', group: 'Header band' },
  { key: 'on-brand-muted', label: 'Text on band, muted', group: 'Header band' },
  { key: 'on-brand-accent', label: 'Text on band, accent', group: 'Header band' },

  { key: 'lane-0', label: 'Lane 1', group: 'Graph lanes' },
  { key: 'lane-1', label: 'Lane 2', group: 'Graph lanes' },
  { key: 'lane-2', label: 'Lane 3', group: 'Graph lanes' },
  { key: 'lane-3', label: 'Lane 4', group: 'Graph lanes' },
  { key: 'lane-4', label: 'Lane 5', group: 'Graph lanes' },
  { key: 'lane-5', label: 'Lane 6', group: 'Graph lanes' },

  { key: 'ok', label: 'Added', group: 'Diffs and status' },
  { key: 'ok-bg', label: 'Added, line background', group: 'Diffs and status' },
  { key: 'ok-bg-strong', label: 'Added, gutter', group: 'Diffs and status' },
  { key: 'danger', label: 'Removed and destructive', group: 'Diffs and status' },
  { key: 'danger-bg', label: 'Removed, line background', group: 'Diffs and status' },
  { key: 'danger-bg-strong', label: 'Removed, gutter', group: 'Diffs and status' },
  { key: 'on-danger', label: 'Text on destructive buttons', group: 'Diffs and status' },
  { key: 'warn', label: 'Warning and modified', group: 'Diffs and status' },
  { key: 'warn-bg', label: 'Warning background', group: 'Diffs and status' },
];

export const TOKEN_KEYS = THEME_TOKENS.map((t) => t.key);

/** The theme a new install uses, and the light and dark picks when following the system. */
export const DEFAULT_THEME = 'graphite';
export const DEFAULT_LIGHT = 'daylight';
export const DEFAULT_DARK = 'graphite';

export const BUILTIN_THEMES = [
  {
    id: 'daylight',
    name: 'Daylight',
    kind: 'light',
    colors: {
      'bg': '#FFFFFF', 'panel': '#F4F6F8', 'panel-strong': '#E8ECF0', 'raised': '#FFFFFF',
      'line': '#D5DBE2', 'line-soft': '#E7EBEF', 'hover': '#EEF2F6', 'selected': '#DCE8F8', 'highlight': '#DDF3F1',
      'text': '#3B4350', 'text-strong': '#151A22', 'muted': '#687282', 'title': '#1F5FBF', 'link': '#1F5FBF',
      'accent': '#1F5FBF', 'accent-hover': '#184E9F', 'on-accent': '#FFFFFF', 'accent-2': '#0B7F7A', 'on-accent-2': '#FFFFFF',
      'brand-1': '#1F5FBF', 'brand-2': '#152B52', 'on-brand': '#FFFFFF', 'on-brand-muted': '#D5E3F7', 'on-brand-accent': '#8FE3DC',
      'lane-0': '#1F5FBF', 'lane-1': '#0B7F7A', 'lane-2': '#C2410C', 'lane-3': '#7C3AED', 'lane-4': '#687282', 'lane-5': '#B8C4D4',
      'ok': '#1F7A4D', 'ok-bg': '#E2F3E9', 'ok-bg-strong': '#BFE3CF',
      'danger': '#B42335', 'danger-bg': '#FBE9EB', 'danger-bg-strong': '#F4C5CB', 'on-danger': '#FFFFFF',
      'warn': '#946000', 'warn-bg': '#FBF0D9',
    },
  },
  {
    id: 'paper',
    name: 'Paper',
    kind: 'light',
    colors: {
      'bg': '#FBF9F4', 'panel': '#F3EFE6', 'panel-strong': '#E9E3D6', 'raised': '#FFFFFF',
      'line': '#DDD5C5', 'line-soft': '#EAE4D8', 'hover': '#F0EBE0', 'selected': '#E9E0CF', 'highlight': '#DDEEEA',
      'text': '#4A4640', 'text-strong': '#26231F', 'muted': '#786F62', 'title': '#A14A2A', 'link': '#A14A2A',
      'accent': '#A14A2A', 'accent-hover': '#883D21', 'on-accent': '#FFFFFF', 'accent-2': '#2A7370', 'on-accent-2': '#FFFFFF',
      'brand-1': '#A14A2A', 'brand-2': '#5B2A3A', 'on-brand': '#FFFFFF', 'on-brand-muted': '#F1DDD0', 'on-brand-accent': '#FFD9A8',
      'lane-0': '#A14A2A', 'lane-1': '#2A7370', 'lane-2': '#3D4F7D', 'lane-3': '#B47E12', 'lane-4': '#7A6E8F', 'lane-5': '#B9AE9C',
      'ok': '#2B7349', 'ok-bg': '#E3F1E5', 'ok-bg-strong': '#C3E2C9',
      'danger': '#AD3434', 'danger-bg': '#F9E6E2', 'danger-bg-strong': '#F1C7C0', 'on-danger': '#FFFFFF',
      'warn': '#8F6208', 'warn-bg': '#F8EDCF',
    },
  },
  {
    id: 'graphite',
    name: 'Graphite',
    kind: 'dark',
    colors: {
      'bg': '#1E1F22', 'panel': '#26282C', 'panel-strong': '#2F3237', 'raised': '#2B2D31',
      'line': '#3A3D43', 'line-soft': '#303338', 'hover': '#2C2F34', 'selected': '#353A44', 'highlight': '#2A3A55',
      'text': '#C9CDD4', 'text-strong': '#F2F3F5', 'muted': '#8F95A0', 'title': '#F2F3F5', 'link': '#7FB2FF',
      'accent': '#3F6FCC', 'accent-hover': '#4C7CD8', 'on-accent': '#FFFFFF', 'accent-2': '#7FB2FF', 'on-accent-2': '#14161A',
      'brand-1': '#3F6FCC', 'brand-2': '#23272E', 'on-brand': '#FFFFFF', 'on-brand-muted': '#D3D9E3', 'on-brand-accent': '#A9CBFF',
      'lane-0': '#7FB2FF', 'lane-1': '#E0A458', 'lane-2': '#9AD17A', 'lane-3': '#D98BD0', 'lane-4': '#6FD0C8', 'lane-5': '#A0A6B0',
      'ok': '#7BC98F', 'ok-bg': '#20362A', 'ok-bg-strong': '#2B4C39',
      'danger': '#F08A8A', 'danger-bg': '#3A2326', 'danger-bg-strong': '#57303A', 'on-danger': '#1E1F22',
      'warn': '#E5B567', 'warn-bg': '#3A3323',
    },
  },
  {
    id: 'midnight',
    name: 'Midnight',
    kind: 'dark',
    colors: {
      'bg': '#0B0D12', 'panel': '#11141B', 'panel-strong': '#181C26', 'raised': '#141821',
      'line': '#252B38', 'line-soft': '#1A1F2A', 'hover': '#161A24', 'selected': '#1F2536', 'highlight': '#172A3A',
      'text': '#B8C0D0', 'text-strong': '#FFFFFF', 'muted': '#7A859B', 'title': '#FFFFFF', 'link': '#5EEAD4',
      'accent': '#6D4AFF', 'accent-hover': '#8064FF', 'on-accent': '#FFFFFF', 'accent-2': '#5EEAD4', 'on-accent-2': '#0B0D12',
      'brand-1': '#6D4AFF', 'brand-2': '#1B2A6B', 'on-brand': '#FFFFFF', 'on-brand-muted': '#D5CEFF', 'on-brand-accent': '#5EEAD4',
      'lane-0': '#A78BFA', 'lane-1': '#5EEAD4', 'lane-2': '#F472B6', 'lane-3': '#60A5FA', 'lane-4': '#FBBF24', 'lane-5': '#94A3B8',
      'ok': '#4ADE80', 'ok-bg': '#0F2A1C', 'ok-bg-strong': '#174428',
      'danger': '#FB7185', 'danger-bg': '#2E141C', 'danger-bg-strong': '#4A1D2A', 'on-danger': '#0B0D12',
      'warn': '#FBBF24', 'warn-bg': '#2B2410',
    },
  },
  {
    id: 'evergreen',
    name: 'Evergreen',
    kind: 'dark',
    colors: {
      'bg': '#10201C', 'panel': '#152A25', 'panel-strong': '#1C362F', 'raised': '#19302A',
      'line': '#2B4A41', 'line-soft': '#213B34', 'hover': '#1A322C', 'selected': '#24443B', 'highlight': '#1E4038',
      'text': '#C9DDD5', 'text-strong': '#F3FBF7', 'muted': '#86A399', 'title': '#F3FBF7', 'link': '#7FE0B5',
      'accent': '#22785A', 'accent-hover': '#2A8A68', 'on-accent': '#FFFFFF', 'accent-2': '#F2C14E', 'on-accent-2': '#10201C',
      'brand-1': '#22785A', 'brand-2': '#123B4A', 'on-brand': '#FFFFFF', 'on-brand-muted': '#CFE8DD', 'on-brand-accent': '#F2C14E',
      'lane-0': '#7FE0B5', 'lane-1': '#F2C14E', 'lane-2': '#8CC8FF', 'lane-3': '#F29E8E', 'lane-4': '#C9A7F0', 'lane-5': '#86A399',
      'ok': '#7FE0B5', 'ok-bg': '#17382C', 'ok-bg-strong': '#1F4E3C',
      'danger': '#F29E8E', 'danger-bg': '#3B2622', 'danger-bg-strong': '#57332C', 'on-danger': '#10201C',
      'warn': '#F2C14E', 'warn-bg': '#383219',
    },
  },
].map((theme) => ({ ...theme, builtin: true }));

const HEX = /^#[0-9a-fA-F]{6}$/;

// -- lookup -----------------------------------------------------------------------

export function customThemes(settings) {
  return Array.isArray(settings.custom_themes) ? settings.custom_themes : [];
}

let modThemes = [];

/**
 * Take the themes of the enabled mods.
 * - mods is the list from /api/state; the server has already checked every theme.
 * - Mod themes cannot be edited, like built-in ones, and carry the mod's name.
 */
export function setModThemes(mods) {
  modThemes = (Array.isArray(mods) ? mods : [])
    .filter((m) => m.enabled)
    .flatMap((m) => (m.themes || []).map((t) => ({ ...t, builtin: true, mod: m.name })));
}

/** Built-in themes, then those from mods, then the user's own. */
export function allThemes(settings) {
  return [...BUILTIN_THEMES, ...modThemes, ...customThemes(settings).map((t) => ({ ...t, builtin: false }))];
}

export function findTheme(settings, id) {
  return allThemes(settings).find((t) => t.id === id) || null;
}

const prefersDark = () => window.matchMedia('(prefers-color-scheme: dark)').matches;

/**
 * The theme that should be on screen right now.
 * - settings.theme is "system" or a theme id.
 * - "system" picks settings.theme_light or settings.theme_dark from the OS setting.
 * - A chosen theme that no longer exists falls back to the default theme.
 * - A system pick that no longer exists falls back to the default light or dark theme.
 */
export function activeTheme(settings) {
  if (settings.theme && settings.theme !== 'system') {
    return findTheme(settings, settings.theme) || findTheme(settings, DEFAULT_THEME);
  }
  const dark = prefersDark();
  return findTheme(settings, dark ? settings.theme_dark : settings.theme_light)
    || findTheme(settings, dark ? DEFAULT_DARK : DEFAULT_LIGHT);
}

// -- painting ---------------------------------------------------------------------

const CACHE_KEY = 'gitsense.theme';

/**
 * Paint a theme onto the page.
 * - Missing colors fall back to the default theme of the same kind.
 * - remember: false is for live previews, so a preview is never shown at the next start.
 */
export function applyTheme(theme, { remember = true } = {}) {
  const root = document.documentElement;
  const kind = theme.kind === 'dark' ? 'dark' : 'light';
  const base = BUILTIN_THEMES.find((t) => t.id === (kind === 'dark' ? DEFAULT_DARK : DEFAULT_LIGHT));
  const colors = {};
  root.dataset.theme = kind;
  root.dataset.themeId = theme.id || '';
  for (const key of TOKEN_KEYS) {
    colors[key] = theme.colors && HEX.test(theme.colors[key] || '') ? theme.colors[key] : base.colors[key];
    root.style.setProperty(`--${key}`, colors[key]);
  }
  if (remember) {
    try { localStorage.setItem(CACHE_KEY, JSON.stringify({ id: theme.id, kind, colors })); } catch { /* storage unavailable */ }
  }
}

/** Paint the theme used last time, before settings have loaded, so there is no flash at start. */
export function paintRememberedTheme() {
  try {
    const cached = JSON.parse(localStorage.getItem(CACHE_KEY) || 'null');
    if (cached && cached.colors) applyTheme(cached, { remember: false });
  } catch { /* first run or storage unavailable */ }
}

// -- custom themes ----------------------------------------------------------------

const slug = (text) => text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'theme';

function uniqueId(settings, name) {
  const taken = new Set(allThemes(settings).map((t) => t.id));
  const stem = `custom-${slug(name)}`;
  let id = stem;
  for (let n = 2; taken.has(id); n++) id = `${stem}-${n}`;
  return id;
}

function uniqueName(settings, name) {
  const taken = new Set(allThemes(settings).map((t) => t.name.toLowerCase()));
  if (!taken.has(name.toLowerCase())) return name;
  for (let n = 2; ; n++) {
    const candidate = `${name} ${n}`;
    if (!taken.has(candidate.toLowerCase())) return candidate;
  }
}

/** A new custom theme that starts as a copy of another. It is not saved yet. */
export function cloneTheme(settings, source, name = null) {
  const title = uniqueName(settings, name || `${source.name} copy`);
  return {
    id: uniqueId(settings, title),
    name: title,
    kind: source.kind,
    colors: { ...source.colors },
    based_on: source.id,
  };
}

/**
 * Check and tidy a theme that came from outside (an import or a pasted file).
 * - Throws an Error with a plain message when it cannot be used.
 * - Unknown colors are dropped.
 * - Missing colors are filled from the default theme of the same kind.
 * - Always returns a theme with a fresh id, so it cannot replace a built-in.
 */
export function sanitizeTheme(input, settings) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('That is not a theme.');
  const kind = input.kind === 'dark' ? 'dark' : input.kind === 'light' ? 'light' : null;
  if (!kind) throw new Error('The theme needs a kind: "light" or "dark".');
  if (!input.colors || typeof input.colors !== 'object') throw new Error('The theme has no colors.');
  const base = BUILTIN_THEMES.find((t) => t.id === (kind === 'dark' ? DEFAULT_DARK : DEFAULT_LIGHT));
  const colors = {};
  let known = 0;
  for (const key of TOKEN_KEYS) {
    const value = input.colors[key];
    if (typeof value === 'string' && HEX.test(value)) {
      colors[key] = value.toUpperCase();
      known += 1;
    } else {
      colors[key] = base.colors[key];
    }
  }
  if (!known) throw new Error('None of the colors in that theme could be read. Colors must look like #3F6FCC.');
  const name = uniqueName(settings, String(input.name || 'Imported theme').trim().slice(0, 60) || 'Imported theme');
  return { id: uniqueId(settings, name), name, kind, colors };
}

/** JSON text for sharing a theme with someone else. */
export function exportTheme(theme) {
  return JSON.stringify({ gitsense_theme: 1, name: theme.name, kind: theme.kind, colors: theme.colors }, null, 2);
}

// -- color math -------------------------------------------------------------------

function luminance(hex) {
  const n = parseInt(hex.slice(1), 16);
  const channel = (v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255);
}

/** WCAG contrast ratio between two "#RRGGBB" colors (1 to 21). */
export function contrast(a, b) {
  if (!HEX.test(a || '') || !HEX.test(b || '')) return 1;
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * Text and background pairs that should stay readable, for the editor's warnings.
 * Each is [text token, background token, minimum ratio, description].
 */
export const CONTRAST_PAIRS = [
  ['text', 'bg', 4.5, 'Text on the background'],
  ['text', 'panel', 4.5, 'Text on panels'],
  ['text-strong', 'bg', 4.5, 'Strong text on the background'],
  ['muted', 'bg', 3, 'Muted text on the background'],
  ['on-accent', 'accent', 4.5, 'Text on accent buttons'],
  ['on-accent-2', 'accent-2', 3, 'Text on second-accent badges'],
  ['on-brand', 'brand-1', 4.5, 'Text on the header band'],
  ['on-brand', 'brand-2', 4.5, 'Text on the header band'],
  ['text-strong', 'ok-bg', 4.5, 'Added lines in a diff'],
  ['text-strong', 'danger-bg', 4.5, 'Removed lines in a diff'],
  ['on-danger', 'danger', 4.5, 'Text on destructive buttons'],
];
