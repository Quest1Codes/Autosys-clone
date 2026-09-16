import React, { createContext, useContext, useState, useCallback } from 'react';
import axios from 'axios';

interface AuthState {
  token: string | null;
  username: string | null;
  role: string | null;
}

interface AuthContextValue extends AuthState {
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  isAuthenticated: boolean;
  isAdmin: boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/** JWTs are signed, not encrypted — the payload is safe to read client-side. */
function decodeRole(token: string | null): string | null {
  if (!token) return null;
  try {
    const payload = token.split('.')[1];
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
    return JSON.parse(json).role ?? null;
  } catch {
    return null;
  }
}

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [state, setState] = useState<AuthState>(() => {
    const token = localStorage.getItem('wcc_token');
    return {
      token,
      username: localStorage.getItem('wcc_user'),
      role: decodeRole(token),
    };
  });

  const login = useCallback(async (username: string, password: string) => {
    const res = await axios.post('/api/v1/auth/token', { username, password });
    const { access_token } = res.data;
    localStorage.setItem('wcc_token', access_token);
    localStorage.setItem('wcc_user', username);
    setState({ token: access_token, username, role: decodeRole(access_token) });
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem('wcc_token');
    localStorage.removeItem('wcc_user');
    setState({ token: null, username: null, role: null });
  }, []);

  return (
    <AuthContext.Provider value={{
      ...state,
      login,
      logout,
      isAuthenticated: !!state.token,
      isAdmin: state.role === 'admin',
    }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthContextValue => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
};
