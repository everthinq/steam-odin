import { useState, useEffect, useCallback, useMemo } from 'react';
import { RefreshCw, ShoppingCart, FlaskConical, AlertTriangle } from 'lucide-react';

// Andvari "Buy games": check every account (country, wallet, owned games, regional
// price in US dollars), then buy the planned games with the Steam wallet. Backend:
// /api/huginn/card-deals/buy*. Every purchase only goes through when Steam's final
// price equals the plan to the cent, and only on an empty cart.

const STATUS = {
    buy: { label: 'buy', tone: 'text-emerald-300' },
    owned: { label: 'owned', tone: 'text-slate-500' },
    too_expensive: { label: 'above the maximum', tone: 'text-amber-300' },
    balance: { label: 'balance too low', tone: 'text-amber-300' },
    currency: { label: 'other currency', tone: 'text-amber-300' },
    unavailable: { label: 'not for sale', tone: 'text-red-400' },
    bought: { label: 'bought', tone: 'text-emerald-400' },
    check: { label: 'payment unclear: check the account', tone: 'text-red-400' },
};
const APPS_STORAGE_KEY = 'andvari.buy.apps';

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
const savedApps = () => {
    try { return window.localStorage.getItem(APPS_STORAGE_KEY) || ''; } catch { return ''; }
};

const BuyPanel = () => {
    const [state, setState] = useState(null);
    const [apps, setApps] = useState(savedApps);
    const [maxUsd, setMaxUsd] = useState('0.45');
    const [picks, setPicks] = useState({ planAt: null, bySteamid: {} });   // edits of one plan's selection
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);
    const [loadedAt, setLoadedAt] = useState(0);      // when the status was read (plan age without Date.now in render)

    const load = useCallback(() => fetch('/api/huginn/card-deals/buy')
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

    // A new plan starts with every game it marks "buy" selected; ticking edits that plan's copy.
    const defaultSelection = useMemo(() => {
        const next = {};
        (plan?.accounts || []).forEach((row) => {
            const buy = row.games.filter((g) => g.status === 'buy').map((g) => g.app_id);
            if (buy.length) next[row.steamid] = buy;
        });
        return next;
    }, [plan]);
    const selection = plan && picks.planAt === plan.created_at ? picks.bySteamid : defaultSelection;

    const gameColumns = useMemo(() => {
        const seen = new Map();
        (plan?.accounts || []).forEach((row) => row.games.forEach((g) => { if (!seen.has(g.app_id)) seen.set(g.app_id, g.name); }));
        return (plan?.app_ids || []).map((id) => ({ app_id: id, name: seen.get(id) || String(id) }));
    }, [plan]);

    const chosen = useMemo(() => {
        const rows = [];
        (plan?.accounts || []).forEach((row) => {
            const ids = selection[row.steamid] || [];
            const games = row.games.filter((g) => ids.includes(g.app_id) && g.status === 'buy');
            if (games.length) rows.push({ row, games });
        });
        return rows;
    }, [plan, selection]);
    const chosenCopies = chosen.reduce((n, c) => n + c.games.length, 0);
    const chosenUsd = chosen.reduce((sum, c) => sum + c.games.reduce((s, g) => s + (g.usd || 0), 0), 0);

    const toggle = (steamid, appId) => {
        const ids = new Set(selection[steamid] || []);
        if (ids.has(appId)) ids.delete(appId); else ids.add(appId);
        setPicks({ planAt: plan.created_at, bySteamid: { ...selection, [steamid]: [...ids] } });
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
    const check = () => {
        try { window.localStorage.setItem(APPS_STORAGE_KEY, apps); } catch { /* storage blocked */ }
        post('/api/huginn/card-deals/buy/plan', { apps, max_usd_per_game: Number(maxUsd) });
    };
    const run = (dryRun) => {
        const selectionBody = chosen.map((c) => ({ steamid: c.row.steamid, app_ids: c.games.map((g) => g.app_id) }));
        if (!dryRun && !window.confirm(`Buy ${chosenCopies} games on ${chosen.length} accounts for about $${chosenUsd.toFixed(2)} from their Steam wallets?`)) return;
        post('/api/huginn/card-deals/buy/run', { selection: selectionBody, dry_run: dryRun, plan_created_at: plan.created_at });
    };

    const stats = state?.statistics;
    return (
        <div className="flex-1 overflow-auto custom-scrollbar space-y-3 text-sm">
            <div className="rounded-xl border border-white/10 bg-odin-blue/30 p-4 flex flex-wrap items-end gap-3">
                <label className="flex-1 min-w-[18rem] text-slate-300">
                    Games (store links or app ids)
                    <textarea rows={2} value={apps} onChange={(e) => setApps(e.target.value)}
                        placeholder="https://store.steampowered.com/app/745740/Reflex/"
                        className="mt-1 w-full bg-black/30 border border-white/10 rounded px-2 py-1 font-mono text-xs" />
                </label>
                <label className="text-slate-300">
                    Maximum per game (US dollars, converted)
                    <input type="number" min="0.01" step="0.01" value={maxUsd} onChange={(e) => setMaxUsd(e.target.value)}
                        className="mt-1 block w-28 bg-black/30 border border-white/10 rounded px-2 py-1" />
                </label>
                <button type="button" onClick={check} disabled={busy || running || !apps.trim()}
                    className="flex items-center gap-2 px-3 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-medium disabled:opacity-50">
                    <RefreshCw size={14} className={running && state?.job?.kind === 'plan' ? 'animate-spin' : ''} /> Check accounts
                </button>
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
                        <span>Checked {ago(plan.created_at)} · maximum ${plan.max_usd_per_game} per game · planned {plan.totals.copies} copies on {plan.totals.accounts} accounts ≈ ${plan.totals.usd.toFixed(2)}{plan.totals.profit ? ` · Andvari profit ≈ $${plan.totals.profit.toFixed(2)}` : ''}</span>
                        {!planFresh && <span className="text-amber-300">Older than 30 minutes: check again before buying</span>}
                        <div className="ml-auto flex gap-2">
                            <button type="button" onClick={() => run(true)} disabled={busy || running || !planFresh || !chosenCopies}
                                title="Goes through the whole checkout on the selected accounts and cancels before paying"
                                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-white/15 text-slate-200 hover:bg-white/5 disabled:opacity-50">
                                <FlaskConical size={13} /> Dry run
                            </button>
                            <button type="button" onClick={() => run(false)} disabled={busy || running || !planFresh || !chosenCopies}
                                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium disabled:opacity-50">
                                <ShoppingCart size={13} /> Buy {chosenCopies} on {chosen.length} accounts (≈ ${chosenUsd.toFixed(2)})
                            </button>
                        </div>
                    </div>
                    <div className="overflow-auto custom-scrollbar max-h-[28rem]">
                        <table className="w-full text-xs">
                            <thead className="text-slate-500 text-left sticky top-0 bg-odin-dark">
                                <tr>
                                    <th className="py-1">Account</th><th>Country</th><th className="text-right">Wallet</th>
                                    {gameColumns.map((g) => <th key={g.app_id} className="pl-4">{g.name}</th>)}
                                    <th className="text-right pl-4">Planned</th>
                                </tr>
                            </thead>
                            <tbody>
                                {plan.accounts.map((row) => (
                                    <tr key={row.steamid} className="border-t border-white/5 align-top">
                                        <td className="py-1 text-slate-200">{row.account_name}</td>
                                        <td className="text-slate-400">{row.country || '??'}</td>
                                        <td className="text-right tabular-nums text-slate-300">{row.error ? '—' : minor(row.balance, row.currency)}</td>
                                        {row.error ? (
                                            <td colSpan={gameColumns.length + 1} className="pl-4 text-red-400">{row.error}</td>
                                        ) : (
                                            <>
                                                {gameColumns.map((column) => {
                                                    const g = row.games.find((x) => x.app_id === column.app_id);
                                                    if (!g) return <td key={column.app_id} />;
                                                    const status = STATUS[g.status] || { label: g.status, tone: 'text-slate-400' };
                                                    return (
                                                        <td key={column.app_id} className="pl-4">
                                                            <label className={`flex items-center gap-1.5 ${g.status === 'buy' ? 'cursor-pointer' : ''}`}>
                                                                {g.status === 'buy' && (
                                                                    <input type="checkbox" className="accent-emerald-500"
                                                                        checked={(selection[row.steamid] || []).includes(g.app_id)}
                                                                        onChange={() => toggle(row.steamid, g.app_id)} />
                                                                )}
                                                                <span className="tabular-nums text-slate-300">{minor(g.price, g.currency)}</span>
                                                                {g.currency !== 'USD' && g.usd != null && <span className="text-slate-500">(${g.usd.toFixed(2)})</span>}
                                                                <span className={status.tone}>{status.label}</span>
                                                                {g.profit != null && g.status !== 'owned' && <span className="text-emerald-400/70">{g.profit >= 0 ? '+' : ''}${g.profit.toFixed(2)}</span>}
                                                            </label>
                                                        </td>
                                                    );
                                                })}
                                                <td className="text-right tabular-nums pl-4 text-emerald-300">{row.planned_total ? minor(row.planned_total, row.currency) : '—'}</td>
                                            </>
                                        )}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {stats && stats.purchases > 0 && (
                <div className="grid gap-3 md:grid-cols-2">
                    <div className="rounded-xl border border-white/10 bg-black/20 p-3">
                        <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Bought per account ({stats.copies} games in {stats.purchases} purchases)</p>
                        <table className="w-full text-xs"><tbody>
                            {Object.entries(stats.accounts).map(([name, entry]) => (
                                <tr key={name} className="border-t border-white/5">
                                    <td className="py-0.5 text-slate-300">{name}</td>
                                    <td className="text-slate-400">{entry.games.join(', ')}</td>
                                    <td className="text-right tabular-nums text-amber-200">{amounts(entry.spent)}</td>
                                </tr>
                            ))}
                        </tbody></table>
                    </div>
                    <div className="rounded-xl border border-white/10 bg-black/20 p-3">
                        <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Bought per game</p>
                        <table className="w-full text-xs"><tbody>
                            {Object.entries(stats.games).map(([name, entry]) => (
                                <tr key={name} className="border-t border-white/5">
                                    <td className="py-0.5 text-slate-300">{name}</td>
                                    <td className="text-right tabular-nums">{entry.copies} copies</td>
                                    <td className="text-right tabular-nums text-amber-200 pl-3">{amounts(entry.spent)}</td>
                                </tr>
                            ))}
                        </tbody></table>
                    </div>
                </div>
            )}

            {state?.history?.length > 0 && (
                <div className="rounded-xl border border-white/10 bg-black/20 p-3">
                    <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Purchase log</p>
                    <div className="max-h-64 overflow-auto custom-scrollbar">
                        <table className="w-full text-xs">
                            <thead className="text-slate-500 text-left"><tr><th>When</th><th>Account</th><th>Games</th><th className="text-right">Planned</th><th className="text-right">Charged</th><th className="pl-3">Result</th></tr></thead>
                            <tbody>
                                {state.history.map((h) => (
                                    <tr key={`${h.steamid}-${h.at}`} className="border-t border-white/5">
                                        <td className="py-0.5 text-slate-400">{ago(h.at)}</td>
                                        <td className="text-slate-300">{h.account_name}</td>
                                        <td className="text-slate-200">{h.games.map((g) => g.name).join(', ')}</td>
                                        <td className="text-right tabular-nums">{minor(h.expected, h.currency)}</td>
                                        <td className="text-right tabular-nums">{minor(h.charged, h.currency)}</td>
                                        <td className={`pl-3 ${h.state === 'in progress' ? 'text-amber-300' : h.dry_run && h.ok ? 'text-sky-300' : h.paid ? 'text-emerald-300' : 'text-red-400'}`}>
                                            {h.state === 'in progress' ? 'In progress…'
                                                : h.dry_run && h.ok ? 'Dry run OK (nothing paid)'
                                                    : h.paid ? (h.error ? `Bought · ${h.error.replace(/^bought; /, '')}` : 'Bought')
                                                        : h.error}
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

export default BuyPanel;
