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

export const deleteJob = async (name: string): Promise<void> => {
  await api.delete(`/jobs/${encodeURIComponent(name)}`);
};
