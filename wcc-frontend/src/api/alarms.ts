import { api } from './client';
import axios from 'axios';
import type { Alarm } from '../types';

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

export const fetchAlarms = async (activeOnly?: boolean): Promise<Alarm[]> => {
  const params: Record<string, string> = {};
  if (activeOnly === true)  params.active = 'true';
  if (activeOnly === false) params.active = 'false';
  const res = await wccApi.get('/alarms', { params });
  return res.data.alarms ?? res.data;
};

export const clearAlarm = async (alarmId: string): Promise<void> => {
  await api.post(`/alarms/${encodeURIComponent(alarmId)}/clear`);
};
