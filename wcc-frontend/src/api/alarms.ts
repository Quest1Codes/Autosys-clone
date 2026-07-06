import { api } from './client';
import type { Alarm } from '../types';

export const fetchAlarms = async (activeOnly?: boolean): Promise<Alarm[]> => {
  const params: Record<string, string> = {};
  if (activeOnly === true)  params.active = 'true';
  if (activeOnly === false) params.active = 'false';
  const res = await api.get('/alarms', { params });
  return res.data.alarms ?? res.data;
};

export const clearAlarm = async (alarmId: string): Promise<void> => {
  await api.post(`/alarms/${encodeURIComponent(alarmId)}/clear`);
};
