// Small DOM and formatting helpers shared by every module.

import { icon } from './icons.js';

/**
 * Create an element.
 * - attrs: class, dataset, style (object), on* handlers, or plain attributes
 * - children: nodes, strings, arrays; null and false are skipped
 */
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === 'class') el.className = value;
      else if (key === 'dataset') Object.assign(el.dataset, value);
      else if (key === 'style' && typeof value === 'object') {
        for (const [prop, v] of Object.entries(value)) {
          if (prop.startsWith('--')) el.style.setProperty(prop, v);
          else el.style[prop] = v;
        }
      }
      else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2).toLowerCase(), value);
      else if (key === 'value') el.value = value;
      else if (key === 'checked' || key === 'disabled' || key === 'selected') el[key] = Boolean(value);
      else el.setAttribute(key, value === true ? '' : value);
    }
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function clear(el) {
  el.replaceChildren();
  return el;
}

export { icon };

/** An icon button with a tooltip. */
export function iconButton(name, title, onClick, opts = {}) {
  return h('button', {
    class: `icon-btn ${opts.class || ''}`.trim(),
    title,
    'aria-label': title,
    type: 'button',
    disabled: opts.disabled,
    onclick: (ev) => { ev.stopPropagation(); onClick(ev); },
  }, icon(name, opts.size || 15));
}

export function button(label, onClick, opts = {}) {
  return h('button', {
    class: `btn ${opts.class || ''}`.trim(),
    type: 'button',
    title: opts.title,
    disabled: opts.disabled,
    onclick: onClick,
  }, opts.icon ? icon(opts.icon, 14) : null, label);
}

export const shortSha = (sha) => (sha || '').slice(0, 7);

export function basename(path) {
  const trimmed = path.replace(/\/+$/, '');
  const i = trimmed.lastIndexOf('/');
  return i < 0 ? trimmed : trimmed.slice(i + 1);
}

export function dirname(path) {
  const trimmed = path.replace(/\/+$/, '');
  const i = trimmed.lastIndexOf('/');
  return i < 0 ? '' : trimmed.slice(0, i + 1);
}

export const plural = (n, word, many) => `${n} ${n === 1 ? word : (many || word + 's')}`;

/** "3 minutes ago" style time from a unix timestamp in seconds. */
export function relTime(ts) {
  if (!ts) return '';
  const diff = Date.now() / 1000 - ts;
  if (diff < 45) return 'just now';
  const steps = [
    [60, 'minute'], [3600, 'hour'], [86400, 'day'], [604800, 'week'], [2629800, 'month'], [31557600, 'year'],
  ];
  let label = 'just now';
  for (let i = steps.length - 1; i >= 0; i--) {
    const [size, name] = steps[i];
    if (diff >= size) {
      const n = Math.floor(diff / size);
      label = `${n} ${name}${n === 1 ? '' : 's'} ago`;
      break;
    }
  }
  return label;
}

const DATE_FMT = new Intl.DateTimeFormat(undefined, {
  year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
});
const DAY_FMT = new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'short', day: 'numeric' });

export const fullDate = (ts) => (ts ? DATE_FMT.format(new Date(ts * 1000)) : '');

/** Compact date for table columns: time today, otherwise the day. */
export function shortDate(ts) {
  if (!ts) return '';
  const diff = Date.now() / 1000 - ts;
  if (diff < 86400 * 6) return relTime(ts);
  return DAY_FMT.format(new Date(ts * 1000));
}

export function initials(name) {
  const parts = (name || '?').trim().split(/[\s._-]+/).filter(Boolean);
  if (!parts.length) return '?';
  const first = parts[0][0] || '?';
  const last = parts.length > 1 ? parts[parts.length - 1][0] : '';
  return (first + last).toUpperCase();
}

const AVATAR_TONES = 5; // .av-0 to .av-4 in base.css, colored from the theme

/** A round initials avatar. No network requests are made for pictures. */
export function avatar(name, cls = '') {
  let hash = 0;
  for (const ch of name || '') hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return h('span', { class: `avatar av-${hash % AVATAR_TONES} ${cls}`.trim(), title: name }, initials(name));
}

export function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = h('textarea', { style: { position: 'fixed', opacity: '0' } });
    area.value = text;
    document.body.append(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    area.remove();
    return ok;
  }
}

/** Status letter box for a changed file. */
export function statusLetter(status) {
  const letter = status === '?' ? 'U' : status;
  const cls = status === '?' ? 's-untracked' : `s-${status}`;
  const names = { M: 'Modified', A: 'Added', D: 'Deleted', R: 'Renamed', C: 'Copied', T: 'Type changed', U: 'Conflicted', '?': 'Untracked' };
  return h('span', { class: `status-letter ${cls}`, title: names[status] || status }, status === '?' ? '+' : letter);
}

/** Drag handle that resizes a neighbouring panel. onDone gets the final width. */
export function splitter(getWidth, setWidth, { min = 160, max = 720, invert = false, onDone } = {}) {
  const el = h('div', { class: 'splitter', role: 'separator', 'aria-orientation': 'vertical' });
  el.addEventListener('pointerdown', (ev) => {
    ev.preventDefault();
    const startX = ev.clientX;
    const startW = getWidth();
    el.classList.add('dragging');
    el.setPointerCapture(ev.pointerId);
    const move = (e) => {
      const delta = (e.clientX - startX) * (invert ? -1 : 1);
      setWidth(Math.max(min, Math.min(max, startW + delta)));
    };
    const up = () => {
      el.classList.remove('dragging');
      el.removeEventListener('pointermove', move);
      el.removeEventListener('pointerup', up);
      if (onDone) onDone(getWidth());
    };
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerup', up);
  });
  return el;
}
