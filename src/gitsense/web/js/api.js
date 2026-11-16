// JSON client for the local GitSense server.

const TOKEN = document.querySelector('meta[name="gitsense-token"]')?.content || '';

export class ApiError extends Error {
  constructor(message, detail, status, cancelled = false) {
    super(message);
    this.detail = detail || '';
    this.status = status;
    this.cancelled = cancelled; // the user cancelled the action
  }
}

async function request(method, path, params, body) {
  let url = path;
  if (params) {
    const qs = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value != null && value !== '') qs.set(key, value);
    }
    const text = qs.toString();
    if (text) url += `?${text}`;
  }
  let res;
  try {
    res = await fetch(url, {
      method,
      headers: {
        'X-GitSense-Token': TOKEN,
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError('GitSense is not running. Start it again with `gitsense`.', '', 0);
  }
  let data = null;
  try { data = await res.json(); } catch { data = null; }
  if (!res.ok) {
    throw new ApiError((data && data.error) || `Request failed (${res.status})`, data && data.detail, res.status, !!(data && data.cancelled));
  }
  return data;
}

export const api = {
  get: (path, params) => request('GET', path, params),
  post: (path, body) => request('POST', path, null, body || {}),
};
