import { useState, useEffect, useRef } from 'react';
import { Landmark, ChevronDown, Plus, Trash2, Zap } from 'lucide-react';

// The instant-redeploy market whitelist: which markets do NOT lock your balance
// for days after a sale, so proceeds can fund the freshly-limited case right away.
// Filled in gradually (Ivan verifies each market by hand, then records it here).
// Persisted to settings.json via POST /api/huginn/gjallarhorn/holds.
const blankRow = () => ({ id: '', display: '', holdDays: 0, instantRedeploy: true, notes: '' });

const MarketHoldEditor = ({ onSaved }) => {
    const [open, setOpen] = useState(false);
    const [rows, setRows] = useState([]);
    const [suggestions, setSuggestions] = useState([]);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);
    const [count, setCount] = useState(0);
    const ref = useRef(null);

    const loadHolds = () => fetch('/api/huginn/gjallarhorn/holds')
        .then((r) => (r.ok ? r.json() : []))
        .then((data) => { if (Array.isArray(data)) setCount(data.length); })
        .catch(() => {});

    useEffect(() => { loadHolds(); }, []);

    useEffect(() => {
        if (!open) return;
        fetch('/api/huginn/gjallarhorn/holds')
            .then((r) => (r.ok ? r.json() : []))
            .then((data) => { setError(null); setRows(Array.isArray(data) && data.length ? data.map((h) => ({ ...blankRow(), ...h })) : [blankRow()]); })
            .catch(() => setRows([blankRow()]));
        fetch('/api/huginn/markets')
            .then((r) => (r.ok ? r.json() : []))
            .then((data) => { if (Array.isArray(data)) setSuggestions(data.map((m) => m.display)); })
            .catch(() => {});
    }, [open]);

    useEffect(() => {
        const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, []);

    const update = (i, patch) => setRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
    const remove = (i) => setRows((rs) => rs.filter((_, idx) => idx !== i));

    const save = async () => {
        setSaving(true);
        setError(null);
        const holds = rows
            .filter((r) => (r.display || '').trim())
            .map((r) => ({
                id: (r.id || r.display).trim(),
                display: r.display.trim(),
                holdDays: Number.isFinite(+r.holdDays) ? Math.max(0, parseInt(r.holdDays, 10) || 0) : 0,
                instantRedeploy: !!r.instantRedeploy,
                notes: (r.notes || '').slice(0, 200),
            }));
        try {
            const res = await fetch('/api/huginn/gjallarhorn/holds', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ holds }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.error || 'Save failed');
            setCount(Array.isArray(data) ? data.length : holds.length);
            await onSaved?.();
            setOpen(false);
        } catch (e) {
            setError(e.message);
        } finally {
            setSaving(false);
        }
    };

    return (
        <div ref={ref} className="relative shrink-0">
            <button
                type="button"
                onClick={() => setOpen((o) => !o)}
                className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-odin-blue/60 border border-emerald-500/20 hover:border-emerald-500/40 hover:bg-odin-blue/80 transition-all text-sm text-slate-300"
            >
                <Landmark size={13} className="text-emerald-500/70" /> Redeploy markets
                {count > 0 && <span className="text-[10px] tabular-nums text-emerald-400/80">({count})</span>}
                <ChevronDown size={13} className={`text-slate-500 transition-transform ${open ? 'rotate-180' : ''}`} />
            </button>

            {open && (
                <div className="absolute top-full right-0 mt-1.5 z-50 w-[36rem] bg-[#0d1520] border border-white/10 rounded-xl shadow-2xl shadow-black/60">
                    <div className="px-3 py-2.5 border-b border-white/10 text-[11px] leading-snug text-slate-400">
                        Markets whose balance you can re-spend immediately after selling (no multi-day
                        hold). <span className="text-emerald-400/80">Hold days = 0</span> means instant.
                        Add each one as you verify it by hand.
                    </div>
                    <datalist id="gj-market-suggestions">
                        {suggestions.map((s) => <option key={s} value={s} />)}
                    </datalist>
                    <div className="max-h-[50vh] overflow-y-auto custom-scrollbar py-1">
                        <div className="grid grid-cols-[1fr_5rem_4rem_1.4fr_1.6rem] gap-2 px-3 py-1 text-[9px] uppercase tracking-wider text-slate-600">
                            <span>Market</span><span className="text-center">Hold days</span><span className="text-center">Instant</span><span>Notes</span><span />
                        </div>
                        {rows.map((r, i) => (
                            <div key={i} className="grid grid-cols-[1fr_5rem_4rem_1.4fr_1.6rem] gap-2 px-3 py-1.5 items-center">
                                <input
                                    list="gj-market-suggestions"
                                    value={r.display}
                                    placeholder="e.g. CS.Money (Trade)"
                                    onChange={(e) => update(i, { display: e.target.value })}
                                    className="bg-black/30 border border-white/10 rounded px-2 py-1 text-sm text-slate-200 outline-none focus:border-emerald-500/40"
                                />
                                <input
                                    type="number" min="0" step="1"
                                    value={r.holdDays}
                                    onChange={(e) => update(i, { holdDays: e.target.value })}
                                    className="bg-black/30 border border-white/10 rounded px-2 py-1 text-sm text-right text-slate-200 tabular-nums outline-none focus:border-emerald-500/40"
                                />
                                <button
                                    type="button"
                                    onClick={() => update(i, { instantRedeploy: !r.instantRedeploy })}
                                    className={`mx-auto flex items-center justify-center w-8 h-6 rounded ${r.instantRedeploy ? 'bg-emerald-600/70 text-white' : 'bg-white/5 text-slate-500'}`}
                                    title={r.instantRedeploy ? 'Instant redeploy' : 'Held'}
                                >
                                    <Zap size={12} />
                                </button>
                                <input
                                    value={r.notes}
                                    placeholder="withdrawal delay, etc."
                                    onChange={(e) => update(i, { notes: e.target.value })}
                                    className="bg-black/30 border border-white/10 rounded px-2 py-1 text-xs text-slate-300 outline-none focus:border-emerald-500/40"
                                />
                                <button type="button" onClick={() => remove(i)} className="text-slate-600 hover:text-red-400" title="Remove">
                                    <Trash2 size={13} />
                                </button>
                            </div>
                        ))}
                        <button
                            type="button"
                            onClick={() => setRows((rs) => [...rs, blankRow()])}
                            className="flex items-center gap-1.5 mx-3 my-2 px-2.5 py-1.5 rounded-lg text-xs text-emerald-400/80 hover:text-emerald-300 hover:bg-emerald-500/10 border border-dashed border-emerald-500/20"
                        >
                            <Plus size={12} /> Add market
                        </button>
                    </div>
                    {error && <div className="px-3 py-2 text-xs text-red-400">{error}</div>}
                    <div className="flex items-center justify-end gap-2 px-3 py-2.5 border-t border-white/10">
                        <button type="button" onClick={() => setOpen(false)} className="px-3 py-1.5 text-sm text-slate-400 hover:text-slate-200">Cancel</button>
                        <button type="button" onClick={save} disabled={saving} className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium disabled:opacity-50">
                            {saving ? 'Saving…' : 'Save list'}
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
};

export default MarketHoldEditor;
