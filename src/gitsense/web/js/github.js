// GitHub pull requests: the sidebar list and the "Create pull request" dialog.

import { api } from './api.js';
import { h, clear, append, icon, button, copyText, relTime, plural } from './util.js';
import { toast, toastError, withActivity, showMenu, openModal, confirmDialog } from './ui.js';

const MAX_AGE_MS = 2 * 60 * 1000;
const GH_INSTALL_URL = 'https://cli.github.com';

const CHECKS = {
  passing: { icon: 'check', label: 'Checks passing' },
  failing: { icon: 'x', label: 'Checks failing' },
  pending: { icon: 'clock', label: 'Checks running' },
};

const REVIEWS = {
  APPROVED: { icon: 'circle-check', label: 'Approved', cls: 'approved' },
  CHANGES_REQUESTED: { icon: 'file-diff', label: 'Changes requested', cls: 'changes' },
  REVIEW_REQUIRED: { label: 'Review required' },
};

// -- sidebar list -------------------------------------------------------------

/**
 * Per-view cache of the pull request list.
 * - result is { kind: 'list' | 'missing' | 'signedout' | 'error', prs, error }
 * - host and opts remember the last render so the list can repaint itself
 */
function cacheOf(view) {
  if (!view.prCache) view.prCache = { slug: null, at: 0, result: null, pending: null, host: null, opts: {} };
  return view.prCache;
}

/** Fill `host` with the open pull requests of the view's repository. */
export async function renderPullRequests(view, host, opts = {}) {
  const cache = cacheOf(view);
  cache.host = host;
  cache.opts = { filter: opts.filter, onCount: opts.onCount };
  const gh = view.data && view.data.github;
  if (!gh) {
    paint(view, host, { kind: 'noremote' }, opts);
    return;
  }
  const known = cache.result && cache.slug === gh.slug ? cache.result : null;
  if (known && !opts.force && Date.now() - cache.at < MAX_AGE_MS) {
    paint(view, host, known, opts);
    return;
  }
  // Rows already shown stay visible while the list reloads.
  if (known && known.kind === 'list') paint(view, host, known, opts);
  else paint(view, host, { kind: 'loading' }, opts);
  const result = await load(view, gh.slug);
  if (cache.host === host) paint(view, host, result, opts);
}

/** Reload the list into the host it was last drawn in. */
export function refreshPullRequests(view) {
  const cache = cacheOf(view);
  cache.at = 0;
  if (cache.host && cache.host.isConnected) {
    return renderPullRequests(view, cache.host, { ...cache.opts, force: true });
  }
  return Promise.resolve();
}

function load(view, slug) {
  const cache = cacheOf(view);
  if (cache.pending && cache.pending.slug === slug) return cache.pending.promise;
  const promise = (async () => {
    let result;
    try {
      const status = await view.app.github();
      if (!status.installed) result = { kind: 'missing' };
      else if (!status.authenticated) result = { kind: 'signedout' };
      else {
        const res = await api.get('/api/github/prs', { id: view.repo.id });
        result = { kind: 'list', prs: res.prs || [] };
      }
    } catch (err) {
      result = { kind: 'error', error: err };
    }
    cache.result = result;
    cache.slug = slug;
    cache.at = Date.now();
    if (cache.pending && cache.pending.promise === promise) cache.pending = null;
    return result;
  })();
  cache.pending = { slug, promise };
  return promise;
}

function paint(view, host, result, opts) {
  const list = h('div', { class: 'pr-list' });
  clear(host).append(list);
  const retry = async () => {
    await view.app.github(true);
    renderPullRequests(view, host, { ...opts, force: true });
  };
  switch (result.kind) {
    case 'noremote':
      list.append(note('cloud', 'This repository has no GitHub remote.'));
      return;
    case 'loading':
      list.append(note('loader-circle', 'Loading pull requests…', { spin: true }));
      return;
    case 'missing':
      list.append(note('info', 'Install the GitHub CLI (gh) to see pull requests.', {
        actions: [
          linkButton('Get the GitHub CLI', () => openUrl(GH_INSTALL_URL)),
          linkButton('Check again', retry),
        ],
      }));
      return;
    case 'signedout':
      list.append(note('lock', ['Sign in to GitHub: run ', h('code', { class: 'pr-code selectable' }, 'gh auth login'), ' in a terminal.'], {
        actions: [linkButton('Check again', retry)],
      }));
      return;
    case 'error':
      list.append(note('circle-alert', 'Could not load pull requests.', {
        error: true,
        detail: result.error && (result.error.detail || result.error.message),
        actions: [linkButton('Try again', retry)],
      }));
      return;
    default:
      break;
  }

  const prs = result.prs || [];
  if (opts.onCount) opts.onCount(prs.length);
  if (!prs.length) {
    const branch = currentBranch(view);
    list.append(note('git-pull-request', 'No open pull requests.', {
      actions: branch ? [linkButton('Create pull request', () => createPullRequestDialog(view, branch))] : [],
    }));
    return;
  }
  const filter = (opts.filter || '').trim().toLowerCase();
  const shown = filter ? prs.filter((pr) => matches(pr, filter)) : prs;
  if (!shown.length) {
    list.append(note(null, 'No matching pull requests.'));
    return;
  }
  for (const pr of shown) list.append(prRow(view, pr));
}

function matches(pr, filter) {
  const text = [`#${pr.number}`, pr.title, pr.head, pr.base, pr.author, pr.head_owner].filter(Boolean).join(' ').toLowerCase();
  return text.includes(filter);
}

function note(name, message, { spin = false, error = false, detail = '', actions = [] } = {}) {
  return h('div', { class: `pr-note ${error ? 'pr-note-error' : ''}`.trim(), role: error ? 'alert' : null },
    name ? icon(name, 13, spin ? 'spin' : '') : null,
    h('div', { class: 'grow' },
      h('p', { class: 'pr-note-text' }, message),
      detail ? h('p', { class: 'pr-note-detail selectable', title: detail }, detail) : null,
      actions.length ? h('div', { class: 'pr-note-actions' }, actions) : null));
}

function linkButton(label, onClick) {
  return h('button', { class: 'pr-link', type: 'button', onclick: (ev) => { ev.stopPropagation(); onClick(); } }, label);
}

function currentBranch(view) {
  const head = view.data && view.data.head;
  return head && !head.detached && !head.unborn ? head.branch : null;
}

/** True when this pull request's branch is the one checked out. */
function isCheckedOut(view, pr) {
  return !pr.fork && pr.head === currentBranch(view);
}

function tooltip(view, pr) {
  const from = pr.fork && pr.head_owner ? `${pr.head_owner}:${pr.head}` : pr.head;
  const facts = [
    pr.draft ? 'Draft' : null,
    pr.checks ? CHECKS[pr.checks]?.label : null,
    pr.review ? REVIEWS[pr.review]?.label : null,
    pr.mergeable === 'CONFLICTING' ? 'Has conflicts' : null,
  ].filter(Boolean);
  const updated = pr.updated ? relTime(Date.parse(pr.updated) / 1000) : '';
  return [
    `#${pr.number} ${pr.title}`,
    `${pr.author || 'Unknown author'} wants to merge ${from} into ${pr.base}`,
    facts.join(' · '),
    updated ? `Updated ${updated}` : '',
    isCheckedOut(view, pr) ? 'Checked out' : 'Double-click to check out',
  ].filter(Boolean).join('\n');
}

function markers(pr) {
  const out = [];
  const review = REVIEWS[pr.review];
  if (review && review.icon) {
    out.push(h('span', { class: `pr-mark pr-review-${review.cls}`, role: 'img', 'aria-label': review.label, title: review.label }, icon(review.icon, 12)));
  }
  const checks = CHECKS[pr.checks];
  if (checks) {
    out.push(h('span', { class: `pr-mark pr-checks-${pr.checks}`, role: 'img', 'aria-label': checks.label, title: checks.label }, icon(checks.icon, 12)));
  }
  return out;
}

function prRow(view, pr) {
  const current = isCheckedOut(view, pr);
  const row = h('div', {
    class: ['side-row', 'pr-row', pr.draft ? 'pr-draft' : '', current ? 'current' : ''].filter(Boolean).join(' '),
    tabindex: '0',
    title: tooltip(view, pr),
    dataset: { number: String(pr.number) },
    ondblclick: () => checkoutPullRequest(view, pr),
    oncontextmenu: (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      prMenu(view, pr, { x: ev.clientX, y: ev.clientY });
    },
    onkeydown: (ev) => {
      if (ev.key === 'Enter') {
        ev.preventDefault();
        ev.stopPropagation();
        checkoutPullRequest(view, pr);
      } else if (ev.key === 'ContextMenu' || (ev.key === 'F10' && ev.shiftKey)) {
        ev.preventDefault();
        const r = row.getBoundingClientRect();
        prMenu(view, pr, { x: r.left + 24, y: r.bottom });
      } else if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        const next = ev.key === 'ArrowDown' ? row.nextElementSibling : row.previousElementSibling;
        if (next && next.classList.contains('pr-row')) {
          ev.preventDefault();
          next.focus();
        }
      }
    },
  },
  icon(pr.draft ? 'git-pull-request-draft' : 'git-pull-request', 14),
  h('span', { class: 'side-label ellipsis' }, h('span', { class: 'pr-number' }, `#${pr.number}`), ' ', pr.title),
  h('span', { class: 'side-meta pr-marks' }, markers(pr)));
  return row;
}

function prMenu(view, pr, at) {
  showMenu(at, [
    { heading: `Pull request #${pr.number}` },
    { label: isCheckedOut(view, pr) ? 'Check out again' : 'Check out', icon: 'git-branch', onClick: () => checkoutPullRequest(view, pr) },
    { label: 'Open on GitHub', icon: 'external-link', onClick: () => openUrl(pr.url) },
    { sep: true },
    { label: 'Copy link', icon: 'link', onClick: () => copy(pr.url, 'Link copied') },
    { label: 'Copy branch name', icon: 'copy', onClick: () => copy(pr.head, 'Branch name copied') },
  ]);
}

async function copy(text, done) {
  if (await copyText(text)) toast(done, 'ok');
  else toast('Could not copy to the clipboard', 'error');
}

async function openUrl(url) {
  try {
    await api.post('/api/open_url', { url });
  } catch (err) {
    toastError(err);
  }
}

function hasChanges(view) {
  const st = (view.data && view.data.status) || {};
  return ['staged', 'unstaged', 'conflicted'].some((key) => (st[key] || []).length > 0);
}

/** Check out a pull request with `gh pr checkout`, then refresh the view. */
export async function checkoutPullRequest(view, pr) {
  if (hasChanges(view)) {
    const ok = await confirmDialog({
      eyebrow: 'Pull request',
      title: 'Check out with changes',
      message: `You have uncommitted changes. Git carries them over to #${pr.number}, or stops without changing anything if they would be overwritten.`,
      confirmLabel: 'Check out',
    });
    if (!ok) return;
  }
  try {
    await withActivity(`Checking out #${pr.number}…`, api.post('/api/github/pr_checkout', { id: view.repo.id, number: pr.number }));
    toast(`Checked out #${pr.number} on ${pr.head}`, 'ok');
  } catch (err) {
    toastError(err);
  }
  await view.refresh();
}

// -- create dialog ------------------------------------------------------------

const DEFAULT_BASES = ['main', 'master', 'develop', 'trunk'];

function branchEntry(view, name) {
  return ((view.data && view.data.branches) || []).find((b) => b.name === name) || null;
}

/**
 * Base branch choices for a new pull request.
 * - Taken from the GitHub remote's branches, or the local ones when the remote has none
 * - The likely default branch comes first
 */
function baseChoices(view, gh, head) {
  const remote = ((view.data && view.data.remotes) || []).find((r) => r.name === gh.remote);
  let names = (remote ? remote.branches : []).map((b) => b.name);
  if (!names.length) names = ((view.data && view.data.branches) || []).map((b) => b.name);
  names = [...new Set(names)].filter((n) => n && n !== head && n !== 'HEAD').sort((a, b) => a.localeCompare(b));
  const known = gh.default_branch && names.includes(gh.default_branch) ? gh.default_branch : null;
  const preferred = known || DEFAULT_BASES.find((n) => names.includes(n)) || names[0] || 'main';
  return { names: [preferred, ...names.filter((n) => n !== preferred)], preferred };
}

/** The branch name on the GitHub remote, which can differ from the local name. */
function remoteHead(gh, name, entry) {
  const prefix = `${gh.remote}/`;
  if (entry && entry.upstream && !entry.gone && entry.upstream.startsWith(prefix)) return entry.upstream.slice(prefix.length);
  return name;
}

/** What needs pushing before the pull request can be created, or null. */
function pushPlan(view, gh, name) {
  const entry = branchEntry(view, name);
  if (!entry || !entry.upstream || entry.gone) {
    return { args: { branch: name, remote: gh.remote }, text: `${name} is pushed to ${gh.remote} first.` };
  }
  if (entry.ahead > 0) {
    return { args: { branch: name }, text: `${plural(entry.ahead, 'new commit')} on ${name} ${entry.ahead === 1 ? 'is' : 'are'} pushed first.` };
  }
  return null;
}

async function lastSubject(view, entry) {
  if (!entry || !entry.sha) return '';
  try {
    const detail = await api.get('/api/repo/commit', { id: view.repo.id, sha: entry.sha });
    return detail.subject || '';
  } catch {
    return '';
  }
}

/** Open the "Create pull request" dialog for a local branch (default: the current one). */
export function createPullRequestDialog(view, branch) {
  const name = branch || currentBranch(view);
  if (!name) {
    toast('Check out a branch to create a pull request from it', 'info');
    return Promise.resolve(null);
  }
  const gh = view.data && view.data.github;
  if (!gh) {
    toast('This repository has no GitHub remote', 'info');
    return Promise.resolve(null);
  }
  return openCreateDialog(view, name, gh);
}

async function openCreateDialog(view, name, gh) {
  const status = await view.app.github();
  if (!status.installed) {
    toast('Install the GitHub CLI (gh) to create pull requests', 'error', GH_INSTALL_URL);
    return null;
  }
  if (!status.authenticated) {
    toast('Sign in to GitHub first', 'error', 'Run `gh auth login` in a terminal, then try again.');
    return null;
  }
  const entry = branchEntry(view, name);
  const subject = await lastSubject(view, entry);
  const { names, preferred } = baseChoices(view, gh, name);
  const plan = pushPlan(view, gh, name);

  let created = null;
  const result = await openModal({
    eyebrow: gh.slug,
    title: 'Create pull request',
    wide: true,
    build: (close) => {
      const base = h('select', { class: 'input', 'aria-label': 'Base branch' },
        names.map((n) => h('option', { value: n, selected: n === preferred }, n)));
      const title = h('input', {
        class: 'input', type: 'text', value: subject || name, autofocus: true,
        spellcheck: 'true', autocomplete: 'off', 'aria-label': 'Title',
      });
      const body = h('textarea', {
        class: 'input pr-body', rows: 8, spellcheck: 'true', 'aria-label': 'Description',
        placeholder: 'What changed and why (optional)',
      });
      const draft = h('input', { type: 'checkbox' });
      const error = h('p', { class: 'modal-error hidden', role: 'alert' });
      const form = h('div', { class: 'pr-form' },
        h('div', { class: 'field' },
          h('span', { class: 'field-label' }, 'Branches'),
          h('div', { class: 'pr-branches' },
            h('span', { class: 'pr-branch', title: `Merge ${name}` }, icon('git-branch', 13), h('span', { class: 'ellipsis' }, name)),
            h('span', { class: 'pr-into' }, 'into'),
            h('div', { class: 'pr-base' }, base))),
        plan ? h('div', { class: 'pr-push-note' }, icon('arrow-up-from-line', 13), plan.text) : null,
        h('div', { class: 'field' }, h('label', null, 'Title'), title),
        h('div', { class: 'field' }, h('label', null, 'Description'), body),
        h('label', { class: 'check pr-draft-check' }, draft, 'Create as a draft'),
        error);
      const cancel = button('Cancel', () => close(null), { class: 'ghost' });
      const ok = button('Create pull request', () => submit(), { class: 'primary', icon: 'git-pull-request' });

      const fail = (message) => {
        error.textContent = message;
        error.classList.remove('hidden');
      };
      const busy = (label) => {
        ok.disabled = Boolean(label);
        cancel.disabled = Boolean(label);
        const backdrop = ok.closest('.modal-backdrop');
        if (backdrop) backdrop.dataset.busy = label ? '1' : '';
        ok.lastChild.textContent = label || 'Create pull request';
      };

      async function submit() {
        const values = { title: title.value.trim(), body: body.value, base: base.value, draft: draft.checked };
        if (!values.title) { fail('A title is required'); title.focus(); return; }
        error.classList.add('hidden');
        // view.data is refreshed after every push, so the plan is worked out again on a retry.
        const needed = pushPlan(view, gh, name);
        if (needed) {
          busy('Pushing…');
          const pushed = await view.run('push', needed.args, { quiet: true, label: `Pushing ${name}…` });
          if (!pushed) { busy(null); fail(`${name} could not be pushed, so no pull request was created.`); return; }
        }
        busy('Creating…');
        try {
          const res = await withActivity('Creating pull request…', api.post('/api/github/pr_create', {
            id: view.repo.id, ...values, head: remoteHead(gh, name, branchEntry(view, name)),
          }));
          showCreated(res.url || '');
        } catch (err) {
          busy(null);
          fail([err.message, err.detail && err.detail !== err.message ? err.detail : ''].filter(Boolean).join('\n'));
        }
      }

      function showCreated(url) {
        const number = (url.match(/\/pull\/(\d+)/) || [])[1];
        const label = number ? `Pull request #${number} created` : 'Pull request created';
        created = { label, url };
        refreshPullRequests(view);
        const modal = form.closest('.modal');
        if (modal) {
          modal.querySelector('.modal-head .title').textContent = 'Pull request created';
          modal.setAttribute('aria-label', 'Pull request created');
        }
        clear(form).append(h('div', { class: 'pr-done' },
          icon('circle-check', 22),
          h('div', { class: 'grow' },
            h('p', { class: 'heading' }, label),
            url ? h('p', { class: 'pr-done-url selectable mono' }, url) : null)));
        const foot = ok.parentNode;
        const done = button('Done', () => close(url), { class: 'primary' });
        append(clear(foot), [
          url ? button('Copy link', () => copy(url, 'Link copied'), { class: 'ghost', icon: 'link' }) : null,
          h('span', { class: 'spacer' }),
          url ? button('Open on GitHub', () => { openUrl(url); close(url); }, { icon: 'external-link' }) : null,
          done]);
        done.focus();
      }

      return { body: form, foot: [cancel, ok] };
    },
  });
  // Shown after the dialog closes so the toast does not sit on top of it.
  if (created) toast(created.label, 'ok', created.url);
  return result;
}
