import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import quest1Logo from '../assets/quest1-logo.svg';

const Login: React.FC = () => {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('admin');
  const [error, setError]     = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(username, password);
      navigate('/', { replace: true });
    } catch {
      setError('Invalid username or password.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-header">
          <img src={quest1Logo} alt="Quest1 Logo" className="ca-badge-lg" />
          <h2>Workload Control Center</h2>
          <div style={{ color: '#A8C8E8', fontSize: 10, marginTop: 4 }}>AutoSys Simulator v1.0</div>
        </div>
        <form className="login-body" onSubmit={handleSubmit}>
          {error && <div className="login-error">{error}</div>}
          <div className="login-field">
            <label htmlFor="username">Username</label>
            <input
              id="username" type="text" autoComplete="username" autoFocus
              value={username} onChange={e => setUsername(e.target.value)} required
            />
          </div>
          <div className="login-field">
            <label htmlFor="password">Password</label>
            <input
              id="password" type="password" autoComplete="current-password"
              value={password} onChange={e => setPassword(e.target.value)} required
            />
          </div>
          <button className="login-btn" type="submit" disabled={loading}>
            {loading ? 'Signing in…' : 'Sign In'}
          </button>
        </form>
      </div>
    </div>
  );
};

export default Login;
