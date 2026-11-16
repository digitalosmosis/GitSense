// The commit graph: a virtualised list with lane drawing and ref labels.

import { h, clear, icon, avatar, shortDate, fullDate, shortSha, button } from './util.js';
import * as ops from './ops.js';

const ROW_H = 28;
const LANE_MAX = 14; // lane spacing in px when there is room
const LANE_MIN = 8;
const GRAPH_MAX = 176; // widest the graph column grows before lanes are clipped
const PAD = 11;
const OVERSCAN = 10;
const FOOT_H = 44;
const REFS_ROOM = 158; // usable px in the ref label column
const TOP = 0;
const BOTTOM = 1;
const THROUGH = 2;
// How a row relates to the selected commit.
const UNRELATED = 0;
const ANCESTOR = 1;
const DESCENDANT = 2;
const SELECTED = 3;

let laneW = LANE_MAX;
const laneX = (i) => PAD + i * laneW;

/** SVG path for one edge segment inside a row. */
function edgePath(from, to, kind) {
  const mid = ROW_H / 2;
  const xf = laneX(from);
  const xt = laneX(to);
  if (kind !== TOP && kind !== BOTTOM) return `M${xf} 0V${ROW_H}`;
  if (from === to) return kind === TOP ? `M${xf} 0V${mid}` : `M${xf} ${mid}V${ROW_H}`;
  const dir = xt > xf ? 1 : -1;
  const r = Math.min(7, Math.abs(xt - xf), laneW);
  if (kind === TOP) {
    // Down the child's lane, then round the corner into the node.
    return `M${xf} 0V${mid - r}Q${xf} ${mid} ${xf + dir * r} ${mid}H${xt}`;
  }
  // Out of the node, then round the corner down the parent's lane.
  return `M${xf} ${mid}H${xt - dir * r}Q${xt} ${mid} ${xt} ${mid + r}V${ROW_H}`;
}

/**
 * Whether an edge lies between the selected commit and a relative.
 * - Below the selection: one of the edge's children is the selection or an ancestor.
 * - Above the selection: the edge's parent is the selection or a descendant.
 */
function onLineage(edge, rel) {
  const parent = edge[4];
  if (parent >= 0 && rel[parent] >= DESCENDANT) return true;
  return edge[5].some((child) => rel[child] === ANCESTOR || rel[child] === SELECTED);
}

/** Draw one row's lanes. rel is the lineage map, or null when nothing is highlighted. */
function rowSvg(row, lanes, rel) {
  const width = PAD * 2 + Math.max(0, lanes - 1) * laneW;
  const mid = ROW_H / 2;
  const parts = [];
  // Faded lines first, and straight segments before the curved joins that cross them.
  const order = (e) => (e.lit ? 2 : 0) + (e.edge[3] === THROUGH ? 0 : 1);
  const edges = row.edges.map((edge) => ({ edge, lit: rel ? onLineage(edge, rel) : false }));
  edges.sort((a, b) => order(a) - order(b));
  for (const { edge: [from, to, color, kind], lit } of edges) {
    const wip = row.kind === 'wip' ? ' wip' : '';
    const tone = rel ? (lit ? ' lit' : ' faint') : '';
    parts.push(`<path class="g-edge lane-${color % 6}${wip}${tone}" d="${edgePath(from, to, kind)}"/>`);
  }
  const x = laneX(row.col);
  const lane = `lane-${row.color % 6}`;
  if (row.kind === 'wip') {
    parts.push(`<circle class="g-node hollow wip ${lane}" cx="${x}" cy="${mid}" r="5"/>`);
  } else if (row.kind === 'stash') {
    parts.push(`<rect class="g-node hollow ${lane}" x="${x - 4}" y="${mid - 4}" width="8" height="8"/>`);
  } else if (row.parents.length > 1) {
    parts.push(`<circle class="g-node hollow ${lane}" cx="${x}" cy="${mid}" r="3.5"/>`);
  } else {
    parts.push(`<circle class="g-node ${lane}" cx="${x}" cy="${mid}" r="${row.isHead ? 5.5 : 4.5}"/>`);
  }
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width', width);
  svg.setAttribute('height', ROW_H);
  svg.setAttribute('viewBox', `0 0 ${width} ${ROW_H}`);
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = parts.join('');
  return svg;
}

/**
 * Turn the refs on a commit into label chips.
 * - A local branch and its same-named remote branch share one chip.
 * - Returns { chips, names } where names lists everything for the tooltip.
 */
function refChips(refs) {
  const chips = [];
  const used = new Set();
  const locals = refs.filter((r) => r.type === 'local');
  const remotes = refs.filter((r) => r.type === 'remote');
  for (const ref of refs.filter((r) => r.type === 'detached')) {
    chips.push({ cls: 'detached', icons: ['circle-dot'], label: 'HEAD', ref });
  }
  for (const ref of locals) {
    const twin = remotes.find((r) => r.branch === ref.name && !used.has(r));
    if (twin) used.add(twin);
    chips.push({
      cls: ref.current ? 'head' : 'local',
      icons: twin ? ['laptop', 'cloud'] : ['laptop'],
      label: ref.name,
      ref,
      title: twin ? `${ref.name} and ${twin.name}` : ref.name,
    });
  }
  for (const ref of remotes) {
    if (used.has(ref)) continue;
    chips.push({ cls: 'remote', icons: ['cloud'], label: ref.name, ref });
  }
  for (const ref of refs.filter((r) => r.type === 'tag')) {
    chips.push({ cls: 'tag', icons: ['tag'], label: ref.name, ref });
  }
  chips.sort((a, b) => (b.cls === 'head') - (a.cls === 'head'));
  return chips;
}

export class Graph {
  constructor(view) {
    this.view = view;
    this.rows = [];
    this.index = new Map();
    this.lanes = 1;
    this.hasMore = false;
    this.selected = null;
    this.matches = null; // Set of row indexes matching the filter, or null
    this.lineageOn = true; // the "highlight the selected commit's history" setting
    this.kids = []; // row indexes of each row's children
    this.rel = null; // per row: how it relates to the selection, or null when not highlighting
    this.rendered = new Map();
    this.frame = 0;

    const col = (label) => h('div', null, h('p', { class: 'eyebrow' }, label));
    this.head = h('div', { class: 'graph-head' },
      col('Branch / tag'), col('Graph'), col('Commit message'), col('Author'), col('Date'), col('SHA'));
    this.space = h('div', { class: 'graph-space' });
    this.scroll = h('div', { class: 'graph-scroll', tabindex: '0', role: 'listbox', 'aria-label': 'Commits' }, this.space);
    this.el = h('div', { class: 'graph' }, this.head, this.scroll);

    this.scroll.addEventListener('scroll', () => this.schedule(), { passive: true });
    this.scroll.addEventListener('keydown', (ev) => this.onKey(ev));
    this.scroll.addEventListener('click', (ev) => this.onClick(ev));
    this.scroll.addEventListener('dblclick', (ev) => this.onDoubleClick(ev));
    this.scroll.addEventListener('contextmenu', (ev) => this.onContext(ev));
    this.observer = new ResizeObserver(() => this.schedule());
    this.observer.observe(this.scroll);
  }

  destroy() {
    this.observer.disconnect();
    cancelAnimationFrame(this.frame);
  }

  setData(log) {
    this.rows = log.commits;
    this.hasMore = log.has_more;
    // Busy histories get narrower lanes so the message column keeps its room.
    this.laneW = Math.max(LANE_MIN, Math.min(LANE_MAX, Math.floor((GRAPH_MAX - PAD * 2) / Math.max(1, log.width - 1))));
    this.lanes = Math.max(1, Math.min(log.width, Math.floor((GRAPH_MAX - PAD * 2) / this.laneW) + 1));
    this.index = new Map(this.rows.map((row, i) => [row.sha, i]));
    const headSha = log.head && log.head.sha;
    for (const row of this.rows) row.isHead = row.sha === headSha;
    this.kids = this.rows.map(() => []);
    this.rows.forEach((row, i) => {
      for (const parent of row.parents || []) {
        const at = this.index.get(parent);
        if (at != null) this.kids[at].push(i);
      }
    });
    this.traceLineage();
    this.el.style.setProperty('--col-graph', `${Math.max(72, PAD * 2 + (this.lanes - 1) * this.laneW + 6)}px`);
    this.space.style.height = `${this.rows.length * ROW_H + (this.hasMore ? FOOT_H : 0)}px`;
    this.redraw();
  }

  /**
   * Work out which rows are ancestors or descendants of the selected commit.
   * - Only loaded commits are followed.
   * - Nothing is highlighted while the setting is off or a search filter is showing.
   */
  traceLineage() {
    const start = this.index.get(this.selected);
    if (!this.lineageOn || this.matches || start == null) {
      this.rel = null;
      return;
    }
    const rel = new Uint8Array(this.rows.length);
    const walk = (mark, next) => {
      const queue = [start];
      while (queue.length) {
        for (const i of next(queue.pop())) {
          if (rel[i] === UNRELATED) { rel[i] = mark; queue.push(i); }
        }
      }
    };
    rel[start] = SELECTED;
    walk(ANCESTOR, (i) => (this.rows[i].parents || []).map((sha) => this.index.get(sha)).filter((at) => at != null));
    walk(DESCENDANT, (i) => this.kids[i]);
    this.rel = rel;
  }

  setLineage(on) {
    if (this.lineageOn === on) return;
    this.lineageOn = on;
    this.traceLineage();
    this.redraw();
  }

  redraw() {
    this.rendered.clear();
    clear(this.space);
    if (this.hasMore) {
      this.space.append(h('div', { class: 'graph-foot', style: { top: `${this.rows.length * ROW_H}px` } },
        button('Load more commits', () => this.view.loadMore(), { class: 'sm ghost', icon: 'history' })));
    }
    this.draw();
  }

  schedule() {
    cancelAnimationFrame(this.frame);
    this.frame = requestAnimationFrame(() => this.draw());
  }

  draw() {
    const top = this.scroll.scrollTop;
    const height = this.scroll.clientHeight || 600;
    const first = Math.max(0, Math.floor(top / ROW_H) - OVERSCAN);
    const last = Math.min(this.rows.length - 1, Math.ceil((top + height) / ROW_H) + OVERSCAN);
    for (const [i, el] of this.rendered) {
      if (i < first || i > last) {
        el.remove();
        this.rendered.delete(i);
      }
    }
    for (let i = first; i <= last; i++) {
      if (!this.rendered.has(i)) {
        const el = this.buildRow(i);
        this.rendered.set(i, el);
        this.space.append(el);
      }
    }
  }

  buildRow(i) {
    const row = this.rows[i];
    laneW = this.laneW;
    const classes = ['graph-row'];
    if (row.sha === this.selected) classes.push('selected');
    if (row.isHead) classes.push('is-head');
    if (row.kind) classes.push(row.kind);
    if (this.matches) classes.push(this.matches.has(i) ? 'match' : 'dim');
    if (this.rel && this.rel[i] === UNRELATED) classes.push('off-line');

    const refsCell = h('div', { class: 'g-refs' });
    if (row.kind === 'stash') {
      refsCell.append(h('span', { class: 'chip stash', title: row.subject }, icon('archive', 11), h('span', null, `stash@{${row.stash_index}}`)));
    } else if (row.kind !== 'wip') {
      const refs = this.view.refsBySha.get(row.sha);
      if (refs) {
        const chips = refChips(refs);
        // Estimate which chips fit; the rest go behind a "+N" chip.
        let room = REFS_ROOM;
        const shown = [];
        for (const chip of chips) {
          const need = 16 + chip.icons.length * 14 + chip.label.length * 6.3;
          const reserve = chips.length - shown.length > 1 ? 30 : 0;
          if (shown.length && need > room - reserve) break;
          shown.push(chip);
          room -= Math.min(need, room) + 4;
        }
        for (const chip of shown) {
          const el = h('span', { class: `chip ${chip.cls}`, title: chip.title || chip.label },
            chip.icons.map((name) => icon(name, 11)), h('span', null, chip.label));
          el.chipRef = chip.ref;
          refsCell.append(el);
        }
        if (chips.length > shown.length) {
          const rest = chips.slice(shown.length);
          const more = h('span', { class: 'chip more', title: rest.map((c) => c.label).join('\n') }, `+${rest.length}`);
          more.chipRest = rest;
          refsCell.append(more);
        }
      }
    }

    let message = row.subject;
    if (row.kind === 'wip') {
      const st = this.view.data.status;
      const n = st.staged.length + st.unstaged.length + st.conflicted.length;
      message = `Uncommitted changes (${n} ${n === 1 ? 'file' : 'files'})`;
    }

    const el = h('div', {
      class: classes.join(' '),
      role: 'option',
      'aria-selected': row.sha === this.selected ? 'true' : 'false',
      style: { top: `${i * ROW_H}px` },
    },
    refsCell,
    h('div', { class: 'g-graph' }, rowSvg(row, this.lanes, this.rel)),
    h('div', { class: 'g-msg', title: message }, message),
    h('div', { class: 'g-author' }, row.author ? [avatar(row.author), h('span', { class: 'ellipsis' }, row.author)] : null),
    h('div', { class: 'g-date', title: fullDate(row.date) }, shortDate(row.date)),
    h('div', { class: 'g-sha' }, row.kind === 'wip' ? '' : shortSha(row.sha)));
    el.rowIndex = i;
    return el;
  }

  rowFromEvent(ev) {
    const el = ev.target.closest('.graph-row');
    return el ? this.rows[el.rowIndex] : null;
  }

  onClick(ev) {
    const row = this.rowFromEvent(ev);
    if (!row) return;
    const more = ev.target.closest('.chip.more');
    if (more && more.chipRest) {
      ev.stopPropagation();
      ops.refListMenu(this.view, more.chipRest.map((c) => c.ref), more);
      return;
    }
    this.view.select(row.sha);
    this.scroll.focus({ preventScroll: true });
  }

  onDoubleClick(ev) {
    const chip = ev.target.closest('.chip');
    if (chip && chip.chipRef) {
      ops.checkoutRef(this.view, chip.chipRef);
    }
  }

  onContext(ev) {
    const row = this.rowFromEvent(ev);
    if (!row) return;
    ev.preventDefault();
    this.view.select(row.sha);
    const chip = ev.target.closest('.chip');
    const at = { x: ev.clientX, y: ev.clientY };
    if (chip && chip.chipRef) ops.refMenu(this.view, chip.chipRef, at);
    else ops.commitMenu(this.view, row, at);
  }

  onKey(ev) {
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    const at = this.index.get(this.selected);
    let next = null;
    if (ev.key === 'ArrowDown') next = at == null ? 0 : at + 1;
    else if (ev.key === 'ArrowUp') next = at == null ? 0 : at - 1;
    else if (ev.key === 'Home') next = 0;
    else if (ev.key === 'End') next = this.rows.length - 1;
    else if (ev.key === 'PageDown') next = (at || 0) + Math.max(1, Math.floor(this.scroll.clientHeight / ROW_H) - 1);
    else if (ev.key === 'PageUp') next = (at || 0) - Math.max(1, Math.floor(this.scroll.clientHeight / ROW_H) - 1);
    if (next == null) return;
    ev.preventDefault();
    next = Math.max(0, Math.min(this.rows.length - 1, next));
    if (this.rows[next]) this.view.select(this.rows[next].sha, { scroll: true });
  }

  setSelected(sha) {
    if (this.selected === sha) return;
    const prev = this.rendered.get(this.index.get(this.selected));
    if (prev) { prev.classList.remove('selected'); prev.setAttribute('aria-selected', 'false'); }
    this.selected = sha;
    const next = this.rendered.get(this.index.get(sha));
    if (next) { next.classList.add('selected'); next.setAttribute('aria-selected', 'true'); }
    const had = this.rel;
    this.traceLineage();
    if (had || this.rel) this.redraw(); // every visible row may change tone
  }

  /** Scroll a commit into view. Returns false when it is not loaded. */
  scrollTo(sha) {
    const i = this.index.get(sha);
    if (i == null) return false;
    const top = i * ROW_H;
    const view = this.scroll.clientHeight;
    if (top < this.scroll.scrollTop + ROW_H) this.scroll.scrollTop = Math.max(0, top - ROW_H * 2);
    else if (top + ROW_H > this.scroll.scrollTop + view - ROW_H) this.scroll.scrollTop = top - view + ROW_H * 3;
    this.draw();
    return true;
  }

  /**
   * Dim every commit that does not match the text.
   * - Matches subject, author, SHA prefix and ref names.
   * - Returns the number of matches, or null when the filter is empty.
   */
  setFilter(text) {
    const query = (text || '').trim().toLowerCase();
    if (!query) {
      this.matches = null;
      this.matchList = [];
      this.traceLineage();
      this.redraw();
      return null;
    }
    const matches = new Set();
    this.rows.forEach((row, i) => {
      if (row.kind === 'wip') return;
      const refs = this.view.refsBySha.get(row.sha);
      const hay = [row.subject, row.author, row.email, refs ? refs.map((r) => r.name).join(' ') : ''].join('\n').toLowerCase();
      if (hay.includes(query) || row.sha.startsWith(query)) matches.add(i);
    });
    this.matches = matches;
    this.matchList = [...matches];
    this.traceLineage();
    this.redraw();
    return matches.size;
  }

  /** Select the next (or previous) commit that matches the filter. */
  stepMatch(dir = 1) {
    if (!this.matchList || !this.matchList.length) return;
    const at = this.index.get(this.selected) ?? -1;
    let target;
    if (dir > 0) target = this.matchList.find((i) => i > at) ?? this.matchList[0];
    else target = [...this.matchList].reverse().find((i) => i < at) ?? this.matchList[this.matchList.length - 1];
    this.view.select(this.rows[target].sha, { scroll: true });
  }
}
