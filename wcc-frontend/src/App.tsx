import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { ToastProvider } from './contexts/ToastContext';
import ToastContainer from './components/Toast';
import JobMonitor from './pages/JobMonitor';
import JobDetail from './pages/JobDetail';
import BoxGraph from './pages/BoxGraph';
import AlarmConsole from './pages/AlarmConsole';
import { ExecutionModeProvider, useExecutionMode } from './contexts/ExecutionModeContext';

// There is no /login page any more -- sign-in is a Cognito redirect
// (api/client.ts sends the browser there on a 401). While that redirect is
// in flight, or while the initial /auth/me check is still pending, render
// nothing rather than a route that doesn't exist.
const ProtectedRoute: React.FC<{ element: React.ReactElement }> = ({ element }) => {
  const { loaded, isAuthenticated } = useAuth();
  if (!loaded) return null;
  return isAuthenticated ? element : null;
};

// The dry-run-only image has no alarm console.
const AlarmsRoute: React.FC = () => {
  const { loaded, dryRunOnly } = useExecutionMode();
  if (!loaded) return null;
  return dryRunOnly ? <Navigate to="/" replace /> : <AlarmConsole />;
};

const AppRoutes: React.FC = () => (
  <BrowserRouter>
    <Routes>
      <Route path="/"            element={<ProtectedRoute element={<JobMonitor />} />} />
      <Route path="/jobs/:name"  element={<ProtectedRoute element={<JobDetail />} />} />
      <Route path="/boxes/:name" element={<ProtectedRoute element={<BoxGraph />} />} />
      <Route path="/alarms"      element={<ProtectedRoute element={<AlarmsRoute />} />} />
      <Route path="*"            element={<Navigate to="/" replace />} />
    </Routes>
    <ToastContainer />
  </BrowserRouter>
);

const App: React.FC = () => (
  <AuthProvider>
    <ToastProvider>
      <ExecutionModeProvider>
        <AppRoutes />
      </ExecutionModeProvider>
    </ToastProvider>
  </AuthProvider>
);

export default App;
