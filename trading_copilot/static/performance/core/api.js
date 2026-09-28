// /api/paper/* wrappers. They never throw into the UI: every call resolves
// to {ok: true, data} or {ok: false, error} with a plain-language reason.

export async function request(url, { method = 'GET', body, fetchImpl = globalThis.fetch } = {}) {
  let res;
  try {
    res = await fetchImpl(url, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    return { ok: false, error: 'The web process could not be reached.' };
  }
  if (!res.ok) return { ok: false, error: `The web process answered ${res.status}.` };
  let data;
  try {
    data = await res.json();
  } catch {
    return { ok: false, error: 'The web process sent a reply that could not be read.' };
  }
  if (data && data.status === 'error') {
    return { ok: false, error: data.message || 'The web process reported an error.', data };
  }
  return { ok: true, data };
}

const q = (range) => `range=${encodeURIComponent(range)}`;

export const api = {
  metrics: (range, o) => request(`/api/paper/metrics?${q(range)}`, o),
  equity: (range, o) => request(`/api/paper/equity?${q(range)}`, o),
  trades: (range, o) => request(`/api/paper/trades?${q(range)}`, o),
  settings: (o) => request('/api/paper/settings', o),
  saveSettings: (changes, o) => request('/api/paper/settings', { method: 'POST', body: changes, ...o }),
  pause: (o) => request('/api/paper/disable', { method: 'POST', ...o }),
  resume: (o) => request('/api/paper/enable', { method: 'POST', ...o }),
};
