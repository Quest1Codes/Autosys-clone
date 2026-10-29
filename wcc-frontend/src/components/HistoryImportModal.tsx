import React, { useState, useCallback, useEffect } from 'react';
import { importHistory, getHistoryCoverage } from '../api/history';
import type { HistoryImportResponse, HistoryCoverageResponse } from '../api/history';
import { useToast } from '../contexts/ToastContext';

interface Props {
  onClose: () => void;
  onImported: (nJobs: number) => void;
}

// AUTOSYS_HISTORY_MAX_UPLOAD_MB's default on the server (routers/history.py).
// This is a client-side heads-up only -- the server enforces the real limit
// regardless of what this says, in case the two ever drift.
const SERVER_DEFAULT_MAX_MB = 200;

const STATUS_COLOR: Record<string, string> = {
  OK:       '#00A650',
  WARN:     '#B8860B',
  REJECTED: '#CC0000',
};

const fmtBytes = (n: number): string => {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
};

const HistoryImportModal: React.FC<Props> = ({ onClose, onImported }) => {
  const { showToast } = useToast();
  const [file, setFile]           = useState<File | null>(null);
  const [timezone, setTimezone]   = useState('');
  const [onConflict, setOnConflict] = useState<'skip' | 'replace'>('skip');
  const [loading, setLoading]     = useState(false);
  const [dragging, setDragging]   = useState(false);
  const [result, setResult]       = useState<HistoryImportResponse | null>(null);
  const [coverage, setCoverage]   = useState<HistoryCoverageResponse | null>(null);

  const loadCoverage = useCallback(() => {
    getHistoryCoverage().then(setCoverage).catch(() => {});
  }, []);

  // Fetched once on open, and again after every import, so the operator sees
  // the estate's overall coverage move -- not just this one file's own
  // counts, which say nothing about how much of the estate still has no
  // real evidence at all.
  useEffect(() => { loadCoverage(); }, [loadCoverage]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files[0];
    if (f) { setFile(f); setResult(null); }
  }, []);

  const handleImport = async () => {
    if (!file) return;
    setLoading(true);
    setResult(null);
    try {
      const res = await importHistory(file, {
        declaredTimezone: timezone.trim() || undefined,
        onConflict,
      });
      setResult(res);
      if (res.status !== 'REJECTED') {
        showToast(
          `${res.n_rows_loaded.toLocaleString()} row(s) loaded, `
          + `${res.n_rows_duplicate.toLocaleString()} duplicate, `
          + `${res.n_rows_skipped.toLocaleString()} skipped`
          + (res.n_rows_unknown_job ? `, ${res.n_rows_unknown_job.toLocaleString()} for an unknown job` : ''),
          res.status === 'OK' ? 'success' : 'info'
        );
        if (res.n_rows_loaded > 0) onImported(res.n_jobs);
        loadCoverage();
      } else {
        showToast(`Rejected: ${res.reject_reason}`, 'error');
      }
    } catch (err) {
      const resp = (err as { response?: { status?: number; data?: { detail?: string } } })?.response;
      if (resp?.status === 413) {
        showToast(`File too large for upload — use "autosys history import" from the CLI instead`, 'error');
      } else if (resp?.status === 409) {
        // Mutual-exclusivity rejection -- the server's detail message already
        // explains why and what to do (see routers/history.py import_history).
        showToast(resp.data?.detail ?? 'Import rejected: simulated data already exists for this estate', 'error');
      } else {
        showToast('Import request failed', 'error');
      }
    } finally {
      setLoading(false);
    }
  };

  const oversized = file != null && file.size > SERVER_DEFAULT_MAX_MB * 1024 * 1024;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-box"
        style={{ width: 640, maxHeight: '85vh', display: 'flex', flexDirection: 'column' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="modal-header">
          <span>Import Run History</span>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        <div style={{ padding: '12px 16px', flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 10 }}>

          {/* Run history references job_name, which is a foreign key into
              the imported JIL -- a row for a job that doesn't exist yet is
              never guessed into existence, it's just counted as
              n_rows_unknown_job and left out (see
              run_history_ingest.py's module docstring). With no JIL loaded
              at all that would be every single row, which reads as "this
              broke" rather than "load JIL first" unless we say so plainly.
              The toolbar button is disabled for the same reason; this is
              the same guard for anyone who reaches the modal another way. */}
          {coverage && coverage.jobs_total === 0 ? (
            <div style={{
              padding: '10px 12px', borderRadius: 3,
              background: '#FFF0F0', border: '1px solid #E0A0A0', fontSize: 12, color: '#822',
            }}>
              No jobs in the estate yet — import JIL first. Run-history rows reference a{' '}
              <code>job_name</code> that must already exist; with nothing imported, every row
              would come back as "unknown job."
            </div>
          ) : (
          <>
          <div style={{ fontSize: 11, color: '#666' }}>
            Real execution history from a client's own AutoSys — CSV, TSV, pipe-delimited
            (header row, delimiter auto-detected) or JSON Lines. Required columns:{' '}
            <code>job_name</code>, <code>status</code>, <code>start_time</code> — the minimum
            we can compute a failure rate from; real AutoSys records more per run (end time,
            exit code, machine, run number), and we accept all of it if your export has it.
            Replaces generated risk with real evidence for every job it covers.
          </div>

          {coverage && (
            <div style={{
              display: 'flex', gap: 14, fontSize: 11, padding: '6px 10px',
              background: '#F4F7FC', border: '1px solid #D8E2F0', borderRadius: 3,
            }}>
              <span>Estate coverage:</span>
              <span style={{ color: '#00A650' }}>{coverage.jobs_observed_sufficient} observed</span>
              <span style={{ color: '#B8860B' }}>{coverage.jobs_observed_insufficient} below minimum sample</span>
              <span style={{ color: '#888' }}>{coverage.jobs_no_evidence} no evidence yet</span>
              <span style={{ marginLeft: 'auto', color: '#555' }}>
                {(coverage.coverage_pct * 100).toFixed(1)}% of {coverage.jobs_non_box} non-BOX jobs
              </span>
            </div>
          )}

          {/* Drop zone / file picker */}
          <div
            onDragOver={e => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            onClick={() => document.getElementById('history-file-input')?.click()}
            style={{
              border: `2px dashed ${dragging ? '#003D7C' : '#CCC'}`,
              borderRadius: 4,
              padding: '14px',
              textAlign: 'center',
              cursor: 'pointer',
              background: dragging ? '#EEF4FF' : '#F9F9F9',
              fontSize: 12,
              color: '#555',
              flexShrink: 0,
            }}
          >
            {file
              ? <>📄 <strong>{file.name}</strong> ({fmtBytes(file.size)}) — <u>click to change</u></>
              : <>📄 Drop a run-history file here, or <u>click to browse</u></>}
            <input
              id="history-file-input"
              type="file"
              accept=".csv,.tsv,.txt,.jsonl,.json"
              style={{ display: 'none' }}
              onChange={e => { const f = e.target.files?.[0]; if (f) { setFile(f); setResult(null); } }}
            />
          </div>

          {oversized && (
            <div style={{
              padding: '6px 10px', borderRadius: 3,
              background: '#FFF6E5', border: '1px solid #F0C36D', fontSize: 11, color: '#7A5B00',
            }}>
              ⚠ {fmtBytes(file!.size)} is over this server's upload limit (~{SERVER_DEFAULT_MAX_MB} MB) —
              the upload will likely be refused. Run{' '}
              <code>autosys history import {file!.name}</code> from the CLI instead; it streams the
              file directly with no upload size limit.
            </div>
          )}

          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <label style={{ fontSize: 11, color: '#555', display: 'flex', alignItems: 'center', gap: 4 }}>
              Timezone
              <input
                className="wcc-input"
                style={{ width: 160 }}
                placeholder="UTC, or an IANA name"
                value={timezone}
                onChange={e => setTimezone(e.target.value)}
              />
            </label>
            <span style={{ fontSize: 10, color: '#999' }}>
              only needed if timestamps carry no UTC offset — a file that needs one and doesn't get
              one is rejected, not guessed at
            </span>
          </div>

          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <label style={{ fontSize: 11, color: '#555', display: 'flex', alignItems: 'center', gap: 4 }}>
              On conflict
              <select className="wcc-select" style={{ height: 26, fontSize: 12 }}
                value={onConflict} onChange={e => setOnConflict(e.target.value as 'skip' | 'replace')}>
                <option value="skip">skip (re-import is a no-op)</option>
                <option value="replace">replace (a corrected re-export overwrites)</option>
              </select>
            </label>
          </div>

          <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
            <button className="wcc-btn wcc-btn-primary" onClick={handleImport} disabled={loading || !file}>
              {loading ? 'Importing…' : '⬆ Import'}
            </button>
            <button className="wcc-btn wcc-btn-secondary"
              onClick={() => { setFile(null); setResult(null); }} style={{ marginLeft: 'auto' }}>
              Clear
            </button>
          </div>

          {result && (
            <div style={{ flexShrink: 0 }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 6 }}>
                <span style={{
                  background: STATUS_COLOR[result.status] ?? '#888', color: '#FFF',
                  borderRadius: 2, padding: '1px 8px', fontSize: 11, fontWeight: 600,
                }}>
                  {result.status}
                </span>
                <span style={{ fontSize: 11, color: '#888' }}>
                  {result.format} · {result.encoding} · import_id={result.import_id ?? '—'}
                </span>
              </div>

              {result.status === 'REJECTED' ? (
                <div style={{ background: '#FFF0F0', border: '1px solid #CC0000', padding: '8px 12px', borderRadius: 3, color: '#CC0000', fontSize: 12 }}>
                  {result.reject_reason}
                </div>
              ) : (
                <>
                  <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', fontSize: 11, marginBottom: 6, color: '#444' }}>
                    <span>{result.n_rows_read.toLocaleString()} read</span>
                    <span style={{ color: '#00A650' }}>{result.n_rows_loaded.toLocaleString()} loaded</span>
                    <span style={{ color: '#888' }}>{result.n_rows_duplicate.toLocaleString()} duplicate</span>
                    <span style={{ color: '#B8860B' }}>{result.n_rows_skipped.toLocaleString()} skipped</span>
                    {result.n_rows_unknown_job > 0 && (
                      <span style={{ color: '#CC0000' }}>{result.n_rows_unknown_job.toLocaleString()} unknown job</span>
                    )}
                    <span>{result.n_jobs.toLocaleString()} job(s) covered</span>
                  </div>

                  {result.window_start && result.window_end && (
                    <div style={{ fontSize: 11, color: '#666', marginBottom: 6 }}>
                      window: {result.window_start.slice(0, 10)} .. {result.window_end.slice(0, 10)}
                    </div>
                  )}

                  {result.unknown_job_samples.length > 0 && (
                    <div style={{
                      marginBottom: 6, padding: '6px 10px', borderRadius: 3,
                      background: '#FFF0F0', border: '1px solid #E0A0A0', fontSize: 11, color: '#822',
                    }}>
                      Rows for job(s) not in the current JIL (never imported): {' '}
                      {result.unknown_job_samples.slice(0, 10).join(', ')}
                      {result.unknown_job_samples.length > 10 ? ', …' : ''}
                    </div>
                  )}

                  {Object.keys(result.issue_codes).length > 0 && (
                    <table className="wcc-table" style={{ fontSize: 11 }}>
                      <thead><tr><th>Issue</th><th>Count</th></tr></thead>
                      <tbody>
                        {Object.entries(result.issue_codes)
                          .sort((a, b) => b[1] - a[1])
                          .map(([code, count]) => (
                            <tr key={code}>
                              <td style={{ fontFamily: 'monospace' }}>{code}</td>
                              <td>{count.toLocaleString()}</td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  )}
                </>
              )}
            </div>
          )}
          </>
          )}
        </div>
      </div>
    </div>
  );
};

export default HistoryImportModal;
