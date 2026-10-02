import { useState, useEffect, useCallback, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { RefreshCw, ShoppingCart, FlaskConical, AlertTriangle, Archive, LayoutDashboard } from 'lucide-react';

// Ratatoskr "Storage shop": buy Counter-Strike 2 Storage Units (1,000 items each) on many
// accounts from their Steam wallets, through the game's own store. Backend:
// /api/ratatoskr/storage-shop*. Each purchase logs the account into Ratatoskr (pausing its
// ASF farming), checks the game store's price against the plan, and approves the
// transaction only when Steam's approval page names it and shows the planned total.

const STATUS = {
    buy: { label: 'can buy', tone: 'text-emerald-300' },
    balance: { label: 'balance too low', tone: 'text-amber-300' },
    currency: { label: 'wallet currency not sold in the game store', tone: 'text-amber-300' },
    country: { label: 'store country unknown (refresh Andvari accounts)', tone: 'text-amber-300' },
    price: { label: 'price unknown or above $2.50', tone: 'text-amber-300' },
    wallet: { label: 'wallet not readable', tone: 'text-red-400' },
    bought: { label: 'bought', tone: 'text-emerald-400' },
    check: { label: 'payment unclear: check the account', tone: 'text-red-400' },
};

const minor = (value, currency) => (value === null || value === undefined ? '—'
    : currency === 'USD' ? `$${(value / 100).toFixed(2)}` : `${(value / 100).toFixed(2)} ${currency || ''}`);
const amounts = (byCurrency) => Object.entries(byCurrency || {}).map(([c, v]) => minor(v, c)).join(' + ') || '—';
const ago = (epochSeconds) => {
    if (!epochSeconds) return 'never';
    const minutes = Math.round((Date.now() / 1000 - epochSeconds) / 60);
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.round(minutes / 60);
    return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} days ago`;
};

const StorageShop = () => {
    const [state, setState] = useState(null);
    const [picks, setPicks] = useState({ planAt: null, bySteamid: {} });   // {steamid: quantity} for one plan
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);
    const [loadedAt, setLoadedAt] = useState(0);

    const load = useCallback(() => fetch('/api/ratatoskr/storage-shop')
        .then((r) => r.json())
        .then((d) => { setState(d); setLoadedAt(Date.now()); })
        .catch(() => setError('Could not reach the backend.')), []);
    const running = state?.job?.running;
    useEffect(() => {
        load();
        const timer = setInterval(load, running ? 2500 : 30000);
        return () => clearInterval(timer);
    }, [load, running]);

    const plan = state?.plan;
    const planAge = plan ? loadedAt / 1000 - plan.created_at : null;
    const planFresh = plan && planAge < (state?.plan_time_to_live_seconds || 1800);
    const maxPerAccount = state?.max_quantity_per_account || 20;
    const quantities = useMemo(() => (plan && picks.planAt === plan.created_at ? picks.bySteamid : {}), [plan, picks]);

    const chosen = useMemo(() => (plan?.accounts || [])
        .filter((row) => row.status === 'buy' && (quantities[row.steamid] || 0) > 0)
        .map((row) => ({ row, quantity: Math.min(quantities[row.steamid], row.affordable, maxPerAccount) })),
    [plan, quantities, maxPerAccount]);
    const chosenUnits = chosen.reduce((n, c) => n + c.quantity, 0);
    const chosenUsd = chosen.reduce((sum, c) => sum + c.quantity * (c.row.usd_per_unit || 0), 0);

    const setQuantity = (steamid, value) => {
        const quantity = Math.max(0, Math.floor(Number(value) || 0));
        setPicks({ planAt: plan.created_at, bySteamid: { ...quantities, [steamid]: quantity } });
    };
    const fillAll = (perAccount) => {
        const next = {};
        (plan?.accounts || []).forEach((row) => {
            if (row.status === 'buy') next[row.steamid] = Math.min(perAccount, row.affordable, maxPerAccount);
        });
        setPicks({ planAt: plan.created_at, bySteamid: next });
    };

    const post = (path, body) => {
        setBusy(true);
        setError(null);
        return fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
            .then((r) => r.json())
            .then((d) => { if (d.error) setError(d.error); })
            .catch(() => setError('Could not reach the backend.'))
            .finally(() => { setBusy(false); load(); });
    };
    const run = (dryRun) => {
        const selection = chosen.map((c) => ({ steamid: c.row.steamid, quantity: c.quantity }));
        if (!dryRun && !window.confirm(`Buy ${chosenUnits} Storage Units on ${chosen.length} accounts for about $${chosenUsd.toFixed(2)} from their Steam wallets?`)) return;
        post('/api/ratatoskr/storage-shop/run', { selection, dry_run: dryRun, plan_created_at: plan.created_at });
    };

    const stats = state?.statistics;
    const sheet = state?.price_sheet;
    return (
        <div className="min-h-screen p-6 md:p-8 space-y-4 text-sm">
            <div className="flex flex-wrap items-center gap-3">
                <div className="p-2 bg-amber-900/30 rounded-lg border border-amber-600/30"><Archive size={20} className="text-amber-500" /></div>
                <div>
                    <h1 className="text-2xl font-bold text-amber-100 font-serif">Storage shop</h1>
                    <p className="text-xs text-slate-500">Ratatoskr · buy Counter-Strike 2 Storage Units ({state?.storage_unit_capacity || 1000} items each) from each account&apos;s Steam wallet</p>
                </div>
                <Link to="/" className="ml-auto flex items-center gap-2 px-3 py-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/5">
                    <LayoutDashboard size={14} /> Dashboard
                </Link>
            </div>

            <div className="rounded-xl border border-white/10 bg-odin-blue/30 p-4 flex flex-wrap items-center gap-3">
                <button type="button" onClick={() => post('/api/ratatoskr/storage-shop/plan', {})} disabled={busy || running}
                    className="flex items-center gap-2 px-3 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-medium disabled:opacity-50">
                    <RefreshCw size={14} className={running && state?.job?.kind === 'plan' ? 'animate-spin' : ''} /> Check accounts
                </button>
                <p className="text-xs text-slate-400 flex-1 min-w-[16rem]">
                    Reads every wallet and the game store&apos;s Storage Unit price
                    {sheet?.prices ? ` (price sheet read ${ago(sheet.fetched_at)}: ${['USD', 'EUR', 'NOK'].filter((c) => sheet.prices[c] != null).map((c) => minor(sheet.prices[c], c)).join(', ')} …)` : ' (the first check logs one account into Ratatoskr to read it)'}.
                    Buying logs each account into Ratatoskr for the purchase (its ASF farming pauses) and out again.
                </p>
            </div>

            {error && <p className="text-red-400 flex items-center gap-2"><AlertTriangle size={14} /> {error}</p>}
            {running && (
                <p className="text-amber-200 flex items-center gap-2">
                    <RefreshCw size={13} className="animate-spin" /> {state.job.phase} <span className="tabular-nums text-amber-200/60">{state.job.done}/{state.job.total}</span>
                </p>
            )}
            {!running && state?.job?.error && <p className="text-red-400">Last {state.job.kind} failed: {state.job.error}</p>}

            {plan && (
                <div className="rounded-xl border border-white/10 bg-black/20 p-3">
                    <div className="flex flex-wrap items-center gap-3 mb-2 text-xs text-slate-400">
                        <span>Checked {ago(plan.created_at)}</span>
                        {!planFresh && <span className="text-amber-300">Older than 30 minutes: check again before buying</span>}
                        <span className="flex items-center gap-1">Fill every account:
                            {[1, 2, 5].map((n) => (
                                <button key={n} type="button" onClick={() => fillAll(n)} className="px-2 py-0.5 rounded border border-white/15 hover:bg-white/5">{n}</button>
                            ))}
                            <button type="button" onClick={() => fillAll(0)} className="px-2 py-0.5 rounded border border-white/15 hover:bg-white/5">none</button>
                        </span>
                        <div className="ml-auto flex items-center gap-2">
                            <button type="button" onClick={() => run(true)} disabled={busy || running || !planFresh || !chosenUnits}
                                title="Opens the transaction, reads Steam's approval page, and cancels: nothing is paid"
                                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-white/15 text-slate-200 hover:bg-white/5 disabled:opacity-50">
                                <FlaskConical size={13} /> Dry run
                            </button>
                            <button type="button" onClick={() => run(false)} disabled={busy || running || !planFresh || !chosenUnits}
                                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium disabled:opacity-50">
                                <ShoppingCart size={13} /> Buy {chosenUnits} on {chosen.length} accounts (≈ ${chosenUsd.toFixed(2)})
                            </button>
                        </div>
                    </div>
                    <div className="overflow-auto custom-scrollbar max-h-[32rem]">
                        <table className="w-full text-xs">
                            <thead className="text-slate-500 text-left sticky top-0 bg-odin-dark">
                                <tr>
                                    <th className="py-1">Account</th><th>Country</th><th className="text-right">Wallet</th>
                                    <th className="text-right pl-4">Price each</th><th className="text-right pl-4">Affordable</th>
                                    <th className="pl-4">Status</th><th className="pl-4">Buy</th><th className="text-right pl-4">Total</th>
                                </tr>
                            </thead>
                            <tbody>
                                {plan.accounts.map((row) => {
                                    const status = STATUS[row.status] || { label: row.status, tone: 'text-slate-400' };
                                    const quantity = quantities[row.steamid] || 0;
                                    return (
                                        <tr key={row.steamid} className="border-t border-white/5">
                                            <td className="py-1 text-slate-200">{row.account_name}</td>
                                            <td className="text-slate-400">{row.country || '??'}</td>
                                            <td className="text-right tabular-nums text-slate-300">{row.error ? '—' : minor(row.balance, row.currency)}</td>
                                            <td className="text-right tabular-nums pl-4 text-slate-300">
                                                {minor(row.unit_price, row.currency)}
                                                {row.currency !== 'USD' && row.usd_per_unit != null && <span className="text-slate-500"> (${row.usd_per_unit.toFixed(2)})</span>}
                                            </td>
                                            <td className="text-right tabular-nums pl-4">{row.status === 'buy' ? row.affordable : '—'}</td>
                                            <td className={`pl-4 ${status.tone}`} title={row.error || ''}>{row.error || status.label}</td>
                                            <td className="pl-4">
                                                {row.status === 'buy' && (
                                                    <input type="number" min="0" max={Math.min(row.affordable, maxPerAccount)} value={quantity}
                                                        onChange={(e) => setQuantity(row.steamid, e.target.value)}
                                                        className="w-16 bg-black/30 border border-white/10 rounded px-2 py-0.5 tabular-nums" />
                                                )}
                                            </td>
                                            <td className="text-right tabular-nums pl-4 text-emerald-300">
                                                {quantity > 0 && row.status === 'buy' ? minor(Math.min(quantity, row.affordable, maxPerAccount) * row.unit_price, row.currency) : '—'}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {stats && stats.units > 0 && (
                <div className="rounded-xl border border-white/10 bg-black/20 p-3">
                    <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Bought per account ({stats.units} Storage Units · {amounts(stats.spent)})</p>
                    <table className="w-full text-xs"><tbody>
                        {Object.entries(stats.accounts).map(([name, entry]) => (
                            <tr key={name} className="border-t border-white/5">
                                <td className="py-0.5 text-slate-300">{name}</td>
                                <td className="text-right tabular-nums">{entry.units} Storage Units</td>
                                <td className="text-right tabular-nums text-amber-200 pl-3">{amounts(entry.spent)}</td>
                            </tr>
                        ))}
                    </tbody></table>
                </div>
            )}

            {state?.history?.length > 0 && (
                <div className="rounded-xl border border-white/10 bg-black/20 p-3">
                    <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Purchase log</p>
                    <div className="max-h-64 overflow-auto custom-scrollbar">
                        <table className="w-full text-xs">
                            <thead className="text-slate-500 text-left"><tr><th>When</th><th>Account</th><th className="text-right">Storage Units</th><th className="text-right">Total</th><th className="text-right">Count before → after</th><th className="pl-3">Result</th></tr></thead>
                            <tbody>
                                {state.history.map((h) => (
                                    <tr key={`${h.steamid}-${h.at}`} className="border-t border-white/5">
                                        <td className="py-0.5 text-slate-400">{ago(h.at)}</td>
                                        <td className="text-slate-300">{h.account_name}</td>
                                        <td className="text-right tabular-nums">{h.quantity}</td>
                                        <td className="text-right tabular-nums">{minor(h.expected, h.currency)}</td>
                                        <td className="text-right tabular-nums text-slate-400">{h.storage_units_before ?? '—'} → {h.storage_units_after ?? '—'}</td>
                                        <td className={`pl-3 ${h.state === 'in progress' ? 'text-amber-300' : h.dry_run && h.ok ? 'text-sky-300' : h.paid ? 'text-emerald-300' : 'text-red-400'}`}>
                                            {h.state === 'in progress' ? 'In progress…'
                                                : h.dry_run && h.ok ? 'Dry run OK (nothing paid)'
                                                    : h.paid ? (h.error ? `Bought · ${h.error.replace(/^paid; /, '')}` : 'Bought')
                                                        : h.error}
                                            {h.payment_attempted && !h.paid && h.state === 'done' && (
                                                <button type="button" disabled={busy || running}
                                                    onClick={() => post('/api/ratatoskr/storage-shop/deliver-again', { at: h.at })}
                                                    title="Asks the game store to deliver this transaction again; it only delivers an approved one, so nothing is paid twice"
                                                    className="ml-2 px-2 py-0.5 rounded border border-white/15 text-slate-200 hover:bg-white/5 disabled:opacity-50">
                                                    Deliver again
                                                </button>
                                            )}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    );
};

export default StorageShop;
