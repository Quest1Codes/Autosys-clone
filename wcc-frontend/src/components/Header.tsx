import React from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import type { SSEStatus } from '../hooks/useSSE';
import quest1Logo from '../assets/quest1-logo.svg';

interface Props {
  sseStatus?: SSEStatus;
}

const Header: React.FC<Props> = ({ sseStatus = 'connecting' }) => {
  const { username, logout } = useAuth();
  const navigate = useNavigate();

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
