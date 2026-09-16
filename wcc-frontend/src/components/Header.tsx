import React, { useEffect, useState, useCallback } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { getExecutionMode, setExecutionMode } from '../api/settings';
import type { SSEStatus } from '../hooks/useSSE';
import quest1Logo from '../assets/quest1-logo.svg';

interface Props {
  sseStatus?: SSEStatus;
}

const Header: React.FC<Props> = ({ sseStatus = 'connecting' }) => {
  const { username, isAdmin, logout } = useAuth();
  const { showToast } = useToast();
  const navigate = useNavigate();
  const [dryRun, setDryRun] = useState<boolean | null>(null);

  useEffect(() => {
    getExecutionMode().then(m => setDryRun(m.dry_run)).catch(() => {});
  }, []);

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  const handleToggleMode = useCallback(async () => {
    if (dryRun === null) return;
    const next = !dryRun;
    if (next === false) {
      const ok = window.confirm(
        'Switch to REAL RUN?\n\nJobs will execute real commands/scripts instead of being simulated.'
      );
      if (!ok) return;
    }
    try {
      const res = await setExecutionMode(next);
      setDryRun(res.dry_run);
      showToast(`Execution mode: ${res.dry_run ? 'DRY RUN' : 'REAL RUN'}`, 'success');
    } catch {
      showToast('Failed to change execution mode', 'error');
    }
  }, [dryRun, showToast]);

  return (
    <>
      <header className="wcc-header">
        <div className="wcc-header-logo">
          <img src={quest1Logo} alt="Quest1 Logo" className="ca-badge" />
          <span className="wcc-title">Workload Control Center</span>
        </div>
        <div className="wcc-header-spacer" />
        <div className="wcc-header-right">
          {dryRun !== null && (
            <button
              className={`exec-mode-btn ${dryRun ? 'dry-run' : 'real-run'}`}
              disabled={!isAdmin}
              onClick={handleToggleMode}
              title={isAdmin ? 'Click to toggle execution mode' : 'Admin only'}
            >
              {dryRun ? 'DRY RUN' : 'REAL RUN'}
            </button>
          )}
          <div className="live-indicator">
            <span className={`live-dot ${sseStatus === 'live' ? '' : 'disconnected'}`} />
            <span style={{ color: sseStatus === 'live' ? '#90EE90' : '#FF8080' }}>
              {sseStatus === 'live' ? 'LIVE' : sseStatus === 'connecting' ? 'CONNECTING' : 'DISCONNECTED'}
            </span>
          </div>
          {username && <span className="header-user">👤 {username}</span>}
          <button className="header-logout-btn" onClick={handleLogout}>Sign Out</button>
        </div>
      </header>

      <nav className="wcc-nav">
        <NavLink to="/"       className={({ isActive }) => `wcc-nav-tab${isActive ? ' active' : ''}`} end>Job Monitor</NavLink>
        <NavLink to="/alarms" className={({ isActive }) => `wcc-nav-tab${isActive ? ' active' : ''}`}>Alarms</NavLink>
      </nav>
    </>
  );
};

export default Header;
