import type { BoxGraphData } from '../types';

export const fetchBoxGraph = async (name: string): Promise<BoxGraphData> => {
  // WCC data routes require the same login as the API.
  const token = localStorage.getItem('wcc_token');
  const res = await fetch(`/api/wcc/boxes/${encodeURIComponent(name)}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(`Box graph fetch failed: ${res.status}`);
  return res.json();
};
