import React, { createContext, useContext, useEffect, useState } from 'react';
import { useAuth } from './AuthContext';
import { getExecutionMode } from '../api/settings';

interface ExecutionModeValue {
  loaded: boolean;
  dryRun: boolean | null;
  // A dry-run-only image: hold/ice controls and the alarm console are hidden.
  dryRunOnly: boolean;
}

const ExecutionModeContext = createContext<ExecutionModeValue>({ loaded: false, dryRun: null, dryRunOnly: false });

export const ExecutionModeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAuthenticated } = useAuth();
  const [value, setValue] = useState<ExecutionModeValue>({ loaded: false, dryRun: null, dryRunOnly: false });

  useEffect(() => {
    if (!isAuthenticated) return;
    getExecutionMode()
      .then(m => setValue({ loaded: true, dryRun: m.dry_run, dryRunOnly: !!m.dry_run_only }))
      .catch(() => setValue({ loaded: true, dryRun: null, dryRunOnly: false }));
  }, [isAuthenticated]);

  return <ExecutionModeContext.Provider value={value}>{children}</ExecutionModeContext.Provider>;
};

export const useExecutionMode = (): ExecutionModeValue => useContext(ExecutionModeContext);
