import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { ToastProvider } from './contexts/ToastContext';
import ToastContainer from './components/Toast';
import Login from './pages/Login';
import JobMonitor from './pages/JobMonitor';
import JobDetail from './pages/JobDetail';
import BoxGraph from './pages/BoxGraph';
import AlarmConsole from './pages/AlarmConsole';

const ProtectedRoute: React.FC<{ element: React.ReactElement }> = ({ element }) => {
  const { isAuthenticated } = useAuth();
  return isAuthenticated ? element : <Navigate to="/login" replace />;
};

const AppRoutes: React.FC = () => (
  <BrowserRouter>
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/"            element={<ProtectedRoute element={<JobMonitor />} />} />
      <Route path="/jobs/:name"  element={<ProtectedRoute element={<JobDetail />} />} />
      <Route path="/boxes/:name" element={<ProtectedRoute element={<BoxGraph />} />} />
      <Route path="/alarms"      element={<ProtectedRoute element={<AlarmConsole />} />} />
      <Route path="*"            element={<Navigate to="/" replace />} />
    </Routes>
    <ToastContainer />
  </BrowserRouter>
);

const App: React.FC = () => (
  <AuthProvider>
    <ToastProvider>
      <AppRoutes />
    </ToastProvider>
  </AuthProvider>
);

export default App;
