import { api } from './client';

export interface ExecutionMode {
  dry_run: boolean;
}

// V1 removed PUT /settings/execution-mode (no client-facing deployment
// should be able to flip a running server between dry-run and real
// execution over the network) -- GET stays, it's just a read of which mode
// this process is running in.
export const getExecutionMode = (): Promise<ExecutionMode> =>
  api.get('/settings/execution-mode').then((r: { data: ExecutionMode }) => r.data);
