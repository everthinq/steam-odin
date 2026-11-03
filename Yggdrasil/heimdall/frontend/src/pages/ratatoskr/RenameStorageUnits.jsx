import { useState, useEffect, useCallback } from 'react';
import { useOutletContext } from 'react-router-dom';
import { Pencil, Check, X, RefreshCw, Wand2 } from 'lucide-react';
import { readJson } from '../../utils/readJson';

// Rename Storage Unit: every Storage Unit of this account and a name field for each.
// The backend decides everything (allowed names, no duplicates, not during a move,
// the "Storage N" numbering); this page only shows its answers (storage_rename.py).

const post = async (url, body) => {
    const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
    });
    const data = await readJson(response).catch(() => ({}));
    if (!response.ok || data.error) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
};

const RenameStorageUnits = () => {
    const { steamid } = useOutletContext();
    const [units, setUnits] = useState([]);
    const [maxLength, setMaxLength] = useState(20);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [message, setMessage] = useState(null);
    const [editing, setEditing] = useState(null);   // item_id being renamed
    const [draft, setDraft] = useState('');
    const [busy, setBusy] = useState(false);
    const [plan, setPlan] = useState(null);         // naming plan for the unnamed units

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const response = await fetch(`/api/ratatoskr/storage-units/${steamid}`);
            const data = await readJson(response);
            if (!response.ok || data.error) throw new Error(data.error || `HTTP ${response.status}`);
            setUnits(data.units || []);
            setMaxLength(data.name_max_length || 20);
            setError(null);
        } catch (err) {
            setError(`Could not load the Storage Units: ${err.message}`);
        } finally {
            setLoading(false);
        }
    }, [steamid]);

    useEffect(() => { load(); }, [load]);

    const startEdit = (unit) => {
        setEditing(unit.item_id);
        setDraft(unit.name);
        setError(null);
        setMessage(null);
    };

    const save = async () => {
        if (busy) return;
        setBusy(true);
        try {
            const done = await post(`/api/ratatoskr/storage-units/${steamid}/rename`, { item_id: editing, name: draft });
            setMessage(`Renamed to "${done.name}".`);
            setEditing(null);
            await load();
        } catch (err) {
            setError(err.message);
        } finally {
            setBusy(false);
        }
    };

    const makePlan = async () => {
        setBusy(true);
        setError(null);
        setMessage(null);
        try {
            setPlan(await post(`/api/ratatoskr/storage-units/${steamid}/name-unnamed/plan`));
        } catch (err) {
            setError(err.message);
        } finally {
            setBusy(false);
        }
    };

    const executePlan = async () => {
        setBusy(true);
        try {
            const done = await post(`/api/ratatoskr/storage-units/name-plans/${plan.id}/execute`);
            const failures = done.results.filter((r) => !r.ok).map((r) => `${r.name}: ${r.error}`);
            setMessage(`Named ${done.named} Storage Unit${done.named === 1 ? '' : 's'}.`);
            if (failures.length) setError(`Not named: ${failures.join('; ')}`);
            setPlan(null);
            await load();
        } catch (err) {
            setError(err.message);
        } finally {
            setBusy(false);
        }
    };

    const unnamed = units.filter((u) => !u.named).length;

    return (
        <div className="max-w-3xl">
            <div className="flex items-center justify-between mb-6">
                <div>
                    <h1 className="text-2xl font-bold text-amber-100 font-serif">Rename Storage Unit</h1>
                    <p className="text-sm text-slate-400 mt-1">
                        Free in the game, up to {maxLength} characters. A Storage Unit without a name refuses items.
                    </p>
                </div>
                <button type="button" onClick={load} disabled={loading || busy}
                    className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs text-slate-300 border border-white/10 hover:bg-white/5 disabled:opacity-40">
                    <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh
                </button>
            </div>

            {unnamed > 0 && !plan && (
                <div className="mb-4 flex items-center justify-between rounded-xl border border-amber-500/30 bg-amber-900/10 px-4 py-3 text-sm text-amber-200">
                    <span>{unnamed} Storage Unit{unnamed === 1 ? ' has' : 's have'} no name and cannot take items.</span>
                    <button type="button" onClick={makePlan} disabled={busy}
                        className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs bg-amber-600 hover:bg-amber-500 text-white disabled:opacity-40">
                        <Wand2 size={14} /> Name them "Storage N"
                    </button>
                </div>
            )}

            {plan && (
                <div className="mb-4 rounded-xl border border-amber-500/30 bg-amber-900/10 px-4 py-3 text-sm text-amber-100">
                    <div className="mb-2">These names will be given:</div>
                    <ul className="mb-3 text-xs text-slate-300 space-y-0.5">
                        {plan.renames.map((r) => <li key={r.item_id}>Unit {r.item_id} → <b>{r.name}</b></li>)}
                    </ul>
                    <div className="flex gap-2">
                        <button type="button" onClick={executePlan} disabled={busy}
                            className="px-3 py-1.5 rounded-lg text-xs bg-amber-600 hover:bg-amber-500 text-white disabled:opacity-40">
                            {busy ? 'Naming…' : 'Execute plan'}
                        </button>
                        <button type="button" onClick={() => setPlan(null)} disabled={busy}
                            className="px-3 py-1.5 text-xs text-slate-400 hover:text-white">Cancel</button>
                    </div>
                </div>
            )}

            {error && <div className="mb-4 text-sm text-red-400">{error}</div>}
            {message && <div className="mb-4 text-sm text-emerald-400">{message}</div>}

            <div className="rounded-xl border border-white/10 overflow-hidden">
                <table className="w-full text-sm">
                    <thead className="bg-white/5 text-xs text-slate-400">
                        <tr>
                            <th className="text-left px-4 py-2 font-medium">Name</th>
                            <th className="text-right px-4 py-2 font-medium">Items</th>
                            <th className="text-right px-4 py-2 font-medium">Free places</th>
                            <th className="px-4 py-2" />
                        </tr>
                    </thead>
                    <tbody>
                        {!loading && units.length === 0 && (
                            <tr><td colSpan={4} className="px-4 py-6 text-center text-slate-500">No Storage Units on this account.</td></tr>
                        )}
                        {units.map((unit) => (
                            <tr key={unit.item_id} className="border-t border-white/5">
                                <td className="px-4 py-2">
                                    {editing === unit.item_id ? (
                                        <input value={draft} maxLength={maxLength} autoFocus disabled={busy}
                                            onChange={(e) => setDraft(e.target.value)}
                                            onKeyDown={(e) => {
                                                if (e.key === 'Enter') save();
                                                if (e.key === 'Escape') setEditing(null);
                                            }}
                                            className="w-full bg-black/40 border border-white/10 rounded px-2 py-1 text-white focus:outline-none focus:border-amber-500/40" />
                                    ) : unit.named ? (
                                        <span className="text-slate-200">{unit.name}</span>
                                    ) : (
                                        <span className="text-amber-300 italic">no name</span>
                                    )}
                                </td>
                                <td className="px-4 py-2 text-right tabular-nums text-slate-300">{unit.items}</td>
                                <td className="px-4 py-2 text-right tabular-nums text-slate-400">{unit.free_places}</td>
                                <td className="px-4 py-2 text-right whitespace-nowrap">
                                    {editing === unit.item_id ? (
                                        <>
                                            <button type="button" onClick={save} disabled={busy} title="Save"
                                                className="p-1 text-emerald-400 hover:text-emerald-300 disabled:opacity-40"><Check size={15} /></button>
                                            <button type="button" onClick={() => setEditing(null)} disabled={busy} title="Cancel"
                                                className="p-1 text-slate-500 hover:text-white disabled:opacity-40"><X size={15} /></button>
                                        </>
                                    ) : (
                                        <button type="button" onClick={() => startEdit(unit)} disabled={busy || Boolean(plan)}
                                            className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs text-slate-400 hover:text-white hover:bg-white/5 disabled:opacity-40">
                                            <Pencil size={13} /> Rename
                                        </button>
                                    )}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
};

export default RenameStorageUnits;
