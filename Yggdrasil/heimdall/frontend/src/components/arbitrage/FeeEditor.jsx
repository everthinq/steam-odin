import { useState, useEffect, useRef } from 'react';
import { Percent, ChevronDown, Zap } from 'lucide-react';
import NotInPulseNote from './NotInPulseNote';

// Sell-side fee editor. Fees are entered as percent and stored as fractions via
// POST /api/huginn/markets/fees (persisted in settings.json). This is the ONE place
// fees are set: every tab (Arbitrage profiles, Case Arbitrage, Cross-Profile,
// Harvest, LOOT.Farm, auctions) reads them. Tradeon is never a sell target.
const feeToPct = (frac) => (frac == null ? '' : String(+(frac * 100).toFixed(2)));

const FeeEditor = ({ markets, onSaved }) => {
    const [open, setOpen] = useState(false);
    const [draft, setDraft] = useState({});
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);
    const ref = useRef(null);

    const editable = markets.filter(m => m.id !== 'TradeOnMarket');

    // Seed the draft from the current fees each time the panel opens.
    useEffect(() => {
        if (!open) return;
        setDraft(Object.fromEntries(editable.map(m => [m.id, feeToPct(m.fee)])));
        setError(null);
    }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => {
        const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, []);

    const save = async () => {
        setSaving(true);
        setError(null);
        const fees = {};
        for (const [id, v] of Object.entries(draft)) {
            const n = parseFloat(v);
            if (Number.isFinite(n) && n >= 0 && n < 100) fees[id] = n / 100;
        }
        try {
            const r = await fetch('/api/huginn/markets/fees', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ fees }),
            });
            const d = await r.json().catch(() => ({}));
            if (!r.ok) throw new Error(d.error || 'Save failed');
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
                onClick={() => setOpen(o => !o)}
                className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-odin-blue/60 border border-amber-500/20 hover:border-amber-500/40 hover:bg-odin-blue/80 transition-all text-sm text-slate-300"
            >
                <Percent size={13} className="text-amber-500/60" /> Fees
                <ChevronDown size={13} className={`text-slate-500 transition-transform ${open ? 'rotate-180' : ''}`} />
            </button>

            {open && (
                <div className="absolute top-full right-0 mt-1.5 z-50 w-[21rem] bg-[#0d1520] border border-white/10 rounded-xl shadow-2xl shadow-black/60">
                    <div className="px-3 py-2.5 border-b border-white/10 text-[11px] leading-snug text-slate-400">
                        Sell-side fees, netted from each pair&apos;s profit. Your numbers here
                        win; a market you never changed uses our confirmed fee, else Tradeon
                        pulse&apos;s fee (<span className="text-sky-400/70">pulse</span>;
                        <span className="text-amber-400/70"> yours</span> = you changed it).
                        <Zap size={10} className="inline text-emerald-400/70 mx-0.5" />= has buy orders
                        (sells instantly). Re-fetch a profile to apply changes.
                    </div>
                    <div className="max-h-[50vh] overflow-y-auto custom-scrollbar py-1">
                        {editable.map(m => (
                            <div key={m.id} className="flex items-center gap-2 px-3 py-1.5">
                                <span className="flex-1 flex items-center gap-1.5 min-w-0 text-sm text-slate-300">
                                    <span className="truncate">{m.display}</span>
                                    {m.hasAutobuy && (
                                        <span title="Has buy orders: sells instantly" className="inline-flex shrink-0">
                                            <Zap size={11} className="text-emerald-400/70" />
                                        </span>
                                    )}
                                    {m.notInPulseUi && <NotInPulseNote size={11} extra={m.priceNote} />}
                                </span>
                                {m.feeEdited ? (
                                    <span title={`Your fee. Default: ${feeToPct(m.feeDefault)}% (${m.feeSource === 'pulse' ? 'Tradeon pulse' : 'confirmed'})`} className="text-[9px] uppercase tracking-wider text-amber-400/70">yours</span>
                                ) : m.feeSource === 'pulse' && (
                                    <span title="Tradeon pulse's fee for this market" className="text-[9px] uppercase tracking-wider text-sky-400/70">pulse</span>
                                )}
                                <div className="flex items-center gap-1">
                                    <input
                                        type="number"
                                        min="0"
                                        max="99"
                                        step="0.1"
                                        value={draft[m.id] ?? ''}
                                        onChange={e => setDraft(d => ({ ...d, [m.id]: e.target.value }))}
                                        className="w-16 bg-black/30 border border-white/10 rounded px-2 py-1 text-sm text-right text-slate-200 tabular-nums outline-none focus:border-amber-500/40"
                                    />
                                    <span className="text-xs text-slate-500">%</span>
                                </div>
                            </div>
                        ))}
                    </div>
                    {error && <div className="px-3 py-2 text-xs text-red-400">{error}</div>}
                    <div className="flex items-center justify-end gap-2 px-3 py-2.5 border-t border-white/10">
                        <button type="button" onClick={() => setOpen(false)} className="px-3 py-1.5 text-sm text-slate-400 hover:text-slate-200">
                            Cancel
                        </button>
                        <button
                            type="button"
                            onClick={save}
                            disabled={saving}
                            className="px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-sm font-medium disabled:opacity-50"
                        >
                            {saving ? 'Saving…' : 'Save fees'}
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
};

export default FeeEditor;
