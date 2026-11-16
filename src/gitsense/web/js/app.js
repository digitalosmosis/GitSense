// App shell: state, tabs, theme and view switching.

import { api } from './api.js';
import { h, clear, icon, iconButton } from './util.js';
import { toast, toastError, showMenu, setStatus, withActivity, formDialog } from './ui.js';
import { Dashboard } from './dashboard.js';
import { RepoView } from './repoview.js';
import { openSettings } from './settings.js';
import { hideSplash } from './splash.js';
import {
  activeTheme, allThemes, applyTheme as paintTheme, paintRememberedTheme, customThemes, setModThemes,
  DEFAULT_THEME, DEFAULT_LIGHT, DEFAULT_DARK,
} from './themes.js';

paintRememberedTheme();

const HOME = 'home';

export const app = {
  // /api/state: repos, open_tabs, active_tab, groups, active_group, settings, version, home, native, platform, mods
  state: null,
  views: new Map(), // repo id -> RepoView
  dashboard: null,
  current: null, // HOME or a repo id
  dirty: new Set(), // repo ids with uncommitted changes (tab dot)
  _github: null,

  async init() {
    this.state = await api.get('/api/state');
    setModThemes(this.state.mods);
    this.applyTheme();
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => this.applyTheme());
    this.dashboard = new Dashboard(this);
    this.renderTopbarActions();
    this.renderStatusRight();
    document.addEventListener('keydown', (ev) => this.onKey(ev));
    document.addEventListener('contextmenu', (ev) => {
      // Keep the native menu only where it is useful: text fields and selectable text.
      if (!ev.target.closest('input, textarea, .selectable')) ev.preventDefault();
    });
    this.state.groups = this.state.groups || [];
    if (!this.group(this.state.active_group)) this.state.active_group = null;
    const active = this.scope.active_tab;
    if (active && this.repo(active) && this.tabs.includes(active)) this.show(active);
    else this.show(HOME);
    // The splash stays up until the first view has its data, within reason.
    const first = this.views.get(this.current);
    const loaded = first && first.refreshing ? first.refreshing : Promise.resolve();
    await Promise.race([loaded, new Promise((resolve) => setTimeout(resolve, 2500))]);
    hideSplash();
  },

  // -- state ------------------------------------------------------------------

  repo(id) {
    return this.state.repos.find((r) => r.id === id) || null;
  },

  get settings() {
    return this.state.settings;
  },

  // -- groups -----------------------------------------------------------------

  group(id) {
    return (id && this.state.groups.find((g) => g.id === id)) || null;
  },

  /** The group on screen, or null when the "All repositories" list is. */
  get activeGroup() {
    return this.group(this.state.active_group);
  },

  /**
   * Whatever holds the tabs on screen: the active group, or the state itself.
   * - Both have open_tabs and active_tab.
   */
  get scope() {
    return this.activeGroup || this.state;
  },

  get tabs() {
    return this.scope.open_tabs;
  },

  /** The repositories of the list on screen. */
  scopeRepos() {
    const group = this.activeGroup;
    if (!group) return this.state.repos;
    return group.repos.map((id) => this.repo(id)).filter(Boolean);
  },

  /**
   * Show another list of repositories, with its own tabs.
   * - id is a group id, or null for "All repositories".
   * - home: true lands on the repository list; false returns to the tab that list had open.
   */
  switchGroup(id, { home = true } = {}) {
    const target = this.group(id);
    if ((target ? target.id : null) === (this.activeGroup ? this.activeGroup.id : null)) {
      if (home) this.show(HOME);
      return;
    }
    if (this.current === HOME) this.dashboard.deactivate();
    else if (this.current) this.views.get(this.current)?.deactivate();
    this.current = null;
    this.state.active_group = target ? target.id : null;
    const last = this.scope.active_tab;
    this.show(!home && last && this.tabs.includes(last) && this.repo(last) ? last : HOME);
  },

  async createGroup(name, repos = []) {
    const res = await api.post('/api/groups/create', { name, repos });
    this.state.groups.push(res.group);
    this.renderTabs();
    this.dashboard.refresh();
    return res.group;
  },

  /** Rename a group or change its repositories: { name, add: [ids], remove: [ids] }. */
  async updateGroup(id, changes) {
    const res = await api.post('/api/groups/update', { id, ...changes });
    const group = this.group(id);
    if (!group) return res.group;
    group.name = res.group.name;
    group.repos = res.group.repos;
    // Repositories that left the group lose their tab in it.
    const gone = group.open_tabs.filter((tab) => !group.repos.includes(tab));
    if (group === this.activeGroup) {
      gone.forEach((tab) => this.closeTab(tab, { save: false }));
      if (gone.length) this.saveTabs();
    } else {
      group.open_tabs = group.open_tabs.filter((tab) => group.repos.includes(tab));
      if (!group.repos.includes(group.active_tab)) group.active_tab = null;
      this.dropUnusedViews();
    }
    this.renderTabs();
    this.dashboard.refresh();
    return group;
  },

  /**
   * Put the groups in a new order, given as group ids.
   * - The list changes at once and is saved in the background.
   * - If saving fails, the old order comes back.
   */
  async reorderGroups(ids) {
    const before = this.state.groups;
    const place = new Map(ids.map((id, i) => [id, i]));
    const spot = (g) => (place.has(g.id) ? place.get(g.id) : ids.length);
    this.state.groups = [...before].sort((a, b) => spot(a) - spot(b));
    this.dashboard.refresh();
    try {
      await api.post('/api/groups/order', { ids: this.state.groups.map((g) => g.id) });
    } catch (err) {
      this.state.groups = before.filter((g) => this.state.groups.includes(g));
      this.dashboard.refresh();
      toast('Could not save the group order', 'error', err.message);
    }
  },

  /** Delete a group. Its repositories stay in GitSense. */
  async deleteGroup(id) {
    await api.post('/api/groups/delete', { id });
    if (this.state.active_group === id) this.switchGroup(null);
    this.state.groups = this.state.groups.filter((g) => g.id !== id);
    this.dropUnusedViews();
    this.renderTabs();
    this.dashboard.refresh();
  },

  /** Ask for a name and create a group. Resolves the group, or null when cancelled. */
  async newGroupDialog({ repos = [], open = true } = {}) {
    const group = await formDialog({
      eyebrow: 'Groups',
      title: 'New group',
      intro: 'A group is a list of repositories you pick, with its own open tabs.',
      fields: [{ name: 'name', label: 'Name', placeholder: 'Firmware', required: true }],
      confirmLabel: 'Create group',
      submit: ({ name }) => this.createGroup(name, repos),
    });
    if (group && open) this.switchGroup(group.id);
    return group || null;
  },

  /** Put repositories in the group on screen, so anything added there shows up in it. */
  async _joinActiveGroup(ids) {
    const group = this.activeGroup;
    const fresh = group ? ids.filter((id) => !group.repos.includes(id)) : [];
    if (!fresh.length) return;
    try {
      const res = await api.post('/api/groups/update', { id: group.id, add: fresh });
      group.repos = res.group.repos;
    } catch (err) {
      // The repository itself was added, so carry on and say what is missing.
      toast(`Added to GitSense, but not to ${group.name}`, 'error', err.message);
    }
  },

  /** Close the views of repositories that no list has open any more. */
  dropUnusedViews() {
    const open = new Set([this.state, ...this.state.groups].flatMap((scope) => scope.open_tabs));
    for (const [id, view] of this.views) {
      if (id !== this.current && !open.has(id)) {
        view.destroy();
        this.views.delete(id);
      }
    }
  },

  /** Apply settings now and save them. Saves are sent one at a time, in order. */
  updateSettings(changes) {
    Object.assign(this.state.settings, changes);
    this.applyTheme();
    const ticket = (this._saveTicket = (this._saveTicket || 0) + 1);
    this._saving = (this._saving || Promise.resolve()).then(async () => {
      try {
        const res = await api.post('/api/settings', changes);
        // Only the newest save may overwrite local values with the server's.
        if (ticket === this._saveTicket) this.state.settings = res.settings;
      } catch (err) {
        toastError(err);
      }
    });
    return this._saving;
  },

  /** GitHub CLI status, fetched once: { installed, authenticated, user }. */
  github(force = false) {
    if (!this._github || force) {
      this._github = api.get('/api/github/status').catch(() => ({ installed: false, authenticated: false, user: null }));
      this._github.then(() => this.renderStatusRight());
    }
    return this._github;
  },

  /** Save the tabs of the list on screen. Saves are sent one at a time, in order. */
  saveTabs() {
    const group = this.activeGroup;
    const scope = this.scope;
    scope.active_tab = this.current === HOME ? null : this.current;
    const body = { open_tabs: [...scope.open_tabs], active: scope.active_tab, group: group ? group.id : null };
    this._tabSave = (this._tabSave || Promise.resolve()).then(() => api.post('/api/tabs', body).catch(() => {}));
  },

  // -- repositories -----------------------------------------------------------

  /** Add a repository by folder path and open it. Returns the repo entry. */
  async addRepo(path, { open = true } = {}) {
    const res = await api.post('/api/repos/add', { path });
    this._remember(res.repo);
    await this._joinActiveGroup([res.repo.id]);
    if (open) this.openRepo(res.repo.id);
    else this.dashboard.refresh();
    return res.repo;
  },

  /** Add several repositories without opening them. Returns { repos, failed }. */
  async addRepos(paths) {
    const res = await api.post('/api/repos/add_many', { paths });
    res.repos.forEach((r) => this._remember(r));
    await this._joinActiveGroup(res.repos.map((r) => r.id));
    this.dashboard.refresh();
    return res;
  },

  /** Clone a repository, add it and open it. */
  async cloneRepo({ url, parent, name }) {
    const res = await withActivity(`Cloning ${url}…`, api.post('/api/repos/clone', { url, parent, name }));
    this._remember(res.repo);
    await this._joinActiveGroup([res.repo.id]);
    this.openRepo(res.repo.id);
    return res.repo;
  },

  async initRepo(path) {
    const res = await api.post('/api/repos/init', { path });
    this._remember(res.repo);
    await this._joinActiveGroup([res.repo.id]);
    this.openRepo(res.repo.id);
    return res.repo;
  },

  _remember(repo) {
    if (!this.repo(repo.id)) this.state.repos.push(repo);
  },

  /** Remove a repository from GitSense. Files on disk are not touched. */
  async removeRepo(id) {
    await api.post('/api/repos/remove', { id });
    this.closeTab(id, { save: false });
    this.state.repos = this.state.repos.filter((r) => r.id !== id);
    // It also leaves every group, and the tabs of the lists that are not on screen.
    for (const scope of [this.state, ...this.state.groups]) {
      scope.open_tabs = scope.open_tabs.filter((tab) => tab !== id);
      if (scope.active_tab === id) scope.active_tab = null;
    }
    for (const group of this.state.groups) group.repos = group.repos.filter((r) => r !== id);
    this.dropUnusedViews();
    this.dirty.delete(id);
    this.saveTabs();
    this.renderTabs();
    this.dashboard.refresh();
  },

  async updateRepo(id, changes) {
    const res = await api.post('/api/repos/update', { id, ...changes });
    const entry = this.repo(id);
    if (entry) Object.assign(entry, res.repo);
    this.renderTabs();
    return res.repo;
  },

  /** Native folder chooser. Resolves a path, or null when cancelled or unsupported. */
  async pickFolder(start = '') {
    try {
      const res = await api.post('/api/pick_folder', { start: start || this.state.home });
      if (!res.supported) toast('No folder chooser is available here. Type the path instead.', 'info');
      return res.path || null;
    } catch (err) {
      toastError(err);
      return null;
    }
  },

  // -- tabs and views ---------------------------------------------------------

  openRepo(id) {
    if (!this.repo(id)) return;
    // A repository outside the group on screen opens in the full list instead.
    const group = this.activeGroup;
    if (group && !group.repos.includes(id)) this.switchGroup(null);
    if (!this.tabs.includes(id)) this.tabs.push(id);
    this.show(id);
  },

  closeTab(id, { save = true } = {}) {
    const tabs = this.tabs;
    const at = tabs.indexOf(id);
    if (at < 0) return;
    tabs.splice(at, 1);
    if (this.current === id) {
      this.views.get(id)?.deactivate();
      const next = tabs[at] || tabs[at - 1] || HOME;
      this.current = null;
      this.show(next);
    } else {
      this.renderTabs();
    }
    // Another list may still have this repository open, so its view may stay.
    this.dropUnusedViews();
    if (save) this.saveTabs();
  },

  showHome() {
    this.show(HOME);
  },

  show(target) {
    const host = document.getElementById('view');
    if (this.current === target) return;
    if (this.current === HOME) this.dashboard.deactivate();
    else if (this.current) this.views.get(this.current)?.deactivate();
    this.current = target;
    clear(host);
    if (target === HOME) {
      host.append(this.dashboard.el);
      this.dashboard.activate();
      document.title = 'GitSense';
      setStatus(`${this.scopeRepos().length} repositories`);
    } else {
      let view = this.views.get(target);
      if (!view) {
        view = new RepoView(this, this.repo(target));
        this.views.set(target, view);
      }
      host.append(view.el);
      view.activate();
      document.title = `${this.repo(target).name} - GitSense`;
    }
    this.renderTabs();
    this.saveTabs();
  },

  setDirty(id, dirty) {
    const had = this.dirty.has(id);
    if (dirty === had) return;
    if (dirty) this.dirty.add(id);
    else this.dirty.delete(id);
    this.renderTabs();
  },

  renderTabs() {
    // Redrawing would drop the tab being dragged; it is redrawn when the drag ends.
    if (this._tabDrag) return;
    const nav = clear(document.getElementById('tabs'));
    const group = this.activeGroup;
    const home = h('button', {
      class: `tab tab-home ${this.current === HOME ? 'active' : ''}`,
      type: 'button',
      title: group ? `Group: ${group.name}` : 'All repositories',
      onclick: () => this.show(HOME),
      oncontextmenu: (ev) => {
        ev.preventDefault();
        this.groupMenu({ x: ev.clientX, y: ev.clientY });
      },
    }, icon(group ? 'folders' : 'layout-grid', 15), h('span', { class: 'tab-label' }, group ? group.name : 'Repositories'));
    // With groups, the home tab also switches between them.
    if (this.state.groups.length) {
      home.append(h('span', {
        class: 'tab-switch', role: 'button', 'aria-label': 'Switch group', title: 'Switch group',
        onclick: (ev) => { ev.stopPropagation(); this.groupMenu(home); },
      }, icon('chevron-down', 13)));
    }
    nav.append(home);
    for (const id of this.tabs) {
      const repo = this.repo(id);
      if (!repo) continue;
      const tab = h('button', {
        class: `tab ${this.current === id ? 'active' : ''}`,
        type: 'button',
        title: repo.path,
        dataset: { id },
        onclick: () => this.show(id),
        onpointerdown: (ev) => this.startTabDrag(ev, tab),
        onauxclick: (ev) => { if (ev.button === 1) this.closeTab(id); },
        oncontextmenu: (ev) => {
          ev.preventDefault();
          showMenu({ x: ev.clientX, y: ev.clientY }, [
            { label: 'Close tab', icon: 'x', onClick: () => this.closeTab(id) },
            { label: 'Close other tabs', icon: 'layers', onClick: () => this.closeOthers(id) },
          ]);
        },
      },
      icon('folder-git-2', 14),
      h('span', { class: 'tab-label' }, repo.name),
      this.dirty.has(id) ? h('span', { class: 'tab-dot', title: 'Uncommitted changes' }) : null,
      h('span', {
        class: 'tab-close', role: 'button', 'aria-label': `Close ${repo.name}`,
        onclick: (ev) => { ev.stopPropagation(); this.closeTab(id); },
      }, icon('x', 12)));
      nav.append(tab);
    }
    nav.querySelector('.tab.active')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  },

  /**
   * Drag a repository tab sideways to reorder the tabs of the list on screen.
   * - The drag starts after a few pixels, so a plain click still just opens the tab.
   * - The tab follows the pointer and swaps places as it passes the middle of a neighbour.
   * - Uses pointer events, not HTML drag and drop, so it works the same in every web view.
   */
  startTabDrag(ev, tab) {
    if (ev.button !== 0 || ev.target.closest('.tab-close')) return;
    const nav = tab.parentElement;
    const startX = ev.clientX;
    const grab = startX - tab.getBoundingClientRect().left;
    const middle = (el) => { const r = el.getBoundingClientRect(); return r.left + r.width / 2; };
    const isRepoTab = (el) => Boolean(el && el.dataset.id);
    const move = (e) => {
      if (!this._tabDrag) {
        if (Math.abs(e.clientX - startX) < 6) return;
        this._tabDrag = true;
        tab.classList.add('dragging');
      }
      tab.style.transform = '';
      const prev = tab.previousElementSibling;
      const next = tab.nextElementSibling;
      if (isRepoTab(prev) && e.clientX < middle(prev)) nav.insertBefore(tab, prev);
      else if (isRepoTab(next) && e.clientX > middle(next)) nav.insertBefore(next, tab);
      // Keep the tab under the pointer, but never over the home tab or past the last tab.
      const tabs = [...nav.querySelectorAll('.tab[data-id]')];
      const low = tabs[0].getBoundingClientRect().left;
      const high = tabs[tabs.length - 1].getBoundingClientRect().right - tab.offsetWidth;
      const natural = tab.getBoundingClientRect().left;
      const wanted = Math.max(low, Math.min(high, e.clientX - grab));
      tab.style.transform = `translateX(${wanted - natural}px)`;
    };
    const end = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
      window.removeEventListener('pointercancel', end);
      if (!this._tabDrag) return;
      this._tabDrag = false;
      const order = [...nav.querySelectorAll('.tab[data-id]')].map((el) => el.dataset.id);
      const changed = order.some((id, i) => id !== this.tabs[i]);
      if (changed) this.scope.open_tabs = order.filter((id) => this.tabs.includes(id));
      this.renderTabs();
      if (changed) this.saveTabs();
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', end);
  },

  closeOthers(id) {
    for (const other of [...this.tabs]) {
      if (other !== id) this.closeTab(other, { save: false });
    }
    this.saveTabs();
  },

  /** Menu for moving between "All repositories" and the groups. */
  groupMenu(anchor) {
    const active = this.activeGroup;
    showMenu(anchor, [
      { heading: 'Show' },
      {
        label: 'All repositories', icon: active ? 'layout-grid' : 'check', hint: String(this.state.repos.length),
        onClick: () => this.switchGroup(null, { home: false }),
      },
      ...this.state.groups.map((g) => ({
        label: g.name,
        icon: active && active.id === g.id ? 'check' : 'folders',
        hint: String(g.repos.length),
        onClick: () => this.switchGroup(g.id, { home: false }),
      })),
      { sep: true },
      { label: 'New group…', icon: 'plus', onClick: () => this.newGroupDialog() },
    ]);
  },

  // -- chrome -----------------------------------------------------------------

  // -- themes -----------------------------------------------------------------

  /** The theme on screen: the chosen one, or the light or dark pick when following the system. */
  theme() {
    return activeTheme(this.state.settings);
  },

  /** Repaint the page from the saved settings. Also undoes any live preview. */
  applyTheme() {
    const theme = this.theme();
    paintTheme(theme);
    this.renderTopbarActions();
    this.rememberTheme(theme);
  },

  /** Tell the server which colors are on screen, so the next launch starts in them. */
  rememberTheme(theme) {
    const picked = this.state.settings.theme;
    const record = JSON.stringify({ kind: theme.kind, colors: theme.colors, follow_system: !picked || picked === 'system' });
    if (record === this._paintedTheme) return;
    this._paintedTheme = record;
    api.post('/api/ui/theme', JSON.parse(record)).catch(() => {});
  },

  /** Switch theme. id is a theme id, or "system" to follow the OS light/dark setting. */
  setTheme(id) {
    return this.updateSettings({ theme: id });
  },

  /** Add a custom theme, or replace the one with the same id. */
  saveCustomTheme(theme) {
    const entry = { id: theme.id, name: theme.name, kind: theme.kind, colors: { ...theme.colors } };
    if (theme.based_on) entry.based_on = theme.based_on;
    const list = customThemes(this.state.settings).filter((t) => t.id !== theme.id);
    return this.updateSettings({ custom_themes: [...list, entry] });
  },

  /** Delete a custom theme. Anything that was using it goes back to the default theme. */
  deleteCustomTheme(id) {
    const s = this.state.settings;
    const changes = { custom_themes: customThemes(s).filter((t) => t.id !== id) };
    if (s.theme === id) changes.theme = DEFAULT_THEME;
    if (s.theme_light === id) changes.theme_light = DEFAULT_LIGHT;
    if (s.theme_dark === id) changes.theme_dark = DEFAULT_DARK;
    return this.updateSettings(changes);
  },

  /**
   * Switch a mod on or off, then reload the page.
   * - Mod stylesheets, splash and icon are part of the page, so only a reload shows the change.
   * - Switching one on also puts its default themes in place.
   */
  async setMod(id, enabled) {
    await this._saving;
    const res = await api.post('/api/mods/set', { id, enabled });
    this.state.settings = res.settings;
    this.state.mods = res.mods;
    window.location.reload();
  },

  themeMenu(anchor) {
    const s = this.state.settings;
    const current = this.theme();
    const following = !s.theme || s.theme === 'system';
    showMenu(anchor, [
      { heading: 'Theme' },
      { label: 'Match the system', icon: following ? 'check' : 'monitor', hint: following ? current.name : '', onClick: () => this.setTheme('system') },
      { sep: true },
      ...allThemes(s).map((t) => ({
        label: t.name,
        icon: !following && t.id === current.id ? 'check' : t.kind === 'dark' ? 'moon' : 'sun',
        hint: t.mod || (t.builtin ? '' : 'custom'),
        onClick: () => this.setTheme(t.id),
      })),
      { sep: true },
      { label: 'Customize themes…', icon: 'palette', onClick: () => openSettings(this, { section: 'appearance' }) },
    ]);
  },

  renderTopbarActions() {
    const host = document.getElementById('topbar-actions');
    if (!host) return;
    const themeButton = iconButton('palette', `Theme: ${this.theme().name}`, () => this.themeMenu(themeButton), { class: 'lg', size: 17 });
    clear(host).append(
      themeButton,
      iconButton('settings', 'Settings', () => openSettings(this), { class: 'lg', size: 17 }),
    );
  },

  async renderStatusRight() {
    const host = document.getElementById('status-right');
    const gh = this._github ? await this._github : null;
    clear(host);
    if (gh) {
      host.append(h('span', { title: gh.authenticated ? 'Signed in through the GitHub CLI' : 'Run `gh auth login` to enable GitHub features' },
        gh.authenticated ? `GitHub: ${gh.user}` : gh.installed ? 'GitHub: not signed in' : 'GitHub CLI not installed'));
    }
    host.append(h('span', null, `GitSense ${this.state.version}`));
  },

  onKey(ev) {
    const mod = ev.metaKey || ev.ctrlKey;
    // Never let the browser act on this one: it would close the whole window.
    if (mod && ev.key === 'w') ev.preventDefault();
    if (document.querySelector('.modal-backdrop')) return;
    if (mod && ev.key === 'w') {
      if (this.current !== HOME) this.closeTab(this.current);
    } else if (mod && ev.key === ',') {
      ev.preventDefault();
      openSettings(this);
    } else if (mod && /^[1-9]$/.test(ev.key)) {
      const n = Number(ev.key);
      const target = n === 1 ? HOME : this.tabs[n - 2];
      if (target) { ev.preventDefault(); this.show(target); }
    } else if (this.current !== HOME) {
      this.views.get(this.current)?.onKey(ev);
    }
  },
};

// Called by the native window when Cmd+W is pressed (see cli.py).
window.gitsense = {
  closeActiveTab() {
    if (document.querySelector('.modal-backdrop') || app.current === HOME || !app.current) return false;
    app.closeTab(app.current);
    return true;
  },
};

app.init().catch((err) => {
  hideSplash();
  document.getElementById('view').append(
    h('div', { class: 'empty' }, icon('circle-alert', 32), h('p', { class: 'heading' }, 'GitSense could not start'), h('p', null, err.message)));
});
