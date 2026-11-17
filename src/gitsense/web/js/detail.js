// Right panel: the working directory (stage and commit) or one commit's details.

import { api } from './api.js';
import {
  h, clear, append, icon, iconButton, button, avatar, statusLetter, basename, dirname,
  fullDate, relTime, shortSha, plural, copyText,
} from './util.js';
import { toast } from './ui.js';
import * as ops from './ops.js';

const SUMMARY_SOFT_LIMIT = 72;

const OPERATION_TEXT = {
  merge: 'Merge in progress',
  rebase: 'Rebase in progress',
  'cherry-pick': 'Cherry-pick in progress',
  revert: 'Revert in progress',
  am: 'Applying patches (git am)',
  bisect: 'Bisect in progress',
};

const fileKey = (file, ctx) => `${ctx.kind}\n${ctx.sha || ''}\n${file.path}`;

export class Detail {
  constructor(view) {
    this.view = view;
    this.el = h('aside', { class: 'detail', 'aria-label': 'Details' });
    this.cache = new Map(); // sha -> commit detail
    this.token = 0;
    this.committing = false;
    // Several staged or unstaged files picked with Cmd-click or Shift-click.
    // - Only one list at a time
    // - Only while two or more are picked; one file is just the open diff
    this.multi = null; // { kind, paths: Set } or null
    this.anchor = null; // { kind, path } of the last plain or Cmd-click, where a Shift-click range starts
    this.buildCommitForm();
    // The working directory panel is built once and only its parts are refilled,
    // so the commit message fields never leave the page while you type.
    this.wipHead = h('div', { class: 'detail-head' });
    this.wipBanner = h('div', { style: { display: 'contents' } });
    this.wipLists = h('div', { style: { display: 'contents' } });
    this.wipBody = h('div', { class: 'detail-body' }, this.wipLists, this.form);
    this.loadDraft();
  }

  // -- commit message draft (survives closing the window) -----------------------

  get draftKey() {
    return `gitsense.draft.${this.view.id}`;
  }

  loadDraft() {
    try {
      const draft = JSON.parse(localStorage.getItem(this.draftKey) || 'null');
      if (draft) {
        this.summary.value = draft.summary || '';
        this.description.value = draft.description || '';
      }
    } catch { /* no saved draft */ }
  }

  saveDraft() {
    try {
      if (this.summary.value || this.description.value) {
        localStorage.setItem(this.draftKey, JSON.stringify({ summary: this.summary.value, description: this.description.value }));
      } else {
        localStorage.removeItem(this.draftKey);
      }
    } catch { /* storage unavailable */ }
  }

  // -- commit form (kept alive across re-renders so typing is never lost) -------

  buildCommitForm() {
    this.summary = h('input', {
      class: 'input', type: 'text', placeholder: 'Summary', spellcheck: 'true', 'aria-label': 'Commit summary',
      oninput: () => { this.updateForm(); this.saveDraft(); },
      onkeydown: (ev) => { if (ev.key === 'Enter' && !ev.metaKey && !ev.ctrlKey) { ev.preventDefault(); this.description.focus(); } },
    });
    this.description = h('textarea', {
      class: 'input', rows: '3', placeholder: 'Description (optional)', spellcheck: 'true', 'aria-label': 'Commit description',
      oninput: () => this.saveDraft(),
      // Tab goes straight to the commit button, so Space or Enter commits.
      // - Done by hand because some web views only tab between text fields.
      onkeydown: (ev) => {
        if (ev.key !== 'Tab' || ev.shiftKey || ev.altKey || ev.metaKey || ev.ctrlKey || this.commitButton.disabled) return;
        ev.preventDefault();
        this.commitButton.focus();
      },
    });
    this.count = h('span', { class: 'commit-count' }, '0');
    this.amend = h('input', { type: 'checkbox', onchange: () => this.onAmendToggle() });
    this.commitButton = button('Commit', () => this.commit(), { class: 'primary block commit-go', icon: 'check' });
    this.commitButton.addEventListener('keydown', (ev) => {
      if (ev.key !== 'Tab' || !ev.shiftKey) return;
      ev.preventDefault();
      this.description.focus();
    });
    // The button comes before the amend box in tab order; CSS shows it below.
    this.form = h('div', { class: 'commit-form' },
      h('div', { class: 'row' },
        h('p', { class: 'eyebrow' }, 'Commit message'),
        this.count),
      this.summary,
      this.description,
      this.commitButton,
      h('div', { class: 'row commit-amend' },
        h('label', { class: 'check', title: 'Replace the last commit with a new one that includes the staged changes' }, this.amend, 'Amend last commit')));
  }

  async onAmendToggle() {
    if (this.amend.checked && !this.summary.value && !this.description.value) {
      const sha = this.view.data.head.sha;
      if (sha) {
        try {
          const detail = await this.loadCommit(sha);
          if (this.amend.checked && !this.summary.value) {
            this.summary.value = detail.subject;
            this.description.value = detail.body;
          }
        } catch { /* the message stays empty */ }
      }
    }
    this.updateForm();
  }

  updateForm() {
    const st = this.view.data.status;
    const staged = st.staged.length;
    const unstaged = st.unstaged.length;
    const n = this.summary.value.length;
    this.count.textContent = String(n);
    this.count.classList.toggle('over', n > SUMMARY_SOFT_LIMIT);
    this.count.title = n > SUMMARY_SOFT_LIMIT ? `Summaries over ${SUMMARY_SOFT_LIMIT} characters get cut off in many tools` : '';
    const amend = this.amend.checked;
    const label = this.commitButton.lastChild;
    const conflicted = st.conflicted.length > 0;
    this.stageAllOnCommit = false;
    let text;
    let enabled = Boolean(this.summary.value.trim()) && !conflicted;
    if (amend) {
      text = staged ? `Amend with ${plural(staged, 'staged file')}` : 'Amend last commit';
    } else if (staged) {
      text = `Commit ${plural(staged, 'staged file')}`;
    } else if (unstaged) {
      text = `Stage all and commit ${plural(unstaged, 'file')}`;
      this.stageAllOnCommit = true;
    } else {
      text = 'Nothing to commit';
      enabled = false;
    }
    if (conflicted) text = 'Resolve conflicts to commit';
    if (this.committing) text = 'Committing…';
    label.textContent = text;
    this.commitButton.disabled = !enabled || this.committing;
    this.amend.disabled = !this.view.data.head.sha;
  }

  async commit() {
    if (this.commitButton.disabled || this.committing) return;
    const typed = [this.summary.value, this.description.value];
    const summary = typed[0].trim();
    const body = typed[1].trim();
    const message = body ? `${summary}\n\n${body}` : summary;
    const amend = this.amend.checked;
    // Locked until the request finishes, so a double-click cannot commit twice.
    const viaKeyboard = document.activeElement === this.commitButton;
    this.committing = true;
    this.updateForm();
    let res = null;
    try {
      res = await this.view.run('commit', {
        message,
        amend,
        stage_all: this.stageAllOnCommit,
      }, { label: 'Committing', success: amend ? 'Commit amended' : 'Committed', select: 'head' });
    } finally {
      this.committing = false;
    }
    if (res) this.amend.checked = false;
    // Clear the message only if it was not edited while the commit ran.
    if (res && this.summary.value === typed[0] && this.description.value === typed[1]) {
      this.summary.value = '';
      this.description.value = '';
      this.saveDraft();
    }
    this.updateForm();
    // The disabled button dropped the focus; give it back so a failed commit can be retried with Space.
    if (viaKeyboard && !this.commitButton.disabled && this.form.isConnected) this.commitButton.focus();
  }

  focusCommit() {
    this.summary.focus();
  }

  // -- rendering ----------------------------------------------------------------

  render() {
    const view = this.view;
    if (!view.data) return;
    if (view.selected === 'WIP' || view.selected == null) this.renderWip();
    else this.renderCommit(view.selected);
  }

  fileRow(file, ctx) {
    const view = this.view;
    const open = view.diffTarget;
    const selected = this.multi ? this.isPicked(file, ctx) : open && fileKey(open.file, open.ctx) === fileKey(file, ctx);
    const actions = [];
    if (ctx.kind === 'unstaged') {
      actions.push(iconButton('undo-2', 'Discard changes', () => ops.discardFiles(view, [file]), { size: 13 }));
      actions.push(iconButton('plus', 'Stage file', () => view.run('stage', { paths: [file.path] }, { quiet: true }), { size: 14 }));
    } else if (ctx.kind === 'staged') {
      actions.push(iconButton('minus', 'Unstage file', () => view.run('unstage', { paths: ops.stagePaths(file) }, { quiet: true }), { size: 14 }));
    } else if (ctx.kind === 'conflicted') {
      actions.push(iconButton('circle-check', 'Mark as resolved', () => view.run('mark_resolved', { paths: [file.path] }, { quiet: true }), { size: 14 }));
    }
    const stats = file.added != null || file.deleted != null
      ? h('span', { class: 'file-stats' },
        file.added ? h('span', { class: 'a' }, `+${file.added}`) : null,
        file.deleted ? h('span', { class: 'd' }, `-${file.deleted}`) : null)
      : null;
    const title = file.orig_path ? `${file.orig_path} → ${file.path}` : file.path;
    const row = h('div', {
      class: `file-row ${selected ? 'selected' : ''}`, tabindex: '0', title,
      onclick: (ev) => this.clickFile(ev, file, ctx),
      onkeydown: (ev) => {
        if (ev.key === 'Enter') { ev.preventDefault(); view.openDiff(file, ctx); }
        else if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
          ev.preventDefault();
          const rows = [...this.el.querySelectorAll('.file-row')];
          const next = rows[rows.indexOf(ev.currentTarget) + (ev.key === 'ArrowDown' ? 1 : -1)];
          if (next) { next.focus(); next.click(); }
        } else if (ev.key === ' ' && ctx.kind !== 'commit') {
          ev.preventDefault();
          const files = this.isPicked(file, ctx) ? this.pickedFiles() : [file];
          if (ctx.kind === 'unstaged') view.run('stage', { paths: files.map((f) => f.path) }, { quiet: true });
          else if (ctx.kind === 'staged') view.run('unstage', { paths: files.flatMap(ops.stagePaths) }, { quiet: true });
        }
      },
      oncontextmenu: (ev) => {
        ev.preventDefault();
        const anchor = { x: ev.clientX, y: ev.clientY };
        if (this.isPicked(file, ctx)) ops.filesMenu(view, this.pickedFiles(), ctx, anchor);
        else ops.fileMenu(view, file, ctx, anchor);
      },
    },
    statusLetter(ctx.kind === 'conflicted' ? 'U' : file.status),
    h('span', { class: 'file-name' },
      h('span', { class: 'base' }, basename(file.path)),
      dirname(file.path) ? h('span', { class: 'dir' }, h('bdi', null, dirname(file.path))) : null),
    h('span', { class: 'fill' }),
    stats,
    actions.length ? h('span', { class: 'file-actions' }, actions) : null);
    row.fileKey = fileKey(file, ctx);
    row.file = file;
    row.ctx = ctx;
    return row;
  }

  /** Mark the picked rows, or the row whose diff is open, without rebuilding the lists. */
  highlightFile() {
    const open = this.view.diffTarget;
    // An open diff means one file is selected, so a multi-file pick has ended.
    if (open && this.multi) this.setMulti(null);
    const key = open ? fileKey(open.file, open.ctx) : null;
    for (const row of this.el.querySelectorAll('.file-row')) {
      row.classList.toggle('selected', this.multi ? this.isPicked(row.file, row.ctx) : row.fileKey === key);
    }
  }

  // -- multi-file selection -------------------------------------------------------

  isPicked(file, ctx) {
    return Boolean(this.multi && this.multi.kind === ctx.kind && this.multi.paths.has(file.path));
  }

  /** The picked files, in list order. */
  pickedFiles() {
    if (!this.multi) return [];
    return this.view.data.status[this.multi.kind].filter((f) => this.multi.paths.has(f.path));
  }

  setMulti(multi) {
    this.multi = multi;
    this.renderSectionActions();
  }

  /**
   * A click on a file row.
   * - Plain click opens the file's diff
   * - Cmd-click adds the file to the selection, or takes it out if it is already in
   * - Shift-click selects every file from the last click to this one
   * - Cmd and Shift work in the staged and unstaged lists, one list at a time
   */
  clickFile(ev, file, ctx) {
    const view = this.view;
    const kind = ctx.kind;
    const toggle = ev.metaKey || ev.ctrlKey;
    if ((kind !== 'staged' && kind !== 'unstaged') || (!toggle && !ev.shiftKey)) {
      this.anchor = { kind, path: file.path };
      if (this.multi) this.setMulti(null);
      view.openDiff(file, ctx);
      return;
    }
    const list = view.data.status[kind];
    const from = this.anchor && this.anchor.kind === kind ? list.findIndex((f) => f.path === this.anchor.path) : -1;
    let paths;
    if (ev.shiftKey && from >= 0) {
      const to = list.findIndex((f) => f.path === file.path);
      paths = new Set(list.slice(Math.min(from, to), Math.max(from, to) + 1).map((f) => f.path));
    } else if (toggle) {
      // Start from what is selected in this list now: the picked files, or the file whose diff is open.
      const open = view.diffTarget;
      if (this.multi && this.multi.kind === kind) paths = new Set(this.multi.paths);
      else if (open && open.ctx.kind === kind) paths = new Set([open.file.path]);
      else paths = new Set();
      if (paths.has(file.path)) paths.delete(file.path);
      else paths.add(file.path);
      this.anchor = { kind, path: file.path };
    } else {
      // Shift-click with no earlier click in this list.
      paths = new Set([file.path]);
      this.anchor = { kind, path: file.path };
    }
    ev.currentTarget.focus({ preventScroll: true });
    if (paths.size >= 2) {
      this.setMulti({ kind, paths });
      if (view.diffTarget) view.closeDiff();
      else this.highlightFile();
    } else {
      if (this.multi) this.setMulti(null);
      const only = list.find((f) => paths.has(f.path));
      if (only) view.openDiff(only, { kind });
      else { view.closeDiff(); this.highlightFile(); }
    }
  }

  /** Clear a multi-file selection. Returns false when there was none. */
  clearMulti() {
    if (!this.multi) return false;
    this.setMulti(null);
    this.highlightFile();
    return true;
  }

  /** The buttons in the unstaged and staged list headings, which act on the picked files when there are any. */
  renderSectionActions() {
    const view = this.view;
    const st = view.data && view.data.status;
    if (!st || !this.sectionActions) return;
    const count = this.pickedFiles().length;
    const multi = this.multi ? this.multi.kind : null;
    const total = st.staged.length + st.unstaged.length + st.conflicted.length;
    append(clear(this.sectionActions.unstaged), multi === 'unstaged' ? [
      button(`Discard ${count}`, () => ops.discardFiles(view, this.pickedFiles()), { class: 'sm ghost', title: `Discard the changes to ${plural(count, 'selected file')}` }),
      button(`Stage ${count}`, () => view.run('stage', { paths: this.pickedFiles().map((f) => f.path) }, { quiet: true }), { class: 'sm', title: `Stage ${plural(count, 'selected file')}` }),
    ] : [
      button('Discard all', () => ops.discardAll(view), { class: 'sm ghost', disabled: !total }),
      button('Stage all', () => view.run('stage_all', {}, { quiet: true }), { class: 'sm', disabled: !st.unstaged.length }),
    ]);
    append(clear(this.sectionActions.staged), multi === 'staged' ? [
      button(`Unstage ${count}`, () => view.run('unstage', { paths: this.pickedFiles().flatMap(ops.stagePaths) }, { quiet: true }), { class: 'sm', title: `Unstage ${plural(count, 'selected file')}` }),
    ] : [
      button('Unstage all', () => view.run('unstage_all', {}, { quiet: true }), { class: 'sm', disabled: !st.staged.length }),
    ]);
  }

  fileSection(label, files, ctx, actions, flex = true) {
    return h('div', { class: `file-section ${flex ? 'flex' : ''}`, dataset: { kind: ctx.kind } },
      h('div', { class: 'file-section-head' },
        h('p', { class: 'eyebrow' }, `${label} (${files.length})`),
        actions),
      h('div', { class: 'file-list' },
        files.length ? files.map((f) => this.fileRow(f, ctx)) : h('div', { class: 'side-empty', style: { paddingLeft: '16px' } }, ctx.kind === 'staged' ? 'Stage files to include them in the commit' : 'No changes')));
  }

  operationBanner() {
    const op = this.view.data.operation;
    if (!op) return null;
    const view = this.view;
    const conflicts = view.data.status.conflicted.length;
    let text = OPERATION_TEXT[op.kind] || `${op.kind} in progress`;
    if (op.kind === 'rebase' && op.step && op.total) text += ` (step ${op.step} of ${op.total})`;
    return h('div', { class: 'banner op-banner' },
      icon('triangle-alert', 16),
      h('div', { class: 'grow' }, text, conflicts ? h('div', { class: 'muted', style: { fontWeight: 400, fontSize: '11.5px' } }, `${plural(conflicts, 'conflicted file')} to resolve`) : null),
      op.kind !== 'merge' && op.kind !== 'bisect' ? button('Skip', () => view.run('op_skip', {}, { label: 'Skipping' }), { class: 'sm ghost' }) : null,
      button('Abort', () => view.run('op_abort', {}, { label: 'Aborting', success: `${op.kind} aborted` }), { class: 'sm danger' }),
      op.kind !== 'bisect' ? button('Continue', () => view.run('op_continue', {}, { label: 'Continuing', success: `${op.kind} continued` }), { class: 'sm primary', disabled: conflicts > 0 }) : null);
  }

  renderWip() {
    const view = this.view;
    const st = view.data.status;
    const head = view.data.head;
    const total = st.staged.length + st.unstaged.length + st.conflicted.length;
    const where = head.branch ? `on ${head.branch}` : head.detached ? `detached at ${shortSha(head.sha)}` : 'new repository';

    // A focused file row is found again after the lists are rebuilt.
    const active = document.activeElement;
    const rowsBefore = [...this.wipLists.querySelectorAll('.file-row')];
    const focused = rowsBefore.includes(active) ? { key: active.fileKey, index: rowsBefore.indexOf(active) } : null;
    const scrolls = new Map([...this.wipLists.querySelectorAll('.file-section')].map((el) => [el.dataset.kind, el.querySelector('.file-list').scrollTop]));

    append(clear(this.wipHead), [
      h('p', { class: 'eyebrow' }, 'Working directory'),
      h('h2', { class: 'title' }, total ? `${plural(total, 'file change')}` : 'Nothing to commit'),
      h('div', { class: 'detail-sub' }, icon('git-branch', 12), where,
        st.truncated ? h('span', { title: 'Only the first 5000 changed files are listed' }, '(list truncated)') : null)]);
    append(clear(this.wipBanner), [this.operationBanner()]);
    // Picked files that left their list (staged, discarded, committed) drop out of the selection.
    if (this.multi) {
      const here = new Set(st[this.multi.kind].map((f) => f.path));
      const paths = new Set([...this.multi.paths].filter((p) => here.has(p)));
      this.multi = paths.size >= 2 ? { kind: this.multi.kind, paths } : null;
    }
    this.sectionActions = {
      unstaged: h('span', { style: { display: 'contents' } }),
      staged: h('span', { style: { display: 'contents' } }),
    };
    this.renderSectionActions();
    append(clear(this.wipLists), [
      st.conflicted.length ? this.fileSection('Conflicted', st.conflicted, { kind: 'conflicted' }, null, false) : null,
      this.fileSection('Unstaged files', st.unstaged, { kind: 'unstaged' }, this.sectionActions.unstaged),
      this.fileSection('Staged files', st.staged, { kind: 'staged' }, this.sectionActions.staged)]);
    if (this.mode !== 'wip' || !this.wipBody.isConnected) {
      append(clear(this.el), [this.wipHead, this.wipBanner, this.wipBody]);
    }
    for (const el of this.wipLists.querySelectorAll('.file-section')) {
      const top = scrolls.get(el.dataset.kind);
      if (top) el.querySelector('.file-list').scrollTop = top;
    }
    if (focused) {
      const rows = [...this.wipLists.querySelectorAll('.file-row')];
      const again = rows.find((r) => r.fileKey === focused.key) || rows[Math.min(focused.index, rows.length - 1)];
      if (again) again.focus({ preventScroll: true });
    }
    this.updateForm();
    this.mode = 'wip';
  }

  async loadCommit(sha) {
    if (this.cache.has(sha)) return this.cache.get(sha);
    const detail = await api.get('/api/repo/commit', { id: this.view.id, sha });
    if (detail.kind !== 'stash') {
      this.cache.set(sha, detail);
      if (this.cache.size > 60) this.cache.delete(this.cache.keys().next().value);
    }
    return detail;
  }

  async renderCommit(sha) {
    const view = this.view;
    this.multi = null;
    const token = ++this.token;
    const cached = this.cache.get(sha);
    if (!cached && this.mode !== `commit:${sha}`) {
      clear(this.el).append(
        h('div', { class: 'detail-head' }, h('p', { class: 'eyebrow' }, 'Commit'), h('h2', { class: 'title mono' }, shortSha(sha))),
        h('div', { class: 'empty' }, icon('loader-circle', 22, 'spin')));
    }
    let detail;
    try {
      detail = await this.loadCommit(sha);
    } catch (err) {
      if (token !== this.token) return;
      clear(this.el).append(h('div', { class: 'empty' }, icon('circle-alert', 24), h('p', null, err.message)));
      return;
    }
    if (token !== this.token || view.selected !== sha) return;
    const isStash = detail.kind === 'stash';
    const stash = isStash ? view.data.stashes.find((s) => s.sha === sha) : null;
    const refs = view.refsBySha.get(sha) || [];
    const added = detail.files.reduce((n, f) => n + (f.added || 0), 0);
    const deleted = detail.files.reduce((n, f) => n + (f.deleted || 0), 0);

    const headEl = h('div', { class: 'detail-head' },
      h('p', { class: 'eyebrow' }, isStash ? 'Stash' : detail.parents.length > 1 ? 'Merge commit' : 'Commit'),
      h('div', { class: 'detail-sub', style: { marginTop: '2px' } },
        h('span', { class: 'sha-link selectable', title: 'Copy the full SHA', onclick: async () => { if (await copyText(detail.sha)) toast('SHA copied', 'ok'); } }, detail.sha.slice(0, 12)),
        iconButton('copy', 'Copy the full SHA', async () => { if (await copyText(detail.sha)) toast('SHA copied', 'ok'); }, { size: 12 })));

    const facts = h('dl', { class: 'commit-facts' });
    if (!isStash && detail.parents.length) {
      facts.append(h('dt', null, detail.parents.length > 1 ? 'Parents' : 'Parent'),
        h('dd', null, detail.parents.map((p) => h('span', { class: 'sha-link', onclick: () => view.select(p, { scroll: true }) }, shortSha(p)))));
    }
    if (refs.length) {
      facts.append(h('dt', null, 'Refs'), h('dd', null, refs.map((r) => h('span', { class: `chip ${r.type === 'local' ? (r.current ? 'head' : 'local') : r.type}` }, h('span', null, r.name)))));
    }
    if (!isStash && detail.committer && (detail.committer !== detail.author || Math.abs(detail.cdate - detail.date) > 60)) {
      facts.append(h('dt', null, 'Committed'), h('dd', null, `${detail.committer}, ${fullDate(detail.cdate)}`));
    }

    const meta = h('div', { class: 'commit-meta' },
      h('p', { class: 'commit-subject selectable' }, detail.subject),
      detail.body ? h('p', { class: 'commit-body selectable' }, detail.body) : null,
      h('div', { class: 'commit-author' },
        avatar(detail.author, 'lg'),
        h('div', { class: 'grow' },
          h('div', { class: 'who ellipsis', title: detail.email }, detail.author),
          h('div', { class: 'when', title: fullDate(detail.date) }, `${relTime(detail.date)} · ${fullDate(detail.date)}`))),
      facts.children.length ? facts : null,
      isStash && stash ? h('div', { class: 'commit-actions' },
        button('Apply', () => ops.applyStash(view, stash), { class: 'sm primary', icon: 'archive-restore' }),
        button('Pop', () => ops.popStash(view, stash), { class: 'sm', icon: 'archive-restore', title: 'Apply the stash and remove it from the list' }),
        button('Drop', () => ops.dropStash(view, stash), { class: 'sm danger', icon: 'trash-2' })) : null);

    const files = h('div', { class: 'file-section flex' },
      h('div', { class: 'file-section-head' },
        h('p', { class: 'eyebrow' }, plural(detail.files.length, 'changed file')),
        added ? h('span', { class: 'stat-add' }, `+${added}`) : null,
        deleted ? h('span', { class: 'stat-del' }, `-${deleted}`) : null),
      h('div', { class: 'file-list' },
        detail.files.length
          ? detail.files.map((f) => this.fileRow(f, { kind: 'commit', sha: f.from_sha || detail.sha }))
          : h('div', { class: 'side-empty', style: { paddingLeft: '16px' } }, 'No file changes')));

    const scroll = this.el.querySelector('.file-list')?.scrollTop || 0;
    const same = this.mode === `commit:${sha}`;
    clear(this.el).append(headEl, h('div', { class: 'detail-body' }, meta, files));
    if (same && scroll) this.el.querySelector('.file-list').scrollTop = scroll;
    this.mode = `commit:${sha}`;
  }
}
