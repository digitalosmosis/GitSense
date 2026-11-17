// Git operations as the user sees them: context menus and the dialogs behind them.

import { api } from './api.js';
import { h, copyText, shortSha, plural } from './util.js';
import { toast, showMenu, confirmDialog, formDialog } from './ui.js';
import { createPullRequestDialog } from './github.js';

const copy = async (text, what) => {
  if (await copyText(text)) toast(`${what} copied`, 'ok');
};

const code = (text) => h('code', null, text);

// -- checkout -------------------------------------------------------------------

/**
 * Check out a branch, tag or commit.
 * - If local changes block the switch, offer to stash them first.
 */
export async function checkout(view, action, args, label) {
  try {
    await view.exec(action, args, `Checking out ${label}`);
    await view.refresh();
    toast(`Checked out ${label}`, 'ok');
  } catch (err) {
    const blocked = /would be overwritten|commit your changes or stash them|local changes/i.test(`${err.message} ${err.detail}`);
    if (!blocked) {
      view.fail(err);
      return;
    }
    const ok = await confirmDialog({
      eyebrow: 'Check out',
      title: 'Local changes are in the way',
      message: h('p', null, 'Your uncommitted changes would be overwritten by checking out ', code(label), '. Stash them and switch? The stash stays in the list until you apply it.'),
      confirmLabel: 'Stash and switch',
    });
    if (!ok) return;
    const stashed = await view.run('stash_save', { message: `Before switching to ${label}`, include_untracked: true }, { quiet: true });
    if (stashed) await view.run(action, args, { label: `Checking out ${label}`, success: `Checked out ${label}. Your changes are in the newest stash.` });
  }
}

export function checkoutRef(view, ref) {
  if (ref.type === 'local') {
    if (!ref.current) checkout(view, 'checkout', { ref: ref.name }, ref.name);
  } else if (ref.type === 'remote') {
    checkout(view, 'checkout_remote', { full: ref.name }, ref.branch);
  } else if (ref.type === 'tag') {
    checkout(view, 'checkout', { ref: ref.name, detach: true }, ref.name);
  }
}

// -- branches -------------------------------------------------------------------

export function createBranch(view, start = null, startLabel = '') {
  return formDialog({
    eyebrow: 'Branch',
    title: 'Create a branch',
    intro: start ? h('p', null, 'Starts at ', code(startLabel || shortSha(start)), '.') : h('p', null, 'Starts at the current commit.'),
    fields: [
      { name: 'name', label: 'Branch name', placeholder: 'feature/my-change', required: true },
      { name: 'checkout', type: 'checkbox', label: 'Check out the new branch', value: true },
    ],
    confirmLabel: 'Create branch',
    validate: (v) => (/\s/.test(v.name) ? 'Branch names cannot contain spaces' : null),
    submit: async (v) => {
      await view.exec('branch_create', { name: v.name, start, checkout: v.checkout });
      await view.refresh();
      toast(`Created ${v.name}`, 'ok');
    },
  });
}

export function renameBranch(view, name) {
  return formDialog({
    eyebrow: 'Branch',
    title: 'Rename branch',
    fields: [{ name: 'name', label: 'New name', value: name, required: true }],
    confirmLabel: 'Rename',
    submit: async (v) => {
      if (v.name !== name) await view.exec('branch_rename', { old: name, new: v.name });
      await view.refresh();
    },
  });
}

export async function deleteBranch(view, branch) {
  const ok = await confirmDialog({
    eyebrow: 'Branch',
    title: 'Delete branch',
    message: h('p', null, 'Delete the local branch ', code(branch.name), '? Commits that exist only on this branch become hard to find.'),
    confirmLabel: 'Delete branch',
    danger: true,
  });
  if (!ok) return;
  try {
    await view.exec('branch_delete', { name: branch.name }, `Deleting ${branch.name}`);
    await view.refresh();
    toast(`Deleted ${branch.name}`, 'ok');
  } catch (err) {
    if (!/not fully merged/i.test(`${err.message} ${err.detail}`)) {
      view.fail(err);
      return;
    }
    const force = await confirmDialog({
      eyebrow: 'Branch',
      title: 'Branch is not merged',
      message: h('p', null, code(branch.name), ' has commits that are not merged anywhere else. Delete it anyway?'),
      confirmLabel: 'Delete anyway',
      danger: true,
    });
    if (force) await view.run('branch_delete', { name: branch.name, force: true }, { success: `Deleted ${branch.name}` });
  }
}

export async function deleteRemoteBranch(view, ref) {
  const ok = await confirmDialog({
    eyebrow: 'Remote branch',
    title: 'Delete from remote',
    message: h('p', null, 'Delete ', code(ref.branch), ' from ', code(ref.remote), '? This removes the branch for everyone using that remote.'),
    confirmLabel: 'Delete from remote',
    danger: true,
  });
  if (ok) await view.run('remote_branch_delete', { remote: ref.remote, name: ref.branch }, { label: `Deleting ${ref.name}`, success: `Deleted ${ref.name}` });
}

export function setUpstream(view, branchName) {
  const options = view.data.remotes.flatMap((r) => r.branches.map((b) => ({ value: b.full, label: b.full })));
  if (!options.length) {
    toast('There are no remote branches yet. Push the branch to create one.', 'info');
    return null;
  }
  const branch = view.data.branches.find((b) => b.name === branchName);
  const guess = options.find((o) => o.value.endsWith(`/${branchName}`));
  return formDialog({
    eyebrow: 'Branch',
    title: 'Set upstream',
    intro: h('p', null, 'Choose the remote branch that ', code(branchName), ' pulls from and pushes to.'),
    fields: [{ name: 'upstream', label: 'Upstream branch', type: 'select', options, value: (branch && branch.upstream) || (guess && guess.value) || options[0].value }],
    confirmLabel: 'Set upstream',
    submit: async (v) => {
      await view.exec('set_upstream', { branch: branchName, upstream: v.upstream });
      await view.refresh();
    },
  });
}

// fullRef (refs/heads/x, refs/remotes/o/x) keeps git from picking a tag of the same name.
export function mergeRef(view, refName, fullRef = refName) {
  const current = view.data.head.branch || 'HEAD';
  return formDialog({
    eyebrow: 'Merge',
    title: `Merge into ${current}`,
    intro: h('p', null, 'Merge ', code(refName), ' into ', code(current), '.'),
    fields: [{
      name: 'mode', label: 'How to merge', type: 'select', value: 'default',
      options: [
        { value: 'default', label: 'Fast-forward when possible' },
        { value: 'no-ff', label: 'Always create a merge commit' },
        { value: 'ff-only', label: 'Fast-forward only, otherwise stop' },
        { value: 'squash', label: 'Squash into staged changes (no commit)' },
      ],
    }],
    confirmLabel: 'Merge',
    submit: async (v) => {
      const res = await view.exec('merge', { ref: fullRef, mode: v.mode }, `Merging ${refName}`);
      await view.afterAction(res, `Merged ${refName} into ${current}`);
    },
  });
}

export async function rebaseOnto(view, refName, fullRef = refName) {
  const current = view.data.head.branch || 'HEAD';
  const ok = await confirmDialog({
    eyebrow: 'Rebase',
    title: `Rebase ${current}`,
    message: h('p', null, 'Replay the commits of ', code(current), ' on top of ', code(refName), '. This rewrites those commits, so a branch that is already pushed will need a force push afterwards.'),
    confirmLabel: 'Rebase',
  });
  if (ok) await view.run('rebase', { onto: fullRef }, { label: `Rebasing onto ${refName}`, success: `Rebased ${current} onto ${refName}` });
}

export async function pushBranch(view, branchName = null, extra = {}) {
  const name = branchName || view.data.head.branch;
  if (!name) {
    toast('Check out a branch before pushing', 'info');
    return null;
  }
  // A force push after a rejection is a follow-up, not a second click.
  if (!extra.force && view.pendingOps('push').length) return null;
  try {
    const res = await view.exec('push', { branch: name, ...extra }, `Pushing ${name}`);
    await view.refresh();
    toast(`Pushed ${name}`, 'ok');
    return res;
  } catch (err) {
    const rejected = /rejected|non-fast-forward|fetch first|stale info/i.test(`${err.message} ${err.detail}`);
    if (!rejected || extra.force) {
      view.fail(err);
      return null;
    }
    await view.refresh();
    const force = await confirmDialog({
      eyebrow: 'Push',
      title: 'The remote has other commits',
      message: h('p', null, 'The push was rejected because ', code(name), ' on the remote has commits you do not have. Pull first to keep them. Force pushing replaces the remote branch with yours.'),
      detail: (err.detail || err.message).trim(),
      confirmLabel: 'Force push (with lease)',
      danger: true,
    });
    if (force) return pushBranch(view, name, { ...extra, force: true });
    return null;
  }
}

export function pull(view, mode = null) {
  const names = { 'ff-only': 'fast-forward only', rebase: 'rebase', merge: 'merge' };
  const chosen = mode || view.app.settings.pull_mode;
  if (view.pendingOps('pull').length) return null;
  return view.run('pull', { mode: chosen }, { label: `Pulling (${names[chosen] || chosen})`, success: 'Pulled' });
}

// -- commits --------------------------------------------------------------------

/** Change the message of the last commit. Staged changes are folded in, as with any amend. */
export async function rewordHead(view) {
  const sha = view.data.head.sha;
  let detail;
  try {
    detail = await api.get('/api/repo/commit', { id: view.id, sha });
  } catch (err) {
    view.fail(err);
    return null;
  }
  const staged = view.data.status.staged.length;
  return formDialog({
    eyebrow: 'Commit',
    title: 'Edit commit message',
    intro: staged
      ? h('p', null, `${plural(staged, 'staged file')} will be added to the commit as well.`)
      : h('p', null, 'This replaces the last commit. If it is already pushed, a force push is needed afterwards.'),
    fields: [
      { name: 'summary', label: 'Summary', value: detail.subject, required: true },
      { name: 'body', label: 'Description', type: 'textarea', rows: 6, value: detail.body },
    ],
    confirmLabel: 'Amend commit',
    submit: async (v) => {
      await view.exec('commit', { message: v.body ? `${v.summary}\n\n${v.body}` : v.summary, amend: true }, 'Amending');
      await view.refresh();
      toast('Commit message updated', 'ok');
    },
  });
}

export async function resetTo(view, sha, mode) {
  const current = view.data.head.branch || 'HEAD';
  const text = {
    soft: 'Keeps all changes staged.',
    mixed: 'Keeps all changes in the working directory, unstaged.',
    hard: 'Discards every change after this commit, including uncommitted work. This cannot be undone from GitSense.',
  }[mode];
  const ok = await confirmDialog({
    eyebrow: 'Reset',
    title: `Reset ${current} (${mode})`,
    message: h('p', null, 'Move ', code(current), ' to ', code(shortSha(sha)), '. ', text),
    confirmLabel: `Reset (${mode})`,
    danger: mode === 'hard',
  });
  if (ok) await view.run('reset', { sha, mode }, { label: 'Resetting', success: `Reset ${current} to ${shortSha(sha)}` });
}

export function createTag(view, sha) {
  return formDialog({
    eyebrow: 'Tag',
    title: 'Create a tag',
    intro: h('p', null, 'Tags ', code(sha ? shortSha(sha) : 'the current commit'), '.'),
    fields: [
      { name: 'name', label: 'Tag name', placeholder: 'v1.0.0', required: true },
      { name: 'message', label: 'Message (optional, makes an annotated tag)', type: 'textarea', rows: 3 },
      view.data.remotes.length ? { name: 'push', type: 'checkbox', label: 'Push the tag to the remote', value: false } : null,
    ],
    confirmLabel: 'Create tag',
    validate: (v) => (/\s/.test(v.name) ? 'Tag names cannot contain spaces' : null),
    submit: async (v) => {
      await view.exec('tag_create', { name: v.name, sha, message: v.message });
      if (v.push) await view.exec('tag_push', { name: v.name }, `Pushing tag ${v.name}`);
      await view.refresh();
      toast(`Created tag ${v.name}`, 'ok');
    },
  });
}

async function deleteTag(view, name, remote) {
  const ok = await confirmDialog({
    eyebrow: 'Tag',
    title: remote ? 'Delete tag from remote' : 'Delete tag',
    message: h('p', null, 'Delete the tag ', code(name), remote ? ' from the remote? Other people will lose it too.' : ' from this repository?'),
    confirmLabel: 'Delete tag',
    danger: true,
  });
  if (ok) await view.run(remote ? 'tag_delete_remote' : 'tag_delete', { name }, { success: `Deleted tag ${name}` });
}

// -- stashes --------------------------------------------------------------------

export function stashSave(view) {
  const st = view.data.status;
  if (!st.staged.length && !st.unstaged.length) {
    toast('There is nothing to stash', 'info');
    return null;
  }
  return formDialog({
    eyebrow: 'Stash',
    title: 'Stash changes',
    intro: 'Set your uncommitted changes aside and return to a clean working directory.',
    fields: [
      { name: 'message', label: 'Label (optional)', placeholder: 'What were you working on?' },
      { name: 'include_untracked', type: 'checkbox', label: 'Include untracked files', value: true },
      { name: 'keep_index', type: 'checkbox', label: 'Keep staged changes in place', value: false },
    ],
    confirmLabel: 'Stash',
    submit: async (v) => {
      await view.exec('stash_save', v, 'Stashing');
      await view.refresh();
      toast('Changes stashed', 'ok');
    },
  });
}

export async function dropStash(view, stash) {
  const ok = await confirmDialog({
    eyebrow: 'Stash',
    title: 'Drop stash',
    message: h('p', null, 'Delete ', code(stash.message || `stash@{${stash.index}}`), '? The stashed changes are lost.'),
    confirmLabel: 'Drop stash',
    danger: true,
  });
  if (ok) await view.run('stash_drop', { sha: stash.sha }, { success: 'Stash dropped' });
}

export const applyStash = (view, stash) => view.run('stash_apply', { sha: stash.sha }, { label: 'Applying stash', success: 'Stash applied' });
export const popStash = (view, stash) => view.run('stash_pop', { sha: stash ? stash.sha : null }, { label: 'Popping stash', success: 'Stash applied and removed' });

// -- working directory ----------------------------------------------------------

export async function discardFiles(view, files) {
  const untracked = files.filter((f) => f.status === '?').length;
  const ok = await confirmDialog({
    eyebrow: 'Discard',
    title: files.length === 1 ? 'Discard changes' : `Discard ${files.length} files`,
    message: h('p', null,
      files.length === 1 ? ['Discard the changes to ', code(files[0].path), '? '] : `Discard the changes to ${plural(files.length, 'file')}? `,
      untracked ? `${plural(untracked, 'untracked file')} will be deleted from disk. ` : '',
      'This cannot be undone.'),
    confirmLabel: 'Discard',
    danger: true,
  });
  if (ok) await view.run('discard', { paths: files.map((f) => f.path) }, { label: 'Discarding' });
}

export async function discardAll(view) {
  const st = view.data.status;
  const untracked = st.unstaged.filter((f) => f.status === '?').length;
  const result = await confirmDialog({
    eyebrow: 'Discard',
    title: 'Discard all changes',
    message: 'Reset the working directory to the last commit. Staged and unstaged changes are lost. This cannot be undone.',
    checkbox: untracked ? { label: `Also delete ${plural(untracked, 'untracked file')}`, checked: false } : null,
    confirmLabel: 'Discard all',
    danger: true,
  });
  if (!result) return;
  await view.run('discard_all', { include_untracked: Boolean(result.checked) }, { label: 'Discarding' });
}

// -- remotes --------------------------------------------------------------------

export function addRemote(view) {
  return formDialog({
    eyebrow: 'Remote',
    title: 'Add a remote',
    fields: [
      { name: 'name', label: 'Name', value: view.data.remotes.length ? '' : 'origin', placeholder: 'origin', required: true },
      { name: 'url', label: 'URL', placeholder: 'git@github.com:owner/repo.git', required: true },
    ],
    confirmLabel: 'Add remote',
    submit: async (v) => {
      await view.exec('remote_add', v);
      await view.refresh();
      view.run('fetch', { remote: v.name }, { label: `Fetching ${v.name}`, quiet: true });
    },
  });
}

function editRemote(view, remote) {
  return formDialog({
    eyebrow: 'Remote',
    title: `Edit ${remote.name}`,
    fields: [{ name: 'url', label: 'URL', value: remote.url, required: true }],
    confirmLabel: 'Save',
    submit: async (v) => {
      await view.exec('remote_set_url', { name: remote.name, url: v.url });
      await view.refresh();
    },
  });
}

async function removeRemote(view, remote) {
  const ok = await confirmDialog({
    eyebrow: 'Remote',
    title: 'Remove remote',
    message: h('p', null, 'Remove the remote ', code(remote.name), ' and its tracking branches from this repository? Nothing is deleted on the server.'),
    confirmLabel: 'Remove remote',
    danger: true,
  });
  if (ok) await view.run('remote_remove', { name: remote.name }, { success: `Removed ${remote.name}` });
}

// -- menus ----------------------------------------------------------------------

function relativeItems(view, refName, fullRef) {
  const head = view.data.head;
  if (head.unborn) return [];
  const current = head.branch || 'HEAD';
  return [
    { label: `Merge ${refName} into ${current}`, icon: 'git-merge', onClick: () => mergeRef(view, refName, fullRef) },
    head.branch ? { label: `Rebase ${current} onto ${refName}`, icon: 'git-graph', onClick: () => rebaseOnto(view, refName, fullRef) } : null,
  ];
}

export function branchMenu(view, branch, anchor) {
  const github = view.data.github;
  showMenu(anchor, [
    { heading: branch.name },
    { label: 'Check out', icon: 'check', disabled: branch.current, onClick: () => checkout(view, 'checkout', { ref: branch.name }, branch.name) },
    ...(branch.current ? [] : relativeItems(view, branch.name, `refs/heads/${branch.name}`)),
    { sep: true },
    branch.current ? { label: 'Pull', icon: 'arrow-down-to-line', disabled: !branch.upstream, onClick: () => pull(view) } : null,
    { label: branch.upstream ? 'Push' : 'Push and set upstream', icon: 'arrow-up-from-line', disabled: !view.data.remotes.length || view.pendingOps('push').length > 0, hint: view.pendingOps('push').length ? 'Pushing…' : '', onClick: () => pushBranch(view, branch.name) },
    { label: 'Set upstream…', icon: 'link', onClick: () => setUpstream(view, branch.name) },
    github ? { label: 'Create pull request…', icon: 'git-pull-request', onClick: () => createPullRequestDialog(view, branch.name) } : null,
    { sep: true },
    { label: 'Create branch here…', icon: 'git-branch', onClick: () => createBranch(view, `refs/heads/${branch.name}`, branch.name) },
    { label: 'Create tag here…', icon: 'tag', onClick: () => createTag(view, branch.sha) },
    { label: 'Rename…', icon: 'pencil', onClick: () => renameBranch(view, branch.name) },
    { label: 'Delete…', icon: 'trash-2', danger: true, disabled: branch.current, onClick: () => deleteBranch(view, branch) },
    { sep: true },
    { label: 'Copy branch name', icon: 'copy', onClick: () => copy(branch.name, 'Branch name') },
  ]);
}

export function remoteBranchMenu(view, ref, anchor) {
  const github = view.data.github;
  showMenu(anchor, [
    { heading: ref.name },
    { label: 'Check out', icon: 'check', onClick: () => checkout(view, 'checkout_remote', { full: ref.name }, ref.branch) },
    ...relativeItems(view, ref.name, `refs/remotes/${ref.name}`),
    { sep: true },
    { label: 'Create branch here…', icon: 'git-branch', onClick: () => createBranch(view, `refs/remotes/${ref.name}`, ref.name) },
    github && github.remote === ref.remote ? { label: 'Open on GitHub', icon: 'external-link', onClick: () => view.openUrl(`${github.url}/tree/${ref.branch.split('/').map(encodeURIComponent).join('/')}`) } : null,
    { label: 'Delete from remote…', icon: 'trash-2', danger: true, onClick: () => deleteRemoteBranch(view, ref) },
    { sep: true },
    { label: 'Copy branch name', icon: 'copy', onClick: () => copy(ref.name, 'Branch name') },
  ]);
}

export function tagMenu(view, tag, anchor) {
  const hasRemote = view.data.remotes.length > 0;
  showMenu(anchor, [
    { heading: tag.name },
    { label: 'Check out (detached)', icon: 'check', onClick: () => checkout(view, 'checkout', { ref: tag.name, detach: true }, tag.name) },
    { label: 'Create branch here…', icon: 'git-branch', onClick: () => createBranch(view, `refs/tags/${tag.name}`, tag.name) },
    { sep: true },
    { label: 'Push tag', icon: 'arrow-up-from-line', disabled: !hasRemote, onClick: () => view.run('tag_push', { name: tag.name }, { label: `Pushing tag ${tag.name}`, success: `Pushed tag ${tag.name}` }) },
    { label: 'Delete tag…', icon: 'trash-2', danger: true, onClick: () => deleteTag(view, tag.name, false) },
    { label: 'Delete tag from remote…', icon: 'trash-2', danger: true, disabled: !hasRemote, onClick: () => deleteTag(view, tag.name, true) },
    { sep: true },
    { label: 'Copy tag name', icon: 'copy', onClick: () => copy(tag.name, 'Tag name') },
  ]);
}

export function stashMenu(view, stash, anchor) {
  showMenu(anchor, [
    { heading: stash.message || `stash@{${stash.index}}` },
    { label: 'Apply stash', icon: 'archive-restore', onClick: () => applyStash(view, stash) },
    { label: 'Pop stash (apply and remove)', icon: 'archive-restore', onClick: () => popStash(view, stash) },
    { sep: true },
    { label: 'Drop stash…', icon: 'trash-2', danger: true, onClick: () => dropStash(view, stash) },
  ]);
}

export function remoteMenu(view, remote, anchor) {
  showMenu(anchor, [
    { heading: remote.name },
    { label: `Fetch ${remote.name}`, icon: 'refresh-cw', onClick: () => view.run('fetch', { remote: remote.name }, { label: `Fetching ${remote.name}`, success: `Fetched ${remote.name}` }) },
    { label: 'Edit URL…', icon: 'pencil', onClick: () => editRemote(view, remote) },
    { label: 'Copy URL', icon: 'copy', onClick: () => copy(remote.url, 'Remote URL') },
    { sep: true },
    { label: 'Remove remote…', icon: 'trash-2', danger: true, onClick: () => removeRemote(view, remote) },
  ]);
}

/** Context menu for a ref label in the graph. */
export function refMenu(view, ref, anchor) {
  if (ref.type === 'local') branchMenu(view, ref.data, anchor);
  else if (ref.type === 'remote') remoteBranchMenu(view, ref, anchor);
  else if (ref.type === 'tag') tagMenu(view, ref.data, anchor);
}

/** Menu listing the refs hidden behind a "+N" chip. */
export function refListMenu(view, refs, anchor) {
  const icons = { local: 'laptop', remote: 'cloud', tag: 'tag', detached: 'circle-dot' };
  showMenu(anchor, refs.map((ref) => ({
    label: ref.name,
    icon: icons[ref.type],
    hint: ref.type === 'tag' ? 'tag' : ref.type,
    onClick: () => refMenu(view, ref, anchor),
  })));
}

export function commitMenu(view, row, anchor) {
  if (row.kind === 'wip') {
    showMenu(anchor, [
      { label: 'Stash changes…', icon: 'archive', onClick: () => stashSave(view) },
      { label: 'Stage all changes', icon: 'plus', onClick: () => view.run('stage_all', {}, { quiet: true }) },
      { sep: true },
      { label: 'Discard all changes…', icon: 'trash-2', danger: true, onClick: () => discardAll(view) },
    ]);
    return;
  }
  if (row.kind === 'stash') {
    const stash = view.data.stashes.find((s) => s.sha === row.sha);
    if (stash) stashMenu(view, stash, anchor);
    return;
  }
  const head = view.data.head;
  const current = head.branch || 'HEAD';
  const github = view.data.github;
  const short = shortSha(row.sha);
  showMenu(anchor, [
    { heading: `${short}  ${row.subject}` },
    { label: 'Check out this commit', icon: 'check', disabled: row.isHead && head.detached, onClick: () => checkout(view, 'checkout', { ref: row.sha, detach: true }, short) },
    { label: 'Create branch here…', icon: 'git-branch', onClick: () => createBranch(view, row.sha, short) },
    { label: 'Create tag here…', icon: 'tag', onClick: () => createTag(view, row.sha) },
    row.isHead && !view.data.operation ? { label: 'Edit commit message…', icon: 'pencil', onClick: () => rewordHead(view) } : null,
    { sep: true },
    { label: 'Cherry-pick commit', icon: 'cherry', disabled: row.isHead, onClick: () => view.run('cherry_pick', { sha: row.sha }, { label: `Cherry-picking ${short}`, success: `Cherry-picked ${short}` }) },
    { label: 'Revert commit', icon: 'undo-2', onClick: () => view.run('revert', { sha: row.sha }, { label: `Reverting ${short}`, success: `Reverted ${short}` }) },
    { sep: true },
    { heading: `Reset ${current} to this commit` },
    { label: 'Soft: keep changes staged', icon: 'rotate-ccw', disabled: row.isHead, onClick: () => resetTo(view, row.sha, 'soft') },
    { label: 'Mixed: keep changes unstaged', icon: 'rotate-ccw', disabled: row.isHead, onClick: () => resetTo(view, row.sha, 'mixed') },
    { label: 'Hard: discard all changes…', icon: 'rotate-ccw', danger: true, onClick: () => resetTo(view, row.sha, 'hard') },
    { sep: true },
    { label: 'Copy commit SHA', icon: 'copy', onClick: () => copy(row.sha, 'SHA') },
    { label: 'Copy commit message', icon: 'copy', onClick: () => copy(row.subject, 'Message') },
    github ? { label: 'Open on GitHub', icon: 'external-link', onClick: () => view.openUrl(`${github.url}/commit/${row.sha}`) } : null,
  ]);
}

/**
 * Context menu for a changed file.
 * ctx.kind is "unstaged", "staged", "conflicted" or "commit".
 */
export function fileMenu(view, file, ctx, anchor) {
  const open = (what) => api.post('/api/repo/open', { id: view.id, what, path: file.path }).catch((err) => view.fail(err));
  const deleted = file.status === 'D';
  showMenu(anchor, [
    { heading: file.path },
    ctx.kind === 'unstaged' ? { label: 'Stage file', icon: 'plus', onClick: () => view.run('stage', { paths: [file.path] }, { quiet: true }) } : null,
    ctx.kind === 'staged' ? { label: 'Unstage file', icon: 'minus', onClick: () => view.run('unstage', { paths: stagePaths(file) }, { quiet: true }) } : null,
    ctx.kind === 'unstaged' ? { label: 'Discard changes…', icon: 'undo-2', danger: true, onClick: () => discardFiles(view, [file]) } : null,
    ctx.kind === 'conflicted' ? { label: 'Use our version', icon: 'check', onClick: () => view.run('resolve', { path: file.path, side: 'ours' }, { quiet: true }) } : null,
    ctx.kind === 'conflicted' ? { label: 'Use their version', icon: 'check', onClick: () => view.run('resolve', { path: file.path, side: 'theirs' }, { quiet: true }) } : null,
    ctx.kind === 'conflicted' ? { label: 'Mark as resolved', icon: 'circle-check', onClick: () => view.run('mark_resolved', { paths: [file.path] }, { quiet: true }) } : null,
    { sep: true },
    { label: 'Open in editor', icon: 'square-pen', disabled: deleted && ctx.kind !== 'commit', onClick: () => open('editor') },
    { label: 'Show in folder', icon: 'folder-open', disabled: deleted && ctx.kind !== 'commit', onClick: () => open('folder') },
    { label: 'File history', icon: 'history', onClick: () => view.showFileHistory(file.path) },
    { sep: true },
    { label: 'Copy path', icon: 'copy', onClick: () => copy(file.path, 'Path') },
  ]);
}

/** Context menu for several picked staged or unstaged files. */
export function filesMenu(view, files, ctx, anchor) {
  const count = plural(files.length, 'file');
  showMenu(anchor, [
    { heading: `${count} selected` },
    ctx.kind === 'unstaged' ? { label: `Stage ${count}`, icon: 'plus', onClick: () => view.run('stage', { paths: files.map((f) => f.path) }, { quiet: true }) } : null,
    ctx.kind === 'staged' ? { label: `Unstage ${count}`, icon: 'minus', onClick: () => view.run('unstage', { paths: files.flatMap(stagePaths) }, { quiet: true }) } : null,
    ctx.kind === 'unstaged' ? { label: `Discard changes to ${count}…`, icon: 'undo-2', danger: true, onClick: () => discardFiles(view, files) } : null,
    { sep: true },
    { label: 'Copy paths', icon: 'copy', onClick: () => copy(files.map((f) => f.path).join('\n'), 'Paths') },
  ]);
}

/** Both sides of a staged rename have to be unstaged together. */
export function stagePaths(file) {
  return file.orig_path ? [file.orig_path, file.path] : [file.path];
}
