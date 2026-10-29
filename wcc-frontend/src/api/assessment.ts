import { api } from './client';

export interface JobRisk {
  risk: string;          // "HIGH" | "MEDIUM" | "LOW" | "NONE" | "NO_DATA"
  risk_source: string;   // "observed" | "observed_insufficient" | "simulated" | "none"
  risk_drivers: string;
}

// simulate=false deliberately -- this is read on every Job Monitor load, and
// the simulation (when it runs) is on an isolated scratch DB, never the
// live one (see analysis/simulated_risk.py), so it can't itself be the
// source of unwanted live data; it's skipped here purely so opening this
// page doesn't kick off a background simulation thread nobody asked for,
// on top of whatever already-imported/live evidence exists.
export const getJobRiskMap = async (): Promise<Record<string, JobRisk>> => {
  const res = await api.get('/assessment/report', { params: { simulate: false } });
  const map: Record<string, JobRisk> = {};
  for (const j of res.data.jobs ?? []) {
    map[j.job_name] = { risk: j.risk, risk_source: j.risk_source, risk_drivers: j.risk_drivers };
  }
  return map;
};

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
