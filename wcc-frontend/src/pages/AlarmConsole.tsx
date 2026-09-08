import React, { useState, useEffect, useCallback } from 'react';
import { fetchAlarms, clearAlarm } from '../api/alarms';
import Header from '../components/Header';
import type { Alarm } from '../types';

type Filter = 'all' | 'active' | 'cleared';

const AlarmConsole: React.FC = () => {
  const [alarms, setAlarms]   = useState<Alarm[]>([]);
  const [filter, setFilter]   = useState<Filter>('all');
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const activeOnly = filter === 'active' ? true : filter === 'cleared' ? false : undefined;
      const data = await fetchAlarms(activeOnly);
      setAlarms(data);
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => { load(); }, [load]);

  // Auto-refresh every 10 seconds
  useEffect(() => {
    const interval = setInterval(load, 10_000);
    return () => clearInterval(interval);
  }, [load]);

  const handleClear = async (alarmId: string) => {
    try {
      await clearAlarm(alarmId);
      setAlarms(prev => prev.map(a =>
        a.alarm_id === alarmId ? { ...a, active: false, cleared_at: new Date().toISOString() } : a
      ));
    } catch {
      // silently ignore
    }
  };

  const activeCount = alarms.filter(a => a.active).length;

  return (
    <div className="wcc-shell">
      <Header />
      <div className="wcc-content">
        <div className="alarm-page">

          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
            <div className="panel-heading" style={{ flex: 1 }}>
              Alarm Console
              {activeCount > 0 && (
                <span style={{ background: '#CC0000', color: '#FFF', borderRadius: 10, padding: '1px 8px', fontSize: 11, marginLeft: 10 }}>
                  {activeCount} active
                </span>
              )}
            </div>
            <button className="wcc-btn wcc-btn-secondary" onClick={load} style={{ flexShrink: 0 }}>↺ Refresh</button>
          </div>

          {/* Filter tabs */}
          <div style={{ display: 'flex', gap: 2, flexShrink: 0 }}>
            {(['all', 'active', 'cleared'] as Filter[]).map(f => (
              <button
                key={f}
                className={`wcc-btn ${filter === f ? 'wcc-btn-primary' : 'wcc-btn-secondary'}`}
                onClick={() => setFilter(f)}
                style={{ textTransform: 'capitalize' }}
              >
                {f}
              </button>
            ))}
          </div>

          <div className="wcc-panel">
            {loading ? (
              <div className="loading-state">Loading alarms…</div>
            ) : alarms.length === 0 ? (
              <div className="empty-state">
                {filter === 'active' ? 'No active alarms.' : 'No alarms found.'}
              </div>
            ) : (
              <table className="wcc-table">
                <thead>
                  <tr>
                    <th>Job Name</th>
                    <th>Alarm Type</th>
                    <th>Message</th>
                    <th>Raised At</th>
                    <th>Cleared At</th>
                    <th>Status</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {alarms.map(a => (
                    <tr key={a.alarm_id} className={a.active ? 'alarm-row-active' : ''}>
                      <td style={{ fontWeight: a.active ? 700 : 400 }}>{a.job_name}</td>
                      <td>{a.alarm_type}</td>
                      <td style={{ maxWidth: 280 }}>{a.message}</td>
                      <td style={{ fontFamily: 'monospace', fontSize: 10 }}>{a.raised_at || '—'}</td>
                      <td style={{ fontFamily: 'monospace', fontSize: 10 }}>{a.cleared_at || '—'}</td>
                      <td>
                        {a.active
                          ? <span className="status-badge FAILURE" style={{ minWidth: 56 }}>ACTIVE</span>
                          : <span className="status-badge SUCCESS" style={{ minWidth: 56 }}>CLEARED</span>
                        }
                      </td>
                      <td>
                        {a.active && (
                          <button className="alarm-clear-btn" onClick={() => handleClear(a.alarm_id)}>
                            Clear
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

        </div>
      </div>
    </div>
  );
};

export default AlarmConsole;
