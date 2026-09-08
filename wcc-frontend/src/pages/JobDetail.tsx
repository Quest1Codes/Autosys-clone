import React, { useState, useEffect, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { fetchJob, sendEvent } from '../api/jobs';
import { fetchRuns, fetchRunOutput } from '../api/runs';
import { useToast } from '../contexts/ToastContext';
import Header from '../components/Header';
import StatusBadge from '../components/StatusBadge';
import SendEventModal from '../components/SendEventModal';
import type { Job, JobRun, RunOutput, EventType } from '../types';

const kv = (label: string, value: React.ReactNode) => (
  <tr key={label}>
    <td>{label}</td>
    <td>{value ?? '—'}</td>
  </tr>
);

const JobDetail: React.FC = () => {
  const { name } = useParams<{ name: string }>();
  const { showToast } = useToast();
  const [job, setJob]         = useState<Job | null>(null);
  const [runs, setRuns]       = useState<JobRun[]>([]);
  const [output, setOutput]   = useState<RunOutput[]>([]);
  const [selRun, setSelRun]   = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [modal, setModal]     = useState(false);

  const load = useCallback(async () => {
    if (!name) return;
    setLoading(true);
    try {
      const [j, r] = await Promise.all([fetchJob(name), fetchRuns(name)]);
      setJob(j);
      setRuns(r);
    } catch {
      showToast('Failed to load job detail', 'error');
    } finally {
      setLoading(false);
    }
  }, [name, showToast]);

  useEffect(() => { load(); }, [load]);

  const handleRunClick = async (runId: string) => {
    setSelRun(runId);
    try {
      const lines = await fetchRunOutput(runId);
      setOutput(lines);
    } catch {
      setOutput([{ seq: 0, line: '(output unavailable)' }]);
    }
  };

  const handleSendEvent = async (eventType: EventType) => {
    if (!name) return;
    await sendEvent(name, eventType);
    showToast(`${eventType} sent to ${name}`, 'success');
    setTimeout(load, 600);
  };

  if (loading) return (
    <div className="wcc-shell">
      <Header />
      <div className="wcc-content"><div className="loading-state">Loading…</div></div>
    </div>
  );

  if (!job) return (
    <div className="wcc-shell">
      <Header />
      <div className="wcc-content"><div className="empty-state">Job not found.</div></div>
    </div>
  );

  return (
    <div className="wcc-shell">
      <Header />
      <div className="wcc-content">
        <div className="job-detail-page">

          {/* Top bar */}
          <div className="detail-top-bar">
            <Link to="/" className="back-link">← Job Monitor</Link>
            <span style={{ color: '#AAA' }}>›</span>
            <span className="detail-job-name">{job.job_name}</span>
            <StatusBadge status={job.status} />
            {job.job_type === 'BOX' && (
              <Link to={`/boxes/${encodeURIComponent(job.job_name)}`}
                style={{ fontSize: 11, color: '#0055A5', textDecoration: 'none' }}>
                ⬡ View BOX Graph
              </Link>
            )}
            <div className="detail-actions">
              <button className="wcc-btn wcc-btn-primary" onClick={() => setModal(true)}>
                ▶ Send Event
              </button>
              <button className="wcc-btn wcc-btn-secondary" onClick={load}>↺ Refresh</button>
            </div>
          </div>

          {/* Two-column definition + runtime */}
          <div className="detail-two-col">
            {/* Definition */}
            <div className="detail-panel">
              <div className="detail-panel-title">Job Definition</div>
              <table className="detail-kv">
                <tbody>
                  {kv('Name',       job.job_name)}
                  {kv('Type',       job.job_type)}
                  {kv('Machine',    job.machine)}
                  {kv('Owner',      job.owner)}
                  {kv('BOX Name',   job.box_name)}
                  {kv('Command',    job.command && <code style={{ fontSize: 10 }}>{job.command}</code>)}
                  {kv('Condition',  job.condition && <code style={{ fontSize: 10 }}>{job.condition}</code>)}
                  {kv('Schedule',   job.schedule)}
                  {kv('Max Retries',job.n_retrys !== undefined ? String(job.n_retrys) : undefined)}
                </tbody>
              </table>
            </div>

            {/* Runtime */}
            <div className="detail-panel">
              <div className="detail-panel-title">Runtime Status</div>
              <table className="detail-kv">
                <tbody>
                  {kv('Status',     <StatusBadge status={job.status} />)}
                  {kv('Last Start', job.last_start)}
                  {kv('Last End',   job.last_end)}
                  {kv('Run Date',   job.last_run_date)}
                  {kv('Exit Code',  job.exit_code !== null && job.exit_code !== undefined ? String(job.exit_code) : undefined)}
                </tbody>
              </table>
            </div>
          </div>

          {/* Run History */}
          <div className="detail-panel">
            <div className="detail-panel-title">Run History</div>
            {runs.length === 0 ? (
              <div className="empty-state">No runs found.</div>
            ) : (
              <table className="wcc-table">
                <thead>
                  <tr>
                    <th>Run ID</th><th>Status</th><th>Start Time</th>
                    <th>End Time</th><th>Duration (s)</th><th>Machine</th><th>Exit Code</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map(r => (
                    <tr key={r.run_id}
                      className={`job-row${selRun === r.run_id ? ' selected' : ''}`}
                      onClick={() => handleRunClick(r.run_id)}
                      style={{ cursor: 'pointer' }}>
                      <td style={{ fontFamily: 'monospace', fontSize: 10 }}>{r.run_id.slice(0, 8)}…</td>
                      <td><StatusBadge status={r.status} /></td>
                      <td style={{ fontFamily: 'monospace', fontSize: 10 }}>{r.start_time || '—'}</td>
                      <td style={{ fontFamily: 'monospace', fontSize: 10 }}>{r.end_time || '—'}</td>
                      <td>{r.duration_s !== null ? r.duration_s : '—'}</td>
                      <td>{r.machine || '—'}</td>
                      <td>{r.exit_code !== null ? r.exit_code : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Output viewer */}
          {selRun && (
            <div className="detail-panel">
              <div className="detail-panel-title">
                Run Output — <span style={{ fontFamily: 'monospace', fontSize: 10 }}>{selRun.slice(0, 8)}…</span>
              </div>
              <div className="output-viewer">
                {output.length === 0
                  ? <div style={{ color: '#666', padding: '6px 12px' }}>No output captured.</div>
                  : output.map((l, i) => (
                    <div key={i} className="output-line">
                      <span className="output-lineno">{l.seq || i + 1}</span>
                      <span className="output-text">{l.line}</span>
                    </div>
                  ))}
              </div>
            </div>
          )}

        </div>
      </div>

      {modal && (
        <SendEventModal
          jobName={job.job_name}
          onClose={() => setModal(false)}
          onSubmit={handleSendEvent}
        />
      )}
    </div>
  );
};

export default JobDetail;
