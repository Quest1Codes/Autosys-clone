import { useEffect, useState } from 'react';

export type SSEStatus = 'connecting' | 'live' | 'disconnected';

interface JobStatusUpdate {
  job_name: string;
  status: string;
}

export const useSSE = (
  onUpdate: (updates: JobStatusUpdate[]) => void
): SSEStatus => {
  const [sseStatus, setSseStatus] = useState<SSEStatus>('connecting');

  useEffect(() => {
    let es: EventSource;
    let reconnectTimer: ReturnType<typeof setTimeout>;

    const connect = () => {
      // EventSource cannot send an Authorization header, so the stream takes
      // the login token as a query parameter (the server accepts ?token= on
      // stream routes only).
      const token = localStorage.getItem('wcc_token') ?? '';
      es = new EventSource(`/api/sse/jobs?token=${encodeURIComponent(token)}`);

      es.onopen = () => setSseStatus('live');

      es.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as JobStatusUpdate[];
          onUpdate(data);
          setSseStatus('live');
        } catch {
          // ignore parse errors
        }
      };

      es.onerror = () => {
        setSseStatus('disconnected');
        es.close();
        reconnectTimer = setTimeout(connect, 5000);
      };
    };

    connect();

    return () => {
      clearTimeout(reconnectTimer);
      es?.close();
    };
  }, [onUpdate]);

  return sseStatus;
};
