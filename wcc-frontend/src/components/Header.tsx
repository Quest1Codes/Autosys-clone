import React, { useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { useExecutionMode } from '../contexts/ExecutionModeContext';
import { useToast } from '../contexts/ToastContext';
import { downloadAssessmentJson } from '../api/assessment';
import type { SSEStatus } from '../hooks/useSSE';
import quest1Logo from '../assets/quest1-logo.svg';

interface Props {
  sseStatus?: SSEStatus;
}

const Header: React.FC<Props> = ({ sseStatus = 'connecting' }) => {
  const { username, logout } = useAuth();
  const navigate = useNavigate();
  const { dryRun, dryRunOnly } = useExecutionMode();
  const { showToast } = useToast();
  const [downloading, setDownloading] = useState(false);

  const handleDownload = async () => {
    setDownloading(true);
    try {
      const name = await downloadAssessmentJson();
      showToast(`Saved ${name}`, 'success');
    } catch {
      showToast('Could not generate the complexity assessment', 'error');
    } finally {
      setDownloading(false);
    }
  };

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

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
            <span
              className={`exec-mode-btn ${dryRun ? 'dry-run' : 'real-run'}`}
              title="Execution mode for this server (fixed for its lifetime, set by how it was started)"
            >
              {dryRun ? 'DRY RUN' : 'REAL RUN'}
            </span>
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
        {!dryRunOnly && (
          <NavLink to="/alarms" className={({ isActive }) => `wcc-nav-tab${isActive ? ' active' : ''}`}>Alarms</NavLink>
        )}
        <div style={{ flex: 1 }} />
        <button className="toolbar-btn" onClick={handleDownload} disabled={downloading}
          title="Download the complexity assessment report as JSON">
          <span>⬇</span>
          <span>{downloading ? 'Preparing…' : 'Complexity assessment (JSON)'}</span>
        </button>
      </nav>
    </>
  );
};

export default Header;
