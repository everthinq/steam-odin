import React, { useState, useEffect, useCallback } from 'react';
import { X, History, RotateCcw, Download, Camera, RefreshCw, Check, AlertTriangle, HardDrive } from 'lucide-react';
import { ConfirmDialog } from './DraupnirDialog';

// Draupnir backups browser — lists point-in-time snapshots of portfolios.json
// and lets you restore, download, or take one now. Backend: /api/portfolios/backups*.
const REASON_STYLE = {
    change: { label: 'change', cls: 'text-sky-300 bg-sky-500/10' },
    daily: { label: 'daily', cls: 'text-emerald-300 bg-emerald-500/10' },
    boot: { label: 'boot', cls: 'text-slate-300 bg-white/5' },
    manual: { label: 'manual', cls: 'text-yellow-300 bg-yellow-500/10' },
    'pre-restore': { label: 'pre-restore', cls: 'text-orange-300 bg-orange-500/10' },
};

const fmtSize = (n) => {
    if (n == null) return '—';
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
};

const fmtWhen = (iso) => {
    try {
        return new Date(iso).toLocaleString([], {
            year: 'numeric', month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit',
        });
    } catch { return iso; }
};

const BackupsPanel = ({ onClose, onRestored }) => {
    const [backups, setBackups] = useState([]);
    const [stats, setStats] = useState(null);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState('');   // 'snapshot' | 'restore' | ''
    const [msg, setMsg] = useState(null);    // {ok, text}
    const [confirm, setConfirm] = useState(null);   // backup pending restore

    const flash = (ok, text) => { setMsg({ ok, text }); setTimeout(() => setMsg(null), 6000); };

    const load = useCallback(async () => {
        try {
            const r = await fetch('/api/portfolios/backups');
            if (!r.ok) throw new Error('Failed to load backups');
            const d = await r.json();
            setBackups(d.backups || []);
            setStats(d.stats || null);
        } catch (e) {
            flash(false, e.message);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);
    useEffect(() => {
        const onKey = (e) => { if (e.key === 'Escape' && !confirm) onClose?.(); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [onClose, confirm]);

    const snapshotNow = async () => {
        setBusy('snapshot');
        try {
            const r = await fetch('/api/portfolios/backups/snapshot', { method: 'POST' });
            const d = await r.json();
            flash(true, d.deduped ? 'Already up to date — no change since the last snapshot.' : 'Snapshot saved.');
            load();
        } catch (e) {
            flash(false, e.message);
        } finally { setBusy(''); }
    };

    const doRestore = async (name) => {
        setConfirm(null);
        setBusy('restore');
        try {
            const r = await fetch('/api/portfolios/backups/restore', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name }),
            });
            const d = await r.json();
            if (!d.ok) throw new Error(d.error || 'restore failed');
            flash(true, 'Restored. Current state was saved first as a pre-restore backup.');
            load();
            onRestored?.();
        } catch (e) {
            flash(false, e.message);
        } finally { setBusy(''); }
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
            <div className="relative w-full max-w-2xl max-h-[85vh] flex flex-col bg-slate-900 border border-white/10 rounded-2xl shadow-2xl">
                {/* Header */}
                <div className="shrink-0 flex items-center gap-2.5 px-5 py-4 border-b border-white/5">
                    <History size={18} className="text-yellow-500" />
                    <h3 className="font-serif font-semibold text-white">Portfolio backups</h3>
                    <span className="text-[11px] text-slate-500">point-in-time restore</span>
                    <div className="ml-auto flex items-center gap-2">
                        <button
                            onClick={snapshotNow} disabled={busy === 'snapshot'}
                            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-yellow-600 hover:bg-yellow-500 text-white text-xs font-medium disabled:opacity-50 transition-colors"
                        >
                            <Camera size={13} className={busy === 'snapshot' ? 'animate-pulse' : ''} /> Snapshot now
                        </button>
                        <button onClick={onClose} className="p-1.5 rounded text-slate-500 hover:text-white hover:bg-white/10 transition-colors"><X size={16} /></button>
                    </div>
                </div>

                {/* Stats */}
                {stats && (
                    <div className="shrink-0 flex items-center gap-4 px-5 py-2.5 text-[11px] text-slate-500 border-b border-white/5 bg-black/20">
                        <span className="flex items-center gap-1.5"><HardDrive size={12} /> {stats.count} snapshot{stats.count !== 1 ? 's' : ''} · {fmtSize(stats.total_size)}</span>
                        {stats.oldest && <span>oldest {fmtWhen(stats.oldest)}</span>}
                        <span className="ml-auto text-slate-600">auto: on every change + daily</span>
                    </div>
                )}

                {msg && (
                    <div className={`shrink-0 flex items-center gap-1.5 px-5 py-2 text-xs ${msg.ok ? 'text-emerald-400' : 'text-red-400'}`}>
                        {msg.ok ? <Check size={13} /> : <AlertTriangle size={13} />} {msg.text}
                    </div>
                )}

                {/* List */}
                <div className="flex-1 overflow-y-auto px-2 py-2">
                    {loading ? (
                        <div className="flex justify-center items-center h-40">
                            <RefreshCw className="animate-spin text-yellow-500" size={28} />
                        </div>
                    ) : backups.length === 0 ? (
                        <div className="text-center py-16 text-slate-500 text-sm">
                            No backups yet — one is taken automatically on the next change or daily tick.
                        </div>
                    ) : (
                        <div className="flex flex-col">
                            {backups.map((b, i) => {
                                const rs = REASON_STYLE[b.reason] || REASON_STYLE.manual;
                                return (
                                    <div key={b.name} className="flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-white/5 transition-colors group">
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2">
                                                <span className="text-sm text-slate-200">{fmtWhen(b.timestamp)}</span>
                                                {i === 0 && <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300">latest</span>}
                                                <span className={`text-[10px] px-1.5 py-0.5 rounded ${rs.cls}`}>{rs.label}</span>
                                            </div>
                                            <div className="text-[11px] text-slate-600 font-mono truncate">{b.hash} · {fmtSize(b.size)}</div>
                                        </div>
                                        <a
                                            href={`/api/portfolios/backups/${b.name}/download`}
                                            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/10 text-xs transition-colors opacity-0 group-hover:opacity-100"
                                            title="Download this snapshot"
                                        >
                                            <Download size={13} />
                                        </a>
                                        <button
                                            onClick={() => setConfirm(b)}
                                            disabled={busy === 'restore'}
                                            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-white/5 hover:bg-yellow-600 border border-white/10 text-slate-300 hover:text-white text-xs font-medium disabled:opacity-50 transition-colors"
                                            title="Restore this snapshot"
                                        >
                                            <RotateCcw size={13} /> Restore
                                        </button>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </div>
            </div>

            <ConfirmDialog
                open={!!confirm}
                danger
                title="Restore this backup?"
                message={confirm ? `Portfolios will be replaced with the snapshot from ${fmtWhen(confirm.timestamp)}. Your current state is saved first as a pre-restore backup, so this is reversible.` : ''}
                confirmLabel="Restore"
                onConfirm={() => doRestore(confirm.name)}
                onCancel={() => setConfirm(null)}
            />
        </div>
    );
};

export default BackupsPanel;
