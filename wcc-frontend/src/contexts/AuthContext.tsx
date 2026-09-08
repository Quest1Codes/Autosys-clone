import React, { createContext, useContext, useState, useCallback } from 'react';
import axios from 'axios';

interface AuthState {
  token: string | null;
  username: string | null;
}

interface AuthContextValue extends AuthState {
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  isAuthenticated: boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [state, setState] = useState<AuthState>(() => ({
    token: localStorage.getItem('wcc_token'),
    username: localStorage.getItem('wcc_user'),
  }));

  const login = useCallback(async (username: string, password: string) => {
    const res = await axios.post('/api/v1/auth/token', { username, password });
    const { access_token } = res.data;
    localStorage.setItem('wcc_token', access_token);
    localStorage.setItem('wcc_user', username);
    setState({ token: access_token, username });
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem('wcc_token');
    localStorage.removeItem('wcc_user');
    setState({ token: null, username: null });
  }, []);

  return (
    <AuthContext.Provider value={{
      ...state,
      login,
      logout,
      isAuthenticated: !!state.token,
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
