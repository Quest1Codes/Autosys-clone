import { api } from './client';
import type { Job, EventType } from '../types';

export const fetchJobs = async (params?: {
  status?: string;
  pattern?: string;
}): Promise<Job[]> => {
  const res = await api.get('/jobs', { params });
  return res.data;
};

export const fetchJob = async (name: string): Promise<Job> => {
  const res = await api.get(`/jobs/${encodeURIComponent(name)}`);
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
