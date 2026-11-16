// Dashboard: the repositories GitSense knows, with live status and bulk actions.
// - A rail on the left switches between "All repositories" and the user's groups.
// - The table, totals and bulk actions cover the list on screen only.

import { api } from './api.js';
import { h, clear, icon, iconButton, button, relTime, fullDate, plural, debounce, copyText, shortSha, dirname } from './util.js';
import { toast, toastError, withActivity, showMenu, openModal, confirmDialog, formDialog, setStatus } from './ui.js';

const SUMMARY_CONCURRENCY = 6;
const FETCH_CONCURRENCY = 3;
const REFRESH_MS = 45000;
const PREFS_KEY = 'gitsense.dashboard';
const DRAG_TYPE = 'application/x-gitsense-repo';

const OPERATIONS = {
  merge: 'Merging',
  rebase: 'Rebasing',
  'cherry-pick': 'Cherry-picking',
  revert: 'Reverting',
  bisect: 'Bisecting',
};

const changeCount = (s) => s.staged + s.unstaged + s.conflicted;
const hasChanges = (s) => Boolean(s && !s.missing && (changeCount(s) > 0 || s.operation));

const FILTERS = [
  { id: 'all', label: 'All', test: () => true },
  { id: 'changes', label: 'Changes', test: hasChanges },
  { id: 'behind', label: 'Behind', test: (s) => Boolean(s && !s.missing && s.behind > 0) },
  { id: 'ahead', label: 'Ahead', test: (s) => Boolean(s && !s.missing && s.ahead > 0) },
];

const COLUMNS = [
  { id: 'star', label: '' },
  { id: 'name', label: 'Repository', sort: 'name', dir: 1 },
  { id: 'branch', label: 'Branch', sort: 'branch', dir: 1 },
  { id: 'sync', label: 'Sync' },
  { id: 'changes', label: 'Working tree', sort: 'changes', dir: -1 },
  { id: 'commit', label: 'Last commit', sort: 'date', dir: -1 },
];

// -- helpers ------------------------------------------------------------------

/** Run worker over items with at most `limit` in flight. The worker must not throw. */
async function pool(items, limit, worker) {
  let next = 0;
  const lane = async () => {
    while (next < items.length) {
      const item = items[next];
      next += 1;
      await worker(item);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, lane));
}

function loadPrefs() {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') || {};
  } catch {
    return {};
  }
}

function savePrefs(changes) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ ...loadPrefs(), ...changes }));
  } catch {
    // Storage can be unavailable; preferences are only a convenience.
  }
}

let fieldIds = 0;

function textInput(value = '', placeholder = '') {
  const input = h('input', {
    class: 'input', type: 'text', placeholder,
    spellcheck: 'false', autocomplete: 'off', autocapitalize: 'off',
  });
  input.value = value;
  return input;
}

/** A labelled form field. `extra` sits beside the input on the same row. */
function field(label, input, { extra = null, hint = '' } = {}) {
  fieldIds += 1;
  input.id = input.id || `dash-field-${fieldIds}`;
  return h('div', { class: 'field' },
    h('label', { for: input.id }, label),
    extra ? h('div', { class: 'field-row' }, input, extra) : input,
    hint ? h('span', { class: 'hint' }, hint) : null);
}

function showError(el, err) {
  el.textContent = typeof err === 'string'
    ? err
    : [err.message, err.detail && err.detail !== err.message ? err.detail : ''].filter(Boolean).join('\n');
  el.classList.remove('hidden');
}

/** Disable a button and show "Working…" while fn runs. Errors go to the error element. */
async function working(btn, errorEl, fn) {
  const labelNode = btn.lastChild;
  const original = labelNode.textContent;
  btn.disabled = true;
  labelNode.textContent = 'Working…';
  errorEl.classList.add('hidden');
  try {
    return await fn();
  } catch (err) {
    showError(errorEl, err);
    return undefined;
  } finally {
    btn.disabled = false;
    labelNode.textContent = original;
  }
}

function setButton(btn, iconName, label) {
  btn.replaceChildren(icon(iconName, 14), label);
}

/** Folder name git clone would pick for a URL. */
function cloneName(url) {
  const last = url.trim().replace(/\/+$/, '').split('/').pop().split(':').pop();
  return last.replace(/\.git$/, '');
}

function skeleton(width) {
  return h('span', { class: 'dash-skel', style: { width } });
}

// -- dashboard ----------------------------------------------------------------

export class Dashboard {
  constructor(app) {
    this.app = app;
    this.summaries = new Map(); // repo id -> summary
    this.pending = new Set(); // repo ids with a summary request in flight
    this.busy = new Map(); // repo id -> "fetch" | "pull"
    this.rows = new Map(); // repo id -> { el, key }
    this.loading = false;
    this.reloadQueued = false;
    this.lastLoad = 0;
    this.updatedAt = 0;
    this.timer = null;
    this.fetchingAll = false;
    this.frame = 0;

    const prefs = loadPrefs();
    this.sort = prefs.sort && COLUMNS.some((c) => c.sort === prefs.sort.key) ? prefs.sort : { key: 'name', dir: 1 };
    this.filter = FILTERS.some((f) => f.id === prefs.filter) ? prefs.filter : 'all';
    this.query = '';

    this.onVisibility = () => {
      if (!document.hidden && Date.now() - this.lastLoad > REFRESH_MS) this.loadAll();
    };
    this.onDocKey = (ev) => this.handleDocKey(ev);
    this.build();
  }

  // -- lifecycle --------------------------------------------------------------

  activate() {
    this.render();
    this.loadAll();
    clearInterval(this.timer);
    this.timer = setInterval(() => {
      if (!document.hidden) this.loadAll();
    }, REFRESH_MS);
    document.addEventListener('visibilitychange', this.onVisibility);
    document.addEventListener('keydown', this.onDocKey);
  }

  deactivate() {
    clearInterval(this.timer);
    this.timer = null;
    document.removeEventListener('visibilitychange', this.onVisibility);
    document.removeEventListener('keydown', this.onDocKey);
  }

  refresh() {
    const ids = new Set(this.app.state.repos.map((r) => r.id));
    for (const id of [...this.summaries.keys()]) if (!ids.has(id)) this.summaries.delete(id);
    for (const id of [...this.rows.keys()]) if (!ids.has(id)) this.rows.delete(id);
    this.render();
    const fresh = this.scope().filter((r) => !this.summaries.has(r.id) && !this.pending.has(r.id));
    if (fresh.length) pool(fresh.map((r) => r.id), SUMMARY_CONCURRENCY, (id) => this.loadOne(id));
  }

  // -- data -------------------------------------------------------------------

  /** The repositories of the list on screen: a group's, or all of them. */
  scope() {
    return this.app.scopeRepos();
  }

  async loadAll() {
    if (this.loading) {
      this.reloadQueued = true;
      return;
    }
    this.loading = true;
    this.lastLoad = Date.now();
    this.renderUpdated();
    // Visible rows first, so the screen fills in before the rest.
    const shown = this.visibleRepos().map((r) => r.id);
    const rest = this.scope().map((r) => r.id).filter((id) => !shown.includes(id));
    await pool([...shown, ...rest], SUMMARY_CONCURRENCY, (id) => this.loadOne(id));
    this.loading = false;
    this.updatedAt = Date.now();
    this.renderUpdated();
    if (this.reloadQueued) {
      this.reloadQueued = false;
      this.loadAll();
    }
  }

  async loadOne(id) {
    if (!this.app.repo(id)) return;
    this.pending.add(id);
    try {
      const summary = await api.get('/api/repos/summary', { id });
      if (!this.app.repo(id)) return;
      this.summaries.set(id, summary);
      if (!summary.missing) this.app.setDirty(id, changeCount(summary) > 0);
    } catch (err) {
      // Keep the last good status when the server itself is unreachable.
      if (err.status !== 0 && this.app.repo(id)) this.summaries.set(id, { missing: true, error: err.message });
    } finally {
      this.pending.delete(id);
      this.scheduleRender();
    }
  }

  scheduleRender() {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      if (this.el.isConnected) this.render();
    });
  }

  visibleRepos() {
    const terms = this.query.toLowerCase().split(/\s+/).filter(Boolean);
    const test = FILTERS.find((f) => f.id === this.filter).test;
    const list = this.scope().filter((repo) => {
      const s = this.summaries.get(repo.id);
      if (!test(s)) return false;
      if (!terms.length) return true;
      const text = [repo.name, repo.path, s?.branch, s?.github?.slug].filter(Boolean).join(' ').toLowerCase();
      return terms.every((t) => text.includes(t));
    });
    const byName = (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });
    return list.sort((a, b) => {
      if (Boolean(a.pinned) !== Boolean(b.pinned)) return a.pinned ? -1 : 1;
      return this.compare(a, b) || byName(a, b);
    });
  }

  /** Compare two repos by the current sort column. Repos without a status sort last. */
  compare(a, b) {
    const { key, dir } = this.sort;
    if (key === 'name') return dir * a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });
    const value = (repo) => {
      const s = this.summaries.get(repo.id);
      if (!s || s.missing) return null;
      if (key === 'branch') return s.branch || '';
      if (key === 'changes') return s.conflicted * 1e6 + (s.operation ? 1e5 : 0) + s.staged + s.unstaged;
      return s.last_commit ? s.last_commit.date : 0;
    };
    const va = value(a);
    const vb = value(b);
    if (va === null || vb === null) return va === vb ? 0 : va === null ? 1 : -1;
    if (typeof va === 'string') return dir * va.localeCompare(vb, undefined, { numeric: true, sensitivity: 'base' });
    return dir * (va - vb);
  }

  totals() {
    let changes = 0;
    let behind = 0;
    let ahead = 0;
    const repos = this.scope();
    for (const repo of repos) {
      const s = this.summaries.get(repo.id);
      if (hasChanges(s)) changes += 1;
      if (s && !s.missing && s.behind > 0) behind += 1;
      if (s && !s.missing && s.ahead > 0) ahead += 1;
    }
    return { all: repos.length, changes, behind, ahead };
  }

  // -- layout -----------------------------------------------------------------

  build() {
    this.subtitle = h('p', { class: 'dash-sub' });
    this.kicker = h('p', { class: 'dash-kicker' });
    this.title = h('h1', { class: 'dash-title' });
    this.fetchAllBtn = button('Fetch all', () => this.fetchAll(), { class: 'dash-hbtn', icon: 'download' });
    // In a group, picking from the repositories already in GitSense comes first.
    this.chooseBtn = button('Choose repositories', () => this.chooseDialog(), { class: 'dash-hbtn dash-hbtn-primary', icon: 'list-checks' });
    this.addBtn = button('Add repository', () => this.addDialog(), { class: 'dash-hbtn', icon: 'folder-plus' });
    this.heroActions = h('div', { class: 'dash-hero-actions' },
      this.chooseBtn,
      this.addBtn,
      button('Scan a folder', () => this.scanDialog(), { class: 'dash-hbtn', icon: 'folder-search' }),
      button('Clone', () => this.cloneDialog(), { class: 'dash-hbtn', icon: 'copy' }),
      h('span', { class: 'dash-hero-sep', 'aria-hidden': 'true' }),
      this.fetchAllBtn);
    const hero = h('header', { class: 'dash-hero' },
      h('div', { class: 'dash-hero-text' }, this.kicker, this.title, this.subtitle),
      this.heroActions);

    this.search = textInput('', 'Filter by name, branch or path');
    this.search.setAttribute('aria-label', 'Filter repositories');
    const applyQuery = debounce(() => this.renderRows(), 80);
    this.search.addEventListener('input', () => {
      this.query = this.search.value.trim();
      applyQuery();
    });
    this.search.addEventListener('keydown', (ev) => this.onSearchKey(ev));
    this.filterSeg = h('div', { class: 'seg dash-filters', role: 'group', 'aria-label': 'Show' });
    this.updated = h('span', { class: 'dash-updated', role: 'status' });
    const toolbar = h('div', { class: 'dash-toolbar' },
      h('div', { class: 'search dash-search' }, icon('search', 14), this.search),
      this.filterSeg,
      h('div', { class: 'grow' }),
      this.updated,
      iconButton('refresh-cw', 'Refresh status', () => this.loadAll()));

    this.head = h('div', { class: 'dash-grid dash-head', role: 'row' });
    this.list = h('div', { class: 'dash-list', role: 'rowgroup' });
    this.noMatch = h('div', { class: 'empty dash-nomatch hidden' },
      icon('filter', 28),
      h('p', { class: 'heading' }, 'No repositories match'),
      button('Show all', () => this.clearFilter(), { class: 'sm' }));
    const table = h('div', { class: 'dash-table', role: 'table', 'aria-label': 'Repositories' },
      h('div', { role: 'rowgroup' }, this.head), this.list);
    this.scroller = h('div', { class: 'dash-scroll' }, table, this.noMatch);

    this.emptyState = h('div', { class: 'dash-empty' });
    this.rail = h('nav', { class: 'dash-rail', 'aria-label': 'Groups' });
    this.el = h('div', { class: 'dash' },
      this.rail,
      h('div', { class: 'dash-body' }, hero, h('div', { class: 'dash-main' }, toolbar, this.scroller), this.emptyState));
  }

  /** Fill the page shown when the list on screen has no repositories. */
  renderEmpty() {
    const group = this.app.activeGroup;
    const tile = (iconName, title, text, onClick) => h('button', { class: 'dash-tile', type: 'button', onclick: onClick },
      h('span', { class: 'dash-tile-icon' }, icon(iconName, 18)),
      h('span', { class: 'dash-tile-title' }, title),
      h('span', { class: 'dash-tile-text' }, text));
    const add = [
      tile('folder-plus', 'Add a repository', 'Pick a folder that already holds a Git repository, or start a new one.', () => this.addDialog()),
      tile('folder-search', 'Scan a folder', 'Find every repository under a folder such as ~/Repos and add them together.', () => this.scanDialog()),
      tile('copy', 'Clone', 'Copy a repository from GitHub or any Git URL to this computer.', () => this.cloneDialog()),
    ];
    const key = group ? `${group.id}:${this.app.state.repos.length > 0}` : 'all';
    if (this.emptyState.dataset.for === key) return;
    this.emptyState.dataset.for = key;
    if (!group) {
      clear(this.emptyState).append(h('div', { class: 'dash-empty-inner' },
        h('p', { class: 'eyebrow' }, 'Get started'),
        h('h2', { class: 'title' }, 'Add your first repository'),
        h('p', { class: 'dash-empty-text' },
          'GitSense keeps the repositories you work on in one list, with branch, sync and working tree status for each.'),
        h('div', { class: 'dash-tiles' }, add)));
      return;
    }
    clear(this.emptyState).append(h('div', { class: 'dash-empty-inner' },
      h('p', { class: 'eyebrow' }, 'Group'),
      h('h2', { class: 'title' }, 'This group is empty'),
      h('p', { class: 'dash-empty-text' },
        'Pick repositories that are already in GitSense, or add new ones here. A repository can be in more than one group.'),
      h('div', { class: 'dash-tiles' },
        this.app.state.repos.length
          ? tile('list-checks', 'Choose repositories', 'Tick the repositories from your full list that belong in this group.', () => this.chooseDialog())
          : null,
        add)));
  }

  render() {
    this.renderRail();
    const empty = this.scope().length === 0;
    this.el.classList.toggle('is-empty', empty);
    this.renderHero();
    if (empty) {
      this.renderEmpty();
      return;
    }
    this.renderFilters();
    this.renderHead();
    this.renderRows();
    this.renderUpdated();
  }

  // -- group rail -------------------------------------------------------------

  /** Draw the rail. It is rebuilt only when it changed, so focus and clicks survive status updates. */
  renderRail() {
    const app = this.app;
    if (this.groupDrag) return;
    const active = app.activeGroup;
    const key = JSON.stringify([active ? active.id : null, app.state.repos.length, app.state.groups.map((g) => [g.id, g.name, g.repos.length])]);
    if (key === this.railKey) return;
    this.railKey = key;
    const row = (group) => {
      const on = group ? active === group : !active;
      const main = h('button', {
        class: `dash-rail-item ${on ? 'on' : ''}`,
        type: 'button',
        'aria-current': on ? 'true' : null,
        title: group ? group.name : 'Every repository in GitSense',
        onclick: () => app.switchGroup(group ? group.id : null),
      },
      icon(group ? 'folders' : 'layout-grid', 14),
      h('span', { class: 'dash-rail-name ellipsis' }, group ? group.name : 'All repositories'),
      h('span', { class: 'dash-rail-count' }, String(group ? group.repos.length : app.state.repos.length)));
      const el = h('div', { class: 'dash-rail-row' }, main);
      if (!group) return el;
      el.append(iconButton('ellipsis', `Actions for ${group.name}`, (ev) => this.showGroupMenu(ev.currentTarget, group), { size: 14, class: 'dash-rail-more' }));
      el.addEventListener('contextmenu', (ev) => {
        ev.preventDefault();
        this.showGroupMenu({ x: ev.clientX, y: ev.clientY }, group);
      });
      el.dataset.group = group.id;
      el.addEventListener('pointerdown', (ev) => this.startGroupDrag(ev, el));
      // A repository row dropped here joins the group.
      el.addEventListener('dragover', (ev) => {
        // Only a repository row from this page; never a file or text dragged in from outside.
        if (!this.dragging || !ev.dataTransfer.types.includes(DRAG_TYPE) || group.repos.includes(this.dragging)) return;
        ev.preventDefault();
        ev.dataTransfer.dropEffect = 'copy';
        el.classList.add('drop');
      });
      el.addEventListener('dragleave', () => el.classList.remove('drop'));
      el.addEventListener('drop', (ev) => {
        ev.preventDefault();
        el.classList.remove('drop');
        const repo = app.repo(ev.dataTransfer.getData(DRAG_TYPE));
        this.endDrag();
        if (repo) this.setMembership(repo, group, true);
      });
      return el;
    };
    clear(this.rail).append(
      h('div', { class: 'dash-rail-head' },
        h('p', { class: 'eyebrow' }, 'Groups'),
        iconButton('plus', 'New group', () => app.newGroupDialog(), { size: 15 })),
      h('div', { class: 'dash-rail-list' }, row(null), app.state.groups.map(row)));
    if (!app.state.groups.length) {
      this.rail.append(h('div', { class: 'dash-rail-hint' },
        h('p', null, 'Keep sets of repositories together. Each group has its own list and its own open tabs.'),
        button('New group', () => app.newGroupDialog(), { class: 'sm', icon: 'plus' })));
    }
  }

  /**
   * Drag a group up or down to reorder the groups.
   * - The drag starts after a few pixels, so a plain click still just shows the group.
   * - The row follows the pointer and swaps places as it passes the middle of a neighbour.
   * - "All repositories" stays first.
   * - Holding the row near the top or bottom edge scrolls a long list.
   */
  startGroupDrag(ev, row) {
    if (ev.button !== 0 || ev.target.closest('.dash-rail-more')) return;
    const list = row.parentElement;
    const startY = ev.clientY;
    const grab = startY - row.getBoundingClientRect().top;
    const middle = (el) => { const r = el.getBoundingClientRect(); return r.top + r.height / 2; };
    const isGroup = (el) => Boolean(el && el.dataset.group);
    let y = startY;
    let timer = 0;
    const place = () => {
      row.style.transform = '';
      const prev = row.previousElementSibling;
      const next = row.nextElementSibling;
      if (isGroup(prev) && y < middle(prev)) list.insertBefore(row, prev);
      else if (isGroup(next) && y > middle(next)) list.insertBefore(next, row);
      // Keep the row under the pointer, but never over "All repositories" or past the last group.
      const rows = [...list.querySelectorAll('.dash-rail-row[data-group]')];
      const low = rows[0].getBoundingClientRect().top;
      const high = rows[rows.length - 1].getBoundingClientRect().bottom - row.offsetHeight;
      const natural = row.getBoundingClientRect().top;
      const wanted = Math.max(low, Math.min(high, y - grab));
      row.style.transform = `translateY(${wanted - natural}px)`;
    };
    const scroll = () => {
      const box = list.getBoundingClientRect();
      const step = y < box.top + 20 ? -6 : y > box.bottom - 20 ? 6 : 0;
      const from = list.scrollTop;
      if (step) list.scrollTop += step;
      if (list.scrollTop !== from) place();
    };
    const move = (e) => {
      y = e.clientY;
      if (!this.groupDrag) {
        if (Math.abs(y - startY) < 6) return;
        this.groupDrag = true;
        row.classList.add('dragging');
        timer = setInterval(scroll, 16);
      }
      place();
    };
    const end = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
      window.removeEventListener('pointercancel', end);
      if (!this.groupDrag) return;
      clearInterval(timer);
      this.groupDrag = false;
      row.classList.remove('dragging');
      row.style.transform = '';
      // The click that ends a drag must not also switch to the group.
      const swallow = (e) => { e.stopPropagation(); e.preventDefault(); };
      window.addEventListener('click', swallow, { capture: true, once: true });
      setTimeout(() => window.removeEventListener('click', swallow, { capture: true }), 0);
      const order = [...list.querySelectorAll('.dash-rail-row[data-group]')].map((el) => el.dataset.group);
      if (order.some((id, i) => id !== this.app.state.groups[i]?.id)) this.app.reorderGroups(order);
      else this.renderRail();
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', end);
  }

  /** Move a group one place up (-1) or down (1). */
  moveGroup(group, by) {
    const ids = this.app.state.groups.map((g) => g.id);
    const from = ids.indexOf(group.id);
    const to = from + by;
    if (from < 0 || to < 0 || to >= ids.length) return;
    ids.splice(to, 0, ...ids.splice(from, 1));
    this.app.reorderGroups(ids);
  }

  endDrag() {
    this.dragging = null;
    this.el.classList.remove('is-dragging');
    for (const el of this.rail.querySelectorAll('.drop')) el.classList.remove('drop');
    this.renderRows();
  }

  showGroupMenu(anchor, group) {
    const groups = this.app.state.groups;
    const at = groups.indexOf(group);
    showMenu(anchor, [
      { heading: group.name },
      { label: 'Choose repositories…', icon: 'list-checks', onClick: () => this.chooseDialog(group) },
      { label: 'Rename…', icon: 'pencil', onClick: () => this.renameGroup(group) },
      { sep: true },
      { label: 'Move up', icon: 'arrow-up', disabled: at === 0, onClick: () => this.moveGroup(group, -1) },
      { label: 'Move down', icon: 'arrow-down', disabled: at === groups.length - 1, onClick: () => this.moveGroup(group, 1) },
      { sep: true },
      { label: 'Delete group', icon: 'trash-2', danger: true, onClick: () => this.deleteGroup(group) },
    ]);
  }

  renameGroup(group) {
    return formDialog({
      eyebrow: 'Group',
      title: 'Rename group',
      fields: [{ name: 'name', label: 'Name', value: group.name, required: true }],
      confirmLabel: 'Rename',
      submit: ({ name }) => this.app.updateGroup(group.id, { name }),
    });
  }

  async deleteGroup(group) {
    const ok = await confirmDialog({
      eyebrow: 'Group',
      title: 'Delete group',
      message: `Delete the group ${group.name}? Its ${plural(group.repos.length, 'repository', 'repositories')} stay in GitSense and on disk.`,
      confirmLabel: 'Delete group',
      danger: true,
    });
    if (!ok) return;
    try {
      await this.app.deleteGroup(group.id);
    } catch (err) {
      toastError(err);
    }
  }

  /** Put a repository in a group, or take it out. */
  async setMembership(repo, group, member) {
    try {
      await this.app.updateGroup(group.id, member ? { add: [repo.id] } : { remove: [repo.id] });
      toast(member ? `Added ${repo.name} to ${group.name}` : `Removed ${repo.name} from ${group.name}`, 'ok');
    } catch (err) {
      toastError(err);
    }
  }

  /** Menu of groups for one repository; picking a group toggles it. */
  repoGroupsMenu(anchor, repo) {
    const groups = this.app.state.groups;
    showMenu(anchor, [
      { heading: groups.length ? `${repo.name} is in` : repo.name },
      ...groups.map((g) => {
        const member = g.repos.includes(repo.id);
        return {
          label: g.name,
          icon: member ? 'check' : 'folders',
          hint: member ? 'remove' : 'add',
          onClick: () => this.setMembership(repo, g, !member),
        };
      }),
      { sep: true },
      {
        label: 'New group with this repository…',
        icon: 'plus',
        onClick: async () => {
          const group = await this.app.newGroupDialog({ repos: [repo.id], open: false });
          if (group) toast(`Added ${repo.name} to ${group.name}`, 'ok');
        },
      },
    ]);
  }

  /** Tick list of every repository in GitSense, to set which ones a group holds. */
  chooseDialog(group = this.app.activeGroup) {
    if (!group) return null;
    const repos = [...this.app.state.repos].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }));
    return openModal({
      eyebrow: group.name,
      title: 'Choose repositories',
      wide: true,
      build: (close) => {
        const error = h('p', { class: 'modal-error hidden' });
        const filter = textInput('', 'Filter by name or path');
        filter.setAttribute('aria-label', 'Filter repositories');
        const count = h('span', { class: 'heading' });
        const entries = repos.map((repo) => {
          const box = h('input', { type: 'checkbox', checked: group.repos.includes(repo.id), onchange: () => update() });
          const el = h('label', { class: 'check dash-scan-item' },
            box,
            h('span', { class: 'dash-scan-text' },
              h('span', { class: 'dash-scan-name ellipsis' }, repo.name),
              h('span', { class: 'dash-scan-path ellipsis', title: repo.path }, this.tildify(repo.path))));
          return { repo, box, el, text: `${repo.name} ${repo.path}`.toLowerCase() };
        });
        const shown = () => entries.filter((e) => !e.el.classList.contains('hidden'));
        const update = () => {
          const n = entries.filter((e) => e.box.checked).length;
          count.textContent = `${n} of ${plural(repos.length, 'repository', 'repositories')} in this group`;
        };
        const all = (value) => {
          for (const e of shown()) e.box.checked = value;
          update();
        };
        filter.addEventListener('input', () => {
          const terms = filter.value.toLowerCase().split(/\s+/).filter(Boolean);
          for (const e of entries) e.el.classList.toggle('hidden', !terms.every((t) => e.text.includes(t)));
        });
        const save = button('Save', () => working(save, error, async () => {
          const want = new Set(entries.filter((e) => e.box.checked).map((e) => e.repo.id));
          const add = [...want].filter((id) => !group.repos.includes(id));
          const remove = group.repos.filter((id) => !want.has(id));
          if (add.length || remove.length) await this.app.updateGroup(group.id, { add, remove });
          close(true);
        }), { class: 'primary', icon: 'check' });
        update();
        return {
          body: [
            repos.length
              ? [
                h('div', { class: 'search dash-choose-filter' }, icon('search', 14), filter),
                h('div', { class: 'dash-scan-head' },
                  count,
                  h('span', { class: 'grow' }),
                  button('Select all', () => all(true), { class: 'sm ghost' }),
                  button('Select none', () => all(false), { class: 'sm ghost' })),
                h('div', { class: 'dash-scan-list' }, entries.map((e) => e.el)),
              ]
              : h('div', { class: 'empty dash-scan-empty' },
                icon('folder-search', 26),
                h('p', { class: 'heading' }, 'No repositories yet'),
                h('p', null, 'Add a repository to GitSense first, then pick it here.')),
            error,
          ],
          foot: [button('Cancel', () => close(null), { class: 'ghost' }), repos.length ? save : null],
        };
      },
    });
  }

  renderHero() {
    const group = this.app.activeGroup;
    this.kicker.textContent = group ? 'Group' : 'GitSense workspace';
    this.title.textContent = group ? group.name : 'All repositories';
    this.title.title = group ? group.name : '';
    this.chooseBtn.classList.toggle('hidden', !group);
    this.addBtn.classList.toggle('dash-hbtn-primary', !group);
    const t = this.totals();
    const parts = t.all === 0
      ? ['No repositories yet']
      : [plural(t.all, 'repository', 'repositories'), `${t.changes} with changes`, `${t.behind} behind`];
    clear(this.subtitle).append(parts.join('  ·  '));
    if (this.el.isConnected) setStatus(plural(t.all, 'repository', 'repositories'));
    this.heroActions.classList.toggle('hidden', t.all === 0);
    this.fetchAllBtn.disabled = this.fetchingAll;
  }

  renderFilters() {
    const t = this.totals();
    clear(this.filterSeg).append(...FILTERS.map((f) => h('button', {
      type: 'button',
      class: f.id === this.filter ? 'on' : '',
      'aria-pressed': String(f.id === this.filter),
      onclick: () => {
        this.filter = f.id;
        savePrefs({ filter: f.id });
        this.render();
      },
    }, f.label, h('span', { class: 'dash-count' }, String(t[f.id])))));
  }

  renderHead() {
    clear(this.head).append(...COLUMNS.map((col) => {
      if (!col.sort) return h('div', { class: `dash-th dash-col-${col.id}`, role: 'columnheader' }, col.label);
      const on = this.sort.key === col.sort;
      return h('div', {
        class: `dash-th dash-col-${col.id}`,
        role: 'columnheader',
        'aria-sort': on ? (this.sort.dir > 0 ? 'ascending' : 'descending') : 'none',
      }, h('button', {
        class: `dash-sort ${on ? 'on' : ''}`,
        type: 'button',
        title: `Sort by ${col.label.toLowerCase()}`,
        onclick: () => {
          this.sort = on ? { key: col.sort, dir: -this.sort.dir } : { key: col.sort, dir: col.dir };
          savePrefs({ sort: this.sort });
          this.renderHead();
          this.renderRows();
        },
      }, col.label, on ? icon(this.sort.dir > 0 ? 'arrow-up' : 'arrow-down', 11) : null));
    }));
  }

  renderUpdated() {
    if (this.loading && !this.updatedAt) {
      clear(this.updated).append(icon('loader-circle', 12, 'spin'), 'Checking status…');
    } else if (this.updatedAt) {
      clear(this.updated);
      if (this.loading) this.updated.append(icon('loader-circle', 12, 'spin'));
      this.updated.append(`Updated ${relTime(this.updatedAt / 1000)}`);
      this.updated.title = fullDate(this.updatedAt / 1000);
    }
  }

  /** Render the table body, rebuilding only rows whose data changed. */
  renderRows() {
    // A row replaced while it is being dragged would never report the end of the drag.
    if (this.dragging) return;
    const repos = this.visibleRepos();
    const focusedId = document.activeElement?.closest?.('.dash-row')?.dataset.id;
    const minute = Math.floor(Date.now() / 60000);
    // A hairline separates pinned repos from the rest.
    const pinEnd = repos.some((r) => !r.pinned) ? repos.filter((r) => r.pinned).pop() : null;
    const els = repos.map((repo) => {
      const s = this.summaries.get(repo.id);
      const key = JSON.stringify([repo.name, repo.path, Boolean(repo.pinned), repo === pinEnd, this.app.state.groups.length > 0,
        s || null, s ? false : this.pending.has(repo.id), this.busy.get(repo.id) || null, minute]);
      let row = this.rows.get(repo.id);
      if (!row || row.key !== key) {
        const el = this.buildRow(repo, s, repo === pinEnd);
        if (row && row.el.isConnected) row.el.replaceWith(el);
        row = { el, key };
        this.rows.set(repo.id, row);
      }
      return row.el;
    });
    const current = [...this.list.children];
    if (current.length !== els.length || current.some((el, i) => el !== els[i])) this.list.replaceChildren(...els);
    if (focusedId) {
      const el = this.rows.get(focusedId)?.el;
      if (el && el.isConnected && !el.contains(document.activeElement)) el.focus({ preventScroll: true });
    }
    const filtered = repos.length === 0 && this.scope().length > 0;
    this.noMatch.classList.toggle('hidden', !filtered);
    this.head.parentElement.classList.toggle('hidden', filtered);
  }

  // -- rows -------------------------------------------------------------------

  buildRow(repo, s, lastPinned) {
    const row = h('div', {
      class: `dash-grid dash-row ${lastPinned ? 'dash-row-pin-end' : ''} ${s && s.missing ? 'dash-row-missing' : ''}`,
      role: 'row',
      tabindex: '0',
      'aria-label': repo.name,
      dataset: { id: repo.id },
      // Rows can be dragged onto a group in the rail.
      draggable: this.app.state.groups.length ? 'true' : null,
      ondragstart: (ev) => {
        this.dragging = repo.id;
        ev.dataTransfer.setData(DRAG_TYPE, repo.id);
        ev.dataTransfer.effectAllowed = 'copy';
        this.el.classList.add('is-dragging');
      },
      ondragend: () => this.endDrag(),
      ondblclick: (ev) => { if (!ev.target.closest('button')) this.open(repo); },
      onkeydown: (ev) => this.onRowKey(ev, repo),
      oncontextmenu: (ev) => {
        ev.preventDefault();
        row.focus({ preventScroll: true });
        this.showRowMenu({ x: ev.clientX, y: ev.clientY }, repo);
      },
    });

    const star = h('button', {
      class: `dash-star ${repo.pinned ? 'on' : ''}`,
      type: 'button',
      tabindex: '-1',
      title: repo.pinned ? 'Unpin' : 'Pin to top',
      'aria-label': repo.pinned ? `Unpin ${repo.name}` : `Pin ${repo.name} to top`,
      'aria-pressed': String(Boolean(repo.pinned)),
      onclick: (ev) => { ev.stopPropagation(); this.togglePin(repo); },
    }, icon('star', 14));

    const sub = s?.github
      ? [icon('cloud', 11), s.github.slug]
      : [icon('folder', 11), this.shortPath(dirname(repo.path).replace(/\/$/, '') || '/')];
    const name = h('div', { class: 'dash-cell dash-name', role: 'cell' },
      h('span', { class: 'dash-name-main ellipsis', title: repo.path }, repo.name),
      h('span', { class: 'dash-name-sub', title: s?.github ? s.github.url : repo.path }, sub[0], h('span', { class: 'ellipsis' }, sub[1])));

    row.append(h('div', { class: 'dash-cell dash-col-star', role: 'cell' }, star), name);

    if (s && s.missing) {
      const notFound = s.error === 'Folder not found';
      row.append(h('div', { class: 'dash-cell dash-missing', role: 'cell' },
        icon('triangle-alert', 14),
        h('span', { class: 'dash-missing-label' }, notFound ? 'Folder not found' : 'Cannot read this repository'),
        h('span', { class: 'dash-missing-detail ellipsis', title: notFound ? repo.path : s.error },
          notFound ? this.tildify(repo.path) : s.error),
        button('Remove', (ev) => { ev.stopPropagation(); this.removeRepo(repo); }, { class: 'sm danger', icon: 'trash-2' })));
      return row;
    }

    row.append(
      this.branchCell(s),
      this.syncCell(repo, s),
      this.changesCell(s),
      this.commitCell(s),
      this.rowActions(repo, s));
    for (const btn of row.querySelectorAll('.dash-actions button')) btn.tabIndex = -1;
    return row;
  }

  branchCell(s) {
    const cell = h('div', { class: 'dash-cell dash-col-branch', role: 'cell' });
    if (!s) {
      cell.append(skeleton('70%'));
    } else if (s.detached) {
      cell.append(icon('git-commit-horizontal', 13),
        h('span', { class: 'dash-branch ellipsis', title: 'Detached HEAD' },
          h('span', { class: 'muted' }, 'Detached at '), h('span', { class: 'mono' }, shortSha(s.last_commit?.sha))));
    } else {
      cell.append(icon('git-branch', 13),
        h('span', { class: 'dash-branch ellipsis', title: s.upstream ? `${s.branch} tracks ${s.upstream}` : s.branch || '' }, s.branch || 'No branch'));
    }
    return cell;
  }

  syncCell(repo, s) {
    const cell = h('div', { class: 'dash-cell dash-col-sync', role: 'cell' });
    const busy = this.busy.get(repo.id);
    if (busy) {
      cell.append(h('span', { class: 'dash-quiet' }, icon('loader-circle', 12, 'spin'), busy === 'pull' ? 'Pulling…' : 'Fetching…'));
      return cell;
    }
    if (!s) {
      cell.append(skeleton('55%'));
    } else if (s.detached) {
      cell.append(h('span', { class: 'dash-quiet' }, 'Detached'));
    } else if (!s.upstream) {
      cell.append(h('span', { class: 'dash-quiet', title: 'This branch does not track a remote branch' }, 'No upstream'));
    } else if (!s.ahead && !s.behind) {
      cell.append(h('span', { class: 'dash-quiet', title: `Up to date with ${s.upstream}` }, icon('check', 12), 'Up to date'));
    } else {
      if (s.ahead) cell.append(h('span', { class: 'dash-chip ahead', title: `${plural(s.ahead, 'commit')} to push to ${s.upstream}` }, icon('arrow-up', 11), s.ahead));
      if (s.behind) cell.append(h('span', { class: 'dash-chip behind', title: `${plural(s.behind, 'commit')} to pull from ${s.upstream}` }, icon('arrow-down', 11), s.behind));
    }
    return cell;
  }

  changesCell(s) {
    const cell = h('div', { class: 'dash-cell dash-col-changes', role: 'cell' });
    if (!s) {
      cell.append(skeleton('45%'));
      return cell;
    }
    if (s.operation) cell.append(h('span', { class: 'dash-op', title: `A ${s.operation} is in progress` }, OPERATIONS[s.operation] || s.operation));
    if (s.conflicted) cell.append(h('span', { class: 'dash-chip danger', title: `${plural(s.conflicted, 'conflicted file')}` }, icon('triangle-alert', 11), s.conflicted));
    if (s.staged) cell.append(h('span', { class: 'dash-chip ok', title: `${plural(s.staged, 'staged file')}` }, icon('file-plus', 11), s.staged));
    if (s.unstaged) cell.append(h('span', { class: 'dash-chip warn', title: `${plural(s.unstaged, 'unstaged file')}` }, icon('file-diff', 11), s.unstaged));
    if (!s.operation && !changeCount(s)) cell.append(h('span', { class: 'dash-quiet' }, 'Clean'));
    return cell;
  }

  commitCell(s) {
    const cell = h('div', { class: 'dash-cell dash-col-commit', role: 'cell' });
    if (!s) {
      cell.append(skeleton('80%'), skeleton('40%'));
      return cell;
    }
    const c = s.last_commit;
    if (!c) {
      cell.append(h('span', { class: 'dash-quiet' }, 'No commits yet'));
      return cell;
    }
    cell.append(
      h('span', { class: 'dash-subject ellipsis', title: c.subject }, c.subject),
      h('span', { class: 'dash-meta ellipsis', title: fullDate(c.date) }, `${relTime(c.date)}  ·  ${c.author}`));
    return cell;
  }

  rowActions(repo, s) {
    return h('div', { class: 'dash-actions' },
      iconButton('download', 'Fetch', () => this.runAction(repo, 'fetch'), { size: 14 }),
      iconButton('arrow-down-to-line', 'Pull', () => this.runAction(repo, 'pull'), { size: 14, disabled: !s?.upstream }),
      iconButton('terminal', 'Open in terminal', () => this.openIn(repo, 'terminal'), { size: 14 }),
      iconButton('folder-open', 'Reveal folder', () => this.openIn(repo, 'folder'), { size: 14 }),
      s?.github ? iconButton('external-link', 'Open on GitHub', () => this.openUrl(s.github.url), { size: 14 }) : null,
      iconButton('ellipsis', 'More actions', (ev) => this.showRowMenu(ev.currentTarget, repo), { size: 14 }),
      button('Open', (ev) => { ev.stopPropagation(); this.open(repo); }, { class: 'sm dash-open' }));
  }

  menuItems(repo, anchor) {
    const group = this.app.activeGroup;
    const s = this.summaries.get(repo.id);
    const ok = s && !s.missing;
    const unusable = s && s.missing;
    return [
      { heading: repo.name },
      { label: 'Open', icon: 'folder-git-2', hint: 'Enter', disabled: unusable, onClick: () => this.open(repo) },
      { sep: true },
      { label: 'Fetch', icon: 'download', disabled: unusable, onClick: () => this.runAction(repo, 'fetch') },
      { label: 'Pull', icon: 'arrow-down-to-line', disabled: !ok || !s.upstream, onClick: () => this.runAction(repo, 'pull') },
      { sep: true },
      { label: 'Open in terminal', icon: 'terminal', disabled: unusable, onClick: () => this.openIn(repo, 'terminal') },
      { label: 'Reveal folder', icon: 'folder-open', disabled: unusable, onClick: () => this.openIn(repo, 'folder') },
      { label: 'Open in editor', icon: 'square-pen', disabled: unusable, onClick: () => this.openIn(repo, 'editor') },
      { label: 'Open on GitHub', icon: 'external-link', disabled: !ok || !s.github, onClick: () => this.openUrl(s.github.url) },
      { label: 'Copy path', icon: 'copy', onClick: () => this.copyPath(repo) },
      { sep: true },
      { label: repo.pinned ? 'Unpin' : 'Pin to top', icon: 'star', onClick: () => this.togglePin(repo) },
      { label: 'Rename', icon: 'pencil', onClick: () => this.renameDialog(repo) },
      { label: 'Groups…', icon: 'folders', onClick: () => this.repoGroupsMenu(anchor, repo) },
      { sep: true },
      group ? { label: `Remove from ${group.name}`, icon: 'folder-minus', onClick: () => this.setMembership(repo, group, false) } : null,
      { label: 'Remove from GitSense', icon: 'trash-2', danger: true, onClick: () => this.removeRepo(repo) },
    ];
  }

  showRowMenu(anchor, repo) {
    // Fixed coordinates: the groups menu reopens here after the row was redrawn.
    let at = anchor;
    if (anchor instanceof Element) {
      const r = anchor.getBoundingClientRect();
      at = { x: r.left, y: r.bottom + 2 };
    }
    showMenu(at, this.menuItems(repo, at));
  }

  // -- keyboard ---------------------------------------------------------------

  onRowKey(ev, repo) {
    if (ev.target !== ev.currentTarget) return;
    const row = ev.currentTarget;
    const rows = [...this.list.children];
    const at = rows.indexOf(row);
    const focusRow = (el) => { if (el) { ev.preventDefault(); el.focus(); el.scrollIntoView({ block: 'nearest' }); } };
    if (ev.key === 'Enter') {
      ev.preventDefault();
      this.open(repo);
    } else if (ev.key === 'ArrowDown') focusRow(rows[at + 1]);
    else if (ev.key === 'ArrowUp') {
      if (at === 0) { ev.preventDefault(); this.search.focus(); } else focusRow(rows[at - 1]);
    } else if (ev.key === 'Home') focusRow(rows[0]);
    else if (ev.key === 'End') focusRow(rows[rows.length - 1]);
    else if (ev.key === 'ContextMenu' || (ev.shiftKey && ev.key === 'F10')) {
      ev.preventDefault();
      const r = row.getBoundingClientRect();
      this.showRowMenu({ x: r.left + 48, y: r.bottom - 6 }, repo);
    }
  }

  onSearchKey(ev) {
    if (ev.key === 'Escape' && this.search.value) {
      ev.preventDefault();
      this.search.value = '';
      this.query = '';
      this.renderRows();
    } else if (ev.key === 'ArrowDown') {
      ev.preventDefault();
      this.list.firstElementChild?.focus();
    } else if (ev.key === 'Enter') {
      const first = this.visibleRepos()[0];
      if (first && !this.summaries.get(first.id)?.missing) this.open(first);
    }
  }

  handleDocKey(ev) {
    if (document.querySelector('.modal-backdrop') || !this.el.isConnected) return;
    const typing = ev.target.closest && ev.target.closest('input, textarea, select');
    if ((ev.key === '/' && !typing && !ev.metaKey && !ev.ctrlKey) || ((ev.metaKey || ev.ctrlKey) && ev.key === 'f')) {
      if (this.scope().length === 0) return;
      ev.preventDefault();
      this.search.focus();
      this.search.select();
    }
  }

  clearFilter() {
    this.search.value = '';
    this.query = '';
    this.filter = 'all';
    savePrefs({ filter: 'all' });
    this.render();
  }

  // -- row actions ------------------------------------------------------------

  open(repo) {
    this.app.openRepo(repo.id);
  }

  tildify(path) {
    const home = this.app.state.home;
    return home && (path === home || path.startsWith(`${home}/`)) ? `~${path.slice(home.length)}` : path;
  }

  /** A parent folder short enough for the table: "~/Repos", or "…/two/levels" when long. */
  shortPath(path) {
    const short = this.tildify(path);
    if (short.length <= 40) return short;
    return `…/${short.split('/').slice(-2).join('/')}`;
  }

  async runAction(repo, action) {
    if (this.busy.has(repo.id)) return;
    this.busy.set(repo.id, action);
    this.renderRows();
    try {
      const label = `${action === 'pull' ? 'Pulling' : 'Fetching'} ${repo.name}…`;
      const res = await withActivity(label, api.post('/api/repo/action', { id: repo.id, action, args: {} }));
      if (res && res.conflict) toast(`Pulling ${repo.name} stopped on conflicts. Open it to resolve them.`, 'error');
      else if (action === 'pull') toast(`Pulled ${repo.name}`, 'ok');
    } catch (err) {
      toastError(err);
    } finally {
      this.busy.delete(repo.id);
      await this.loadOne(repo.id);
    }
  }

  async fetchAll() {
    if (this.fetchingAll) return;
    const targets = this.scope().filter((repo) => {
      const s = this.summaries.get(repo.id);
      if (this.busy.has(repo.id)) return false;
      return !s || (!s.missing && s.remote_url);
    });
    if (!targets.length) {
      toast('No repositories with a remote to fetch');
      return;
    }
    this.fetchingAll = true;
    this.renderHero();
    let done = 0;
    const failed = [];
    const progress = () => setButton(this.fetchAllBtn, 'loader-circle', `Fetching ${done}/${targets.length}`);
    progress();
    this.fetchAllBtn.querySelector('.icon').classList.add('spin');
    try {
      await withActivity(`Fetching ${plural(targets.length, 'repository', 'repositories')}…`,
        pool(targets, FETCH_CONCURRENCY, async (repo) => {
          this.busy.set(repo.id, 'fetch');
          this.scheduleRender();
          try {
            await api.post('/api/repo/action', { id: repo.id, action: 'fetch', args: {} });
          } catch (err) {
            failed.push(`${repo.name}: ${err.message}`);
          }
          this.busy.delete(repo.id);
          done += 1;
          progress();
          this.fetchAllBtn.querySelector('.icon').classList.add('spin');
          await this.loadOne(repo.id);
        }));
    } finally {
      this.fetchingAll = false;
      setButton(this.fetchAllBtn, 'download', 'Fetch all');
      this.renderHero();
    }
    if (failed.length) {
      toast(`Fetch failed for ${plural(failed.length, 'repository', 'repositories')}`, 'error', failed.join('\n'));
    } else {
      toast(`Fetched ${plural(targets.length, 'repository', 'repositories')}`, 'ok');
    }
  }

  openIn(repo, what) {
    api.post('/api/repo/open', { id: repo.id, what }).catch(toastError);
  }

  openUrl(url) {
    api.post('/api/open_url', { url }).catch(toastError);
  }

  async copyPath(repo) {
    if (await copyText(repo.path)) toast('Path copied', 'ok');
  }

  async togglePin(repo) {
    try {
      await this.app.updateRepo(repo.id, { pinned: !repo.pinned });
      this.renderRows();
    } catch (err) {
      toastError(err);
    }
  }

  async renameDialog(repo) {
    const res = await formDialog({
      eyebrow: 'Repository',
      title: 'Rename',
      fields: [{ name: 'name', label: 'Name', value: repo.name, required: true, hint: 'Only GitSense uses this name. The folder keeps its own.' }],
      confirmLabel: 'Rename',
      submit: ({ name }) => this.app.updateRepo(repo.id, { name }),
    });
    if (res) this.render();
  }

  async removeRepo(repo) {
    const ok = await confirmDialog({
      eyebrow: 'Repository',
      title: 'Remove from GitSense',
      message: `Remove ${repo.name} from the list? The folder and its files stay on disk.`,
      confirmLabel: 'Remove',
      danger: true,
    });
    if (!ok) return;
    try {
      await this.app.removeRepo(repo.id);
    } catch (err) {
      toastError(err);
    }
  }

  // -- dialogs ----------------------------------------------------------------

  addDialog() {
    return formDialog({
      eyebrow: this.app.activeGroup ? this.app.activeGroup.name : 'Repositories',
      title: 'Add repository',
      fields: [
        {
          name: 'path',
          label: 'Folder',
          placeholder: `${this.app.state.home}/Repos/project`,
          required: true,
          keepSpaces: true,
          hint: 'A Git repository, or any folder inside one.',
          trailing: (input) => button('Browse…', async () => {
            const path = await this.app.pickFolder(input.value);
            if (path) {
              input.value = path;
              input.focus();
            }
          }, { icon: 'folder-open' }),
        },
        { name: 'init', type: 'checkbox', label: 'Create a new repository in this folder' },
      ],
      confirmLabel: 'Add repository',
      submit: ({ path, init }) => (init ? this.app.initRepo(path) : this.app.addRepo(path)),
    });
  }

  scanDialog() {
    const prefs = loadPrefs();
    // In a group, a repository counts as added only when it is in that group.
    const group = this.app.activeGroup;
    const inGroup = new Set(this.scope().map((r) => r.path));
    const have = (f) => (group ? inGroup.has(f.path) : f.known);
    return openModal({
      eyebrow: group ? group.name : 'Repositories',
      title: 'Scan a folder',
      wide: true,
      build: (close) => {
        const path = textInput(prefs.scanDir || this.app.settings.clone_dir || this.app.state.home);
        const depth = h('select', { class: 'input dash-depth' },
          [1, 2, 3, 4, 5, 6].map((n) => h('option', { value: String(n), selected: n === (prefs.scanDepth || 3) }, plural(n, 'level'))));
        const browse = button('Browse…', async () => {
          const picked = await this.app.pickFolder(path.value);
          if (picked) {
            path.value = picked;
            setMode('scan');
            primary.click();
          }
        }, { icon: 'folder-open' });
        const error = h('p', { class: 'modal-error hidden' });
        const results = h('div', { class: 'dash-scan hidden' });
        const primary = button('Scan', () => (mode === 'scan' ? scan() : add()), { class: 'primary', icon: 'folder-search' });
        let mode = 'scan';
        let entries = []; // { box, path } for each repository found

        const selected = () => entries.filter((e) => e.box.checked && !e.box.disabled);
        const setMode = (next) => {
          mode = next;
          if (mode === 'scan') {
            setButton(primary, 'folder-search', 'Scan');
            primary.disabled = false;
          } else {
            const n = selected().length;
            setButton(primary, 'folder-plus', n ? `Add ${plural(n, 'repository', 'repositories')}` : 'Add repositories');
            primary.disabled = n === 0;
          }
        };
        path.addEventListener('input', () => setMode('scan'));
        depth.addEventListener('change', () => setMode('scan'));

        const scan = async () => {
          const found = await working(primary, error, async () => {
            const res = await api.post('/api/repos/scan', { path: path.value.trim(), depth: Number(depth.value) });
            savePrefs({ scanDir: path.value.trim(), scanDepth: Number(depth.value) });
            return res.found;
          });
          if (!found) return;
          renderResults(found);
          setMode(found.length ? 'add' : 'scan');
        };

        const renderResults = (found) => {
          const known = found.filter(have).length;
          entries = found.map((f) => ({
            path: f.path,
            box: h('input', { type: 'checkbox', checked: !have(f), disabled: have(f), onchange: () => setMode('add') }),
          }));
          const all = (value) => {
            for (const { box } of entries) if (!box.disabled) box.checked = value;
            setMode('add');
          };
          results.classList.remove('hidden');
          if (!found.length) {
            clear(results).append(h('div', { class: 'empty dash-scan-empty' },
              icon('folder-search', 26),
              h('p', { class: 'heading' }, 'No repositories found'),
              h('p', null, 'Try a parent folder or a deeper search.')));
            return;
          }
          clear(results).append(
            h('div', { class: 'dash-scan-head' },
              h('span', { class: 'heading' }, `${plural(found.length, 'repository', 'repositories')} found`),
              known ? h('span', { class: 'muted' }, group ? `${known} already in this group` : `${known} already added`) : null,
              h('span', { class: 'grow' }),
              button('Select all', () => all(true), { class: 'sm ghost', disabled: known === found.length }),
              button('Select none', () => all(false), { class: 'sm ghost', disabled: known === found.length })),
            h('div', { class: 'dash-scan-list' }, found.map((f, i) => h('label', { class: `check dash-scan-item ${have(f) ? 'known' : ''}` },
              entries[i].box,
              h('span', { class: 'dash-scan-text' },
                h('span', { class: 'dash-scan-name ellipsis' }, f.name),
                h('span', { class: 'dash-scan-path ellipsis', title: f.path }, this.tildify(f.path))),
              have(f) ? h('span', { class: 'badge' }, group ? 'In group' : 'Added') : null))));
        };

        const add = () => working(primary, error, async () => {
          const res = await this.app.addRepos(selected().map((e) => e.path));
          close(res);
          if (res.failed.length) {
            toast(`Could not add ${plural(res.failed.length, 'repository', 'repositories')}`, 'error',
              res.failed.map((f) => `${this.tildify(f.path)}: ${f.error}`).join('\n'));
          }
          if (res.repos.length) toast(`Added ${plural(res.repos.length, 'repository', 'repositories')}`, 'ok');
        });

        return {
          body: [
            h('div', { class: 'dash-scan-form' },
              field('Folder', path, { extra: browse }),
              field('Depth', depth)),
            error,
            results,
          ],
          foot: [button('Cancel', () => close(null), { class: 'ghost' }), primary],
        };
      },
    });
  }

  cloneDialog() {
    let parts = null;
    return openModal({
      eyebrow: this.app.activeGroup ? this.app.activeGroup.name : 'Repositories',
      title: 'Clone a repository',
      build: (close) => {
        const url = textInput('', 'git@github.com:owner/repo.git or https://…');
        const parent = textInput(this.app.settings.clone_dir || this.app.state.home);
        const name = textInput('', 'Taken from the URL');
        // The primary stays disabled until there is a URL, so Enter in the GitHub filter cannot submit.
        const syncUrl = () => {
          name.placeholder = cloneName(url.value) || 'Taken from the URL';
          primary.disabled = !url.value.trim();
        };
        url.addEventListener('input', syncUrl);
        const browse = button('Browse…', async () => {
          const picked = await this.app.pickFolder(parent.value);
          if (picked) parent.value = picked;
        }, { icon: 'folder-open' });
        const error = h('p', { class: 'modal-error hidden' });
        const ghTop = h('div', { class: 'dash-gh-slot' });
        const ghNote = h('p', { class: 'dash-gh-note' }, icon('loader-circle', 12, 'spin'), 'Checking GitHub sign-in…');
        const primary = button('Clone', () => working(primary, error, async () => {
          const values = { url: url.value.trim(), parent: parent.value.trim(), name: name.value.trim() };
          if (!values.url) throw new Error('Enter a repository URL');
          if (!values.parent) throw new Error('Choose a folder to clone into');
          if (values.parent !== this.app.settings.clone_dir) this.app.updateSettings({ clone_dir: values.parent });
          const repo = await this.app.cloneRepo(values);
          close(repo);
        }), { class: 'primary', icon: 'copy', disabled: true });
        parts = { url, name, ghTop, ghNote, primary, syncUrl };
        return {
          body: [
            ghTop,
            field('Repository URL', url),
            field('Clone into', parent, { extra: browse }),
            field('Folder name', name, { hint: 'Optional. Leave empty to use the repository name.' }),
            error,
            ghNote,
          ],
          foot: [button('Cancel', () => close(null), { class: 'ghost' }), primary],
        };
      },
      onOpen: ({ modal }) => this.fillGitHub(modal, parts),
    });
  }

  /** Fill the clone dialog's GitHub picker, or a one-line hint when gh is unavailable. */
  async fillGitHub(modal, parts, force = false) {
    const { url, ghTop, ghNote, primary, syncUrl } = parts;
    const gh = await this.app.github(force);
    if (!modal.isConnected) return;
    if (!gh.authenticated) {
      const again = h('button', { class: 'dash-link', type: 'button', onclick: () => {
        clear(ghNote).append(icon('loader-circle', 12, 'spin'), 'Checking GitHub sign-in…');
        this.fillGitHub(modal, parts, true);
      } }, 'Check again');
      clear(ghNote).append(icon('info', 12), h('span', null,
        gh.installed
          ? ['To pick from your GitHub repositories, run ', h('code', null, 'gh auth login'), ' in a terminal. ']
          : ['To pick from your GitHub repositories, install the GitHub CLI and run ', h('code', null, 'gh auth login'), '. '],
        again));
      return;
    }
    ghNote.remove();
    modal.classList.add('wide');

    const prefs = loadPrefs();
    let protocol = prefs.cloneProtocol === 'https' ? 'https' : 'ssh';
    let chosen = null;
    const cache = new Map();
    const owner = h('select', { class: 'input dash-gh-owner', 'aria-label': 'Owner' }, h('option', { value: '' }, gh.user));
    const filter = textInput('', 'Filter repositories');
    filter.setAttribute('aria-label', 'Filter GitHub repositories');
    const list = h('div', { class: 'dash-gh-list', role: 'listbox', 'aria-label': 'GitHub repositories' });
    const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Clone over' });

    const urlFor = (r) => (protocol === 'ssh' ? r.ssh_url : `${r.url}.git`);
    const choose = (r) => {
      chosen = r;
      url.value = urlFor(r);
      syncUrl();
      for (const item of list.children) item.classList.toggle('on', item.dataset.slug === r.slug);
    };
    const renderSeg = () => clear(seg).append(...['ssh', 'https'].map((p) => h('button', {
      type: 'button',
      class: p === protocol ? 'on' : '',
      'aria-pressed': String(p === protocol),
      onclick: () => {
        protocol = p;
        savePrefs({ cloneProtocol: p });
        renderSeg();
        if (chosen) choose(chosen);
      },
    }, p.toUpperCase())));
    renderSeg();

    const renderList = (repos) => {
      const terms = filter.value.toLowerCase().split(/\s+/).filter(Boolean);
      const shown = repos.filter((r) => terms.every((t) => `${r.slug} ${r.description}`.toLowerCase().includes(t)));
      if (!shown.length) {
        clear(list).append(h('p', { class: 'dash-gh-msg' }, repos.length ? 'No repositories match' : 'No repositories for this owner'));
        return;
      }
      clear(list).append(...shown.map((r) => h('button', {
        class: `dash-gh-item ${chosen && chosen.slug === r.slug ? 'on' : ''}`,
        type: 'button',
        role: 'option',
        dataset: { slug: r.slug },
        onclick: () => choose(r),
        ondblclick: () => { choose(r); primary.click(); },
      },
      h('span', { class: 'dash-gh-text' },
        h('span', { class: 'dash-gh-slug ellipsis' }, r.slug),
        r.description ? h('span', { class: 'dash-gh-desc ellipsis' }, r.description) : null),
      r.private ? h('span', { class: 'dash-gh-tag', title: 'Private' }, icon('lock', 10), 'Private') : null,
      r.fork ? h('span', { class: 'dash-gh-tag' }, 'Fork') : null,
      r.archived ? h('span', { class: 'dash-gh-tag' }, 'Archived') : null,
      h('span', { class: 'dash-gh-date' }, r.updated ? relTime(Date.parse(r.updated) / 1000) : ''))));
    };

    const load = async () => {
      const key = owner.value;
      clear(list).append(h('p', { class: 'dash-gh-msg' }, icon('loader-circle', 12, 'spin'), 'Loading repositories…'));
      if (!cache.has(key)) cache.set(key, api.get('/api/github/repos', { owner: key }).then((r) => r.repos));
      try {
        const repos = await cache.get(key);
        if (owner.value === key) renderList(repos);
      } catch (err) {
        cache.delete(key);
        if (owner.value === key) clear(list).append(h('p', { class: 'dash-gh-msg error' }, err.message));
      }
    };
    owner.addEventListener('change', load);
    filter.addEventListener('input', debounce(async () => {
      const repos = await cache.get(owner.value)?.catch(() => null);
      if (repos) renderList(repos);
    }, 60));

    ghTop.append(h('div', { class: 'dash-gh' },
      h('div', { class: 'dash-gh-head' },
        h('span', { class: 'field-label' }, 'From GitHub'),
        h('span', { class: 'grow' }),
        seg),
      h('div', { class: 'dash-gh-bar' },
        owner,
        h('div', { class: 'search grow' }, icon('search', 14), filter)),
      list));
    filter.dataset.enterOwn = '';
    filter.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') { ev.preventDefault(); ev.stopPropagation(); list.querySelector('.dash-gh-item')?.click(); }
      else if (ev.key === 'ArrowDown') { ev.preventDefault(); list.querySelector('.dash-gh-item')?.focus(); }
    });
    if (document.activeElement === url && !url.value) filter.focus();
    load();
    api.get('/api/github/orgs').then((res) => {
      for (const org of res.orgs) owner.append(h('option', { value: org }, org));
    }).catch(() => {});
  }
}
