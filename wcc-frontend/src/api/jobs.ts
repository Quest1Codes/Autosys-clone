import { api } from './client';
import axios from 'axios';
import type { Job, EventType } from '../types';

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

export const fetchJobs = async (params?: {
  status?: string;
  pattern?: string;
}): Promise<Job[]> => {
  const res = await wccApi.get('/jobs', { params });
  return res.data.jobs;
};

export const fetchJob = async (name: string): Promise<Job> => {
  const res = await wccApi.get(`/jobs/${encodeURIComponent(name)}`);
  return res.data;
};

// Goes through `api` (baseURL /api/v1, port 9000), not `wccApi` -- sendevent
// lives on the main app server, not the WCC read-only dashboard backend.
// Requires the "operator" or "admin" role server-side (V2); `api`'s request
// interceptor already attaches the logged-in user's bearer token.
export const sendEvent = async (
  jobName: string,
  eventType: EventType,
  newStatus?: string
): Promise<void> => {
  await api.post(`/jobs/${encodeURIComponent(jobName)}/sendevent`, {
    event_type: eventType,
    ...(newStatus ? { new_status: newStatus } : {}),
  });
};
