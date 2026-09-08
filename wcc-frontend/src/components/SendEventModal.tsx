import React, { useState } from 'react';
import type { EventType } from '../types';

const EVENT_DESCRIPTIONS: Record<EventType, string> = {
  STARTJOB:       'Start the job if its conditions are satisfied.',
  FORCE_STARTJOB: 'Force start the job, bypassing all conditions and dependencies.',
  KILLJOB:        'Terminate the running job immediately.',
  JOB_ON_HOLD:    'Place the job on hold — it will not start until released.',
  JOB_OFF_HOLD:   'Release the job from hold, returning it to INACTIVE.',
  JOB_ON_ICE:     'Freeze the job for the current cycle — it will not run.',
  JOB_OFF_ICE:    'Release the job from ice.',
  CHANGE_STATUS:  'Manually force the job to a specific status.',
};

const DESTRUCTIVE: EventType[] = ['KILLJOB', 'FORCE_STARTJOB'];

const ALL_EVENTS: EventType[] = [
  'STARTJOB', 'FORCE_STARTJOB', 'KILLJOB',
  'JOB_ON_HOLD', 'JOB_OFF_HOLD', 'JOB_ON_ICE', 'JOB_OFF_ICE',
];

interface Props {
  jobName: string;
  initialEvent?: EventType;
  onClose: () => void;
  onSubmit: (event: EventType) => Promise<void>;
}

const SendEventModal: React.FC<Props> = ({ jobName, initialEvent = 'STARTJOB', onClose, onSubmit }) => {
  const [selectedEvent, setSelectedEvent] = useState<EventType>(initialEvent);
  const [confirmed, setConfirmed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isDestructive = DESTRUCTIVE.includes(selectedEvent);
  const canSubmit = !isDestructive || confirmed;

  const handleSubmit = async () => {
    setLoading(true);
    setError(null);
    try {
      await onSubmit(selectedEvent);
      onClose();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Request failed';
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const handleEventChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setSelectedEvent(e.target.value as EventType);
    setConfirmed(false);
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <span>Send Event — {jobName}</span>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">
          <div className="modal-field">
            <label>Event Type</label>
            <select className="wcc-select" style={{ height: 26, fontSize: 12 }}
              value={selectedEvent} onChange={handleEventChange}>
              {ALL_EVENTS.map(ev => (
                <option key={ev} value={ev}>{ev}</option>
              ))}
            </select>
          </div>

          <div className="modal-note">
            {EVENT_DESCRIPTIONS[selectedEvent]}
          </div>

          {isDestructive && (
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, cursor: 'pointer' }}>
              <input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />
              I understand this is a destructive operation and confirm I want to proceed.
            </label>
          )}

          {error && (
            <div style={{ background: '#FFE8E8', border: '1px solid #CC0000', borderRadius: 2, padding: '6px 10px', color: '#CC0000', fontSize: 11 }}>
              Error: {error}
            </div>
          )}
        </div>
        <div className="modal-footer">
          <button className="wcc-btn wcc-btn-secondary" onClick={onClose}>Cancel</button>
          <button
            className={`wcc-btn ${isDestructive ? 'wcc-btn-danger' : 'wcc-btn-primary'}`}
            onClick={handleSubmit}
            disabled={!canSubmit || loading}
          >
            {loading ? 'Sending…' : 'Send Event'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default SendEventModal;
