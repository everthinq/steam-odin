import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import {
    Archive, LayoutDashboard, Search, RefreshCw, Minus, Plus, ShoppingCart, FlaskConical, X,
    CheckCircle2, AlertTriangle, Loader2, Circle, Wallet, History,
} from 'lucide-react';
import { useColumnSort, sortRows } from '../../components/draupnir/columnSort';
import SortHeader from '../../components/ratatoskr/SortHeader';

// Ratatoskr "Buy Storage Units": pick any account from the dashboard, choose how many Storage
// Units (1,000 items each), review the order with freshly read wallets, pay. Backend:
// /api/ratatoskr/storage-shop*. The account list shows what is last known (nothing is read
// from Steam until "Review & buy" or "Check wallets"); every purchase is re-checked
// against Steam's own approval request before anything is paid.

const MAX_PER_ACCOUNT = 20;
const STEPS = [
    ['wallet', 'Checking the wallet'],
    ['login', 'Logging in'],
    ['opening', 'Opening the purchase'],
    ['approving', 'Approving'],
    ['delivering', 'Delivering'],
];
const PLAN_PROBLEMS = {
    balance: 'Not enough in the wallet',
    currency: 'Wallet currency not sold in the game store',
    country: 'Store country unknown (refresh the Andvari accounts)',
    price: 'Price unknown or unusual',
    wallet: 'Wallet could not be read',
    bought: 'Already bought in this check',
    check: 'A purchase is unclear: check the account first',
};

// What each sortable column sorts on. Wallets are compared in US dollars (they are in
// different currencies); an unknown value (never checked, currency not sold) sorts last.
const SORT_VALUES = {
    account: (a) => a.account_name,
    wallet: (a) => a.balance_usd,
    storage_units: (a) => a.storage_units,
    can_buy: (a) => (a.sold_in_currency ? a.affordable : null),
};

const money = (minorUnits, currency) => {
    if (minorUnits === null || minorUnits === undefined || !currency) return '—';
    return currency === 'USD' ? `$${(minorUnits / 100).toFixed(2)}` : `${(minorUnits / 100).toFixed(2)} ${currency}`;
};
const ago = (epochSeconds) => {
    if (!epochSeconds) return null;
    const minutes = Math.round((Date.now() / 1000 - epochSeconds) / 60);
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.round(minutes / 60);
    return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} days ago`;
};
const sumByCurrency = (lines) => lines.reduce((totals, line) => {
    if (line.currency && line.total != null) totals[line.currency] = (totals[line.currency] || 0) + line.total;
    return totals;
}, {});
const moneyList = (byCurrency) => Object.entries(byCurrency).map(([c, v]) => money(v, c)).join(' + ') || '—';
const payableLines = (lines) => (lines || []).filter((l) => l.quantity > 0 && l.total != null);

const post = (path, body) => fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
}).then((r) => r.json());

// ---- small parts ---------------------------------------------------------------------------

const Stepper = ({ value, max, onChange, label }) => (
    <div className="inline-flex items-center rounded-lg border border-white/15 bg-black/30" role="group" aria-label={label}>
        <button type="button" onClick={() => onChange(value - 1)} disabled={value <= 1}
            className="p-1.5 text-slate-300 hover:text-white disabled:opacity-30" aria-label="One fewer">
            <Minus size={13} />
        </button>
        <span className="w-7 text-center tabular-nums text-sm text-white" aria-live="polite">{value}</span>
        <button type="button" onClick={() => onChange(value + 1)} disabled={value >= max}
            className="p-1.5 text-slate-300 hover:text-white disabled:opacity-30" aria-label="One more">
            <Plus size={13} />
        </button>
    </div>
);

const Modal = ({ title, onClose, children, closable = true }) => createPortal(
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
        role="dialog" aria-modal="true" aria-label={title}
        onKeyDown={(e) => { if (e.key === 'Escape' && closable) onClose(); }}>
        <div className="w-full max-w-2xl max-h-[90vh] overflow-auto custom-scrollbar rounded-2xl border border-amber-500/30 bg-slate-900 p-5 shadow-2xl">
            <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-bold text-amber-100 font-serif">{title}</h2>
                {closable && (
                    <button type="button" onClick={onClose} autoFocus className="p-1 rounded text-slate-400 hover:text-white hover:bg-white/10" aria-label="Close">
                        <X size={18} />
                    </button>
                )}
            </div>
            {children}
        </div>
    </div>,
    document.body,
);

// The order's lines once the wallets were read: what each account can really buy now.
const reviewLines = (wanted, plan, byId) => {
    const rows = Object.fromEntries((plan.accounts || []).map((r) => [r.steamid, r]));
    return Object.entries(wanted).map(([steamid, asked]) => {
        const row = rows[steamid] || { account_name: byId[steamid]?.account_name || steamid, status: 'wallet' };
        const limit = row.status === 'buy' ? Math.min(row.affordable, MAX_PER_ACCOUNT) : 0;
        const quantity = Math.min(asked, limit);
        return {
            steamid, account_name: row.account_name, asked, quantity, currency: row.currency,
            unit_price: row.unit_price, balance: row.balance, usd_per_unit: row.usd_per_unit,
            total: quantity ? quantity * row.unit_price : null,
            problem: row.status !== 'buy' ? (PLAN_PROBLEMS[row.status] || row.error || row.status)
                : quantity < asked ? `The wallet covers ${quantity}, not ${asked}` : null,
        };
    });
};

// ---- the page ------------------------------------------------------------------------------

const StorageShop = () => {
    const [state, setState] = useState(null);
    const [error, setError] = useState(null);
    const [search, setSearch] = useState('');
    const [filter, setFilter] = useState('all');          // all | buyable | selected
    const [cart, setCart] = useState({});                 // {steamid: quantity}
    const sort = useColumnSort();                         // {sortKey, sortDir, toggle}
    // The order dialog as started here: {stage: 'checking', jobStartedAt, wanted} or
    // {stage: 'running', jobStartedAt, purchaseStartedAt, dryRun, paying}. The job is
    // followed by the server's start time (null until the server answered), never by the
    // browser's clock; review and results follow from that job.
    const [order, setOrder] = useState(null);
    const paying = useRef(false);          // one Pay request at a time, even on a double click
    const loads = useRef(0);               // only the newest status answer is kept

    const load = useCallback(() => {
        const number = ++loads.current;
        return fetch('/api/ratatoskr/storage-shop')
            .then((r) => r.json())
            .then((d) => { if (number === loads.current) setState(d); })
            .catch(() => setError('Could not reach the backend.'));
    }, []);
    const running = Boolean(state?.job?.running);
    useEffect(() => {
        load();
        const timer = setInterval(load, running || order ? 1500 : 30000);
        return () => clearInterval(timer);
    }, [load, running, order]);

    const accounts = useMemo(() => state?.accounts || [], [state]);
    const byId = useMemo(() => Object.fromEntries(accounts.map((a) => [a.steamid, a])), [accounts]);

    // A purchase running with no dialog open here (another tab, or this page reloaded):
    // its progress is shown all the same.
    const shown = useMemo(() => {
        if (order || !state?.job?.running || !['purchase', 'delivery'].includes(state.job.kind)) return order;
        const started = state.job.started_at;
        const paying = (state.history || []).filter((h) => h.at >= started).map((h) => ({
            steamid: h.steamid, account_name: h.account_name, quantity: h.quantity, unit_price: h.unit_price, currency: h.currency,
        }));
        return { stage: 'running', jobStartedAt: started, purchaseStartedAt: started, dryRun: state.job.dry_run, paying, adopted: true };
    }, [order, state]);

    // Where the dialog is, from the backend's job: checking → review (failed, or out of date
    // when another check replaced it); running → results.
    const view = useMemo(() => {
        if (!shown || !state) return null;
        const job = state.job || {};
        if (shown.jobStartedAt == null) return { stage: shown.stage, results: [] };   // the server has not answered yet
        const ours = job.started_at === shown.jobStartedAt;
        if (shown.stage === 'checking') {
            if (!ours) return { stage: 'stale' };
            if (job.running) return { stage: 'checking' };
            const plan = state.plan;
            if (job.error || !plan || plan.created_at < shown.jobStartedAt) {
                return { stage: 'failed', message: job.error || 'The wallets could not be checked.' };
            }
            return { stage: 'review', planCreatedAt: plan.created_at, lines: reviewLines(shown.wanted, plan, byId) };
        }
        const results = (state.history || []).filter((h) => h.at >= shown.purchaseStartedAt
            && shown.paying.some((l) => l.steamid === h.steamid));
        return { stage: ours && job.running ? 'running' : 'results', results, jobError: ours ? job.error : null };
    }, [shown, state, byId]);

    const visible = useMemo(() => {
        const needle = search.trim().toLowerCase();
        const matching = accounts.filter((a) => (!needle || a.account_name.toLowerCase().includes(needle))
            && (filter === 'all' || (filter === 'selected' ? cart[a.steamid] : a.affordable !== 0 && a.sold_in_currency)));
        return sortRows(matching, sort.sortKey, sort.sortDir, SORT_VALUES);
    }, [accounts, search, filter, cart, sort.sortKey, sort.sortDir]);

    const selectable = (a) => a.affordable !== 0 && a.sold_in_currency;
    const maxFor = (a) => (a?.affordable ? Math.min(a.affordable, MAX_PER_ACCOUNT) : MAX_PER_ACCOUNT);
    const toggle = (a) => setCart((current) => {
        const next = { ...current };
        if (next[a.steamid]) delete next[a.steamid]; else next[a.steamid] = 1;
        return next;
    });
    const setQuantity = (steamid, quantity) => setCart((current) => ({
        ...current, [steamid]: Math.max(1, Math.min(quantity, maxFor(byId[steamid]))),
    }));
    const visibleSelectable = visible.filter(selectable);
    const allVisibleSelected = visibleSelectable.length > 0 && visibleSelectable.every((a) => cart[a.steamid]);
    const toggleAllVisible = () => setCart((current) => {
        const next = { ...current };
        visibleSelectable.forEach((a) => {
            if (allVisibleSelected) delete next[a.steamid]; else next[a.steamid] = next[a.steamid] || 1;
        });
        return next;
    });

    const cartLines = Object.entries(cart).filter(([steamid]) => byId[steamid]).map(([steamid, quantity]) => {
        const a = byId[steamid];
        return { steamid, account_name: a.account_name, quantity, currency: a.currency,
                 total: a.unit_price ? a.unit_price * quantity : null,
                 usd: a.usd_per_unit ? a.usd_per_unit * quantity : 1.99 * quantity };
    });
    const cartUnits = cartLines.reduce((n, l) => n + l.quantity, 0);
    const cartUnknown = cartLines.some((l) => l.total == null);

    const checkWallets = () => {
        setError(null);
        post('/api/ratatoskr/storage-shop/plan', {})
            .then((d) => { if (!d.started) setError(d.error); })
            .catch(() => setError('Could not reach the backend.'))
            .finally(load);
    };
    const review = () => {
        setError(null);
        setOrder({ stage: 'checking', jobStartedAt: null, wanted: { ...cart } });
        post('/api/ratatoskr/storage-shop/plan', { steamids: Object.keys(cart) })
            .then((d) => {
                if (d.started) setOrder((o) => (o ? { ...o, jobStartedAt: d.started_at } : o));
                else { setOrder(null); setError(d.error); }
            })
            .catch(() => { setOrder(null); setError('Could not reach the backend.'); })
            .finally(load);
    };
    const pay = (dryRun) => {
        if (paying.current || view?.stage !== 'review') return;
        paying.current = true;
        const lines = payableLines(view.lines);
        const previous = order;
        setOrder({ stage: 'running', jobStartedAt: null, purchaseStartedAt: null, dryRun, paying: lines });
        post('/api/ratatoskr/storage-shop/run', {
            selection: lines.map((l) => ({ steamid: l.steamid, quantity: l.quantity })),
            dry_run: dryRun, plan_created_at: view.planCreatedAt,
        })
            .then((d) => {
                if (d.started) {
                    setOrder((o) => ({ ...o, jobStartedAt: d.started_at, purchaseStartedAt: d.started_at }));
                } else {
                    setOrder(previous);       // refused before starting: back to the review
                    setError(d.error);
                }
            })
            .catch(() => { setOrder(null); setError('Could not reach the backend: look at the purchase log before trying again.'); })
            .finally(() => { paying.current = false; load(); });
    };
    const closeOrder = () => {
        if (view?.stage === 'results' && order && !order.dryRun) {
            // Bought accounts leave the order; anything that failed stays for another try.
            const bought = new Set(view.results.filter((h) => h.paid).map((h) => h.steamid));
            setCart((current) => Object.fromEntries(Object.entries(current).filter(([s]) => !bought.has(s))));
        }
        if (view?.stage === 'failed') setError(view.message);
        setOrder(null);
    };
    const deliverAgain = (at) => post('/api/ratatoskr/storage-shop/deliver-again', { at })
        .then((d) => {
            if (!d.started) setError(d.error);
            else setOrder((o) => (o && o.stage === 'running' ? { ...o, jobStartedAt: d.started_at } : o));   // follow the delivery
        })
        .catch(() => setError('Could not reach the backend.'))
        .finally(load);

    if (!state) {
        return <div className="min-h-screen flex items-center justify-center text-slate-400"><Loader2 className="animate-spin mr-2" size={18} /> Loading Buy Storage Units…</div>;
    }
    const stats = state.statistics || { units: 0, spent: {}, accounts: {} };
    const unclear = (state.history || []).filter((h) => h.payment_attempted && !h.paid && h.state === 'done');
    const checkingAll = running && state.job.kind === 'plan' && !shown;
    // The Store Catalogue's "Read prices again" shares this page's job: nothing can start meanwhile.
    const readingPrices = running && state.job.kind === 'price sheet';

    return (
        <div className="min-h-screen p-4 md:p-8 text-sm">
            <header className="flex flex-wrap items-center gap-3 mb-6">
                <div className="p-2 bg-amber-900/30 rounded-lg border border-amber-600/30"><Archive size={22} className="text-amber-500" /></div>
                <div className="mr-auto rounded-lg">
                    <h1 className="text-2xl font-bold text-amber-100 font-serif">Buy Storage Units</h1>
                    <p className="text-xs text-slate-300 [text-shadow:0_1px_3px_rgb(0_0_0)]">Buy Counter-Strike 2 Storage Units ({state.storage_unit_capacity || 1000} items each) from each account&apos;s Steam wallet</p>
                </div>
                {stats.units > 0 && (
                    <span className="text-xs text-slate-400 rounded-lg border border-white/10 px-3 py-1.5">
                        Bought so far: <span className="text-emerald-300 font-medium">{stats.units}</span> for {moneyList(stats.spent)}
                    </span>
                )}
                <Link to="/" className="flex items-center gap-2 px-3 py-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/5">
                    <LayoutDashboard size={14} /> Dashboard
                </Link>
            </header>

            {error && (
                <div className="mb-4 flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-950/80 px-3 py-2 text-red-300" role="alert">
                    <AlertTriangle size={15} className="mt-0.5 shrink-0" /> <span className="flex-1">{error}</span>
                    <button type="button" onClick={() => setError(null)} aria-label="Dismiss" className="text-red-300/70 hover:text-red-200"><X size={14} /></button>
                </div>
            )}
            {unclear.length > 0 && (
                <div className="mb-4 rounded-lg border border-amber-500/40 bg-amber-950/80 px-3 py-2 text-amber-200" role="status">
                    <p className="font-medium flex items-center gap-2"><AlertTriangle size={15} /> A purchase was approved but not delivered</p>
                    {unclear.map((h) => (
                        <div key={h.at} className="flex flex-wrap items-center gap-2 mt-1 text-xs">
                            <span>{h.account_name}: {h.quantity} × {money(h.unit_price, h.currency)} ({ago(h.at)})</span>
                            <button type="button" disabled={running} onClick={() => deliverAgain(h.at)}
                                className="px-2 py-0.5 rounded border border-amber-400/40 hover:bg-amber-500/10 disabled:opacity-50">
                                Deliver again
                            </button>
                            <span className="text-amber-200/60">Asks the game store to deliver that same purchase; it cannot charge twice.</span>
                        </div>
                    ))}
                </div>
            )}

            <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
                {/* ---- accounts ---- */}
                <section className="rounded-xl border border-white/10 bg-slate-950/85 backdrop-blur-md shadow-xl min-w-0" aria-label="Accounts">
                    <div className="flex flex-wrap items-center gap-2 p-3 border-b border-white/10">
                        <label className="relative flex-1 min-w-[12rem]">
                            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
                            <span className="sr-only">Search accounts</span>
                            <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search accounts"
                                className="w-full bg-black/30 border border-white/10 rounded-lg pl-8 pr-2 py-1.5 text-sm focus:border-amber-500/60 focus:outline-none" />
                        </label>
                        <div className="flex rounded-lg border border-white/10 overflow-hidden text-xs" role="tablist" aria-label="Show">
                            {[['all', `All ${accounts.length}`], ['buyable', 'Can buy'], ['selected', `Selected ${Object.keys(cart).length}`]].map(([key, label]) => (
                                <button key={key} type="button" role="tab" aria-selected={filter === key} onClick={() => setFilter(key)}
                                    className={`px-2.5 py-1.5 ${filter === key ? 'bg-amber-600/20 text-amber-200' : 'text-slate-400 hover:text-white hover:bg-white/5'}`}>
                                    {label}
                                </button>
                            ))}
                        </div>
                        <button type="button" onClick={checkWallets} disabled={running}
                            title="Reads every account's wallet and counts its Storage Units (about 8 seconds per account); nothing is bought"
                            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-white/15 text-slate-300 hover:bg-white/5 disabled:opacity-50 text-xs">
                            <RefreshCw size={13} className={checkingAll || readingPrices ? 'animate-spin' : ''} />
                            {checkingAll ? `Checking ${Math.max(0, state.job.done - 1)}/${Math.max(0, state.job.total - 1)}`
                                : readingPrices ? 'Reading the store prices…' : 'Check wallets & Storage Units'}
                        </button>
                    </div>

                    <div className="overflow-auto custom-scrollbar max-h-[65vh]">
                        <table className="w-full">
                            <thead className="sticky top-0 z-10 bg-slate-950 text-[11px] uppercase tracking-wider text-slate-500 text-left">
                                <tr>
                                    <th className="py-2 pl-3 w-8">
                                        <input type="checkbox" className="accent-amber-500" checked={allVisibleSelected} onChange={toggleAllVisible}
                                            disabled={!visibleSelectable.length} aria-label="Select every account shown" />
                                    </th>
                                    <SortHeader resetLabel="the dashboard order" column="account" label="Account" sort={sort} />
                                    <SortHeader resetLabel="the dashboard order" column="wallet" label="Wallet" sort={sort} align="right"
                                        title="Wallets in other currencies are compared in US dollars" />
                                    <SortHeader resetLabel="the dashboard order" column="storage_units" label="Storage Units" sort={sort} align="right"
                                        title="Storage Units the account holds now (each holds 1,000 items), from its Counter-Strike 2 inventory" />
                                    <SortHeader resetLabel="the dashboard order" column="can_buy" label="Can buy" sort={sort} align="right" />
                                    <th className="font-medium text-right pr-3">Quantity</th>
                                </tr>
                            </thead>
                            <tbody>
                                {visible.length === 0 && (
                                    <tr><td colSpan={6} className="py-8 text-center text-slate-500">No account matches.</td></tr>
                                )}
                                {visible.map((a) => {
                                    const selected = Boolean(cart[a.steamid]);
                                    const blocked = !selectable(a);
                                    return (
                                        <tr key={a.steamid} onClick={() => !blocked && toggle(a)}
                                            className={`border-t border-white/5 ${blocked ? 'opacity-50' : 'cursor-pointer hover:bg-white/[0.03]'} ${selected ? 'bg-amber-500/[0.06]' : ''}`}>
                                            <td className="py-2 pl-3" onClick={(e) => e.stopPropagation()}>
                                                <input type="checkbox" className="accent-amber-500" checked={selected} disabled={blocked}
                                                    onChange={() => toggle(a)} aria-label={`Buy for ${a.account_name}`} />
                                            </td>
                                            <td>
                                                <span className="text-slate-100">{a.account_name}</span>
                                                {a.country && <span className="ml-2 text-[10px] text-slate-500 border border-white/10 rounded px-1">{a.country}</span>}
                                            </td>
                                            <td className="text-right tabular-nums">
                                                {a.balance != null ? (
                                                    <>
                                                        <span className="text-slate-200">{money(a.balance, a.currency)}</span>
                                                        <span className="block text-[10px] text-slate-500">{ago(a.checked_at)}</span>
                                                    </>
                                                ) : <span className="text-slate-500 text-xs">not checked</span>}
                                            </td>
                                            <td className="text-right tabular-nums">
                                                {a.storage_units != null ? (
                                                    <span title={`Room for ${(a.storage_units * (state.storage_unit_capacity || 1000)).toLocaleString()} items · counted ${ago(a.storage_units_at)}`}>
                                                        <span className={a.storage_units ? 'text-slate-200' : 'text-slate-400'}>{a.storage_units}</span>
                                                        <span className="block text-[10px] text-slate-500">{ago(a.storage_units_at)}</span>
                                                    </span>
                                                ) : <span className="text-slate-500 text-xs" title="Counted by “Check wallets & Storage Units”">not counted</span>}
                                            </td>
                                            <td className="text-right text-xs">
                                                {!a.sold_in_currency ? <span className="text-amber-300">currency not sold</span>
                                                    : a.affordable == null ? <span className="text-slate-500" title="Checked when you review the order">?</span>
                                                        : a.affordable === 0 ? <span className="text-amber-300">balance too low</span>
                                                            : <span className="text-emerald-300">up to {a.affordable}</span>}
                                            </td>
                                            <td className="text-right pr-3" onClick={(e) => e.stopPropagation()}>
                                                {selected && (
                                                    <Stepper value={cart[a.steamid]} max={maxFor(a)} label={`Quantity for ${a.account_name}`}
                                                        onChange={(q) => setQuantity(a.steamid, q)} />
                                                )}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                </section>

                {/* ---- order ---- */}
                <aside className="lg:sticky lg:top-4 self-start rounded-xl border border-amber-500/25 bg-slate-900/90 backdrop-blur-md shadow-xl p-4" aria-label="Order">
                    <h2 className="flex items-center gap-2 text-amber-100 font-serif font-bold text-base mb-3"><ShoppingCart size={16} /> Order</h2>
                    {cartLines.length === 0 ? (
                        <p className="text-slate-400 text-xs leading-relaxed">Tick the accounts to buy for. Each starts at 1 Storage Unit; change the quantity in its row.</p>
                    ) : (
                        <>
                            <ul className="space-y-1.5 mb-3 max-h-64 overflow-auto custom-scrollbar">
                                {cartLines.map((l) => (
                                    <li key={l.steamid} className="flex items-center gap-2">
                                        <span className="flex-1 truncate text-slate-200">{l.account_name}</span>
                                        <span className="tabular-nums text-slate-400">× {l.quantity}</span>
                                        <span className="tabular-nums text-slate-200 w-20 text-right">{l.total != null ? money(l.total, l.currency) : `≈ $${l.usd.toFixed(2)}`}</span>
                                        <button type="button" onClick={() => toggle(byId[l.steamid])} aria-label={`Remove ${l.account_name}`}
                                            className="text-slate-500 hover:text-red-300"><X size={13} /></button>
                                    </li>
                                ))}
                            </ul>
                            <div className="border-t border-white/10 pt-2 mb-3 space-y-0.5">
                                <div className="flex justify-between text-slate-400"><span>Storage Units</span><span className="tabular-nums text-slate-200">{cartUnits}</span></div>
                                <div className="flex justify-between text-slate-300 font-medium">
                                    <span>Total</span>
                                    <span className="tabular-nums text-amber-100">{cartUnknown ? `≈ $${cartLines.reduce((s, l) => s + l.usd, 0).toFixed(2)}` : moneyList(sumByCurrency(cartLines))}</span>
                                </div>
                            </div>
                            <button type="button" onClick={review} disabled={running}
                                className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-medium disabled:opacity-50">
                                <Wallet size={15} /> Review &amp; buy
                            </button>
                            <p className="mt-2 text-[11px] text-slate-500 leading-relaxed">
                                The wallets are read again first; you confirm the exact totals before anything is paid.
                            </p>
                            <button type="button" onClick={() => setCart({})} className="mt-2 text-xs text-slate-500 hover:text-slate-300">Clear the order</button>
                        </>
                    )}
                </aside>
            </div>

            {(state.history || []).length > 0 && (
                <details className="mt-6 rounded-xl border border-white/10 bg-slate-950/85 backdrop-blur-md p-3">
                    <summary className="cursor-pointer text-slate-300 inline-flex items-center gap-2"><History size={14} /> Purchase log ({state.history.length})</summary>
                    <div className="overflow-auto custom-scrollbar">
                        <table className="w-full text-xs mt-2">
                            <thead className="text-slate-500 text-left"><tr><th className="py-1">When</th><th>Account</th><th className="text-right">Units</th><th className="text-right">Total</th><th className="text-right">Before → after</th><th className="pl-3">Result</th></tr></thead>
                            <tbody>
                                {state.history.map((h) => (
                                    <tr key={`${h.steamid}-${h.at}`} className="border-t border-white/5">
                                        <td className="py-1 text-slate-400">{ago(h.at)}</td>
                                        <td className="text-slate-300">{h.account_name}</td>
                                        <td className="text-right tabular-nums">{h.quantity}</td>
                                        <td className="text-right tabular-nums">{money(h.expected, h.currency)}</td>
                                        <td className="text-right tabular-nums text-slate-400">{h.storage_units_before ?? '—'} → {h.storage_units_after ?? '—'}</td>
                                        <td className={`pl-3 ${h.paid ? 'text-emerald-300' : h.dry_run && h.ok ? 'text-sky-300' : h.state === 'in progress' ? 'text-amber-300' : 'text-red-400'}`}>
                                            {h.paid ? 'Bought' : h.dry_run && h.ok ? 'Test passed (nothing paid)' : h.state === 'in progress' ? 'In progress…' : h.error}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </details>
            )}

            {view && <OrderDialog order={shown} view={view} job={state.job || {}} onPay={pay} onClose={closeOrder}
                onDeliverAgain={deliverAgain} onReviewAgain={() => { setOrder(null); review(); }} />}
        </div>
    );
};

// ---- the order dialog: checking → review → running → results ------------------------------

const OrderDialog = ({ order, view, job, onPay, onClose, onDeliverAgain, onReviewAgain }) => {
    if (view.stage === 'stale') {
        return (
            <Modal title="The wallets were checked again elsewhere" onClose={onClose}>
                <p className="text-slate-300">Another check replaced this one (another tab or “Check wallets”), so this review is out of date.</p>
                <div className="flex justify-end gap-2 mt-4">
                    <button type="button" onClick={onClose} className="px-3 py-2 rounded-lg text-slate-300 hover:bg-white/5">Cancel</button>
                    <button type="button" onClick={onReviewAgain} disabled={job.running}
                        className="px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-medium disabled:opacity-50">Review again</button>
                </div>
            </Modal>
        );
    }
    if (view.stage === 'checking') {
        const count = Object.keys(order.wanted || {}).length;
        return (
            <Modal title="Checking the wallets" closable={false}>
                <p className="flex items-center gap-2 text-slate-300">
                    <Loader2 size={16} className="animate-spin text-amber-400" />
                    Reading {count} wallet{count > 1 ? 's' : ''} and the game store price…
                    {job.running && job.total ? <span className="tabular-nums text-slate-500">{job.done}/{job.total}</span> : null}
                </p>
                <p className="mt-2 text-xs text-slate-500">About 4 seconds per account. Nothing is bought yet.</p>
            </Modal>
        );
    }

    if (view.stage === 'failed') {
        return (
            <Modal title="The wallets could not be checked" onClose={onClose}>
                <p className="text-red-300">{view.message}</p>
                <div className="flex justify-end mt-4">
                    <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-medium">Close</button>
                </div>
            </Modal>
        );
    }

    if (view.stage === 'review') {
        const payable = payableLines(view.lines);
        const skipped = view.lines.length - payable.length;
        const units = payable.reduce((n, l) => n + l.quantity, 0);
        const totals = sumByCurrency(payable);
        const usd = payable.reduce((s, l) => s + (l.usd_per_unit || 0) * l.quantity, 0);
        return (
            <Modal title="Review your order" onClose={onClose}>
                <div className="overflow-auto custom-scrollbar">
                    <table className="w-full text-sm mb-3">
                        <thead className="text-[11px] uppercase tracking-wider text-slate-500 text-left">
                            <tr><th className="pb-1 font-medium">Account</th><th className="text-right font-medium">Quantity</th><th className="text-right font-medium">Each</th><th className="text-right font-medium">Total</th><th className="text-right font-medium">Wallet now → after</th></tr>
                        </thead>
                        <tbody>
                            {view.lines.map((l) => (
                                <tr key={l.steamid} className={`border-t border-white/5 ${!l.quantity ? 'opacity-60' : ''}`}>
                                    <td className="py-1.5 text-slate-100">
                                        {l.account_name}
                                        {l.problem && <span className={`block text-xs ${l.quantity ? 'text-amber-300' : 'text-red-300'}`}>{l.problem}{!l.quantity && ' — skipped'}</span>}
                                    </td>
                                    <td className="text-right tabular-nums">{l.quantity || '—'}</td>
                                    <td className="text-right tabular-nums text-slate-400">{money(l.unit_price, l.currency)}</td>
                                    <td className="text-right tabular-nums text-slate-100">{money(l.total, l.currency)}</td>
                                    <td className="text-right tabular-nums text-slate-400 whitespace-nowrap">
                                        {money(l.balance, l.currency)}{l.total != null && <> → <span className="text-slate-200">{money(l.balance - l.total, l.currency)}</span></>}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                {payable.length === 0 ? (
                    <p className="text-red-300 mb-3">Nothing can be bought with these wallets.</p>
                ) : (
                    <div className="rounded-lg border border-amber-500/30 bg-amber-500/[0.06] px-3 py-2 mb-4 text-slate-200">
                        <span className="font-medium">{units} Storage Unit{units > 1 ? 's' : ''}</span> on {payable.length} account{payable.length > 1 ? 's' : ''} for{' '}
                        <span className="font-medium text-amber-100">{moneyList(totals)}</span>
                        {Object.keys(totals).some((c) => c !== 'USD') && usd > 0 && <span className="text-slate-400"> (≈ ${usd.toFixed(2)})</span>}
                        {skipped > 0 && <span className="block text-xs text-slate-400 mt-0.5">{skipped} account{skipped > 1 ? 's' : ''} skipped.</span>}
                    </div>
                )}
                <div className="flex flex-wrap items-center justify-end gap-2">
                    <button type="button" onClick={onClose} className="px-3 py-2 rounded-lg text-slate-300 hover:bg-white/5">Cancel</button>
                    <button type="button" onClick={() => onPay(true)} disabled={!payable.length}
                        title="Goes through every check with Steam and cancels before approving: nothing is paid"
                        className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-white/15 text-slate-200 hover:bg-white/5 disabled:opacity-50">
                        <FlaskConical size={14} /> Test without paying
                    </button>
                    <button type="button" onClick={() => onPay(false)} disabled={!payable.length}
                        className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium disabled:opacity-50">
                        <ShoppingCart size={14} /> Pay {moneyList(totals)}
                    </button>
                </div>
            </Modal>
        );
    }

    // running / results: one row per account with its live step or its outcome
    const done = view.stage === 'results';
    const paidUnits = view.results.filter((h) => h.paid).reduce((n, h) => n + h.quantity, 0);
    return (
        <Modal title={done ? (order.dryRun ? 'Test finished' : 'Purchase finished') : (order.dryRun ? 'Testing (nothing is paid)' : 'Buying…')}
            onClose={onClose} closable={done}>
            {!done && !order.dryRun && <p className="mb-3 text-xs text-slate-400">Keep this page open or come back later: the purchase runs on the server either way.</p>}
            {done && !order.dryRun && (
                <p className={`mb-3 flex items-center gap-2 ${paidUnits ? 'text-emerald-300' : 'text-amber-300'}`}>
                    {paidUnits ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
                    {paidUnits ? `Bought ${paidUnits} Storage Unit${paidUnits > 1 ? 's' : ''}.` : 'Nothing was bought.'}
                </p>
            )}
            {done && view.jobError && <p className="mb-3 text-red-300">{view.jobError}</p>}
            <ul className="space-y-3">
                {order.paying.map((line) => {
                    const result = view.results.find((h) => h.steamid === line.steamid);
                    const current = job.running && job.account === line.steamid;
                    const finished = result && result.state === 'done';
                    const good = finished && (result.paid || (order.dryRun && result.ok));
                    // A test stops at the approval page: it never approves or delivers.
                    const steps = order.dryRun
                        ? STEPS.slice(0, 4).map(([k, label]) => [k, k === 'approving' ? 'Checking the approval page' : label])
                        : STEPS;
                    const at = steps.findIndex(([k]) => k === job.step);
                    return (
                        <li key={line.steamid} className="rounded-lg border border-white/10 bg-black/20 p-3">
                            <div className="flex items-center gap-2">
                                {finished ? (good ? <CheckCircle2 size={16} className="text-emerald-400" /> : <AlertTriangle size={16} className="text-red-400" />)
                                    : current ? <Loader2 size={16} className="animate-spin text-amber-400" /> : <Circle size={16} className="text-slate-600" />}
                                <span className="flex-1 text-slate-100">{line.account_name}</span>
                                <span className="tabular-nums text-slate-400">{line.quantity} × {money(line.unit_price, line.currency)}</span>
                            </div>
                            {current && !finished && (
                                <ol className="mt-2 ml-6 flex flex-wrap gap-x-3 gap-y-1 text-xs" aria-label="Steps">
                                    {steps.map(([key, label], index) => (
                                        <li key={key} className={index < at ? 'text-emerald-300' : index === at ? 'text-amber-200' : 'text-slate-600'}>
                                            {index < at ? '✓ ' : ''}{label}
                                        </li>
                                    ))}
                                </ol>
                            )}
                            {!current && !finished && <p className="mt-1 ml-6 text-xs text-slate-500">Waiting</p>}
                            {finished && (
                                <p className={`mt-1 ml-6 text-xs ${result.paid ? 'text-emerald-300' : good ? 'text-sky-300' : 'text-red-300'}`}>
                                    {result.paid
                                        ? `Bought · Storage Units ${result.storage_units_before ?? '?'} → ${result.storage_units_after ?? '?'}${result.error ? ` · ${result.error.replace(/^paid; /, '')}` : ''}`
                                        : good ? 'Every check passed; cancelled before approving (nothing paid)' : result.error}
                                </p>
                            )}
                            {finished && result.payment_attempted && !result.paid && (
                                <button type="button" onClick={() => onDeliverAgain(result.at)} disabled={job.running}
                                    className="mt-2 ml-6 px-2 py-0.5 rounded border border-amber-400/40 text-amber-200 text-xs hover:bg-amber-500/10 disabled:opacity-50">
                                    Deliver again
                                </button>
                            )}
                        </li>
                    );
                })}
            </ul>
            {done && (
                <div className="flex justify-end mt-4">
                    <button type="button" onClick={onClose} autoFocus className="px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-medium">Done</button>
                </div>
            )}
        </Modal>
    );
};

export default StorageShop;
