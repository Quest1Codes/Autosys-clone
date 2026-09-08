export type JobStatus =
  | 'SUCCESS' | 'FAILURE' | 'RUNNING' | 'STARTING' | 'ACTIVATED'
  | 'ON_HOLD' | 'ON_ICE' | 'INACTIVE' | 'TERMINATED' | 'QUE_WAIT';

export type EventType =
  | 'STARTJOB' | 'FORCE_STARTJOB' | 'KILLJOB'
  | 'JOB_ON_HOLD' | 'JOB_OFF_HOLD' | 'JOB_ON_ICE' | 'JOB_OFF_ICE'
  | 'CHANGE_STATUS';

export interface Job {
  job_name: string;
  job_type: 'CMD' | 'BOX' | 'FTP' | string;
  status: JobStatus;
  machine: string;
  owner: string;
  box_name: string | null;
  command?: string;
  condition?: string;
  schedule?: string;
  n_retrys?: number;
  last_start?: string;
  last_end?: string;
  last_run_date?: string;
  exit_code?: number | null;
}

export interface JobRun {
  run_id: string;
  job_name: string;
  status: JobStatus;
  exit_code: number | null;
  machine: string;
  run_date: string;
  start_time: string;
  end_time: string;
  duration_s: number | null;
}

export interface RunOutput {
  seq: number;
  line: string;
}

export interface Alarm {
  alarm_id: string;
  job_name: string;
  alarm_type: string;
  message: string;
  raised_at: string;
  cleared_at: string | null;
  cleared_by?: string;
  active: boolean;
}

export interface Machine {
  machine_name: string;
  host: string;
  port: number;
  status: string;
  last_heartbeat?: string;
}

export interface GraphNode {
  id: string;
  type: string;
  status: JobStatus;
}

export interface GraphEdge {
  source: string;
  target: string;
}

export interface BoxGraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface StatusSummary {
  [key: string]: number;
}
