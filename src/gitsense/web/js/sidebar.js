// Left sidebar: local branches, remotes, pull requests, tags and stashes.

import { h, clear, icon, iconButton, debounce, relTime } from './util.js';
import * as ops from './ops.js';
import { renderPullRequests, createPullRequestDialog } from './github.js';

const TAG_LIMIT = 150;

/** Group names like "feature/login" into a folder tree. */
function buildTree(items, nameOf) {
  const root = { folders: new Map(), leaves: [] };
  for (const item of items) {
    const parts = nameOf(item).split('/');
    let node = root;
    for (const part of parts.slice(0, -1)) {
      if (!node.folders.has(part)) node.folders.set(part, { folders: new Map(), leaves: [] });
      node = node.folders.get(part);
    }
    node.leaves.push({ label: parts[parts.length - 1], item });
  }
  return root;
}

export class Sidebar {
  constructor(view) {
    this.view = view;
    this.filter = '';
    this.collapsed = new Set(this.load());
    this.closedFolders = new Set();
    this.showAllTags = false;
    this.prCount = null;
    this.filterInput = h('input', {
      class: 'input', type: 'text', placeholder: 'Filter branches and tags', spellcheck: 'false',
      'aria-label': 'Filter branches and tags',
      oninput: debounce(() => { this.filter = this.filterInput.value.trim().toLowerCase(); this.render(); }, 120),
    });
    this.body = h('div', { class: 'side-scroll' });
    this.el = h('aside', { class: 'sidebar', 'aria-label': 'Branches' },
      h('div', { class: 'side-filter' }, h('div', { class: 'search' }, icon('search', 13), this.filterInput)),
      this.body);
  }

  load() {
    try { return JSON.parse(localStorage.getItem('gitsense.sidebar.collapsed') || '["tags"]'); } catch { return ['tags']; }
  }

  toggle(key) {
    if (this.collapsed.has(key)) this.collapsed.delete(key);
    else this.collapsed.add(key);
    try { localStorage.setItem('gitsense.sidebar.collapsed', JSON.stringify([...this.collapsed])); } catch { /* private mode */ }
    this.render();
  }

  matches(text) {
    return !this.filter || text.toLowerCase().includes(this.filter);
  }

  section(key, label, count, actions, build) {
    const open = !this.collapsed.has(key) || Boolean(this.filter);
    const head = h('div', {
      class: 'side-section-head', role: 'button', tabindex: '0', 'aria-expanded': String(open),
      onclick: () => this.toggle(key),
      onkeydown: (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); this.toggle(key); } },
    },
    icon(open ? 'chevron-down' : 'chevron-right', 13),
    h('span', { class: 'eyebrow' }, label),
    h('span', { class: 'side-section-actions' }, actions),
    count != null ? h('span', { class: 'badge' }, count) : null);
    const wrap = h('section', null, head);
    if (open) {
      const body = h('div', null);
      wrap.append(body);
      build(body);
    }
    return wrap;
  }

  row({ iconName, label, title, depth = 0, cls = '', meta = null, onClick, onOpen, onMenu }) {
    const el = h('div', {
      class: `side-row ${cls}`.trim(), tabindex: '0', title, style: { '--depth': depth },
      onclick: onClick,
      ondblclick: onOpen,
      onkeydown: (ev) => {
        if (ev.key === 'Enter' && onOpen) { ev.preventDefault(); onOpen(); }
        else if (ev.key === ' ' && onClick) { ev.preventDefault(); onClick(); }
        else if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
          ev.preventDefault();
          const rows = [...this.body.querySelectorAll('.side-row')];
          const next = rows[rows.indexOf(el) + (ev.key === 'ArrowDown' ? 1 : -1)];
          if (next) next.focus();
        }
      },
      oncontextmenu: onMenu ? (ev) => { ev.preventDefault(); ev.stopPropagation(); onMenu({ x: ev.clientX, y: ev.clientY }); } : null,
    }, icon(iconName, 13), h('span', { class: 'side-label ellipsis' }, label), meta);
    return el;
  }

  /** Render a folder tree, or a flat list while the filter is active. */
  tree(host, items, nameOf, makeRow, prefix, depth = 0) {
    if (this.filter) {
      for (const item of items) {
        if (this.matches(nameOf(item))) host.append(makeRow(item, nameOf(item), depth));
      }
      return;
    }
    const walk = (node, path, level) => {
      for (const [name, child] of [...node.folders].sort((a, b) => a[0].localeCompare(b[0]))) {
        const key = `${prefix}:${path}${name}`;
        const open = !this.closedFolders.has(key);
        host.append(this.row({
          iconName: open ? 'folder-open' : 'folder', label: name, depth: level, cls: 'folder',
          onClick: () => {
            if (open) this.closedFolders.add(key); else this.closedFolders.delete(key);
            this.render();
          },
        }));
        if (open) walk(child, `${path}${name}/`, level + 1);
      }
      for (const leaf of node.leaves) host.append(makeRow(leaf.item, leaf.label, level));
    };
    walk(buildTree(items, nameOf), '', depth);
  }

  render() {
    const view = this.view;
    const data = view.data;
    if (!data) return;
    const scroll = this.body.scrollTop;
    clear(this.body);

    // Local branches
    this.body.append(this.section('local', 'Local', data.branches.length,
      iconButton('plus', 'Create a branch', () => ops.createBranch(view), { size: 13 }),
      (host) => {
        if (!data.branches.length) host.append(h('div', { class: 'side-empty' }, 'No branches yet'));
        this.tree(host, data.branches, (b) => b.name, (b, label, depth) => {
          const meta = h('span', { class: 'side-meta' },
            b.ahead ? h('span', { class: 'ahead', title: `${b.ahead} to push` }, `↑${b.ahead}`) : null,
            b.behind ? h('span', { class: 'behind', title: `${b.behind} to pull` }, `↓${b.behind}`) : null,
            b.gone ? h('span', { title: 'The upstream branch was deleted' }, 'gone') : null);
          return this.row({
            iconName: b.current ? 'check' : 'git-branch',
            label,
            depth,
            cls: `${b.current ? 'current' : ''} ${b.gone ? 'gone' : ''}`,
            title: `${b.name}${b.upstream ? `\nTracks ${b.upstream}` : '\nNo upstream'}${b.date ? `\nLast commit ${relTime(b.date)}` : ''}`,
            meta,
            onClick: () => view.select(b.sha, { scroll: true }),
            onOpen: () => { if (!b.current) ops.checkout(view, 'checkout', { ref: b.name }, b.name); },
            onMenu: (at) => ops.branchMenu(view, b, at),
          });
        }, 'local');
      }));

    // Remotes
    const remoteCount = data.remotes.reduce((n, r) => n + r.branches.length, 0);
    this.body.append(this.section('remote', 'Remote', remoteCount,
      iconButton('plus', 'Add a remote', () => ops.addRemote(view), { size: 13 }),
      (host) => {
        if (!data.remotes.length) host.append(h('div', { class: 'side-empty' }, 'No remotes. Add one to push and pull.'));
        for (const remote of data.remotes) {
          const key = `remote:${remote.name}`;
          const open = !this.closedFolders.has(key);
          host.append(this.row({
            iconName: 'cloud', label: remote.name, cls: 'folder', title: remote.url,
            onClick: () => {
              if (open) this.closedFolders.add(key); else this.closedFolders.delete(key);
              this.render();
            },
            onMenu: (at) => ops.remoteMenu(view, remote, at),
          }));
          if (!open && !this.filter) continue;
          this.tree(host, remote.branches, (b) => b.name, (b, label, depth) => {
            const ref = { type: 'remote', name: b.full, branch: b.name, remote: b.remote, data: b };
            return this.row({
              iconName: 'git-branch', label, depth, title: b.full,
              onClick: () => view.select(b.sha, { scroll: true }),
              onOpen: () => ops.checkoutRef(view, ref),
              onMenu: (at) => ops.remoteBranchMenu(view, ref, at),
            });
          }, key, 1);
        }
      }));

    // Pull requests (GitHub repositories only)
    if (data.github) {
      this.body.append(this.section('prs', 'Pull requests', this.prCount,
        [
          iconButton('refresh-cw', 'Refresh pull requests', () => this.renderPrs(true), { size: 12 }),
          iconButton('plus', 'Create a pull request', () => createPullRequestDialog(view, data.head.branch), { size: 13 }),
        ],
        (host) => { this.prHost = host; this.renderPrs(false); }));
    }

    // Tags
    this.body.append(this.section('tags', 'Tags', data.tags.length,
      iconButton('plus', 'Create a tag', () => ops.createTag(view, null), { size: 13 }),
      (host) => {
        const tags = data.tags.filter((t) => this.matches(t.name));
        if (!tags.length) host.append(h('div', { class: 'side-empty' }, data.tags.length ? 'No matching tags' : 'No tags yet'));
        const shown = this.showAllTags || this.filter ? tags : tags.slice(0, TAG_LIMIT);
        for (const tag of shown) {
          host.append(this.row({
            iconName: 'tag', label: tag.name, title: tag.name,
            onClick: () => view.select(tag.sha, { scroll: true }),
            onMenu: (at) => ops.tagMenu(view, tag, at),
          }));
        }
        if (shown.length < tags.length) {
          host.append(this.row({
            iconName: 'ellipsis', label: `Show all ${tags.length} tags`, cls: 'folder',
            onClick: () => { this.showAllTags = true; this.render(); },
          }));
        }
      }));

    // Stashes
    this.body.append(this.section('stashes', 'Stashes', data.stashes.length,
      iconButton('plus', 'Stash changes', () => ops.stashSave(view), { size: 13 }),
      (host) => {
        if (!data.stashes.length) host.append(h('div', { class: 'side-empty' }, 'No stashes'));
        for (const stash of data.stashes) {
          if (!this.matches(stash.message)) continue;
          host.append(this.row({
            iconName: 'archive', label: stash.message.replace(/^(WIP on|On) [^:]+: /, '') || `stash@{${stash.index}}`,
            title: `${stash.message}\n${relTime(stash.date)}`,
            meta: h('span', { class: 'side-meta' }, `{${stash.index}}`),
            onClick: () => view.select(stash.sha, { scroll: true }),
            onOpen: () => ops.applyStash(view, stash),
            onMenu: (at) => ops.stashMenu(view, stash, at),
          }));
        }
      }));

    this.body.scrollTop = scroll;
  }

  renderPrs(force) {
    if (!this.prHost) return;
    renderPullRequests(this.view, this.prHost, {
      force,
      filter: this.filter,
      onCount: (n) => {
        if (this.prCount === n) return;
        this.prCount = n;
        const badge = this.prHost.parentElement && this.prHost.parentElement.querySelector('.side-section-head .badge');
        if (badge) badge.textContent = n;
        else if (this.prHost.parentElement) {
          this.prHost.parentElement.querySelector('.side-section-head').append(h('span', { class: 'badge' }, n));
        }
      },
    });
  }
}
