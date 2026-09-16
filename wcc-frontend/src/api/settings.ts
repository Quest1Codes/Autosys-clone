import { api } from './client';

export interface ExecutionMode {
  dry_run: boolean;
}

export const getExecutionMode = (): Promise<ExecutionMode> =>
  api.get('/settings/execution-mode').then((r: { data: ExecutionMode }) => r.data);

export const setExecutionMode = (dryRun: boolean): Promise<ExecutionMode> =>
  api.put('/settings/execution-mode', { dry_run: dryRun }).then((r: { data: ExecutionMode }) => r.data);
