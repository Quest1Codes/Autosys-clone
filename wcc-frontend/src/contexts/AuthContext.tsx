import React, { createContext, useContext, useState, useCallback, useEffect } from 'react';
import { api } from '../api/client';

interface AuthState {
  username: string | null;
  role: string | null;
  loaded: boolean;
}

interface AuthContextValue extends AuthState {
  logout: () => Promise<void>;
  isAuthenticated: boolean;
  isAdmin: boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [state, setState] = useState<AuthState>({ username: null, role: null, loaded: false });

  useEffect(() => {
    let cancelled = false;
    api
      .get('/auth/me')
      .then((res) => {
        if (!cancelled) setState({ username: res.data.username, role: res.data.role, loaded: true });
      })
      .catch(() => {
        // A 401 here already sends the browser to Cognito (api/client.ts's
        // response interceptor); any other failure just leaves auth
        // unresolved rather than forcing a sign-in redirect of its own.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const logout = useCallback(async () => {
    const res = await api.post('/auth/logout');
    window.location.href = res.data.logout_url;
  }, []);

  return (
    <AuthContext.Provider
      value={{
        ...state,
        logout,
        isAuthenticated: !!state.username,
        isAdmin: state.role === 'admin',
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthContextValue => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
};
