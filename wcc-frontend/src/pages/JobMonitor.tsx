import React, { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { fetchJobs, sendEvent } from '../api/jobs';
import { useSSE } from '../hooks/useSSE';
import { useToast } from '../contexts/ToastContext';
import { useExecutionMode } from '../contexts/ExecutionModeContext';
import { useAuth } from '../contexts/AuthContext';
import { clearEstate } from '../api/estate';
import StatusBadge from '../components/StatusBadge';
import Header from '../components/Header';
import SendEventModal from '../components/SendEventModal';
import JilImportModal from '../components/JilImportModal';
import HistoryImportModal from '../components/HistoryImportModal';
import { getHistoryCoverage, historyMode } from '../api/history';
import { getJobRiskMap } from '../api/assessment';
import type { JobRisk } from '../api/assessment';
import type { HistoryCoverageResponse } from '../api/history';
import type { Job, EventType, JobStatus } from '../types';

const STATUS_COLORS: Record<string, string> = {
  SUCCESS: '#00A650', FAILURE: '#CC0000', RUNNING: '#0055A5',
  STARTING: '#FF8C00', ACTIVATED: '#4A90D9', ON_HOLD: '#FFD700',
  ON_ICE: '#808080', INACTIVE: '#E0E0E0', TERMINATED: '#8B0000', QUE_WAIT: '#6A0DAD',
};

// risk_source: "observed" (real import/live evidence, enough of it) /
// "observed_insufficient" (real evidence, too little to score -- see
// operational_risk.MIN_OBSERVED_RUNS) / "simulated" (FailureInjector, not
// production evidence) / "none". The icon is the provenance signal; colour
// is the risk level itself, same tiers RISK_LEVELS uses server-side.
const RISK_COLORS: Record<string, string> = {
  HIGH: '#CC0000', MEDIUM: '#B8860B', LOW: '#4A90D9', NONE: '#00A650', NO_DATA: '#AAAAAA',
};
const RISK_SOURCE_ICON: Record<string, string> = {
  observed: '✓', observed_insufficient: '✓', simulated: '⟳', none: '',
};
// Short provenance word shown alongside the risk level itself -- the chip
// needs to read as "Real" vs "Sim" at a glance, not just differ by icon,
// since the icon alone was too subtle to settle the "did anything actually
// happen" confusion (see the Run All Boxes / Import Run History distinction
// in the provenance banner below).
const RISK_SOURCE_LABEL: Record<string, string> = {
  observed: 'Real', observed_insufficient: 'Real (low)', simulated: 'Sim', none: '',
};

const RiskCell: React.FC<{ risk?: JobRisk }> = ({ risk }) => {
  if (!risk) return <span style={{ color: '#CCC' }}>—</span>;
  const color = RISK_COLORS[risk.risk] ?? '#AAAAAA';
  const icon = RISK_SOURCE_ICON[risk.risk_source] ?? '';
  const label = RISK_SOURCE_LABEL[risk.risk_source] ?? '';
  const title = risk.risk_drivers
    ? `${risk.risk_source}: ${risk.risk_drivers}`
    : risk.risk_source;
  // Simulated rows read softer (italic, slightly faded) than real ones --
  // the more dynamic-looking Run All Boxes action shouldn't visually
  // out-trust a quiet real import just because it animates more elsewhere.
  const simulated = risk.risk_source === 'simulated';
  return (
    <span
      className="risk-chip"
      style={{
        color, borderColor: color,
        fontStyle: simulated ? 'italic' : 'normal',
        opacity: simulated ? 0.85 : 1,
      }}
      title={title}
    >
      {icon} {risk.risk}{label ? ` · ${label}` : ''}
    </span>
  );
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

// Hold and ice make no sense in a dry-run-only simulator.
const HOLD_ICE_EVENTS: EventType[] = ['JOB_ON_HOLD', 'JOB_OFF_HOLD', 'JOB_ON_ICE', 'JOB_OFF_ICE'];
const HOLD_ICE_STATUSES = ['ON_HOLD', 'ON_ICE'];

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
  const { isAdmin } = useAuth();
  const [clearingEstate, setClearingEstate] = useState(false);
  const { dryRunOnly } = useExecutionMode();
  const toolbarActions = dryRunOnly ? TOOLBAR_ACTIONS.filter(a => !HOLD_ICE_EVENTS.includes(a.id)) : TOOLBAR_ACTIONS;
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
  const [showHistoryImport, setShowHistoryImport] = useState(false);
  const [coverage, setCoverage] = useState<HistoryCoverageResponse | null>(null);
  const loadCoverage = useCallback(() => {
    getHistoryCoverage().then(setCoverage).catch(() => {});
  }, []);
  const [riskMap, setRiskMap] = useState<Record<string, JobRisk>>({});
  const loadRisk = useCallback(() => {
    getJobRiskMap().then(setRiskMap).catch(() => {});
  }, []);
  // Transient highlight right after an import/run-all completes -- a real
  // import never touches live Status, so without this the table can look
  // like nothing happened even though the Risk column did change. Cleared
  // automatically after the flash animation finishes.
  const [flashGroup, setFlashGroup] = useState<'real' | 'sim' | null>(null);
  useEffect(() => {
    if (!flashGroup) return;
    const t = setTimeout(() => setFlashGroup(null), 2500);
    return () => clearTimeout(t);
  }, [flashGroup]);

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
  useEffect(() => { loadCoverage(); }, [loadCoverage]);
  useEffect(() => { loadRisk(); }, [loadRisk]);

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

  const handleRunAllBoxes = async () => {
    // Real run history and Run-All-Boxes-generated (FailureInjector) history
    // are kept mutually exclusive at the UI level -- Run All Boxes sends a
    // genuine FORCE_STARTJOB through the live dry-run EPS, which writes real
    // source="simulated" rows (see analysis/operational_risk.py's
    // jobs_with_dry_run_evidence), not a rolled-back preview. Mixing that
    // with imported real evidence in the same estate would make it unclear
    // which numbers in a report are real. The toolbar button is already
    // disabled for this; this is the same guard against a stale fetch
    // leaving it clickable, or any other call site this is ever reached from.
    if (coverage && coverage.jobs_with_imported_evidence > 0) {
      showToast(
        'Real run history has been imported for this estate — Run All Boxes is disabled '
        + 'to avoid mixing generated data with it. Clear the imported history first if you '
        + 'need simulated data instead.',
        'error'
      );
      return;
    }
    const topLevelBoxes = jobs.filter(j => j.job_type === 'BOX' && !j.box_name);
    if (topLevelBoxes.length === 0) {
      showToast('No top-level BOX jobs found', 'info');
      return;
    }
    const ok = window.confirm(
      `Force-start all ${topLevelBoxes.length} top-level box(es)?\n\n` +
      topLevelBoxes.map(b => `  • ${b.job_name}`).join('\n')
    );
    if (!ok) return;

    let sent = 0;
    for (const box of topLevelBoxes) {
      try {
        await sendEvent(box.job_name, 'FORCE_STARTJOB');
        sent++;
      } catch {
        showToast(`Failed to start ${box.job_name}`, 'error');
      }
    }
    showToast(`FORCE_STARTJOB sent to ${sent}/${topLevelBoxes.length} box(es)`, 'success');
    setTimeout(loadJobs, 600);
    setTimeout(loadCoverage, 600);
    setTimeout(loadRisk, 600);
    setTimeout(() => setFlashGroup('sim'), 600);
  };

  const handleClearEstate = async () => {
    // Two-step confirmation, deliberately stronger than Run All Boxes's
    // single window.confirm -- this is the one action in the app that
    // removes job definitions themselves, not just their run history.
    // Recoverable server-side (see routers/estate.py), but not from this
    // page, so the friction here is intentional.
    const count = jobs.length;
    if (count === 0) return;
    const ok = window.confirm(
      `Delete all ${count} job(s) in this estate?\n\n`
      + `This removes every job definition and its run history, alarms and output. `
      + `Each job's last definition is archived server-side and can be restored by `
      + `re-importing it, but there is no undo from this page.`
    );
    if (!ok) return;
    const typed = window.prompt(`Type the job count (${count}) to confirm:`);
    if (typed === null) return;
    if (typed.trim() !== String(count)) {
      showToast(`Typed "${typed}" does not match ${count} — estate not cleared`, 'error');
      return;
    }

    setClearingEstate(true);
    try {
      const res = await clearEstate(count);
      showToast(`Removed ${res.jobs_removed} job(s)`, 'success');
      loadJobs();
      loadCoverage();
      setRiskMap({});
    } catch (err) {
      const status = (err as { response?: { status?: number; data?: { detail?: string } } })?.response;
      if (status?.status === 409) {
        showToast(status.data?.detail ?? 'Job count changed — refresh and try again', 'error');
      } else if (status?.status === 403) {
        showToast('Admin role required to clear the estate', 'error');
      } else {
        showToast('Clear estate request failed', 'error');
      }
    } finally {
      setClearingEstate(false);
    }
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
        {toolbarActions.map((action) => (
          <React.Fragment key={action.id}>
            {action.id === 'JOB_ON_HOLD' && <div className="toolbar-sep" />}
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
        {jobs.length > 0 && (
          <button className="toolbar-btn" onClick={() => setShowHistoryImport(true)}
            disabled={!!coverage && coverage.jobs_with_dry_run_evidence > 0}
            title={coverage && coverage.jobs_with_dry_run_evidence > 0
              ? 'Run All Boxes has already generated simulated history for this estate — '
                + 'importing real history now would mix the two'
              : 'Import real run history'}>
            📊 Import Run History
          </button>
        )}
        <button className="toolbar-btn" onClick={handleRunAllBoxes}
          disabled={!!coverage && coverage.jobs_with_imported_evidence > 0}
          title={coverage && coverage.jobs_with_imported_evidence > 0
            ? 'Real run history has been imported for this estate — Run All Boxes is disabled '
              + 'so it can\'t mix generated data with it'
            : 'Force-start every top-level BOX'}>
          ▶▶▶ Run All Boxes
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
        {isAdmin && jobs.length > 0 && (
          <>
            <div className="toolbar-sep" />
            <button className="toolbar-btn" onClick={handleClearEstate} disabled={clearingEstate}
              style={{ color: '#CC0000', borderColor: '#E0A0A0' }}
              title="Delete every job definition and its run history, alarms and output (admin only)">
              {clearingEstate ? '…' : '🗑 Clear Estate'}
            </button>
          </>
        )}
      </div>

      {/* Risk data provenance banner -- which of the two mutually exclusive
          sources (imported real history, or Run All Boxes / dry-run
          simulation) this estate's operational-risk numbers currently rest
          on. Shown even when it's "none" -- the point is always answering
          "where did this come from", not just flagging a problem state. */}
      {coverage && (() => {
        const mode = historyMode(coverage);
        const MODE_INFO: Record<string, { label: string; color: string; bg: string; border: string }> = {
          real:     { label: `✓ Risk data is REAL — imported from ${coverage.jobs_with_imported_evidence} job(s)' run history`, color: '#00A650', bg: '#EEFBF3', border: '#B7E8C9' },
          dry_run:  { label: `⟳ Risk data is SIMULATED — from Run All Boxes / dry-run, covering ${coverage.jobs_with_dry_run_evidence} job(s)`, color: '#B8860B', bg: '#FFF8E8', border: '#F0C36D' },
          mixed:    { label: `⚠ Risk data is MIXED — ${coverage.jobs_with_imported_evidence} job(s) real, ${coverage.jobs_with_dry_run_evidence} job(s) simulated; avoid adding more of either until this is resolved`, color: '#CC0000', bg: '#FFF0F0', border: '#E0A0A0' },
          none:     { label: 'No risk data yet — import real run history, or Run All Boxes to simulate it', color: '#888', bg: '#F7F7F7', border: '#DDD' },
        };
        const info = MODE_INFO[mode];
        return (
          <div style={{
            padding: '5px 14px', fontSize: 11, color: info.color,
            background: info.bg, borderBottom: `1px solid ${info.border}`,
          }}>
            {info.label}
          </div>
        );
      })()}

      {/* Filter bar */}
      <div className="wcc-filter-bar">
        <span className="filter-label">Filter:</span>
        <input className="wcc-input" style={{ width: 140 }} placeholder="Job Name"
          value={filterName} onChange={e => setFilterName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && applyFilters()} />
        <select className="wcc-select" value={filterStatus} onChange={e => setFilterStatus(e.target.value)}>
          <option value="">— Status —</option>
          {['RUNNING','STARTING','ACTIVATED','SUCCESS','FAILURE','INACTIVE','ON_HOLD','ON_ICE','TERMINATED','QUE_WAIT']
            .filter(s => !(dryRunOnly && HOLD_ICE_STATUSES.includes(s))).map(s => (
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
                  <th>Risk</th>
                  <SortTh col="machine"  label="Machine" />
                  <SortTh col="last_start" label="Last Start" />
                  <SortTh col="last_end"   label="Last End" />
                  <th>Box</th>
                </tr>
              </thead>
              <tbody>
                {treeRows.map(({ job, depth, isLast, hasChildren }) => {
                  const rowRiskSource = riskMap[job.job_name]?.risk_source ?? 'none';
                  const isReal = rowRiskSource === 'observed' || rowRiskSource === 'observed_insufficient';
                  const isSim = rowRiskSource === 'simulated';
                  const flashClass = flashGroup === 'real' && isReal ? ' flash-real'
                    : flashGroup === 'sim' && isSim ? ' flash-sim' : '';
                  // Status is never faked -- it's always the live scheduler's own
                  // word. But when a job has real imported evidence and never
                  // actually ran live (still INACTIVE), the badge is dimmed so it
                  // visually recedes behind the Risk chip rather than reading as
                  // "nothing happened" -- if the scheduler later does run this job
                  // live, status changes and this dimming drops away on its own.
                  const dimStatus = isReal && job.status === 'INACTIVE';
                  return (
                  <tr
                    key={job.job_name}
                    data-risk-source={rowRiskSource}
                    className={`job-row${selectedJob === job.job_name ? ' selected' : ''}${depth > 0 ? ' child-row' : ''}${flashClass}`}
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
                    <td style={dimStatus ? { opacity: 0.45 } : undefined}>
                      <StatusBadge status={job.status} />
                    </td>
                    <td><RiskCell risk={riskMap[job.job_name]} /></td>
                    <td>{job.machine || '—'}</td>
                    <td style={{ fontFamily: 'monospace', fontSize: 10 }}>{job.last_start || '—'}</td>
                    <td style={{ fontFamily: 'monospace', fontSize: 10 }}>{job.last_end || '—'}</td>
                    <td>{job.box_name || '—'}</td>
                  </tr>
                  );
                })}
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
          {!dryRunOnly && (
            <>
              <div className="context-menu-sep" />
              <div className="context-menu-item" onClick={() => handleCtxAction('JOB_ON_HOLD')}>⏸ Hold</div>
              <div className="context-menu-item" onClick={() => handleCtxAction('JOB_OFF_HOLD')}>▷ Off Hold</div>
              <div className="context-menu-item" onClick={() => handleCtxAction('JOB_ON_ICE')}>❄ On Ice</div>
              <div className="context-menu-item" onClick={() => handleCtxAction('JOB_OFF_ICE')}>✦ Off Ice</div>
            </>
          )}
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

      {/* Run History Import Modal */}
      {showHistoryImport && (
        <HistoryImportModal
          onClose={() => setShowHistoryImport(false)}
          onImported={(nJobs) => {
            loadJobs(); loadCoverage(); loadRisk();
            showToast(`Imported real run history for ${nJobs} job(s) — see the Risk column`, 'success');
            setFlashGroup('real');
          }}
        />
      )}
    </div>
  );
};

export default JobMonitor;
