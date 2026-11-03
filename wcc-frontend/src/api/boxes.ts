import type { BoxGraphData } from '../types';

export const fetchBoxGraph = async (name: string): Promise<BoxGraphData> => {
  // WCC data routes require the same sign-in as the API -- the browser
  // attaches the wcc_session cookie to this same-origin request on its own.
  const res = await fetch(`/api/wcc/boxes/${encodeURIComponent(name)}`, {
    credentials: 'same-origin',
  });
  if (res.status === 401) {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.href = `/api/v1/auth/login?next=${next}`;
    throw new Error('Not signed in');
  }
  if (!res.ok) throw new Error(`Box graph fetch failed: ${res.status}`);
  return res.json();
};
