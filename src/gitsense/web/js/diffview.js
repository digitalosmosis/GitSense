// File diff viewer with hunk and line staging.

import { api } from './api.js';
import { h, clear, icon, iconButton, button, statusLetter, basename, dirname, shortSha, plural } from './util.js';
import { confirmDialog } from './ui.js';
import * as ops from './ops.js';

const RENDER_LIMIT = 4000; // lines drawn before "Show the rest"

const KIND_LABEL = { unstaged: 'Unstaged changes', staged: 'Staged changes', conflicted: 'Conflict', commit: 'Commit' };

export class DiffView {
  constructor(view) {
    this.view = view;
    this.file = null;
    this.ctx = null;
    this.data = null;
    this.selection = new Map(); // hunk index -> Set of line indexes
    this.anchor = null; // last clicked line, for shift-click ranges
    this.token = 0;
    this.ignoreWs = false;
    this.fullFile = false;
    this.headEl = h('div', { class: 'diff-head' });
    this.bodyEl = h('div', { class: 'diff-body selectable', tabindex: '0' });
    this.el = h('div', { class: 'diff-view' }, this.headEl, this.bodyEl);
  }

  get mode() {
    return this.view.app.settings.diff_view === 'split' ? 'split' : 'unified';
  }

  /**
   * Whether hunks and lines can be staged from this diff.
   * - Only working directory diffs of tracked text files.
   * - Not while a view option is on: the server stages from the plain diff,
   *   so hunk and line positions would not match what is shown.
   */
  get editable() {
    return this.ctx && (this.ctx.kind === 'unstaged' || this.ctx.kind === 'staged')
      && this.data && !this.data.binary && !this.data.combined && this.file.status !== '?'
      && !this.ignoreWs && !this.fullFile;
  }

  async open(file, ctx) {
    const sameFile = this.file && this.file.path === file.path && this.ctx.kind === ctx.kind && this.ctx.sha === ctx.sha;
    this.file = file;
    this.ctx = ctx;
    if (!sameFile) {
      this.selection.clear();
      this.fullFile = false;
      this.renderAll = false;
      this.data = null;
      this.renderHead();
      clear(this.bodyEl).append(h('div', { class: 'empty' }, icon('loader-circle', 22, 'spin')));
    }
    await this.load(!sameFile);
  }

  async load(resetScroll = false) {
    const token = ++this.token;
    const kind = this.ctx.kind === 'conflicted' ? 'unstaged' : this.ctx.kind;
    let data;
    try {
      data = await api.get('/api/repo/diff', {
        id: this.view.id,
        kind,
        path: this.file.path,
        sha: this.ctx.sha,
        orig_path: this.file.orig_path,
        ignore_ws: this.ignoreWs ? '1' : '',
        context: this.fullFile ? 100000 : 3,
      });
    } catch (err) {
      if (token !== this.token) return;
      this.data = null;
      this.renderHead();
      clear(this.bodyEl).append(h('div', { class: 'empty' }, icon('circle-alert', 24), h('p', null, err.message)));
      return;
    }
    if (token !== this.token) return;
    const scroll = this.bodyEl.scrollTop;
    this.data = data;
    this.selection.clear();
    this.renderHead();
    this.renderBody();
    this.bodyEl.scrollTop = resetScroll ? 0 : scroll;
  }

  // -- header -------------------------------------------------------------------

  renderHead() {
    const view = this.view;
    const { file, ctx } = this;
    const label = ctx.kind === 'commit' ? `Commit ${shortSha(ctx.sha)}` : KIND_LABEL[ctx.kind];
    const actions = [];
    if (ctx.kind === 'unstaged') {
      actions.push(button('Stage file', () => view.run('stage', { paths: [file.path] }, { quiet: true }), { class: 'sm primary', icon: 'plus' }));
      actions.push(button('Discard', () => ops.discardFiles(view, [file]), { class: 'sm danger', icon: 'undo-2' }));
    } else if (ctx.kind === 'staged') {
      actions.push(button('Unstage file', () => view.run('unstage', { paths: ops.stagePaths(file) }, { quiet: true }), { class: 'sm primary', icon: 'minus' }));
    } else if (ctx.kind === 'conflicted') {
      actions.push(button('Use ours', () => view.run('resolve', { path: file.path, side: 'ours' }, { quiet: true }), { class: 'sm', title: 'Keep the version from the current branch' }));
      actions.push(button('Use theirs', () => view.run('resolve', { path: file.path, side: 'theirs' }, { quiet: true }), { class: 'sm', title: 'Keep the incoming version' }));
      actions.push(button('Mark resolved', () => view.run('mark_resolved', { paths: [file.path] }, { quiet: true }), { class: 'sm primary', icon: 'circle-check' }));
    }
    const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Diff layout' },
      h('button', { type: 'button', class: this.mode === 'unified' ? 'on' : '', title: 'One column', onclick: () => this.setMode('unified') }, icon('rows-2', 12), 'Unified'),
      h('button', { type: 'button', class: this.mode === 'split' ? 'on' : '', title: 'Side by side', onclick: () => this.setMode('split') }, icon('columns-2', 12), 'Split'));
    const ws = h('input', { type: 'checkbox', checked: this.ignoreWs, onchange: () => { this.ignoreWs = ws.checked; this.load(); } });
    const full = h('input', { type: 'checkbox', checked: this.fullFile, onchange: () => { this.fullFile = full.checked; this.load(); } });

    clear(this.headEl).append(
      h('div', { class: 'diff-title' },
        statusLetter(ctx.kind === 'conflicted' ? 'U' : file.status),
        h('div', { class: 'grow' },
          h('p', { class: 'eyebrow' }, label),
          h('div', { class: 'diff-path selectable', title: file.orig_path ? `${file.orig_path} → ${file.path}` : file.path },
            h('span', { class: 'dir' }, dirname(file.path)), h('span', { class: 'base' }, basename(file.path)),
            file.orig_path ? h('span', { class: 'muted' }, `  (was ${file.orig_path})`) : null)),
        actions,
        iconButton('x', 'Close the diff (Esc)', () => view.closeDiff(), { class: 'lg', size: 17 })),
      h('div', { class: 'diff-tools' },
        seg,
        h('label', { class: 'check', title: 'Hide changes that only touch spaces and tabs' }, ws, 'Ignore whitespace'),
        h('label', { class: 'check', title: 'Show the whole file instead of three lines around each change' }, full, 'Whole file'),
        (this.ignoreWs || this.fullFile) && (ctx.kind === 'unstaged' || ctx.kind === 'staged')
          ? h('span', { class: 'muted' }, 'Turn these off to stage by hunk or line') : null,
        h('span', { class: 'grow' }),
        this.data ? h('span', { class: 'muted diff-count' }, this.summary()) : null,
        iconButton('history', 'File history', () => view.showFileHistory(file.path), { size: 14 }),
        iconButton('square-pen', 'Open in editor', () => api.post('/api/repo/open', { id: view.id, what: 'editor', path: file.path }).catch((err) => view.fail(err)), { size: 14 })));
  }

  summary() {
    let added = 0;
    let deleted = 0;
    for (const hunk of this.data.hunks) {
      for (const line of hunk.lines) {
        if (line.t === '+') added += 1;
        else if (line.t === '-') deleted += 1;
      }
    }
    return `+${added}  -${deleted}`;
  }

  setMode(mode) {
    if (mode === this.mode) return;
    this.view.app.updateSettings({ diff_view: mode });
    this.selection.clear();
    this.renderHead();
    this.renderBody();
  }

  // -- body ---------------------------------------------------------------------

  renderBody() {
    const data = this.data;
    const body = clear(this.bodyEl);
    if (data.binary) {
      body.append(h('div', { class: 'empty' }, icon('file', 28), h('p', { class: 'heading' }, 'Binary file'), h('p', null, 'The contents cannot be shown as text.')));
      return;
    }
    if (!data.hunks.length) {
      const reason = this.file.status === 'R' ? 'The file was renamed without content changes.'
        : this.ignoreWs ? 'Only whitespace changed.'
          : data.header.some((l) => l.startsWith('old mode')) ? 'Only the file mode changed.'
            : 'No text changes to show.';
      body.append(h('div', { class: 'empty' }, icon('file', 28), h('p', null, reason)));
      return;
    }
    if (data.combined) {
      body.append(h('div', { class: 'banner' }, icon('triangle-alert', 15),
        h('span', null, 'This file has merge conflicts. Edit it to resolve them, or pick a side above.')));
    }
    let budget = RENDER_LIMIT;
    data.hunks.forEach((hunk, index) => {
      if (!this.renderAll && budget <= 0) return;
      body.append(this.renderHunk(hunk, index, budget));
      budget -= hunk.lines.length;
    });
    const total = data.hunks.reduce((n, hk) => n + hk.lines.length, 0);
    const clipped = total > RENDER_LIMIT && !this.renderAll;
    if (clipped || data.truncated) {
      body.append(h('div', { class: 'diff-more' },
        h('span', { class: 'muted' }, clipped ? `Showing the first ${RENDER_LIMIT} of ${total} lines.` : 'This diff is very large and was cut short.'),
        clipped ? button('Show the rest', () => { this.renderAll = true; this.renderBody(); }, { class: 'sm' }) : null));
    }
  }

  hunkActions(hunk, index) {
    if (!this.editable) return null;
    const selected = this.selection.get(index);
    const n = selected ? selected.size : 0;
    const what = n ? plural(n, 'line') : 'hunk';
    // Taken when the button is pressed, from the hunk on screen, so a refresh
    // while the confirm dialog is open cannot change what is sent.
    const request = () => ({
      path: this.file.path,
      orig_path: this.file.orig_path,
      hunk: index,
      header: hunk.header,
      expect: hunk.lines.map((line) => line.t + line.s).join('\n'),
      lines: n ? [...selected] : null,
    });
    if (this.ctx.kind === 'unstaged') {
      return [
        button(`Discard ${what}`, async () => {
          const req = request();
          const ok = await confirmDialog({
            eyebrow: 'Discard', title: `Discard ${what}`, danger: true, confirmLabel: 'Discard',
            message: `Discard ${n ? `the ${plural(n, 'selected line')}` : 'this hunk'} from ${req.path}? This cannot be undone.`,
          });
          if (ok) this.applyHunk(req, 'discard');
        }, { class: 'sm ghost' }),
        button(`Stage ${what}`, () => this.applyHunk(request(), 'stage'), { class: 'sm', icon: 'plus' }),
      ];
    }
    return [button(`Unstage ${what}`, () => this.applyHunk(request(), 'unstage'), { class: 'sm', icon: 'minus' })];
  }

  async applyHunk(req, mode) {
    await this.view.run('apply_hunk', { ...req, mode },
      { quiet: true, label: mode === 'discard' ? 'Discarding' : mode === 'stage' ? 'Staging' : 'Unstaging' });
  }

  renderHunk(hunk, index, budget) {
    const limit = this.renderAll ? Infinity : budget;
    const actionsEl = h('div', { class: 'hunk-actions' }, this.hunkActions(hunk, index));
    const head = h('div', { class: 'hunk-head' },
      h('span', { class: 'hunk-range' }, hunk.combined ? hunk.header : `@@ -${hunk.old_start},${hunk.old_count} +${hunk.new_start},${hunk.new_count} @@`),
      h('span', { class: 'hunk-section ellipsis' }, hunk.section || ''),
      actionsEl);
    const wrap = h('div', { class: 'hunk' }, head);
    wrap.append(this.mode === 'split' && !hunk.combined ? this.splitLines(hunk, limit) : this.unifiedLines(hunk, index, limit, actionsEl));
    return wrap;
  }

  unifiedLines(hunk, index, limit, actionsEl) {
    const table = h('div', { class: 'diff-lines' });
    const selectable = this.editable;
    hunk.lines.slice(0, limit).forEach((line, i) => {
      if (line.t === '\\') {
        table.append(h('div', { class: 'dl note' }, h('span', { class: 'ln' }), h('span', { class: 'ln' }), h('span', { class: 'dc' }, 'No newline at end of file')));
        return;
      }
      const cls = line.t === '+' ? 'add' : line.t === '-' ? 'del' : 'ctx';
      const row = h('div', { class: `dl ${cls}` },
        h('span', { class: 'ln' }, line.o ?? ''),
        h('span', { class: 'ln' }, line.n ?? ''),
        h('span', { class: 'dm' }, line.t === ' ' ? '' : line.t),
        h('span', { class: 'dc' }, line.s || '​'));
      if (selectable && line.t !== ' ') {
        row.classList.add('pick');
        row.title = 'Click the line number to select this line for staging';
        const toggle = (ev) => {
          ev.preventDefault();
          this.toggleLine(index, i, ev.shiftKey, hunk);
          table.querySelectorAll('.dl').forEach((el, at) => el.classList.toggle('sel', Boolean(this.selection.get(index)?.has(at))));
          clear(actionsEl).append(...(this.hunkActions(hunk, index) || []));
        };
        row.children[0].addEventListener('mousedown', toggle);
        row.children[1].addEventListener('mousedown', toggle);
        row.children[2].addEventListener('mousedown', toggle);
      }
      table.append(row);
    });
    return table;
  }

  toggleLine(index, i, range, hunk) {
    let set = this.selection.get(index);
    if (!set) {
      set = new Set();
      this.selection.set(index, set);
    }
    if (range && this.anchor && this.anchor.index === index) {
      const [a, b] = [Math.min(this.anchor.i, i), Math.max(this.anchor.i, i)];
      for (let k = a; k <= b; k++) {
        if (hunk.lines[k].t === '+' || hunk.lines[k].t === '-') set.add(k);
      }
    } else if (set.has(i)) {
      set.delete(i);
    } else {
      set.add(i);
    }
    this.anchor = { index, i };
    if (!set.size) this.selection.delete(index);
  }

  /** Pair removed and added lines side by side. */
  splitLines(hunk, limit) {
    const table = h('div', { class: 'diff-lines split' });
    const lines = hunk.lines.slice(0, limit).filter((l) => l.t !== '\\');
    let i = 0;
    const cell = (line, side) => {
      if (!line) return [h('span', { class: 'ln' }), h('span', { class: 'dc blank' })];
      const cls = line.t === '+' ? 'add' : line.t === '-' ? 'del' : 'ctx';
      return [h('span', { class: `ln ${cls}` }, side === 'old' ? line.o : line.n), h('span', { class: `dc ${cls}` }, line.s || '​')];
    };
    while (i < lines.length) {
      if (lines[i].t === ' ') {
        table.append(h('div', { class: 'dl' }, cell(lines[i], 'old'), cell(lines[i], 'new')));
        i += 1;
        continue;
      }
      const removed = [];
      const added = [];
      while (i < lines.length && lines[i].t === '-') removed.push(lines[i++]);
      while (i < lines.length && lines[i].t === '+') added.push(lines[i++]);
      const rows = Math.max(removed.length, added.length);
      for (let k = 0; k < rows; k++) {
        table.append(h('div', { class: 'dl' }, cell(removed[k], 'old'), cell(added[k], 'new')));
      }
    }
    return table;
  }
}
