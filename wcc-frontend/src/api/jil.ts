import { api } from './client';

export interface JILJobResult {
  action: string;
  name: string;
  type: string;
}

export interface JILImportResponse {
  success: boolean;
  jobs: JILJobResult[];
  n_inserted: number;
  n_updated: number;
  n_deleted: number;
  n_machines: number;
  error?: string;
}

export const validateJIL = (content: string): Promise<JILImportResponse> =>
  api.post('/jil/validate', { content }).then((r: { data: JILImportResponse }) => r.data);

export const importJIL = (content: string, dry_run = false): Promise<JILImportResponse> =>
  api.post('/jil/import', { content, dry_run }).then((r: { data: JILImportResponse }) => r.data);
