import type { BoxGraphData } from '../types';

export const fetchBoxGraph = async (name: string): Promise<BoxGraphData> => {
  const res = await fetch(`/api/wcc/boxes/${encodeURIComponent(name)}`);
  if (!res.ok) throw new Error(`Box graph fetch failed: ${res.status}`);
  return res.json();
};
