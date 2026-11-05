import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { RefreshCw, ShoppingCart, FlaskConical, AlertTriangle } from 'lucide-react';
import { failureMessage, readJson, readJsonOrThrow } from '../../utils/readJson';
import { followJob, postCommand } from '../../utils/moveCommands';

// Andvari "Buy games": check every account (country, wallet, owned games, regional
// price in US dollars), then buy the planned games with the Steam wallet. The backend
// decides (store_purchase_service.py): the ticked games become a purchase plan
// (/api/huginn/card-deals/buy/purchase-plan), its dry run runs as a job, and only the
// accounts whose dry run of that same purchase plan passed can be bought, once. Every
// purchase only goes through when Steam's final price equals the plan to the cent.

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
const PURCHASES = '/api/huginn/card-deals/buy/purchase-plans';
const JOB_LOST = 'The backend restarted during the purchase, so its result is unknown. Look at the purchase log (and the accounts\' purchase history) before trying again.';
const lostTrack = (reason) => `Lost track of the purchase (${reason}). It keeps running in the backend: the purchase log shows the outcome.`;

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
// [{steamid, app_ids}] (what the backend answers and takes) as {steamid: [app ids]} for the ticks.
const bySteamid = (selection) => Object.fromEntries((selection || []).map((item) => [item.steamid, item.app_ids]));

// One account's dry run outcome in the purchase plan (empty when the account is not in it).
const DryRunCell = ({ line }) => {
    if (!line) return null;
    if (line.purchase) {
        if (line.purchase.paid) return <span className="text-emerald-300">bought</span>;
        if (line.purchase.may_be_paid) return <span className="text-red-400">MAY BE PAID: check the account</span>;
        return <span className="text-red-400">not bought: {line.purchase.reason}</span>;
    }
    if (!line.dry_run) return <span className="text-slate-500">not run</span>;
    return line.dry_run.passed
        ? <span className="text-sky-300">passed ({minor(line.dry_run.charged, line.currency)})</span>
        : <span className="text-red-400">{line.dry_run.reason}</span>;
};

const BuyPanel = () => {
    const [state, setState] = useState(null);
    const [apps, setApps] = useState(savedApps);
    const [maxUsd, setMaxUsd] = useState('0.45');
    const [picks, setPicks] = useState({ planAt: null, bySteamid: {} });   // edits of one plan's selection
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);
    const [emptyCart, setEmptyCart] = useState(true);  // remove what is already in a cart first (logged)
    const [discarded, setDiscarded] = useState(null);  // purchase plan id whose ticks were changed since
    const sending = useRef(false);                     // one command at a time, even on a double click
    const mounted = useRef(true);
    useEffect(() => () => { mounted.current = false; }, []);

    const load = useCallback(() => fetch('/api/huginn/card-deals/buy')
        .then(readJsonOrThrow)
        .then(setState)
        .catch((error) => setError(failureMessage(error))), []);
    const running = state?.job?.running;
    useEffect(() => {
        load();
        const timer = setInterval(load, running ? 2500 : 30000);
        return () => clearInterval(timer);
    }, [load, running]);

    const plan = state?.plan;
    const planFresh = !!plan?.fresh;
    // The purchase plan the backend made from this plan, unless the ticks changed since.
    const purchasePlan = state?.purchase_plan && plan && state.purchase_plan.plan_created_at === plan.created_at
        && state.purchase_plan.purchase_plan_id !== discarded ? state.purchase_plan : null;
    const purchaseLines = useMemo(
        () => Object.fromEntries((purchasePlan?.lines || []).map((line) => [line.steamid, line])), [purchasePlan]);

    // The ticks: this page's edits of this plan, else the purchase plan's games, else the backend's default.
    const selection = useMemo(() => {
        if (plan && picks.planAt === plan.created_at) return picks.bySteamid;
        if (purchasePlan) return bySteamid(purchasePlan.lines.map((line) => ({ steamid: line.steamid, app_ids: line.games.map((g) => g.app_id) })));
        return bySteamid(plan?.default_selection);
    }, [plan, picks, purchasePlan]);

    const gameColumns = useMemo(() => {
        const seen = new Map();
        (plan?.accounts || []).forEach((row) => row.games.forEach((g) => { if (!seen.has(g.app_id)) seen.set(g.app_id, g.name); }));
        return (plan?.app_ids || []).map((id) => ({ app_id: id, name: seen.get(id) || String(id) }));
    }, [plan]);

    const anyTicked = Object.values(selection).some((ids) => ids?.length);
    const toggle = (steamid, appId) => {
        const ids = new Set(selection[steamid] || []);
        if (ids.has(appId)) ids.delete(appId); else ids.add(appId);
        setPicks({ planAt: plan.created_at, bySteamid: { ...selection, [steamid]: [...ids] } });
        if (purchasePlan) setDiscarded(purchasePlan.purchase_plan_id);
    };
    const changeEmptyCart = (value) => {
        setEmptyCart(value);
        if (purchasePlan) setDiscarded(purchasePlan.purchase_plan_id);
    };

    // One command at a time: the ref stops a double click before React re-renders the disabled button.
    const send = async (work) => {
        if (sending.current) return;
        sending.current = true;
        setBusy(true);
        setError(null);
        try {
            await work();
        } catch (error) {
            if (!error.cancelled) setError(failureMessage(error));
        } finally {
            sending.current = false;
            if (mounted.current) setBusy(false);
            load();
        }
    };
    const follow = (job) => followJob(job.id, {
        onProgress: () => load(), isActive: () => mounted.current, lostMessage: JOB_LOST, lostTrackMessage: lostTrack,
    });
    const check = () => send(async () => {
        try { window.localStorage.setItem(APPS_STORAGE_KEY, apps); } catch { /* storage blocked */ }
        const answer = await fetch('/api/huginn/card-deals/buy/plan', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ apps, max_usd_per_game: Number(maxUsd) }),
        }).then(readJson);
        if (answer.error) throw new Error(answer.error);
    });
    // Dry run: the ticked games become a new purchase plan, then its dry run runs (nothing is paid).
    const dryRun = () => send(async () => {
        const selectionBody = Object.entries(selection)
            .filter(([, ids]) => ids?.length)
            .map(([steamid, ids]) => ({ steamid, app_ids: ids }));
        const made = await postCommand('/api/huginn/card-deals/buy/purchase-plan', {
            // what the box shows: the purchase plan's own choice until the box is changed
            plan_created_at: plan.created_at, selection: selectionBody,
            empty_cart: purchasePlan ? purchasePlan.empty_cart : emptyCart,
        });
        setDiscarded(null);
        setPicks({ planAt: plan.created_at, bySteamid: selection });
        const job = await postCommand(`${PURCHASES}/${made.purchase_plan_id}/execute`, { dry_run: true });
        await follow(job);
    });
    // Buy: only what passed this purchase plan's dry run; the backend refuses anything else.
    const buy = () => {
        const totals = purchasePlan.buy;
        const question = `Buy ${totals.copies} games on ${totals.accounts} accounts for about $${totals.total_usd.toFixed(2)} from their Steam wallets? Only the accounts whose dry run passed are bought.`;
        if (sending.current || !window.confirm(question)) return;
        send(async () => {
            const job = await postCommand(`${PURCHASES}/${purchasePlan.purchase_plan_id}/execute`, { dry_run: false });
            const finished = await follow(job);
            if (finished.error) throw new Error(`The purchase stopped: ${finished.error}`);
        });
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
                        {planFresh
                            ? <span>buying possible for {Math.ceil(plan.expires_in_seconds / 60)} more min</span>
                            : <span className="text-amber-300">Older than {Math.round((state.plan_time_to_live_seconds || 0) / 60)} minutes: check again before buying</span>}
                        <div className="ml-auto flex items-center gap-2">
                            <label className="flex items-center gap-1.5 text-slate-300 cursor-pointer" title="Anything already in an account's cart is removed before buying and listed in the purchase log; off = such an account is skipped. The dry run empties the cart too (nothing is paid); off = an account with a full cart fails it.">
                                <input type="checkbox" className="accent-amber-500" checked={purchasePlan ? purchasePlan.empty_cart : emptyCart} onChange={(e) => changeEmptyCart(e.target.checked)} />
                                Empty the cart first
                            </label>
                            <button type="button" onClick={dryRun} disabled={busy || running || !planFresh || !anyTicked}
                                title="Makes a purchase plan of the ticked games and goes through the whole checkout on those accounts, cancelling before paying. Buying needs a passed dry run."
                                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-white/15 text-slate-200 hover:bg-white/5 disabled:opacity-50">
                                <FlaskConical size={13} /> Dry run
                            </button>
                            <button type="button" onClick={buy} disabled={busy || running || !purchasePlan?.executable}
                                title={purchasePlan?.executable ? 'Buys only the accounts whose dry run passed' : 'Run a dry run of the ticked games first: only what passed it can be bought'}
                                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium disabled:opacity-50">
                                <ShoppingCart size={13} />
                                {purchasePlan?.executable
                                    ? `Buy ${purchasePlan.buy.copies} on ${purchasePlan.buy.accounts} accounts (≈ $${purchasePlan.buy.total_usd.toFixed(2)})`
                                    : 'Buy (after a passed dry run)'}
                            </button>
                        </div>
                    </div>
                    {purchasePlan && (
                        <div className="mb-2 text-xs text-slate-400 space-y-0.5">
                            <p>
                                Purchase plan: {purchasePlan.copies} copies on {purchasePlan.accounts} accounts ≈ ${purchasePlan.total_usd.toFixed(2)}
                                {' · '}dry run {purchasePlan.dry_run.state === 'done'
                                    ? <span className={purchasePlan.dry_run.passed ? 'text-sky-300' : 'text-red-400'}>{purchasePlan.dry_run.passed} passed, {purchasePlan.dry_run.failed} failed</span>
                                    : purchasePlan.dry_run.state}
                                {purchasePlan.dry_run.error && <span className="text-red-400"> ({purchasePlan.dry_run.error})</span>}
                                {purchasePlan.job_id && <span className="text-emerald-300"> · bought: see the purchase log</span>}
                            </p>
                            {purchasePlan.refused.length > 0 && (
                                <p className="text-amber-300">Not in the purchase plan: {purchasePlan.refused.map((item) => `${item.account_name || item.steamid} (${item.reason})`).join('; ')}</p>
                            )}
                        </div>
                    )}
                    <div className="overflow-auto custom-scrollbar max-h-[28rem]">
                        <table className="w-full text-xs">
                            <thead className="text-slate-500 text-left sticky top-0 bg-odin-dark">
                                <tr>
                                    <th className="py-1">Account</th><th>Country</th><th className="text-right">Wallet</th>
                                    {gameColumns.map((g) => <th key={g.app_id} className="pl-4">{g.name}</th>)}
                                    <th className="text-right pl-4">Planned</th>
                                    {purchasePlan && <th className="pl-4">Dry run</th>}
                                </tr>
                            </thead>
                            <tbody>
                                {plan.accounts.map((row) => (
                                    <tr key={row.steamid} className="border-t border-white/5 align-top">
                                        <td className="py-1 text-slate-200">{row.account_name}</td>
                                        <td className="text-slate-400">{row.country || '??'}</td>
                                        <td className="text-right tabular-nums text-slate-300">{row.error ? '—' : minor(row.balance, row.currency)}</td>
                                        {row.error ? (
                                            <td colSpan={gameColumns.length + (purchasePlan ? 2 : 1)} className="pl-4 text-red-400">{row.error}</td>
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
                                                {purchasePlan && <td className="pl-4"><DryRunCell line={purchaseLines[row.steamid]} /></td>}
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
                                        <td className="text-slate-200">
                                            {h.games.map((g) => g.name).join(', ')}
                                            {h.removed_from_cart?.length > 0 && <span className="block text-amber-300/80">removed from the cart first: {h.removed_from_cart.join(', ')}</span>}
                                        </td>
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
