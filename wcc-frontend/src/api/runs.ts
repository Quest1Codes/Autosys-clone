import { api } from './client';
import axios from 'axios';
import type { JobRun, RunOutput } from '../types';

const wccApi = axios.create({
  baseURL: '/api/wcc',
  headers: { 'Content-Type': 'application/json' },
});

wccApi.interceptors.request.use((config) => {
  const token = localStorage.getItem('wcc_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

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
