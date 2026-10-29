import { api } from './client';

export interface HistoryImportResponse {
  import_id: number | null;
  status: 'OK' | 'WARN' | 'REJECTED' | string;
  format: string;
  encoding: string;
  reject_reason: string;
  n_rows_read: number;
  n_rows_loaded: number;
  n_rows_skipped: number;
  n_rows_duplicate: number;
  n_rows_unknown_job: number;
  n_jobs: number;
  window_start: string | null;
  window_end: string | null;
  issue_codes: Record<string, number>;
  unknown_job_samples: string[];
  aborted: boolean;
  abort_reason: string;
}

export interface HistoryCoverageResponse {
  jobs_total: number;
  jobs_non_box: number;
  jobs_observed_sufficient: number;
  jobs_observed_insufficient: number;
  jobs_no_evidence: number;
  coverage_pct: number;
  min_runs_threshold: number;
  min_window_days_threshold: number;
  // Estate-level mode signal, not per-job counts -- see schemas.py's
  // HistoryCoverageResponse docstring. Used to keep "Import Run History"
  // and "Run All Boxes" (which writes real source="simulated" rows via a
  // genuine FORCE_STARTJOB, not a rolled-back preview) mutually exclusive.
  jobs_with_imported_evidence: number;
  jobs_with_dry_run_evidence: number;
}

export type HistoryMode = 'none' | 'real' | 'dry_run' | 'mixed';

export const historyMode = (c: HistoryCoverageResponse): HistoryMode => {
  const hasReal = c.jobs_with_imported_evidence > 0;
  const hasDryRun = c.jobs_with_dry_run_evidence > 0;
  if (hasReal && hasDryRun) return 'mixed';
  if (hasReal) return 'real';
  if (hasDryRun) return 'dry_run';
  return 'none';
};

// The File object is appended as-is, never read into a JS string first --
// unlike JIL text (small, pasteable), a run-history export is expected to be
// large (86K jobs x weeks of history can be hundreds of MB), and reading it
// into memory as a string before upload would hold the whole thing twice
// (once as the File, once as JS text) for no benefit. FormData + the raw
// File streams straight from disk through the browser's own upload I/O.
export const importHistory = (
  file: File,
  opts: { declaredTimezone?: string; onConflict?: 'skip' | 'replace' } = {}
): Promise<HistoryImportResponse> => {
  const form = new FormData();
  form.append('file', file);
  const params: Record<string, string> = {};
  if (opts.declaredTimezone) params.declared_timezone = opts.declaredTimezone;
  if (opts.onConflict) params.on_conflict = opts.onConflict;
  return api
    .post('/history/import', form, {
      params,
      // The shared `api` instance defaults to Content-Type: application/json
      // (see client.ts). That explicit header, once set, overrides axios's
      // own FormData auto-detection -- the browser never gets to add the
      // multipart boundary, and the upload would arrive at the server as
      // "application/json" containing a FormData object's string form,
      // which the API correctly rejects as unparseable. Clearing it here
      // lets the browser/XHR layer set the correct
      // "multipart/form-data; boundary=..." header itself.
      headers: { 'Content-Type': undefined },
    })
    .then((r: { data: HistoryImportResponse }) => r.data);
};

export const getHistoryCoverage = (): Promise<HistoryCoverageResponse> =>
  api.get('/history/coverage').then((r: { data: HistoryCoverageResponse }) => r.data);
