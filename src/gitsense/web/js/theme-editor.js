// Theme editor: a sheet docked to the right edge that edits a custom theme while the app repaints behind it.

import { h, clear, icon, iconButton, button } from './util.js';
import { confirmDialog, closeMenu, toastError } from './ui.js';
import {
  THEME_GROUPS, THEME_TOKENS, TOKEN_KEYS, CONTRAST_PAIRS, BUILTIN_THEMES, DEFAULT_LIGHT, DEFAULT_DARK,
  allThemes, applyTheme, contrast,
} from './themes.js';

const root = () => document.getElementById('overlay-root');

let current = null; // the open editor: { focus }

export const isThemeEditorOpen = () => Boolean(current);

export function focusThemeEditor() {
  if (current) current.focus();
}

/** "#abc", "abc123" or "#AbC123" as "#RRGGBB", or null when it is not a color. */
export function normalizeHex(text) {
  let value = String(text || '').trim().replace(/^#/, '');
  if (/^[0-9a-f]{3}$/i.test(value)) value = value.replace(/./g, (c) => c + c);
  return /^[0-9a-f]{6}$/i.test(value) ? `#${value.toUpperCase()}` : null;
}

/** A theme's colors with any gaps filled from the default theme of the same kind. */
function fullColors(theme) {
  const base = BUILTIN_THEMES.find((t) => t.id === (theme.kind === 'dark' ? DEFAULT_DARK : DEFAULT_LIGHT));
  const colors = {};
  for (const key of TOKEN_KEYS) colors[key] = normalizeHex(theme.colors && theme.colors[key]) || base.colors[key];
  return colors;
}

// -- preview ----------------------------------------------------------------------

/**
 * A miniature of the app painted in a theme's own colors.
 * - Each color is set as --t-<token> on the root; css/themes.css draws with them.
 * - It never uses the active theme's colors.
 */
export function themePreview(theme) {
  const style = {};
  const colors = fullColors(theme);
  for (const key of TOKEN_KEYS) style[`--t-${key}`] = colors[key];
  const line = (cls, width) => h('span', { class: `tp-line ${cls}`, style: { width: `${width}%` } });
  const commit = (lane, cls, width, extra = null) => h('span', { class: `tp-row ${extra === 'sel' ? 'tp-sel' : ''}`.trim() },
    h('span', { class: `tp-dot tp-l${lane}`, style: { left: `${4 + lane * 7}px` } }),
    line(cls, width),
    extra === 'tag' ? h('span', { class: 'tp-tag' }) : null);
  return h('div', { class: 'tp', 'aria-hidden': 'true', style },
    h('span', { class: 'tp-band' }),
    h('span', { class: 'tp-top' }, h('span', { class: 'tp-tab on' }), h('span', { class: 'tp-tab' })),
    h('span', { class: 'tp-main' },
      h('span', { class: 'tp-side' },
        line('tp-title', 58),
        h('span', { class: 'tp-btn' }, h('span', { class: 'tp-btn-text' })),
        line('tp-muted', 72),
        h('span', { class: 'tp-side-sel' }, line('tp-strong', 62)),
        line('tp-muted', 50),
        line('tp-muted', 66)),
      h('span', { class: 'tp-graph' },
        h('span', { class: 'tp-rail tp-l0', style: { left: '6px' } }),
        h('span', { class: 'tp-rail tp-l1 tp-rail-top', style: { left: '13px' } }),
        h('span', { class: 'tp-rail tp-l2 tp-rail-bottom', style: { left: '20px' } }),
        commit(0, 'tp-strong', 58, 'tag'),
        commit(1, 'tp-text', 46),
        commit(0, 'tp-text', 54, 'sel'),
        commit(2, 'tp-text', 40),
        h('span', { class: 'tp-diff' },
          h('span', { class: 'tp-add' }, line('tp-ok', 48)),
          h('span', { class: 'tp-del' }, line('tp-danger', 36))))));
}

// -- editor -----------------------------------------------------------------------

/**
 * Open the editor for a custom theme.
 * - Resolves with the saved theme, or null when closed without saving.
 * - The app behind repaints with the draft on every valid change.
 * - Nothing is saved until Save.
 */
export function openThemeEditor(app, theme, { isNew = false } = {}) {
  if (current) {
    current.focus();
    return Promise.resolve(null);
  }
  closeMenu();
  return new Promise((resolve) => {
    const previous = document.activeElement;
    const kindOf = (t) => (t.kind === 'dark' ? 'dark' : 'light');
    const original = { name: theme.name, kind: kindOf(theme), colors: fullColors(theme) };
    const draft = { id: theme.id, name: theme.name, kind: original.kind, colors: { ...original.colors } };
    if (theme.based_on) draft.based_on = theme.based_on;
    const others = allThemes(app.settings).filter((t) => t.id !== theme.id);
    const rows = new Map(); // token key -> { input, swatch, reset, row, groupIndex }
    const groups = []; // { head, body, strip, keys }
    let alive = true;
    let saving = false;
    let asking = false;
    let frame = 0;

    // Live preview, at most once per animation frame. remember: false keeps it out of the next launch.
    const preview = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        if (alive && !saving) applyTheme(draft, { remember: false });
      });
    };

    // -- header and fields --------------------------------------------------------

    const titleEl = h('h2', { class: 'title te-title ellipsis', id: 'te-title' }, draft.name);
    const closeBtn = iconButton('x', 'Close', () => requestClose(), { size: 16 });

    const nameInput = h('input', {
      class: 'input', type: 'text', id: 'te-name', maxlength: '60', value: draft.name,
      spellcheck: 'false', autocomplete: 'off', autocapitalize: 'off', 'aria-describedby': 'te-name-error',
    });
    const nameError = h('p', { class: 'te-error hidden', id: 'te-name-error' });
    nameInput.addEventListener('input', () => {
      draft.name = nameInput.value;
      titleEl.textContent = draft.name.trim() || 'Untitled theme';
      showNameProblem(nameProblem());
      refresh();
    });

    const kindSeg = h('div', { class: 'seg te-seg', role: 'radiogroup', 'aria-label': 'Kind' });
    const paintKind = () => {
      for (const btn of kindSeg.children) {
        const on = btn.dataset.value === draft.kind;
        btn.classList.toggle('on', on);
        btn.setAttribute('aria-checked', String(on));
        btn.tabIndex = on ? 0 : -1;
      }
    };
    for (const [value, label, name] of [['light', 'Light', 'sun'], ['dark', 'Dark', 'moon']]) {
      kindSeg.append(h('button', {
        type: 'button', role: 'radio', dataset: { value },
        onclick: () => setKind(value),
      }, icon(name, 13), label));
    }
    kindSeg.addEventListener('keydown', (ev) => {
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(ev.key)) return;
      ev.preventDefault();
      const next = draft.kind === 'light' ? 'dark' : 'light';
      setKind(next);
      kindSeg.querySelector(`[data-value="${next}"]`).focus();
    });
    const setKind = (value) => {
      draft.kind = value;
      paintKind();
      refresh();
      preview();
    };
    paintKind();

    // -- tools: copy colors from another theme, reset all --------------------------

    const optionFor = (t) => h('option', { value: t.id }, t.name);
    const copyFrom = h('select', { class: 'input te-copy', 'aria-label': 'Copy colors from another theme' },
      h('option', { value: '' }, 'Copy colors from…'),
      h('optgroup', { label: 'Built-in' }, others.filter((t) => t.builtin).map(optionFor)),
      others.some((t) => !t.builtin) ? h('optgroup', { label: 'Your themes' }, others.filter((t) => !t.builtin).map(optionFor)) : null);
    copyFrom.addEventListener('change', () => {
      const source = others.find((t) => t.id === copyFrom.value);
      copyFrom.value = '';
      if (!source) return;
      const colors = fullColors(source);
      for (const key of TOKEN_KEYS) setColor(key, colors[key], { quiet: true });
      draft.kind = kindOf(source);
      paintKind();
      refresh();
      preview();
    });
    const resetAll = button('Reset all', () => {
      for (const key of TOKEN_KEYS) setColor(key, original.colors[key], { quiet: true });
      draft.kind = original.kind;
      paintKind();
      refresh();
      preview();
    }, { class: 'sm ghost', icon: 'rotate-ccw', title: 'Undo every color change since the editor opened' });

    // -- contrast warnings --------------------------------------------------------

    const warnings = h('div', { class: 'te-warn hidden', 'aria-live': 'polite' });
    const renderWarnings = () => {
      // Pairs sharing a description (the band has two ends) show the worse one only.
      const worst = new Map();
      for (const [fg, bg, min, what] of CONTRAST_PAIRS) {
        const ratio = contrast(draft.colors[fg], draft.colors[bg]);
        if (ratio >= min) continue;
        const seen = worst.get(what);
        if (!seen || ratio < seen.ratio) worst.set(what, { ratio, min, fg });
      }
      clear(warnings).classList.toggle('hidden', worst.size === 0);
      if (!worst.size) return;
      warnings.append(h('ul', { class: 'te-warn-list' }, [...worst].map(([what, w]) => h('li', null,
        icon('triangle-alert', 13),
        h('button', {
          type: 'button', class: 'te-warn-item', title: 'Go to this color',
          onclick: () => focusToken(w.fg),
        }, `${what} is hard to read (${Math.floor(w.ratio * 10) / 10}:1, aim for ${w.min}:1)`)))));
    };

    // -- color rows ---------------------------------------------------------------

    const colorRow = (token, groupIndex) => {
      const id = `te-c-${token.key}`;
      const hintId = token.hint ? `${id}-hint` : null;
      const input = h('input', {
        class: 'input mono te-hex', type: 'text', id, value: draft.colors[token.key], maxlength: '9',
        spellcheck: 'false', autocomplete: 'off', autocapitalize: 'off', 'aria-describedby': hintId,
      });
      const swatch = h('input', {
        class: 'te-swatch', type: 'color', value: draft.colors[token.key].toLowerCase(),
        'aria-label': `${token.label}: pick a color`,
      });
      const reset = iconButton('rotate-ccw', `Reset ${token.label}`, () => {
        setColor(token.key, original.colors[token.key]);
        input.focus();
      }, { class: 'te-reset', size: 13 });
      input.addEventListener('input', () => {
        const value = normalizeHex(input.value);
        markInvalid(token.key, !value);
        if (value) setColor(token.key, value, { from: 'text' });
        else refresh();
      });
      input.addEventListener('change', () => {
        const value = normalizeHex(input.value);
        if (value) input.value = value;
      });
      swatch.addEventListener('input', () => setColor(token.key, swatch.value.toUpperCase(), { from: 'swatch' }));
      const row = h('div', { class: 'te-row' },
        h('div', { class: 'te-row-text' },
          h('label', { class: 'te-label', for: id }, token.label),
          token.hint ? h('span', { class: 'te-hint', id: hintId }, token.hint) : null),
        h('div', { class: 'te-row-ctl' }, swatch, input, reset));
      rows.set(token.key, { input, swatch, reset, row, groupIndex, invalid: false });
      return row;
    };

    const groupsEl = h('div', { class: 'te-groups' });
    THEME_GROUPS.forEach((group, index) => {
      const tokens = THEME_TOKENS.filter((t) => t.group === group);
      const bodyId = `te-g-${index}`;
      const strip = h('span', { class: 'te-strip', 'aria-hidden': 'true' }, tokens.map(() => h('span')));
      const changed = h('span', { class: 'te-changed hidden' });
      const body = h('div', { class: 'te-rows', id: bodyId }, tokens.map((t) => colorRow(t, index)));
      const head = h('button', {
        type: 'button', class: 'te-group-head', 'aria-expanded': 'true', 'aria-controls': bodyId,
        onclick: () => setOpen(index, head.getAttribute('aria-expanded') !== 'true'),
      }, icon('chevron-down', 14), h('span', { class: 'eyebrow' }, group), changed, h('span', { class: 'grow' }), strip);
      groups.push({ head, body, strip, changed, keys: tokens.map((t) => t.key) });
      groupsEl.append(h('section', { class: 'te-group' }, head, body));
    });

    function setOpen(index, open) {
      const g = groups[index];
      g.head.setAttribute('aria-expanded', String(open));
      g.body.classList.toggle('hidden', !open);
    }

    function focusToken(key) {
      const r = rows.get(key);
      setOpen(r.groupIndex, true);
      r.row.scrollIntoView({ block: 'center' });
      r.input.focus();
      r.input.select();
    }

    function markInvalid(key, invalid) {
      const r = rows.get(key);
      r.invalid = invalid;
      r.input.classList.toggle('invalid', invalid);
      if (invalid) r.input.setAttribute('aria-invalid', 'true');
      else r.input.removeAttribute('aria-invalid');
    }

    /**
     * Set one color in the draft and the fields that show it.
     * - from: the field the value came from, which is left as the user typed it
     * - quiet: skip the summary refresh and preview; the caller does them once
     */
    function setColor(key, value, { from = null, quiet = false } = {}) {
      const r = rows.get(key);
      draft.colors[key] = value;
      if (from !== 'text') {
        r.input.value = value;
        markInvalid(key, false);
      }
      if (from !== 'swatch') r.swatch.value = value.toLowerCase();
      if (!quiet) {
        refresh();
        preview();
      }
    }

    // -- summary state ------------------------------------------------------------

    const colorsChanged = () => TOKEN_KEYS.some((k) => draft.colors[k] !== original.colors[k]);
    const anyInvalid = () => [...rows.values()].some((r) => r.invalid);
    const isDirty = () => draft.name.trim() !== original.name || draft.kind !== original.kind || colorsChanged() || anyInvalid();

    function refresh() {
      for (const [key, r] of rows) r.reset.classList.toggle('te-off', draft.colors[key] === original.colors[key]);
      for (const g of groups) {
        g.keys.forEach((key, i) => { g.strip.children[i].style.background = draft.colors[key]; });
        const n = g.keys.filter((k) => draft.colors[k] !== original.colors[k]).length;
        g.changed.textContent = n ? `${n} changed` : '';
        g.changed.classList.toggle('hidden', !n);
      }
      resetAll.disabled = !colorsChanged() && draft.kind === original.kind;
      renderWarnings();
      if (!anyInvalid() && !nameProblem()) footError.classList.add('hidden');
    }

    function nameProblem() {
      const name = draft.name.trim();
      if (!name) return 'Give the theme a name.';
      const clash = others.find((t) => t.name.toLowerCase() === name.toLowerCase());
      return clash ? `Another theme is already called ${clash.name}.` : null;
    }

    function showNameProblem(problem) {
      nameError.textContent = problem || '';
      nameError.classList.toggle('hidden', !problem);
      nameInput.classList.toggle('invalid', Boolean(problem));
      if (problem) nameInput.setAttribute('aria-invalid', 'true');
      else nameInput.removeAttribute('aria-invalid');
    }

    // -- footer -------------------------------------------------------------------

    const useBox = h('input', { type: 'checkbox', id: 'te-use', checked: isNew || app.theme().id === theme.id });
    const footError = h('p', { class: 'te-error te-foot-error hidden', role: 'alert' });
    const cancelBtn = button('Cancel', () => requestClose(), { class: 'ghost' });
    const saveBtn = button('Save', () => save(), { class: 'primary', title: 'Save (Cmd or Ctrl+Enter)' });

    async function save() {
      if (saving || asking) return;
      const nameIssue = nameProblem();
      showNameProblem(nameIssue);
      const bad = [...rows].find(([, r]) => r.invalid);
      if (nameIssue || bad) {
        footError.textContent = nameIssue ? 'Fix the name to save.' : 'Fix the colors marked in red to save.';
        footError.classList.remove('hidden');
        if (nameIssue) nameInput.focus();
        else focusToken(bad[0]);
        return;
      }
      saving = true;
      cancelAnimationFrame(frame);
      frame = 0;
      saveBtn.disabled = true;
      cancelBtn.disabled = true;
      saveBtn.lastChild.textContent = 'Saving…';
      const saved = { id: draft.id, name: draft.name.trim(), kind: draft.kind, colors: { ...draft.colors } };
      if (draft.based_on) saved.based_on = draft.based_on;
      try {
        await app.saveCustomTheme(saved);
        const changes = { custom_themes: app.settings.custom_themes };
        if (useBox.checked && app.theme().id !== saved.id) {
          await app.setTheme(saved.id);
          changes.theme = saved.id;
        } else {
          app.applyTheme();
        }
        window.dispatchEvent(new CustomEvent('gitsense:settings', { detail: changes }));
      } catch (err) {
        toastError(err);
        saving = false;
        saveBtn.disabled = false;
        cancelBtn.disabled = false;
        saveBtn.lastChild.textContent = 'Save';
        return;
      }
      finish(saved);
    }

    async function requestClose() {
      if (saving || asking) return;
      if (isDirty()) {
        asking = true;
        const discard = await confirmDialog({
          eyebrow: 'Themes',
          title: 'Unsaved changes',
          message: 'Discard changes to this theme?',
          confirmLabel: 'Discard',
          danger: true,
        });
        asking = false;
        if (!discard || !alive) return;
      }
      finish(null);
    }

    function finish(result) {
      alive = false;
      cancelAnimationFrame(frame);
      sheet.remove();
      document.removeEventListener('keydown', onDocKey);
      scheme.removeEventListener('change', onScheme);
      current = null;
      if (!result) app.applyTheme();
      if (previous && previous.isConnected && previous.focus) previous.focus();
      resolve(result);
    }

    // -- the sheet ----------------------------------------------------------------

    const sheet = h('aside', {
      class: 'tsheet', role: 'dialog', 'aria-modal': 'false', 'aria-labelledby': 'te-title', tabindex: '-1',
    },
    h('div', { class: 'tsheet-head' },
      h('div', { class: 'grow' },
        h('p', { class: 'eyebrow' }, isNew ? 'New theme' : 'Edit theme'),
        titleEl),
      closeBtn),
    warnings,
    h('div', { class: 'tsheet-body' },
      h('div', { class: 'field' },
        h('label', { for: 'te-name' }, 'Name'),
        nameInput,
        nameError),
      h('div', { class: 'field' },
        h('span', { class: 'field-label', id: 'te-kind-label' }, 'Kind'),
        kindSeg,
        h('span', { class: 'hint' }, 'Picks which dark-mode details apply. It does not change any color.')),
      h('div', { class: 'te-tools' }, copyFrom, resetAll),
      groupsEl),
    h('div', { class: 'tsheet-foot' },
      h('label', { class: 'check te-use', for: 'te-use' }, useBox, 'Use this theme after saving'),
      footError,
      h('div', { class: 'tsheet-buttons' }, cancelBtn, saveBtn)));
    kindSeg.setAttribute('aria-labelledby', 'te-kind-label');

    // The sheet keeps the look that was on screen when it opened, so a bad draft cannot make it unreadable.
    const computed = getComputedStyle(document.documentElement);
    for (const key of [...TOKEN_KEYS, 'shadow', 'shadow-pop']) {
      const value = computed.getPropertyValue(`--${key}`).trim();
      if (value) sheet.style.setProperty(`--${key}`, value);
    }
    sheet.style.colorScheme = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
    sheet.dataset.kind = sheet.style.colorScheme;

    // Keys inside the sheet stay in the sheet, except the tab shortcuts.
    sheet.addEventListener('keydown', (ev) => {
      const mod = ev.metaKey || ev.ctrlKey;
      if (ev.key === 'Escape') {
        ev.preventDefault();
        ev.stopPropagation();
        requestClose();
        return;
      }
      if (mod && ev.key === 'Enter') {
        ev.preventDefault();
        ev.stopPropagation();
        save();
        return;
      }
      if (mod && /^[1-9w]$/.test(ev.key)) return;
      ev.stopPropagation();
    });
    // Escape with nothing focused, for example after a click on the app behind.
    const onDocKey = (ev) => {
      if (ev.key !== 'Escape' || document.querySelector('.modal-backdrop')) return;
      if (document.activeElement && document.activeElement !== document.body) return;
      requestClose();
    };
    document.addEventListener('keydown', onDocKey);
    // The app repaints when the OS switches light and dark; paint the draft again after it.
    const scheme = window.matchMedia('(prefers-color-scheme: dark)');
    const onScheme = () => preview();
    scheme.addEventListener('change', onScheme);

    current = {
      focus: () => {
        if (!sheet.contains(document.activeElement)) nameInput.focus();
      },
    };
    root().append(sheet);
    refresh();
    applyTheme(draft, { remember: false });
    nameInput.focus();
    if (isNew) nameInput.select();
  });
}
