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
  // Stanzas that were NOT lost, but need a look. success=true says nothing
  // about these -- a "successful" import can still have quarantined stanzas.
  n_quarantined: number;
  n_warnings: number;
  // Parsed but refused by the database: archived, NOT loaded.
  n_failed: number;
  error?: string;
}

export const validateJIL = (content: string): Promise<JILImportResponse> =>
  api.post('/jil/validate', { content }).then((r: { data: JILImportResponse }) => r.data);

export const importJIL = (content: string, dry_run = false): Promise<JILImportResponse> =>
  api.post('/jil/import', { content, dry_run }).then((r: { data: JILImportResponse }) => r.data);
