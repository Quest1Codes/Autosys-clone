import { api } from './client';

// The report simulates risk for jobs with no run history in the background, so
// the first call after a JIL import can be slow; allow for it.
export const downloadAssessmentJson = async (): Promise<string> => {
  const res = await api.get('/assessment/report', { timeout: 120_000 });
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const name = `complexity-assessment-${stamp}.json`;
  const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  return name;
};
