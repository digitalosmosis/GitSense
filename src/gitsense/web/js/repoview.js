// One repository tab: toolbar, sidebar, graph, detail panel and diff viewer.

import { api } from './api.js';
import { h, clear, icon, button, splitter, debounce, copyText, shortSha, relTime, fullDate } from './util.js';
import { toast, toastError, withActivity, showMenu, setStatus, openModal } from './ui.js';
import { Graph } from './graph.js';
import { Sidebar } from './sidebar.js';
import { Detail } from './detail.js';
import { DiffView } from './diffview.js';
import * as ops from './ops.js';

const POLL_MS = 3000;

// Network actions: their toolbar button shows them running, and right-click cancels them.
const NETWORK = new Set(['fetch', 'pull', 'push']);
const NOUNS = { fetch: 'Fetch', pull: 'Pull', push: 'Push' };

let opCounter = 0;
const newOpId = () => `${Date.now().toString(36)}-${(opCounter += 1).toString(36)}`;

const LABELS = {
  fetch: 'Fetching',
  pull: 'Pulling',
  push: 'Pushing',
  commit: 'Committing',
  merge: 'Merging',
  rebase: 'Rebasing',
  checkout: 'Checking out',
  checkout_remote: 'Checking out',
  stash_save: 'Stashing',
};

export class RepoView {
  constructor(app, repo) {
    this.app = app;
    this.repo = repo;
    this.id = repo.id;
    this.data = null; // GET /api/repo/overview
    this.log = null; // GET /api/repo/log
    this.refsBySha = new Map();
    this.selected = null; // a commit SHA, "WIP", or null before the first load
    this.diffTarget = null; // { file, ctx } while a diff is open
    this.limit = app.settings.commit_limit;
    this.active = false;
    this.busy = 0;
    this.pending = new Map(); // op id -> { action, cancelling } for fetch, pull and push in flight
    this.refreshing = null;
    this.again = false;
    this.pollTimer = null;
    this.lastFetch = Date.now();

    this.sidebar = new Sidebar(this);
    this.graph = new Graph(this);
    this.graph.lineageOn = app.settings.highlight_lineage !== false;
    this.detail = new Detail(this);
    this.diff = new DiffView(this);

    this.toolbarLeft = h('div', { style: { display: 'contents' } });
    this.searchCount = h('span', { class: 'tb-search-count' });
    this.searchInput = h('input', {
      class: 'input', type: 'text', placeholder: 'Find commits', spellcheck: 'false', 'aria-label': 'Find commits',
      oninput: debounce(() => this.applyFilter(), 140),
      onkeydown: (ev) => {
        if (ev.key === 'Enter') { ev.preventDefault(); this.graph.stepMatch(ev.shiftKey ? -1 : 1); }
        else if (ev.key === 'Escape') { ev.stopPropagation(); this.searchInput.value = ''; this.applyFilter(); this.searchInput.blur(); }
      },
    });
    this.toolbar = h('div', { class: 'toolbar', role: 'toolbar', 'aria-label': 'Repository actions' },
      this.toolbarLeft,
      h('div', { class: 'tb-spacer' }),
      h('div', { class: 'tb-search' }, h('div', { class: 'search' }, icon('search', 13), this.searchInput), this.searchCount));

    this.center = h('div', { class: 'center' },
      h('div', { class: 'empty' }, icon('loader-circle', 24, 'spin'), h('p', null, `Opening ${repo.name}`)));
    this.sidebar.el.style.width = `${app.settings.sidebar_width}px`;
    this.detail.el.style.width = `${app.settings.detail_width}px`;
    this.workspace = h('div', { class: 'workspace' },
      this.sidebar.el,
      splitter(() => this.sidebar.el.offsetWidth, (w) => { this.sidebar.el.style.width = `${w}px`; },
        { min: 170, max: 480, onDone: (w) => app.updateSettings({ sidebar_width: w }) }),
      this.center,
      splitter(() => this.detail.el.offsetWidth, (w) => { this.detail.el.style.width = `${w}px`; },
        { min: 280, max: 720, invert: true, onDone: (w) => app.updateSettings({ detail_width: w }) }),
      this.detail.el);
    this.el = h('div', { class: 'repo-view' }, this.toolbar, this.workspace);
    this.onFocus = () => { if (this.active) this.poll(true); };
    this.onSettings = (ev) => {
      const changed = ev.detail || {};
      if ('commit_limit' in changed) this.limit = Number(changed.commit_limit) || this.limit;
      if ('commit_limit' in changed || 'show_remote_branches_in_graph' in changed) this.refresh();
      if ('highlight_lineage' in changed) this.graph.setLineage(changed.highlight_lineage !== false);
      if ('diff_view' in changed && this.diffTarget && this.diff.data) { this.diff.renderHead(); this.diff.renderBody(); }
    };
  }

  // -- lifecycle ----------------------------------------------------------------

  activate() {
    this.active = true;
    window.addEventListener('focus', this.onFocus);
    document.addEventListener('visibilitychange', this.onFocus);
    window.addEventListener('gitsense:settings', this.onSettings);
    this.limit = Math.max(this.limit, this.app.settings.commit_limit);
    this.refresh().then(() => this.graph.schedule());
    this.schedulePoll();
  }

  deactivate() {
    this.active = false;
    window.removeEventListener('focus', this.onFocus);
    document.removeEventListener('visibilitychange', this.onFocus);
    window.removeEventListener('gitsense:settings', this.onSettings);
    clearTimeout(this.pollTimer);
  }

  destroy() {
    this.deactivate();
    this.graph.destroy();
  }

  schedulePoll() {
    clearTimeout(this.pollTimer);
    if (!this.active) return;
    this.pollTimer = setTimeout(() => this.poll(false), POLL_MS);
  }

  /** Check the repository's fingerprint and refresh when it changed on disk. */
  async poll(now) {
    clearTimeout(this.pollTimer);
    if (!this.active) return;
    const idle = !document.hidden && !this.busy && !this.refreshing && !document.querySelector('.modal-backdrop');
    if (idle && this.data) {
      const started = performance.now();
      try {
        const res = await api.get('/api/repo/fingerprint', { id: this.id });
        if (res.fingerprint !== this.data.fingerprint && !this.busy) await this.refresh();
      } catch { /* the next poll tries again */ }
      this.pollCost = performance.now() - started;
      this.maybeAutoFetch();
    } else if (idle && !this.data && now) {
      await this.refresh();
    }
    if (this.active) {
      // Slow repositories are polled less often.
      this.pollTimer = setTimeout(() => this.poll(false), Math.max(POLL_MS, (this.pollCost || 0) * 12));
    }
  }

  maybeAutoFetch() {
    const minutes = Number(this.app.settings.auto_fetch_minutes) || 0;
    if (!minutes || !this.data || !this.data.remotes.length) return;
    if (Date.now() - this.lastFetch < minutes * 60000) return;
    this.lastFetch = Date.now();
    this.run('fetch', {}, { label: 'Fetching in the background', quiet: true, silent: true });
  }

  // -- data ---------------------------------------------------------------------

  /** Reload everything from disk and re-render. Calls made while one runs are merged. */
  refresh() {
    if (this.refreshing) {
      this.again = true;
      return this.refreshing;
    }
    this.refreshing = (async () => {
      try {
        do {
          this.again = false;
          const [data, log] = await Promise.all([
            api.get('/api/repo/overview', { id: this.id }),
            api.get('/api/repo/log', { id: this.id, limit: this.limit }),
          ]);
          this.data = data;
          this.log = log;
          this.buildRefs();
          this.renderAll();
        } while (this.again);
      } catch (err) {
        this.showError(err);
      } finally {
        this.refreshing = null;
      }
    })();
    return this.refreshing;
  }

  buildRefs() {
    const map = new Map();
    const add = (sha, ref) => {
      if (!map.has(sha)) map.set(sha, []);
      map.get(sha).push(ref);
    };
    const { head, branches, remotes, tags } = this.data;
    if (head.detached && head.sha) add(head.sha, { type: 'detached', name: 'HEAD' });
    for (const b of branches) add(b.sha, { type: 'local', name: b.name, current: b.current, data: b });
    for (const remote of remotes) {
      for (const b of remote.branches) add(b.sha, { type: 'remote', name: b.full, branch: b.name, remote: b.remote, data: b });
    }
    for (const t of tags) add(t.sha, { type: 'tag', name: t.name, data: t });
    this.refsBySha = map;
  }

  hasWip() {
    return Boolean(this.log && this.log.commits.length && this.log.commits[0].kind === 'wip');
  }

  renderAll() {
    const head = this.data.head;
    this.graph.setData(this.log);

    // Keep the selection valid as rows come and go.
    const exists = (sha) => sha === 'WIP' ? this.hasWip() : this.graph.index.has(sha);
    if (this.selected == null || !exists(this.selected)) {
      if (this.hasWip() && (this.selected == null || !head.sha)) this.selected = 'WIP';
      else if (head.sha && this.graph.index.has(head.sha)) this.selected = head.sha;
      else this.selected = 'WIP'; // empty repository: the panel shows "Nothing to commit"
    }
    this.pendingSelect = null;

    if (!this.graphShown) {
      clear(this.center).append(this.graph.el);
      this.graphShown = true;
    }
    this.graph.setSelected(this.selected);
    if (this.searchInput.value) this.applyFilter();
    this.renderToolbar();
    this.sidebar.render();
    this.reconcileDiff();
    this.detail.render();

    const st = this.data.status;
    const dirty = st.staged.length + st.unstaged.length + st.conflicted.length;
    this.app.setDirty(this.id, dirty > 0);
    if (this.active) {
      setStatus(`${this.repo.path}  ·  ${head.branch || (head.sha ? `detached at ${shortSha(head.sha)}` : 'no commits yet')}  ·  ${dirty ? `${dirty} changed` : 'clean'}`);
    }
  }

  /** Keep an open working-directory diff pointed at the file's current state. */
  reconcileDiff() {
    const target = this.diffTarget;
    if (!target) return;
    const { file, ctx } = target;
    if (ctx.kind === 'commit') return;
    const st = this.data.status;
    const lists = { unstaged: st.unstaged, staged: st.staged, conflicted: st.conflicted };
    const find = (kind) => lists[kind].find((f) => f.path === file.path);
    let kind = ctx.kind;
    let now = find(kind);
    if (!now) {
      kind = ['conflicted', 'unstaged', 'staged'].find((k) => k !== ctx.kind && find(k));
      now = kind ? find(kind) : null;
    }
    if (!now) {
      this.closeDiff();
      return;
    }
    this.diffTarget = { file: now, ctx: { kind } };
    this.diff.open(now, { kind });
  }

  showError(err) {
    // Leave no half-open diff behind, or the centre stays blank after recovery.
    this.diffTarget = null;
    this.diff.el.remove();
    this.graph.el.classList.remove('hidden');
    this.graphShown = false;
    clear(this.center).append(h('div', { class: 'empty' },
      icon('circle-alert', 30),
      h('p', { class: 'heading' }, 'This repository could not be opened'),
      h('p', { class: 'selectable' }, err.message),
      h('div', { class: 'row' },
        button('Try again', () => this.refresh(), { class: 'primary' }),
        button('Remove from GitSense', () => this.app.removeRepo(this.id), { class: 'ghost' }))));
  }

  // -- actions ------------------------------------------------------------------

  /** Run a git action. Throws ApiError; the caller refreshes. */
  async exec(action, args = {}, label = null) {
    const op = NETWORK.has(action) ? newOpId() : null;
    this.busy += 1;
    if (op) this.setPending(op, { action, cancelling: false });
    try {
      return await withActivity(`${label || LABELS[action] || 'Working'}…`, api.post('/api/repo/action', { id: this.id, action, args, op }));
    } catch (err) {
      if (err && err.cancelled) err.action = action;
      throw err;
    } finally {
      this.busy -= 1;
      if (op) this.setPending(op, null);
    }
  }

  setPending(op, entry) {
    if (entry) this.pending.set(op, entry);
    else this.pending.delete(op);
    if (this.data) this.renderToolbar();
  }

  /** The fetch, pull or push of this kind in flight, oldest first. */
  pendingOps(action) {
    return [...this.pending].filter(([, p]) => p.action === action);
  }

  /** Ask the server to stop every fetch, pull or push of this kind. */
  async cancelPending(action) {
    const list = this.pendingOps(action).filter(([, p]) => !p.cancelling);
    if (!list.length) return;
    for (const [op, p] of list) this.setPending(op, { ...p, cancelling: true });
    const results = await Promise.all(list.map(([op]) => api.post('/api/repo/cancel', { id: this.id, op }).catch(() => null)));
    const refused = results.some((r) => r && r.cancelled === false);
    if (refused) {
      // Only a pull refuses: it is past its fetch and already updating the branch.
      for (const [op, p] of list) if (this.pending.has(op)) this.setPending(op, { ...p, cancelling: false });
      toast(`The ${NOUNS[action].toLowerCase()} is already updating your branch, so it will finish`, 'info');
    }
  }

  /**
   * Run a git action, refresh, and report the outcome.
   * - Returns the result, or null when it failed (the error is shown as a toast).
   * - opts: label, success (toast text), quiet (no success toast), silent (no error toast), select: "head"
   */
  async run(action, args = {}, opts = {}) {
    try {
      const res = await this.exec(action, args, opts.label);
      await this.afterAction(res, opts.quiet ? null : opts.success, opts);
      return res;
    } catch (err) {
      if (opts.silent) await this.refresh();
      else this.fail(err);
      return null;
    }
  }

  async afterAction(res, success, opts = {}) {
    if (opts.select) this.pendingSelect = opts.select;
    await this.refresh();
    if (res && res.conflict) {
      toast('Conflicts need to be resolved before this can finish', 'info', (res.output || '').split('\n').filter((l) => l.startsWith('CONFLICT')).join('\n'));
      if (this.hasWip()) this.select('WIP');
    } else {
      if (success) toast(success, 'ok');
      if (opts.select === 'head' && !this.hasWip() && this.data.head.sha) this.select(this.data.head.sha, { scroll: true });
    }
  }

  fail(err) {
    if (err && err.cancelled) {
      // A push stopped late may already have reached the remote; the next fetch shows it.
      const more = err.action === 'push' ? 'If it had already reached the remote, the next fetch shows it.' : '';
      toast(`${NOUNS[err.action] || 'Action'} cancelled`, 'info', more);
    } else {
      toastError(err);
    }
    return this.refresh();
  }

  openUrl(url) {
    api.post('/api/open_url', { url }).catch(toastError);
  }

  openPath(what, path = null) {
    api.post('/api/repo/open', { id: this.id, what, path }).catch(toastError);
  }

  loadMore() {
    this.limit = Math.min(this.limit * 2, 50000);
    return this.refresh();
  }

  // -- selection and diffs ------------------------------------------------------

  select(sha, opts = {}) {
    if (!this.data) return;
    if (sha !== 'WIP' && !this.graph.index.has(sha)) {
      toast(this.log.has_more ? 'That commit is older than the loaded history. Scroll down and load more commits.' : 'That commit is not in the graph.', 'info');
      return;
    }
    const changed = this.selected !== sha;
    this.selected = sha;
    if (changed && this.diffTarget) this.closeDiff({ render: false });
    this.graph.setSelected(sha);
    if (opts.scroll) this.graph.scrollTo(sha);
    if (changed) this.detail.render();
  }

  openDiff(file, ctx) {
    this.diffTarget = { file, ctx };
    if (!this.diff.el.isConnected) {
      this.graph.el.classList.add('hidden');
      this.center.append(this.diff.el);
    }
    this.diff.open(file, ctx);
    this.detail.highlightFile();
  }

  closeDiff({ render = true } = {}) {
    if (!this.diffTarget && !this.diff.el.isConnected) return;
    this.diffTarget = null;
    this.diff.el.remove();
    this.graph.el.classList.remove('hidden');
    this.graph.schedule();
    if (render) this.detail.highlightFile();
  }

  applyFilter() {
    const count = this.graph.setFilter(this.searchInput.value);
    this.searchCount.textContent = count == null ? '' : `${count} found`;
  }

  async showFileHistory(path) {
    let commits;
    try {
      commits = (await withActivity('Loading file history…', api.get('/api/repo/file_history', { id: this.id, path }))).commits;
    } catch (err) {
      toastError(err);
      return;
    }
    openModal({
      eyebrow: 'File history',
      title: path.split('/').pop(),
      wide: true,
      build: (close) => ({
        body: [
          h('p', { class: 'muted selectable' }, path),
          commits.length
            ? h('div', { class: 'history-list' }, commits.map((c) => h('div', {
              class: 'history-row', tabindex: '0', title: 'Show this commit',
              onclick: () => {
                close();
                if (this.graph.index.has(c.sha)) {
                  this.select(c.sha, { scroll: true });
                  this.openDiff({ path: c.path || path, status: 'M' }, { kind: 'commit', sha: c.sha });
                } else {
                  this.select(c.sha);
                }
              },
            },
            h('span', { class: 'sha' }, shortSha(c.sha)),
            h('span', { class: 'ellipsis' }, c.subject),
            h('span', { class: 'who ellipsis' }, c.author),
            h('span', { class: 'when', title: fullDate(c.date) }, relTime(c.date)))))
            : h('div', { class: 'empty' }, 'No commits touch this file yet'),
        ],
        foot: [button('Close', () => close(), { class: 'primary' })],
      }),
    });
  }

  // -- toolbar ------------------------------------------------------------------

  renderToolbar() {
    const { head, status: st, github, stashes, remotes, branches } = this.data;
    const dirty = st.staged.length + st.unstaged.length > 0;
    const tb = (name, label, title, onClick, opts = {}) => h('button', {
      class: 'tb-btn', type: 'button', title, disabled: opts.disabled, onclick: onClick,
    }, icon(name, 17), label, opts.badge ? h('span', { class: `badge ${opts.badgeClass || 'accent'}` }, opts.badge) : null);
    // A fetch, pull or push button: busy with a spinner while its action runs, right-click cancels.
    const netButton = (action, name, label, title, onClick, opts = {}) => {
      const ops_ = this.pendingOps(action);
      if (!ops_.length) return tb(name, label, title, onClick, opts);
      const cancelling = ops_.every(([, p]) => p.cancelling);
      const verb = LABELS[action];
      const el = h('button', {
        class: 'tb-btn busy', type: 'button', 'aria-busy': 'true', 'aria-pressed': 'true',
        title: cancelling ? 'Cancelling…' : `${verb}… Right-click to cancel`,
        onclick: () => {}, // already running: another click would only queue a second one
        oncontextmenu: (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          showMenu({ x: ev.clientX, y: ev.clientY }, [
            { heading: `${verb}…` },
            { label: cancelling ? 'Cancelling…' : `Cancel ${NOUNS[action].toLowerCase()}`, icon: 'x', disabled: cancelling, onClick: () => this.cancelPending(action) },
          ]);
        },
      }, icon('loader-circle', 17, 'spin'), label);
      return el;
    };

    const repoButton = h('button', {
      class: 'tb-context', type: 'button', title: this.repo.path,
      onclick: () => showMenu(repoButton, [
        { heading: this.repo.path },
        { label: 'Open in terminal', icon: 'terminal', onClick: () => this.openPath('terminal') },
        { label: 'Show in folder', icon: 'folder-open', onClick: () => this.openPath('folder') },
        { label: 'Open in editor', icon: 'square-pen', onClick: () => this.openPath('editor') },
        github ? { label: 'Open on GitHub', icon: 'external-link', onClick: () => this.openUrl(github.url) } : null,
        { sep: true },
        { label: 'Copy path', icon: 'copy', onClick: async () => { if (await copyText(this.repo.path)) toast('Path copied', 'ok'); } },
        { label: 'Refresh', icon: 'refresh-cw', hint: 'F5', onClick: () => this.refresh() },
      ]),
    },
    h('span', { class: 'eyebrow' }, 'Repository'),
    h('span', { class: 'tb-context-value' }, h('span', { class: 'ellipsis' }, this.repo.name), icon('chevron-down', 12)));

    const branchLabel = head.branch || (head.sha ? `Detached at ${shortSha(head.sha)}` : 'No commits yet');
    const branchButton = h('button', {
      class: 'tb-context', type: 'button', title: 'Switch branch',
      onclick: () => {
        const recent = [...branches].sort((a, b) => b.date - a.date).slice(0, 25);
        showMenu(branchButton, [
          { heading: 'Switch to' },
          ...recent.map((b) => ({
            label: b.name, icon: b.current ? 'check' : 'git-branch', disabled: b.current,
            hint: [b.ahead ? `↑${b.ahead}` : '', b.behind ? `↓${b.behind}` : ''].join(' ').trim(),
            onClick: () => ops.checkout(this, 'checkout', { ref: b.name }, b.name),
          })),
          branches.length > recent.length ? { heading: `${branches.length - recent.length} more in the sidebar` } : null,
          { sep: true },
          { label: 'Create branch…', icon: 'plus', onClick: () => ops.createBranch(this) },
        ]);
      },
    },
    h('span', { class: 'eyebrow' }, 'Branch'),
    h('span', { class: 'tb-context-value' }, icon(head.branch ? 'git-branch' : 'circle-dot', 13), h('span', { class: 'ellipsis' }, branchLabel), icon('chevron-down', 12)));

    const hasRemote = remotes.length > 0;
    const pullBusy = this.pendingOps('pull').length > 0;
    const pullButton = netButton('pull', 'arrow-down-to-line', 'Pull', st.upstream ? `Pull from ${st.upstream}` : 'This branch has no upstream to pull from',
      () => ops.pull(this), { disabled: !st.upstream, badge: st.behind || null, badgeClass: 'blue' });
    const pullMore = h('button', {
      class: 'tb-btn split', type: 'button', title: 'Choose how to pull', 'aria-label': 'Choose how to pull', disabled: !st.upstream || pullBusy,
      onclick: () => showMenu(pullMore, [
        { heading: 'Pull' },
        { label: 'Fast-forward only', icon: 'arrow-down-to-line', hint: this.app.settings.pull_mode === 'ff-only' ? 'default' : '', onClick: () => ops.pull(this, 'ff-only') },
        { label: 'Rebase my commits on top', icon: 'git-graph', hint: this.app.settings.pull_mode === 'rebase' ? 'default' : '', onClick: () => ops.pull(this, 'rebase') },
        { label: 'Merge', icon: 'git-merge', hint: this.app.settings.pull_mode === 'merge' ? 'default' : '', onClick: () => ops.pull(this, 'merge') },
      ]),
    }, icon('chevron-down', 12));

    clear(this.toolbarLeft).append(
      repoButton,
      branchButton,
      h('div', { class: 'tb-group' },
        netButton('fetch', 'refresh-cw', 'Fetch', hasRemote ? 'Fetch all remotes' : 'Add a remote to fetch', () => {
          this.lastFetch = Date.now();
          this.run('fetch', {}, { success: 'Fetched' });
        }, { disabled: !hasRemote }),
        h('div', { class: 'tb-pair' }, pullButton, pullMore),
        netButton('push', 'arrow-up-from-line', 'Push', !hasRemote ? 'Add a remote to push' : st.upstream ? `Push to ${st.upstream}` : 'Push and set the upstream branch',
          () => ops.pushBranch(this), { disabled: !hasRemote || !head.branch, badge: st.ahead || null })),
      h('div', { class: 'tb-group' },
        tb('git-branch', 'Branch', 'Create a branch', () => ops.createBranch(this), { disabled: head.unborn }),
        tb('archive', 'Stash', 'Stash uncommitted changes', () => ops.stashSave(this), { disabled: !dirty }),
        tb('archive-restore', 'Pop', 'Apply the newest stash and remove it', () => ops.popStash(this, null), { disabled: !stashes.length })),
      h('div', { class: 'tb-group' },
        tb('terminal', 'Terminal', 'Open a terminal in this repository', () => this.openPath('terminal')),
        github ? tb('external-link', 'GitHub', `Open ${github.slug} on GitHub`, () => this.openUrl(github.url)) : null));
  }

  // -- keyboard -----------------------------------------------------------------

  onKey(ev) {
    const mod = ev.metaKey || ev.ctrlKey;
    if (ev.key === 'Escape' && this.diffTarget && !document.querySelector('.menu')) {
      ev.preventDefault();
      this.closeDiff();
    } else if (ev.key === 'Escape' && !document.querySelector('.menu, .modal') && this.detail.clearMulti()) {
      ev.preventDefault();
    } else if (mod && ev.key === 'Enter') {
      ev.preventDefault();
      if (this.selected === 'WIP') this.detail.commit();
    } else if (mod && ev.key === 'f') {
      ev.preventDefault();
      this.searchInput.focus();
      this.searchInput.select();
    } else if (ev.key === 'F5') {
      ev.preventDefault();
      this.refresh();
    }
  }
}
