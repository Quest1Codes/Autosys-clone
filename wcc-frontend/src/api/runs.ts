import { wccApi } from './client';
import type { JobRun, RunOutput } from '../types';

export const fetchRuns = async (jobName?: string): Promise<JobRun[]> => {
  const res = await wccApi.get('/runs', {
    params: jobName ? { job: jobName } : {},
  });
  return res.data.runs ?? res.data;
};

export const fetchRunOutput = async (runId: string): Promise<RunOutput[]> => {
  const res = await wccApi.get(`/runs/${encodeURIComponent(runId)}/output`);
  return res.data.lines ?? [];
};
