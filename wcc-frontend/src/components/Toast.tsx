import React from 'react';
import { useToast } from '../contexts/ToastContext';

const ToastContainer: React.FC = () => {
  const { toasts, removeToast } = useToast();
  if (!toasts.length) return null;
  return (
    <div className="toast-container">
      {toasts.map(t => (
        <div key={t.id} className={`toast ${t.type}`} onClick={() => removeToast(t.id)}>
          <span>{t.type === 'success' ? '✓' : t.type === 'error' ? '✗' : 'ℹ'}</span>
          <span>{t.message}</span>
        </div>
      ))}
    </div>
  );
};

export default ToastContainer;
