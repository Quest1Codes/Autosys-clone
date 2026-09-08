import React, { useState, useRef, useCallback } from 'react';
import { validateJIL, importJIL } from '../api/jil';
import type { JILImportResponse, JILJobResult } from '../api/jil';
import { useToast } from '../contexts/ToastContext';

interface Props {
  onClose: () => void;
  onImported: () => void;
}

const ACTION_COLOR: Record<string, string> = {
  INSERTED: '#00A650',
  UPDATED:  '#FF8C00',
  DELETED:  '#CC0000',
  OK:       '#0055A5',
  MACHINE:  '#6A0DAD',
};

const JilImportModal: React.FC<Props> = ({ onClose, onImported }) => {
  const { showToast } = useToast();
  const [jilText, setJilText]       = useState('');
  const [result, setResult]         = useState<JILImportResponse | null>(null);
  const [loading, setLoading]       = useState(false);
  const [mode, setMode]             = useState<'validate' | 'import'>('validate');
  const [dragging, setDragging]     = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadFile = (file: File) => {
    const reader = new FileReader();
    reader.onload = e => setJilText((e.target?.result as string) ?? '');
    reader.readAsText(file);
  };

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) loadFile(file);
  }, []);

  const handleValidate = async () => {
    if (!jilText.trim()) return;
    setLoading(true);
    setResult(null);
    try {
      const res = await validateJIL(jilText);
      setResult(res);
      setMode('validate');
    } catch {
      showToast('Validation request failed', 'error');
    } finally {
      setLoading(false);
    }
  };

  const handleImport = async (dryRun = false) => {
    if (!jilText.trim()) return;
    setLoading(true);
    setResult(null);
    try {
      const res = await importJIL(jilText, dryRun);
      setResult(res);
      setMode('import');
      if (res.success && !dryRun) {
        showToast(
          `Imported: ${res.n_inserted} inserted, ${res.n_updated} updated, ${res.n_deleted} deleted`,
          'success'
        );
        onImported();
      }
    } catch {
      showToast('Import request failed', 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-box"
        style={{ width: 720, maxHeight: '85vh', display: 'flex', flexDirection: 'column' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="modal-header">
          <span>Import JIL</span>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        <div style={{ padding: '12px 16px', flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 10 }}>

          {/* Drop zone / file picker */}
          <div
            onDragOver={e => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            onClick={() => fileRef.current?.click()}
            style={{
              border: `2px dashed ${dragging ? '#003D7C' : '#CCC'}`,
              borderRadius: 4,
              padding: '10px 14px',
              textAlign: 'center',
              cursor: 'pointer',
              background: dragging ? '#EEF4FF' : '#F9F9F9',
              fontSize: 12,
              color: '#555',
              flexShrink: 0,
            }}
          >
            📂 Drop a <strong>.jil</strong> file here, or <u>click to browse</u>
            <input
              ref={fileRef}
              type="file"
              accept=".jil,.txt"
              style={{ display: 'none' }}
              onChange={e => e.target.files?.[0] && loadFile(e.target.files[0])}
            />
          </div>

          {/* Text editor */}
          <textarea
            value={jilText}
            onChange={e => setJilText(e.target.value)}
            placeholder={`Paste JIL here, e.g.:\n\ninsert_job: my_job   job_type: CMD\ncommand: /bin/echo hello\nmachine: localhost`}
            style={{
              flex: 1,
              minHeight: 180,
              fontFamily: 'monospace',
              fontSize: 11,
              padding: '8px',
              border: '1px solid #CCC',
              borderRadius: 3,
              resize: 'vertical',
            }}
          />

          {/* Action buttons */}
          <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
            <button className="wcc-btn wcc-btn-secondary" onClick={handleValidate} disabled={loading || !jilText.trim()}>
              {loading && mode === 'validate' ? '…' : '✓ Validate'}
            </button>
            <button className="wcc-btn wcc-btn-secondary" onClick={() => handleImport(true)} disabled={loading || !jilText.trim()}>
              {loading ? '…' : '⟳ Dry Run'}
            </button>
            <button className="wcc-btn wcc-btn-primary" onClick={() => handleImport(false)} disabled={loading || !jilText.trim()}>
              {loading && mode === 'import' ? 'Importing…' : '⬆ Import'}
            </button>
            <button className="wcc-btn wcc-btn-secondary" onClick={() => { setJilText(''); setResult(null); }} style={{ marginLeft: 'auto' }}>
              Clear
            </button>
          </div>

          {/* Results table */}
          {result && (
            <div style={{ flexShrink: 0 }}>
              {result.error ? (
                <div style={{ background: '#FFF0F0', border: '1px solid #CC0000', padding: '8px 12px', borderRadius: 3, color: '#CC0000', fontSize: 12 }}>
                  <strong>Parse error:</strong> {result.error}
                </div>
              ) : (
                <>
                  <div style={{ display: 'flex', gap: 16, fontSize: 11, marginBottom: 6, color: '#444' }}>
                    <span>✅ Success</span>
                    {mode === 'import' && (
                      <>
                        <span style={{ color: ACTION_COLOR.INSERTED }}>+{result.n_inserted} inserted</span>
                        <span style={{ color: ACTION_COLOR.UPDATED }}>↺ {result.n_updated} updated</span>
                        <span style={{ color: ACTION_COLOR.DELETED }}>✗ {result.n_deleted} deleted</span>
                      </>
                    )}
                    {result.n_machines > 0 && <span style={{ color: ACTION_COLOR.MACHINE }}>🖥 {result.n_machines} machines</span>}
                  </div>
                  <table className="wcc-table" style={{ fontSize: 11 }}>
                    <thead>
                      <tr><th>Job Name</th><th>Type</th><th>Action</th></tr>
                    </thead>
                    <tbody>
                      {result.jobs.map((j: JILJobResult, i: number) => (
                        <tr key={i}>
                          <td style={{ fontFamily: 'monospace' }}>{j.name}</td>
                          <td>{j.type}</td>
                          <td>
                            <span style={{
                              background: ACTION_COLOR[j.action] ?? '#888',
                              color: '#FFF', borderRadius: 2,
                              padding: '1px 6px', fontSize: 10,
                            }}>
                              {j.action}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default JilImportModal;
