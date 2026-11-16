// Settings dialog. Every change is saved at once through app.updateSettings().

import { api } from './api.js';
import { h, clear, append, icon, button, iconButton, avatar, copyText, debounce } from './util.js';
import { openModal, confirmDialog, toast, toastError } from './ui.js';
import { allThemes, cloneTheme, exportTheme, sanitizeTheme } from './themes.js';
import { openThemeEditor, isThemeEditorOpen, focusThemeEditor, themePreview } from './theme-editor.js';

const GH_INSTALL_URL = 'https://cli.github.com';

const PULL_MODES = [
  { value: 'ff-only', label: 'Fast-forward only', hint: 'Moves your branch ahead only. Stops when you and the remote both have new commits.' },
  { value: 'rebase', label: 'Rebase', hint: 'Replays your new commits on top of the remote ones, keeping history in a straight line.' },
  { value: 'merge', label: 'Merge', hint: 'Joins the remote commits into your branch with a merge commit when both sides have changed.' },
];

let isOpen = false;

/**
 * Open the Settings dialog.
 * - opts.section: "appearance" scrolls to that section and focuses the theme in use
 * - opts.focusTheme: a theme id whose gallery card gets the focus instead
 */
export function openSettings(app, opts = {}) {
  if (isThemeEditorOpen()) {
    focusThemeEditor();
    return Promise.resolve(null);
  }
  if (isOpen) return Promise.resolve(null);
  isOpen = true;
  return openModal({
    eyebrow: 'GitSense',
    title: 'Settings',
    wide: true,
    build: (close) => ({
      body: h('div', { class: 'set' },
        appearanceSection(app, close),
        gitSection(app),
        githubSection(app),
        filesSection(app),
        modsSection(app),
        aboutSection(app)),
      foot: [
        h('span', { class: 'set-saved muted' }, 'Changes are saved as you make them.'),
        h('span', { class: 'spacer' }),
        button('Done', () => close(null), { class: 'primary' }),
      ],
    }),
    onOpen: ({ modal }) => {
      modal.classList.add('set-modal');
      if (opts.section === 'appearance' || opts.focusTheme) {
        const id = opts.focusTheme || app.theme().id;
        const card = [...modal.querySelectorAll('.tcard-pick')].find((el) => el.dataset.id === id)
          || modal.querySelector('.tcard-pick');
        modal.querySelector('[data-section="appearance"]')?.scrollIntoView({ block: 'start' });
        if (card) {
          card.focus({ preventScroll: true });
          card.scrollIntoView({ block: 'nearest' });
        }
      } else {
        modal.querySelector('.modal-foot .btn.primary')?.focus();
      }
    },
  }).finally(() => { isOpen = false; });
}

/** Save one or more settings and tell other modules about it. */
function save(app, changes) {
  const done = app.updateSettings(changes);
  // After the save, because the server reads some settings itself.
  done.then(() => window.dispatchEvent(new CustomEvent('gitsense:settings', { detail: changes })));
  return done;
}

// -- building blocks -----------------------------------------------------------

function section(title, ...rows) {
  return h('section', { class: 'set-section', dataset: { section: title.toLowerCase() } },
    h('p', { class: 'eyebrow set-eyebrow' }, title),
    rows);
}

/**
 * One setting: a label and hint on the left, the control on the right.
 * - id ties the visible label to the control for screen readers
 * - stack puts the control under the label for wide controls
 */
function row({ label, hint, control, id, stack = false }) {
  return h('div', { class: `set-row ${stack ? 'set-row-stack' : ''}`.trim() },
    h('div', { class: 'set-text' },
      id ? h('label', { class: 'set-label', for: id }, label) : h('span', { class: 'set-label' }, label),
      hint ? h('p', { class: 'set-hint' }, hint) : null),
    h('div', { class: 'set-control' }, control));
}

/** A segmented control. onPick(value) runs when the choice changes. */
function segmented(label, options, value, onPick) {
  const seg = h('div', { class: 'seg set-seg', role: 'radiogroup', 'aria-label': label });
  const paint = (current) => {
    for (const btn of seg.children) {
      const on = btn.dataset.value === current;
      btn.classList.toggle('on', on);
      btn.setAttribute('aria-checked', String(on));
      btn.tabIndex = on ? 0 : -1;
    }
  };
  for (const opt of options) {
    seg.append(h('button', {
      type: 'button',
      role: 'radio',
      dataset: { value: opt.value },
      onclick: () => { paint(opt.value); onPick(opt.value); },
    }, opt.icon ? icon(opt.icon, 13) : null, opt.label));
  }
  // Arrow keys move the choice, as in a native radio group.
  seg.addEventListener('keydown', (ev) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(ev.key)) return;
    ev.preventDefault();
    const buttons = [...seg.children];
    const at = buttons.findIndex((b) => b.classList.contains('on'));
    const step = ev.key === 'ArrowLeft' || ev.key === 'ArrowUp' ? -1 : 1;
    const next = buttons[(at + step + buttons.length) % buttons.length];
    next.focus();
    next.click();
  });
  paint(value);
  return seg;
}

function toggle(app, key, label, hint) {
  const id = `set-${key}`;
  const box = h('input', {
    type: 'checkbox', id, checked: Boolean(app.settings[key]),
    onchange: () => save(app, { [key]: box.checked }),
  });
  return h('div', { class: 'set-row set-row-check' },
    h('label', { class: 'check set-check', for: id }, box,
      h('span', { class: 'set-text' },
        h('span', { class: 'set-label' }, label),
        hint ? h('span', { class: 'set-hint' }, hint) : null)));
}

/** A whole-number input saved when it changes, clamped to [min, max]. */
function numberInput(app, key, { min, max, step = 1, unit }) {
  const id = `set-${key}`;
  const input = h('input', {
    class: 'input set-number', type: 'number', id, min, max, step,
    value: String(app.settings[key]), inputmode: 'numeric',
  });
  const commit = () => {
    const n = Math.round(Number(input.value));
    if (input.value.trim() === '' || !Number.isFinite(n)) {
      input.value = String(app.settings[key]);
      return;
    }
    const value = Math.max(min, Math.min(max, n));
    input.value = String(value);
    if (value !== app.settings[key]) save(app, { [key]: value });
  };
  input.addEventListener('change', commit);
  return { id, control: h('div', { class: 'set-inline' }, input, unit ? h('span', { class: 'muted' }, unit) : null) };
}

/** A text input saved shortly after typing stops, and on blur. */
function textInput(app, key, attrs = {}) {
  const input = h('input', {
    class: 'input', type: 'text', id: `set-${key}`, value: app.settings[key] || '',
    spellcheck: 'false', autocomplete: 'off', autocapitalize: 'off', ...attrs,
  });
  const commit = () => {
    const value = input.value.trim();
    if (value !== (app.settings[key] || '')) save(app, { [key]: value });
  };
  input.addEventListener('input', debounce(commit, 600));
  input.addEventListener('change', commit);
  return input;
}

// -- sections ------------------------------------------------------------------

function appearanceSection(app, closeSettings) {
  return section('Appearance',
    themePicker(app, closeSettings),
    row({
      label: 'Diff view',
      hint: 'How file changes are shown when you open a diff.',
      control: segmented('Diff view', [
        { value: 'unified', label: 'Unified', icon: 'rows-2' },
        { value: 'split', label: 'Split', icon: 'columns-2' },
      ], app.settings.diff_view || 'unified', (value) => save(app, { diff_view: value })),
    }),
    (() => {
      const limit = numberInput(app, 'commit_limit', { min: 100, max: 100000, step: 100, unit: 'commits' });
      return row({ label: 'Commits in the graph', hint: 'How many commits to load. More takes longer on large repositories.', ...limit });
    })(),
    toggle(app, 'show_remote_branches_in_graph', 'Show remote branches in the graph',
      'Include commits that are only on remote branches.'),
    toggle(app, 'highlight_lineage', "Highlight the selected commit's history",
      'Fade commits that are neither an ancestor nor a descendant of the commit you select.'));
}

// -- themes --------------------------------------------------------------------

const following = (settings) => !settings.theme || settings.theme === 'system';

/**
 * The theme part of Appearance: match-the-system switch, light and dark picks, and the gallery.
 * - It redraws itself after every change and keeps the focus on the same control.
 * - data-enter-own stops Enter on a card or select from pressing Done.
 */
function themePicker(app, closeSettings) {
  const el = h('div', { class: 'tpick', 'data-enter-own': '' });

  const render = () => {
    const focusKey = el.contains(document.activeElement) ? document.activeElement.dataset.fk : null;
    const s = app.settings;
    const themes = allThemes(s);
    const mine = themes.filter((t) => !t.builtin);
    const fromMods = themes.filter((t) => t.mod);
    clear(el);
    append(el, [
      h('div', { class: 'set-text tpick-intro' },
        h('span', { class: 'set-label' }, 'Theme'),
        h('span', { class: 'set-hint' }, 'Click a theme to use it. Built-in themes stay as they are; duplicate one to make your own.')),
      matchSystem(app, themes, render),
      h('div', { class: 'tpick-head' }, h('p', { class: 'eyebrow' }, 'Built-in')),
      h('div', { class: 'tgrid' }, themes.filter((t) => t.builtin && !t.mod).map((t) => themeCard(app, t, render, closeSettings))),
      fromMods.length ? h('div', { class: 'tpick-head' }, h('p', { class: 'eyebrow' }, 'From mods')) : null,
      fromMods.length ? h('div', { class: 'tgrid' }, fromMods.map((t) => themeCard(app, t, render, closeSettings))) : null,
      h('div', { class: 'tpick-head' },
        h('p', { class: 'eyebrow' }, 'Your themes'),
        tag(button('Import theme…', () => importTheme(app, render), { class: 'sm ghost', icon: 'download' }), 'import')),
      mine.length
        ? h('div', { class: 'tgrid' }, mine.map((t) => themeCard(app, t, render, closeSettings)))
        : h('p', { class: 'set-hint tpick-empty' }, 'None yet. Duplicate a theme above to make your own, or import one.'),
    ]);
    if (!focusKey) return;
    // The control may be gone (a deleted theme): fall back to the card on screen.
    const again = [...el.querySelectorAll('[data-fk]')].find((n) => n.dataset.fk === focusKey)
      || el.querySelector('.tcard.on .tcard-pick');
    again?.focus();
  };
  render();
  return el;
}

function matchSystem(app, themes, render) {
  const s = app.settings;
  const on = following(s);
  const box = h('input', {
    type: 'checkbox', id: 'set-theme-system', checked: on, dataset: { fk: 'system' },
    onchange: () => {
      // Turning it off keeps whatever is on screen now.
      save(app, { theme: box.checked ? 'system' : app.theme().id });
      render();
    },
  });
  const pick = (kind, key, label) => {
    const id = `set-${key}`;
    const select = h('select', {
      class: 'input', id, dataset: { fk: key },
      onchange: () => {
        save(app, { [key]: select.value });
        render();
      },
    }, themes.filter((t) => t.kind === kind || t.id === s[key]).map((t) => h('option', { value: t.id, selected: t.id === s[key] }, t.name)));
    return h('div', { class: 'field tpick-sys-field' },
      h('label', { for: id }, icon(kind === 'dark' ? 'moon' : 'sun', 12), label), select);
  };
  return h('div', { class: 'tpick-system' },
    h('label', { class: 'check set-check', for: 'set-theme-system' }, box,
      h('span', { class: 'set-text' },
        h('span', { class: 'set-label' }, 'Match the system'),
        h('span', { class: 'set-hint' }, 'Use one theme while your computer is in light mode and another in dark mode.'))),
    on ? h('div', { class: 'tpick-sys' },
      pick('light', 'theme_light', 'Light theme'),
      pick('dark', 'theme_dark', 'Dark theme')) : null);
}

/**
 * One gallery card: a preview in the theme's own colors, its name and kind, and its actions.
 * - The pick button chooses the theme; the actions sit beside it, not inside it.
 * - Match-system mode flags the light and dark picks instead of a single theme.
 */
function themeCard(app, theme, render, closeSettings) {
  const s = app.settings;
  const shown = app.theme();
  const sys = following(s);
  const flags = sys
    ? [theme.id === s.theme_light ? 'Light mode' : null, theme.id === s.theme_dark ? 'Dark mode' : null].filter(Boolean)
    : theme.id === shown.id ? ['In use'] : [];
  const kind = theme.kind === 'dark' ? 'Dark' : 'Light';
  const pick = h('button', {
    type: 'button',
    class: 'tcard-pick',
    dataset: { id: theme.id, fk: `pick:${theme.id}` },
    'aria-pressed': String(!sys && theme.id === shown.id),
    title: sys ? `Use ${theme.name} and stop matching the system` : `Use ${theme.name}`,
    onclick: () => {
      if (!sys && theme.id === shown.id) return;
      save(app, { theme: theme.id });
      render();
    },
  },
  themePreview(theme),
  flags.length ? h('span', { class: 'tcard-flags' }, flags.map((f) => h('span', { class: 'tcard-flag' }, f === 'In use' ? icon('check', 10) : null, f))) : null,
  h('span', { class: 'tcard-meta' },
    h('span', { class: 'tcard-name ellipsis' }, theme.name),
    h('span', { class: 'tcard-kind' }, icon(theme.kind === 'dark' ? 'moon' : 'sun', 11), kind)));

  const edit = (target, isNew) => editTheme(app, target, isNew, closeSettings, theme.id);
  const fk = (el, name) => tag(el, `${name}:${theme.id}`);
  const actions = theme.builtin
    ? [fk(button('Duplicate to customize', () => edit(cloneTheme(app.settings, theme), true), { class: 'sm ghost', icon: 'copy' }), 'dup')]
    : [
      fk(button('Edit', () => edit(theme, false), { class: 'sm ghost', icon: 'pencil' }), 'edit'),
      h('span', { class: 'grow' }),
      fk(iconButton('copy', `Duplicate ${theme.name}`, () => edit(cloneTheme(app.settings, theme), true), { size: 14 }), 'dup'),
      fk(iconButton('upload', `Export ${theme.name}`, async () => {
        if (await copyText(exportTheme(theme))) toast(`Copied ${theme.name} to the clipboard as JSON`, 'ok');
        else toast('Could not copy to the clipboard', 'error');
      }, { size: 14 }), 'export'),
      fk(iconButton('trash-2', `Delete ${theme.name}`, () => deleteTheme(app, theme, render), { size: 14, class: 'tcard-delete' }), 'delete'),
    ];
  const cls = ['tcard', theme.id === shown.id ? 'on' : '', flags.length ? 'flagged' : ''].filter(Boolean).join(' ');
  return h('div', { class: cls }, pick, h('div', { class: 'tcard-actions' }, actions));
}

/** Mark a control so the picker can give it the focus back after a redraw. */
function tag(el, key) {
  el.dataset.fk = key;
  return el;
}

/**
 * Close Settings, edit a theme in the side sheet, then reopen Settings on the gallery.
 * - Settings is closed so the app behind the sheet stays in view.
 * - fallbackId is the card that gets the focus when nothing was saved.
 */
async function editTheme(app, theme, isNew, closeSettings, fallbackId) {
  closeSettings(null);
  const saved = await openThemeEditor(app, theme, { isNew });
  openSettings(app, { section: 'appearance', focusTheme: saved ? saved.id : fallbackId });
}

async function deleteTheme(app, theme, render) {
  const ok = await confirmDialog({
    eyebrow: 'Themes',
    title: 'Delete theme',
    message: `Delete ${theme.name}? This cannot be undone. Anything using it goes back to the default theme.`,
    confirmLabel: 'Delete',
    danger: true,
  });
  if (!ok) return;
  const done = app.deleteCustomTheme(theme.id);
  done.then(() => window.dispatchEvent(new CustomEvent('gitsense:settings', { detail: { custom_themes: app.settings.custom_themes } })));
  await done;
  render();
  toast(`Deleted ${theme.name}`, 'ok');
}

/** Paste theme JSON, check it, and add it to the user's themes. It does not switch to it. */
function importTheme(app, render) {
  return openModal({
    eyebrow: 'Themes',
    title: 'Import theme',
    build: (close) => {
      const area = h('textarea', {
        class: 'input mono timport-text', id: 'timport-text', rows: '10', spellcheck: 'false',
        placeholder: '{ "name": "…", "kind": "dark", "colors": { "bg": "#1E1F22", … } }',
        'aria-describedby': 'timport-error',
      });
      const error = h('p', { class: 'modal-error hidden', id: 'timport-error', role: 'alert' });
      const fail = (message) => {
        error.textContent = message;
        error.classList.remove('hidden');
        area.setAttribute('aria-invalid', 'true');
        area.focus();
      };
      area.addEventListener('input', () => {
        error.classList.add('hidden');
        area.removeAttribute('aria-invalid');
      });
      let busy = false;
      const ok = button('Import', async () => {
        if (busy) return;
        const text = area.value.trim();
        if (!text) return fail('Paste the theme JSON first.');
        let theme;
        try {
          theme = sanitizeTheme(JSON.parse(text), app.settings);
        } catch (err) {
          return fail(err instanceof SyntaxError ? 'That is not valid JSON. Paste the whole text you were sent.' : err.message);
        }
        busy = true;
        ok.disabled = true;
        await app.saveCustomTheme(theme);
        window.dispatchEvent(new CustomEvent('gitsense:settings', { detail: { custom_themes: app.settings.custom_themes } }));
        close(theme);
        render();
        toast(`Imported ${theme.name}`, 'ok');
      }, { class: 'primary', icon: 'download' });
      return {
        body: [
          h('p', null, 'Paste a theme someone exported from GitSense. It is added to your themes; nothing changes until you pick it.'),
          h('div', { class: 'field' }, h('label', { for: 'timport-text' }, 'Theme JSON'), area),
          error,
        ],
        foot: [button('Cancel', () => close(null), { class: 'ghost' }), ok],
      };
    },
  });
}

function gitSection(app) {
  const current = app.settings.pull_mode || 'ff-only';
  const modes = h('div', { class: 'set-radios', role: 'radiogroup', 'aria-label': 'Default pull mode' },
    PULL_MODES.map((mode) => h('label', { class: 'set-radio' },
      h('input', {
        type: 'radio', name: 'set-pull-mode', value: mode.value, checked: mode.value === current,
        onchange: () => save(app, { pull_mode: mode.value }),
      }),
      h('span', { class: 'set-text' },
        h('span', { class: 'set-label' }, mode.label),
        h('span', { class: 'set-hint' }, mode.hint)))));
  const fetchEvery = numberInput(app, 'auto_fetch_minutes', { min: 0, max: 1440, unit: 'minutes' });
  return section('Git',
    row({ label: 'Pull', hint: 'What Pull does when your branch and the remote have both moved.', control: modes, stack: true }),
    toggle(app, 'fetch_prune', 'Prune on fetch', 'Remove remote branches that were deleted on the server.'),
    row({ label: 'Fetch in the background', hint: 'Every few minutes for open repositories. 0 turns it off.', ...fetchEvery }));
}

function githubSection(app) {
  const status = h('div', { class: 'set-gh', 'aria-live': 'polite' });
  const show = async (force) => {
    clear(status).append(h('div', { class: 'set-gh-line muted' }, icon('loader-circle', 14, 'spin'), 'Checking the GitHub CLI…'));
    const gh = await app.github(force);
    clear(status).append(ghStatus(app, gh, () => show(true)));
  };
  show(true);
  return section('GitHub',
    status,
    h('p', { class: 'set-note' }, icon('lock', 13),
      h('span', null, 'GitSense never stores a GitHub token. The GitHub CLI (', h('code', null, 'gh'), ') keeps your sign-in, and GitSense runs it for you.')));
}

function ghStatus(app, gh, recheck) {
  const again = button('Check again', recheck, { class: 'sm ghost', icon: 'refresh-cw' });
  if (gh.authenticated) {
    return h('div', { class: 'set-gh-line' },
      avatar(gh.user || '?'),
      h('div', { class: 'grow' },
        h('p', { class: 'set-label' }, 'Signed in as ', h('span', { class: 'selectable' }, gh.user)),
        h('p', { class: 'set-hint' }, 'Through the GitHub CLI. Pull requests and GitHub cloning are on.')),
      again);
  }
  if (gh.installed) {
    return h('div', { class: 'set-gh-line' },
      h('span', { class: 'set-gh-icon' }, icon('user', 15)),
      h('div', { class: 'grow' },
        h('p', { class: 'set-label' }, 'Not signed in'),
        h('p', { class: 'set-hint' }, 'Run ', h('code', { class: 'selectable' }, 'gh auth login'), ' in a terminal, then check again.')),
      iconButton('copy', 'Copy the command', async () => {
        if (await copyText('gh auth login')) toast('Command copied', 'ok');
      }),
      again);
  }
  return h('div', { class: 'set-gh-line' },
    h('span', { class: 'set-gh-icon' }, icon('download', 15)),
    h('div', { class: 'grow' },
      h('p', { class: 'set-label' }, 'The GitHub CLI is not installed'),
      h('p', { class: 'set-hint' }, 'Install gh, run ', h('code', { class: 'selectable' }, 'gh auth login'), ', then check again.')),
    button('Get gh', () => api.post('/api/open_url', { url: GH_INSTALL_URL }).catch(toastError), { class: 'sm', icon: 'external-link' }),
    again);
}

function filesSection(app) {
  const folder = textInput(app, 'clone_dir', { placeholder: app.state.home || 'Your home folder' });
  const browse = button('Browse…', async () => {
    const path = await app.pickFolder(folder.value.trim());
    if (path) {
      folder.value = path;
      save(app, { clone_dir: path });
    }
  }, { icon: 'folder-open' });
  const editor = textInput(app, 'editor', { placeholder: 'code', list: 'set-editor-list', class: 'input mono' });
  const presets = h('datalist', { id: 'set-editor-list' },
    ['code', 'cursor', 'zed', 'subl', 'idea', 'nvim'].map((cmd) => h('option', { value: cmd })));
  return section('Files',
    row({
      label: 'Clone into',
      hint: 'Default folder for new clones. Empty uses your home folder.',
      id: 'set-clone_dir',
      control: h('div', { class: 'field-row' }, folder, browse),
      stack: true,
    }),
    row({
      label: 'Editor command',
      hint: h('span', null, 'Opens files, for example ', h('code', null, 'code'), ', ', h('code', null, 'cursor'), ' or ', h('code', null, 'zed'), '. Empty uses the default app for each file.'),
      id: 'set-editor',
      control: h('div', { class: 'set-editor' }, editor, presets),
    }));
}

/**
 * Installed mods, each with a switch.
 * - Mods are installed from the command line; this only switches them on and off.
 * - A switch reloads the window, since a mod's styles, splash and icon are part of the page.
 */
function modsSection(app) {
  const mods = Array.isArray(app.state.mods) ? app.state.mods : [];
  const install = h('code', { class: 'selectable' }, 'gitsense --install-mod <folder or .zip>');
  if (!mods.length) {
    return section('Mods',
      h('p', { class: 'set-hint' }, 'Mods add themes, a loading splash and an app icon. None are installed. To install one, run ', install, '.'));
  }
  const parts = (mod) => [
    mod.themes.length ? `${mod.themes.length} ${mod.themes.length === 1 ? 'theme' : 'themes'}` : null,
    mod.has_splash ? 'a loading splash' : null,
    mod.has_icon ? 'an app icon' : null,
  ].filter(Boolean);
  const rows = mods.map((mod) => {
    const id = `set-mod-${mod.id}`;
    const box = h('input', {
      type: 'checkbox', id, checked: mod.enabled,
      onchange: () => {
        box.disabled = true;
        app.setMod(mod.id, box.checked).catch((err) => {
          box.disabled = false;
          box.checked = !box.checked;
          toastError(err);
        });
      },
    });
    const adds = parts(mod);
    return h('div', { class: 'set-row set-row-check set-mod', dataset: { mod: mod.id } },
      h('label', { class: 'check set-check', for: id }, box,
        h('span', { class: 'set-text' },
          h('span', { class: 'set-label' }, mod.name, mod.version ? h('span', { class: 'muted set-mod-version' }, ` ${mod.version}`) : null),
          mod.description ? h('span', { class: 'set-hint' }, mod.description) : null,
          adds.length ? h('span', { class: 'set-hint' }, `Adds ${listing(adds)}.`) : null)));
  });
  return section('Mods',
    ...rows,
    h('p', { class: 'set-note' }, icon('info', 13),
      h('span', null, 'Switching a mod reloads the window. Switching one on also selects its themes. To add more, run ', install, '.')));
}

/** "a", "a and b", "a, b and c". */
function listing(items) {
  return items.length < 2 ? items.join('') : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}

function aboutSection(app) {
  const mod = app.state.platform === 'darwin' ? 'Cmd' : 'Ctrl';
  const shortcuts = [
    [[mod, '1'], 'Repositories'],
    [[mod, '2–9'], 'Open tabs in order'],
    [[mod, 'W'], 'Close tab'],
    [[mod, ','], 'Settings'],
    [[mod, 'Enter'], 'Commit'],
    [[mod, 'F'], 'Filter commits'],
    [['Esc'], 'Close the diff'],
  ];
  const path = app.state.config_path || '';
  return section('About',
    h('div', { class: 'set-about' },
      h('p', { class: 'heading' }, `GitSense ${app.state.version || ''}`.trim()),
      h('p', { class: 'set-hint' }, 'A Git and GitHub client for your local repositories.'),
      path ? h('div', { class: 'set-path' },
        h('span', { class: 'field-label' }, 'Config file'),
        h('div', { class: 'set-path-row' },
          h('code', { class: 'selectable set-path-text', title: path }, path),
          iconButton('copy', 'Copy the path', async () => {
            if (await copyText(path)) toast('Path copied', 'ok');
          }))) : null),
    h('div', { class: 'set-keys' },
      h('span', { class: 'field-label' }, 'Keyboard shortcuts'),
      h('dl', { class: 'set-keys-list' },
        shortcuts.map(([keys, what]) => [
          h('dt', null, keys.map((k, i) => [i ? h('span', { class: 'set-plus' }, '+') : null, h('kbd', null, k)])),
          h('dd', null, what),
        ]))));
}
