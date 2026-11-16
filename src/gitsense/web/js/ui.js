// Overlays: toasts, context menus, modal dialogs, and the status bar activity line.

import { h, clear, icon, button } from './util.js';

const root = () => document.getElementById('overlay-root');

// -- toasts -------------------------------------------------------------------

let toastStack = null;

/**
 * Show a short message in the corner.
 * - kind: "info", "ok" or "error"
 * - errors stay until dismissed; others fade after a few seconds
 */
export function toast(message, kind = 'info', detail = '') {
  if (!toastStack) {
    toastStack = h('div', { class: 'toast-stack' });
    root().append(toastStack);
  }
  const names = { ok: 'circle-check', error: 'circle-alert', info: 'info' };
  const el = h('div', { class: `toast ${kind}`, role: kind === 'error' ? 'alert' : 'status' },
    icon(names[kind] || 'info', 16),
    h('div', { class: 'grow' },
      h('div', { class: 'toast-msg' }, message),
      detail && detail.trim() !== message.trim() ? h('div', { class: 'toast-detail' }, detail) : null),
    h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Dismiss', onclick: () => el.remove() }, icon('x', 13)),
  );
  toastStack.append(el);
  while (toastStack.children.length > 4) toastStack.firstChild.remove();
  if (kind !== 'error') setTimeout(() => el.remove(), 4200);
  return el;
}

export function toastError(err) {
  toast(err.message || String(err), 'error', err.detail || '');
}

// -- status bar ---------------------------------------------------------------

let activityCount = 0;

/** Show a spinner and label in the status bar while a promise runs. */
export async function withActivity(label, promise) {
  const el = document.getElementById('status-activity');
  activityCount += 1;
  el.classList.remove('error');
  clear(el).append(icon('loader-circle', 12, 'spin'), h('span', null, label));
  try {
    const result = await promise;
    activityCount -= 1;
    if (activityCount === 0) clear(el).append(icon('check', 12), h('span', null, `${label.replace(/…$/, '')}: done`));
    return result;
  } catch (err) {
    activityCount -= 1;
    if (err && err.cancelled) {
      if (activityCount === 0) clear(el).append(icon('x', 12), h('span', null, `${label.replace(/…$/, '')}: cancelled`));
    } else {
      el.classList.add('error');
      clear(el).append(icon('circle-alert', 12), h('span', null, `${label.replace(/…$/, '')}: failed`));
    }
    throw err;
  }
}

export function setStatus(text) {
  if (activityCount > 0) return;
  const el = document.getElementById('status-activity');
  el.classList.remove('error');
  clear(el).append(h('span', null, text));
}

// -- context menus ------------------------------------------------------------

let openMenu = null;

export function closeMenu() {
  if (openMenu) {
    openMenu.el.remove();
    document.removeEventListener('pointerdown', openMenu.onDown, true);
    document.removeEventListener('keydown', openMenu.onKey, true);
    window.removeEventListener('blur', closeMenu);
    openMenu = null;
  }
}

/**
 * Open a menu at a point or under an element.
 * items: { label, icon, onClick, danger, disabled, hint } | { sep: true } | { heading: 'text' }
 * Falsy items are skipped.
 */
export function showMenu(anchor, items) {
  closeMenu();
  const el = h('div', { class: 'menu', role: 'menu' });
  let lastWasSep = true;
  const list = items.filter(Boolean);
  list.forEach((item, i) => {
    if (item.sep) {
      if (!lastWasSep && i < list.length - 1) el.append(h('div', { class: 'menu-sep' }));
      lastWasSep = true;
      return;
    }
    if (item.heading) {
      el.append(h('div', { class: 'menu-label' }, item.heading));
      return;
    }
    lastWasSep = false;
    el.append(h('button', {
      class: `menu-item ${item.danger ? 'danger' : ''}`,
      type: 'button',
      role: 'menuitem',
      disabled: item.disabled,
      title: item.title,
      onclick: () => { closeMenu(); item.onClick && item.onClick(); },
    }, icon(item.icon || 'circle-dot', 14), h('span', { class: 'ellipsis' }, item.label),
    item.hint ? h('span', { class: 'menu-hint' }, item.hint) : null));
  });
  if (el.lastChild && el.lastChild.classList.contains('menu-sep')) el.lastChild.remove();
  root().append(el);

  let x; let y;
  if (anchor instanceof Element) {
    const r = anchor.getBoundingClientRect();
    x = r.left; y = r.bottom + 2;
  } else {
    x = anchor.x; y = anchor.y;
  }
  const rect = el.getBoundingClientRect();
  if (x + rect.width > window.innerWidth - 8) x = Math.max(8, window.innerWidth - rect.width - 8);
  if (y + rect.height > window.innerHeight - 8) y = Math.max(8, window.innerHeight - rect.height - 8);
  el.style.left = `${x}px`;
  el.style.top = `${y}px`;

  const onDown = (ev) => { if (!el.contains(ev.target)) closeMenu(); };
  const onKey = (ev) => {
    const buttons = [...el.querySelectorAll('.menu-item:not([disabled])')];
    const at = buttons.indexOf(document.activeElement);
    if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); closeMenu(); }
    else if (ev.key === 'ArrowDown') { ev.preventDefault(); (buttons[at + 1] || buttons[0])?.focus(); }
    else if (ev.key === 'ArrowUp') { ev.preventDefault(); (buttons[at - 1] || buttons[buttons.length - 1])?.focus(); }
  };
  openMenu = { el, onDown, onKey };
  // Registered on the next tick so the click that opened the menu does not close it.
  setTimeout(() => {
    if (!openMenu || openMenu.el !== el) return;
    document.addEventListener('pointerdown', onDown, true);
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('blur', closeMenu);
  }, 0);
  return el;
}

// -- modals -------------------------------------------------------------------

/**
 * Open a modal dialog.
 * - build(close) returns { body, foot } nodes or arrays
 * - returns a promise that resolves with the value passed to close()
 * - Escape and a backdrop click close with null
 * - While the backdrop has data-busy="1" (a request is running) it cannot be dismissed
 * - Enter presses the primary button, except inside an element marked data-enter-own
 */
export function openModal({ eyebrow, title, wide = false, build, onOpen }) {
  closeMenu();
  return new Promise((resolve) => {
    const previous = document.activeElement;
    const isBusy = () => backdrop.dataset.busy === '1';
    const close = (value = null) => {
      backdrop.remove();
      document.removeEventListener('keydown', onKey, true);
      if (previous && previous.focus) previous.focus();
      resolve(value);
    };
    const parts = build(close);
    const body = h('div', { class: 'modal-body' }, parts.body);
    const foot = h('div', { class: 'modal-foot' }, parts.foot);
    const modal = h('div', { class: `modal ${wide ? 'wide' : ''}`, role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
      h('div', { class: 'modal-head' },
        h('div', { class: 'grow' },
          eyebrow ? h('p', { class: 'eyebrow' }, eyebrow) : null,
          h('h2', { class: 'title' }, title)),
        h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Close', onclick: () => { if (!isBusy()) close(null); } }, icon('x', 16))),
      body, foot);
    const backdrop = h('div', { class: 'modal-backdrop' }, modal);
    backdrop.addEventListener('pointerdown', (ev) => { if (ev.target === backdrop && !isBusy()) close(null); });
    const onKey = (ev) => {
      // Only the topmost dialog reacts to keys.
      const open = root().querySelectorAll('.modal-backdrop');
      if (!backdrop.isConnected || backdrop !== open[open.length - 1]) return;
      if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); if (!isBusy()) close(null); }
      if (ev.key === 'Enter' && !isBusy() && !ev.target.closest('[data-enter-own]') && (ev.metaKey || ev.ctrlKey || (ev.target.tagName !== 'TEXTAREA' && ev.target.tagName !== 'BUTTON'))) {
        const primary = foot.querySelector('.btn.primary:not([disabled])');
        if (primary) { ev.preventDefault(); primary.click(); }
      }
    };
    document.addEventListener('keydown', onKey, true);
    root().append(backdrop);
    // A destructive dialog starts on Cancel, so a held Enter or Space cannot confirm it.
    const focusTarget = modal.querySelector('[autofocus], .modal-body input, .modal-body textarea, .modal-body select')
      || foot.querySelector('.btn.primary:not(.danger)') || foot.querySelector('.btn') || modal.querySelector('button');
    if (focusTarget) {
      focusTarget.focus();
      if (focusTarget.select && focusTarget.tagName === 'INPUT' && focusTarget.type === 'text') focusTarget.select();
    }
    if (onOpen) onOpen({ modal, body, foot, close });
  });
}

/**
 * Ask a yes/no question. Resolves true, or false when cancelled.
 * - message: string or node
 * - checkbox: { label, checked } adds an option; then the result is { checked } or false
 */
export function confirmDialog({ eyebrow = 'Confirm', title, message, confirmLabel = 'OK', danger = false, checkbox = null, detail = '' }) {
  return openModal({
    eyebrow,
    title,
    build: (close) => {
      const box = checkbox ? h('input', { type: 'checkbox', checked: Boolean(checkbox.checked) }) : null;
      return {
        body: [
          typeof message === 'string' ? h('p', { class: 'selectable' }, message) : message,
          detail ? h('pre', { class: 'pre' }, detail) : null,
          box ? h('label', { class: 'check', style: { marginBottom: '10px' } }, box, checkbox.label) : null,
        ],
        foot: [
          button('Cancel', () => close(false), { class: 'ghost' }),
          button(confirmLabel, () => close(box ? { checked: box.checked } : true), { class: danger ? 'primary danger' : 'primary' }),
        ],
      };
    },
  }).then((value) => value || false);
}

/**
 * Ask for one or more values. Resolves an object keyed by field name, or null.
 * fields: { name, label, value, placeholder, type, options, hint, required, rows }
 * - type: "text" (default), "textarea", "select", "checkbox"
 * - options for select: [{ value, label }]
 * - validate(values) may return an error string
 * - submit(values) may be async; a thrown error is shown and the dialog stays open
 */
export function formDialog({ eyebrow, title, intro, fields, confirmLabel = 'OK', danger = false, wide = false, validate, submit }) {
  return openModal({
    eyebrow,
    title,
    wide,
    build: (close) => {
      const inputs = {};
      const error = h('p', { class: 'modal-error hidden' });
      const rows = fields.filter(Boolean).map((f) => {
        let input;
        if (f.type === 'textarea') {
          input = h('textarea', { class: 'input', rows: f.rows || 4, placeholder: f.placeholder, spellcheck: 'true' });
          input.value = f.value || '';
        } else if (f.type === 'select') {
          input = h('select', { class: 'input' }, f.options.map((o) => h('option', { value: o.value, selected: o.value === f.value }, o.label)));
        } else if (f.type === 'checkbox') {
          input = h('input', { type: 'checkbox', checked: Boolean(f.value) });
          inputs[f.name] = input;
          return h('label', { class: 'check', style: { marginBottom: '12px' } }, input, f.label);
        } else {
          input = h('input', {
            class: 'input', type: 'text', placeholder: f.placeholder,
            spellcheck: 'false', autocomplete: 'off', autocapitalize: 'off',
          });
          input.value = f.value || '';
        }
        inputs[f.name] = input;
        return h('div', { class: 'field' },
          h('label', null, f.label),
          f.trailing ? h('div', { class: 'field-row' }, input, f.trailing(input)) : input,
          f.hint ? h('span', { class: 'hint' }, f.hint) : null);
      });
      // A field with keepSpaces is sent as typed: a folder name may end in a space.
      const untrimmed = new Set(fields.filter((f) => f && f.keepSpaces).map((f) => f.name));
      const read = () => {
        const out = {};
        for (const [name, input] of Object.entries(inputs)) {
          out[name] = input.type === 'checkbox' ? input.checked : untrimmed.has(name) ? input.value : input.value.trim();
        }
        return out;
      };
      const ok = button(confirmLabel, async () => {
        const values = read();
        let problem = null;
        for (const f of fields.filter(Boolean)) {
          const value = values[f.name];
          if (f.required && !(typeof value === 'string' ? value.trim() : value)) { problem = `${f.label} is required`; break; }
        }
        if (!problem && validate) problem = validate(values);
        if (problem) {
          error.textContent = problem;
          error.classList.remove('hidden');
          return;
        }
        if (!submit) { close(values); return; }
        const backdrop = ok.closest('.modal-backdrop');
        ok.disabled = true;
        cancel.disabled = true;
        if (backdrop) backdrop.dataset.busy = '1';
        const labelNode = ok.lastChild;
        const original = labelNode.textContent;
        labelNode.textContent = 'Working…';
        try {
          const result = await submit(values);
          close(result === undefined ? values : result);
        } catch (err) {
          error.textContent = [err.message, err.detail && err.detail !== err.message ? err.detail : ''].filter(Boolean).join('\n');
          error.classList.remove('hidden');
          ok.disabled = false;
          cancel.disabled = false;
          if (backdrop) backdrop.dataset.busy = '';
          labelNode.textContent = original;
        }
      }, { class: danger ? 'primary danger' : 'primary' });
      const cancel = button('Cancel', () => close(null), { class: 'ghost' });
      return {
        body: [intro ? (typeof intro === 'string' ? h('p', null, intro) : intro) : null, rows, error],
        foot: [cancel, ok],
      };
    },
  });
}
