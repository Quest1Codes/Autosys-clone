import React from 'react';
import type { JobStatus } from '../types';

interface Props {
  status: JobStatus | string;
}

const StatusBadge: React.FC<Props> = ({ status }) => {
  const s = String(status ?? 'INACTIVE').toUpperCase();
  return <span className={`status-badge ${s}`}>{s}</span>;
};

export default StatusBadge;
