import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Link } from 'react-router-dom';
import { fetchJobs, sendEvent } from '../api/jobs';
import { useSSE } from '../hooks/useSSE';
import { useToast } from '../contexts/ToastContext';
import StatusBadge from '../components/StatusBadge';
import Header from '../components/Header';
import SendEventModal from '../components/SendEventModal';
import JilImportModal from '../components/JilImportModal';
import type { Job, EventType, JobStatus } from '../types';

const STATUS_COLORS: Record<string, string> = {
  SUCCESS: '#00A650', FAILURE: '#CC0000', RUNNING: '#0055A5',
  STARTING: '#FF8C00', ACTIVATED: '#4A90D9', ON_HOLD: '#FFD700',
  ON_ICE: '#808080', INACTIVE: '#E0E0E0', TERMINATED: '#8B0000', QUE_WAIT: '#6A0DAD',
};

interface TreeRow { job: Job; depth: number; isLast: boolean; hasChildren: boolean; }

function buildTree(jobs: Job[], expandedBoxes: Set<string>): TreeRow[] {
  const boxJobs = jobs.filter(j => j.job_type === 'BOX');
  const childMap = new Map<string, Job[]>();
  const topLevel: Job[] = [];

  for (const j of jobs) {
    if (j.box_name) {
      const arr = childMap.get(j.box_name) ?? [];
      arr.push(j);
      childMap.set(j.box_name, arr);
    } else {
      topLevel.push(j);
    }
  }

  const rows: TreeRow[] = [];

  for (const j of topLevel) {
    const children = childMap.get(j.job_name) ?? [];
    const hasChildren = children.length > 0 && j.job_type === 'BOX';
    rows.push({ job: j, depth: 0, isLast: false, hasChildren });
    if (hasChildren && expandedBoxes.has(j.job_name)) {
      children.forEach((child, idx) => {
        rows.push({ job: child, depth: 1, isLast: idx === children.length - 1, hasChildren: false });
      });
    }
  }

  // Jobs that belong to a BOX not in topLevel (orphans)
  const seenBoxNames = new Set(boxJobs.map(b => b.job_name));
  for (const j of jobs) {
    if (j.box_name && !seenBoxNames.has(j.box_name) && !topLevel.includes(j)) {
      rows.push({ job: j, depth: 0, isLast: false, hasChildren: false });
    }
  }

  return rows;
}

const TOOLBAR_ACTIONS: Array<{ id: EventType; label: string; icon: string; danger?: boolean }> = [
  { id: 'STARTJOB',       label: 'Start Job',    icon: '▶' },
  { id: 'FORCE_STARTJOB', label: 'Force Start',  icon: '▶▶', danger: true },
  { id: 'KILLJOB',        label: 'Kill Job',     icon: '■', danger: true },
  { id: 'JOB_ON_HOLD',    label: 'Hold',         icon: '⏸' },
  { id: 'JOB_OFF_HOLD',   label: 'Off Hold',     icon: '▷' },
  { id: 'JOB_ON_ICE',     label: 'On Ice',       icon: '❄' },
  { id: 'JOB_OFF_ICE',    label: 'Off Ice',      icon: '✦' },
];

type SortKey = 'job_name' | 'job_type' | 'status' | 'machine' | 'last_start' | 'last_end';

const JobMonitor: React.FC = () => {
  const { showToast } = useToast();
  const [jobs, setJobs]           = useState<Job[]>([]);
  const [loading, setLoading]     = useState(true);
  const [selectedJob, setSelected]= useState<string | null>(null);
  const [expandedBoxes, setExpanded] = useState<Set<string>>(new Set());
  const [modal, setModal]         = useState<{ open: boolean; event?: EventType }>({ open: false });
  const [contextMenu, setCtxMenu] = useState<{ x: number; y: number; jobName: string } | null>(null);
  const [filterName, setFilterName]   = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [filterMachine, setFilterMachine] = useState('');
  const [appliedFilters, setAppliedFilters] = useState({ name: '', status: '', machine: '' });
  const [sortKey, setSortKey]     = useState<SortKey>('job_name');
  const [sortAsc, setSortAsc]     = useState(true);
  const [sseStatus, setSseStatusLocal] = useState<'connecting'|'live'|'disconnected'>('connecting');
  const [showJilImport, setShowJilImport] = useState(false);

  const loadJobs = useCallback(async () => {
    try {
      const data = await fetchJobs();
      setJobs(data);
    } catch {
      showToast('Failed to load jobs', 'error');
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => { loadJobs(); }, [loadJobs]);

  const handleSSEUpdate = useCallback((updates: Array<{ job_name: string; status: string }>) => {
    setJobs(prev => prev.map(j => {
      const update = updates.find(u => u.job_name === j.job_name);
      return update ? { ...j, status: update.status as JobStatus } : j;
    }));
  }, []);

  const sseState = useSSE(handleSSEUpdate);
  useEffect(() => { setSseStatusLocal(sseState); }, [sseState]);

  // Close context menu on outside click
  useEffect(() => {
    const handler = () => setCtxMenu(null);
    document.addEventListener('click', handler);
    return () => document.removeEventListener('click', handler);
  }, []);

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(a => !a);
    else { setSortKey(key); setSortAsc(true); }
  };

  const handleRowClick = (jobName: string) => {
    setSelected(prev => prev === jobName ? null : jobName);
  };

  const handleRowDblClick = (jobName: string) => {
    window.location.href = `/jobs/${encodeURIComponent(jobName)}`;
  };

  const handleBoxToggle = (e: React.MouseEvent, boxName: string) => {
    e.stopPropagation();
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(boxName) ? next.delete(boxName) : next.add(boxName);
      return next;
    });
  };

  const handleContextMenu = (e: React.MouseEvent, jobName: string) => {
    e.preventDefault();
    setSelected(jobName);
    setCtxMenu({ x: e.clientX, y: e.clientY, jobName });
  };

  const handleToolbarAction = (eventType: EventType) => {
    if (!selectedJob) return;
    setModal({ open: true, event: eventType });
  };

  const handleCtxAction = (eventType: EventType) => {
    setCtxMenu(null);
    setModal({ open: true, event: eventType });
  };

  const handleSendEvent = async (eventType: EventType) => {
    if (!selectedJob) return;
    await sendEvent(selectedJob, eventType);
    showToast(`${eventType} sent to ${selectedJob}`, 'success');
    setTimeout(loadJobs, 600);
  };

  const applyFilters = () => {
    setAppliedFilters({ name: filterName, status: filterStatus, machine: filterMachine });
  };

  const resetFilters = () => {
    setFilterName(''); setFilterStatus(''); setFilterMachine('');
    setAppliedFilters({ name: '', status: '', machine: '' });
  };

  // Filter + sort
  let filteredJobs = jobs.filter(j => {
    if (appliedFilters.name    && !j.job_name.toLowerCase().includes(appliedFilters.name.toLowerCase())) return false;
    if (appliedFilters.status  && j.status !== appliedFilters.status) return false;
    if (appliedFilters.machine && !j.machine.toLowerCase().includes(appliedFilters.machine.toLowerCase())) return false;
    return true;
  });

  filteredJobs = [...filteredJobs].sort((a, b) => {
    const av = (a[sortKey as keyof Job] ?? '') as string;
    const bv = (b[sortKey as keyof Job] ?? '') as string;
    return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
  });

  const treeRows = buildTree(filteredJobs, expandedBoxes);

  // Status summary
  const summary: Record<string, number> = {};
  for (const j of jobs) summary[j.status] = (summary[j.status] ?? 0) + 1;

  const SortTh = ({ col, label }: { col: SortKey; label: string }) => (
    <th onClick={() => handleSort(col)} style={{ cursor: 'pointer' }}>
      {label}
      {sortKey === col && <span className="sort-icon">{sortAsc ? '▲' : '▼'}</span>}
    </th>
  );

  return (
    <div className="wcc-shell">
      <Header sseStatus={sseStatus} />

      {/* Toolbar */}
      <div className="wcc-toolbar">
        {TOOLBAR_ACTIONS.map((action, i) => (
          <React.Fragment key={action.id}>
            {i === 3 && <div className="toolbar-sep" />}
            <button
              className="toolbar-btn"
              disabled={!selectedJob}
              onClick={() => handleToolbarAction(action.id)}
              title={action.label}
            >
              <span>{action.icon}</span>
              <span>{action.label}</span>
            </button>
          </React.Fragment>
        ))}
        <div className="toolbar-sep" />
        <button className="toolbar-btn" onClick={() => setShowJilImport(true)} title="Import JIL">
          📂 Import JIL
        </button>
        <div className="toolbar-sep" />
        <button className="toolbar-btn" onClick={loadJobs} title="Refresh">
          ↺ Refresh
        </button>
        {selectedJob && (
          <button className="toolbar-btn" onClick={() => window.location.href = `/jobs/${encodeURIComponent(selectedJob)}`} title="Detail">
            ⬡ Detail
          </button>
        )}
      </div>

      {/* Filter bar */}
      <div className="wcc-filter-bar">
        <span className="filter-label">Filter:</span>
        <input className="wcc-input" style={{ width: 140 }} placeholder="Job Name"
          value={filterName} onChange={e => setFilterName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && applyFilters()} />
        <select className="wcc-select" value={filterStatus} onChange={e => setFilterStatus(e.target.value)}>
          <option value="">— Status —</option>
          {['RUNNING','STARTING','ACTIVATED','SUCCESS','FAILURE','INACTIVE','ON_HOLD','ON_ICE','TERMINATED','QUE_WAIT'].map(s => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <input className="wcc-input" style={{ width: 120 }} placeholder="Machine"
          value={filterMachine} onChange={e => setFilterMachine(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && applyFilters()} />
        <button className="filter-btn filter-btn-apply" onClick={applyFilters}>Apply</button>
        <button className="filter-btn filter-btn-reset" onClick={resetFilters}>Reset</button>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: '#666' }}>
          {filteredJobs.length} job{filteredJobs.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Status summary bar */}
      <div className="wcc-status-bar">
        {Object.entries(summary)
          .filter(([, cnt]) => cnt > 0)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([status, cnt]) => (
            <div key={status} className="status-bar-item"
              onClick={() => { setFilterStatus(status); setAppliedFilters(f => ({ ...f, status })); }}>
              <span className="status-bar-dot" style={{ background: STATUS_COLORS[status] ?? '#CCC' }} />
              <span className="status-bar-count">{cnt}</span>
              <span className="status-bar-name">{status}</span>
            </div>
          ))}
      </div>

      {/* Job table */}
      <div className="wcc-content" style={{ padding: 0 }}>
        <div className="job-monitor-grid">
          {loading ? (
            <div className="loading-state">Loading jobs…</div>
          ) : treeRows.length === 0 ? (
            <div className="empty-state">No jobs found.</div>
          ) : (
            <table className="wcc-table">
              <thead>
                <tr>
                  <SortTh col="job_name" label="Job Name" />
                  <SortTh col="job_type" label="Type" />
                  <SortTh col="status"   label="Status" />
                  <SortTh col="machine"  label="Machine" />
                  <SortTh col="last_start" label="Last Start" />
                  <SortTh col="last_end"   label="Last End" />
                  <th>Box</th>
                </tr>
              </thead>
              <tbody>
                {treeRows.map(({ job, depth, isLast, hasChildren }) => (
                  <tr
                    key={job.job_name}
                    className={`job-row${selectedJob === job.job_name ? ' selected' : ''}${depth > 0 ? ' child-row' : ''}`}
                    onClick={() => handleRowClick(job.job_name)}
                    onDoubleClick={() => handleRowDblClick(job.job_name)}
                    onContextMenu={e => handleContextMenu(e, job.job_name)}
                  >
                    <td style={{ paddingLeft: depth > 0 ? 0 : 6 }}>
                      <div style={{ display: 'flex', alignItems: 'center' }}>
                        {depth > 0 && (
                          <span className="tree-connector">{isLast ? '└─' : '├─'}</span>
                        )}
                        {hasChildren && (
                          <span className="expand-toggle"
                            onClick={e => handleBoxToggle(e, job.job_name)}>
                            {expandedBoxes.has(job.job_name) ? '▼' : '▶'}
                          </span>
                        )}
                        {!hasChildren && depth === 0 && <span style={{ display: 'inline-block', width: 18 }} />}
                        <Link to={`/jobs/${encodeURIComponent(job.job_name)}`}
                          className="job-name-link"
                          onClick={e => e.stopPropagation()}>
                          {job.job_name}
                        </Link>
                        {job.job_type === 'BOX' && (
                          <Link to={`/boxes/${encodeURIComponent(job.job_name)}`}
                            style={{ marginLeft: 6, fontSize: 10, color: '#4A90D9', textDecoration: 'none' }}
                            onClick={e => e.stopPropagation()} title="View dependency graph">
                            ⬡
                          </Link>
                        )}
                      </div>
                    </td>
                    <td>{job.job_type}</td>
                    <td><StatusBadge status={job.status} /></td>
                    <td>{job.machine || '—'}</td>
                    <td style={{ fontFamily: 'monospace', fontSize: 10 }}>{job.last_start || '—'}</td>
                    <td style={{ fontFamily: 'monospace', fontSize: 10 }}>{job.last_end || '—'}</td>
                    <td>{job.box_name || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Context Menu */}
      {contextMenu && (
        <div className="context-menu" style={{ left: contextMenu.x, top: contextMenu.y }}
          onClick={e => e.stopPropagation()}>
          <div className="context-menu-item" onClick={() => handleCtxAction('STARTJOB')}>▶ Start Job</div>
          <div className="context-menu-item" onClick={() => handleCtxAction('FORCE_STARTJOB')}>▶▶ Force Start</div>
          <div className="context-menu-sep" />
          <div className="context-menu-item danger" onClick={() => handleCtxAction('KILLJOB')}>■ Kill Job</div>
          <div className="context-menu-sep" />
          <div className="context-menu-item" onClick={() => handleCtxAction('JOB_ON_HOLD')}>⏸ Hold</div>
          <div className="context-menu-item" onClick={() => handleCtxAction('JOB_OFF_HOLD')}>▷ Off Hold</div>
          <div className="context-menu-item" onClick={() => handleCtxAction('JOB_ON_ICE')}>❄ On Ice</div>
          <div className="context-menu-item" onClick={() => handleCtxAction('JOB_OFF_ICE')}>✦ Off Ice</div>
          <div className="context-menu-sep" />
          <div className="context-menu-item" onClick={() => { setCtxMenu(null); window.location.href = `/jobs/${encodeURIComponent(contextMenu.jobName)}`; }}>
            ⬡ View Detail
          </div>
        </div>
      )}

      {/* Send Event Modal */}
      {modal.open && selectedJob && (
        <SendEventModal
          jobName={selectedJob}
          initialEvent={modal.event}
          onClose={() => setModal({ open: false })}
          onSubmit={handleSendEvent}
        />
      )}

      {/* JIL Import Modal */}
      {showJilImport && (
        <JilImportModal
          onClose={() => setShowJilImport(false)}
          onImported={() => { setShowJilImport(false); loadJobs(); }}
        />
      )}
    </div>
  );
};

export default JobMonitor;
