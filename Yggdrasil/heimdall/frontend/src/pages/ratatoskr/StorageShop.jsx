import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';
import { Link, useParams } from 'react-router-dom';
import {
    Archive, LayoutDashboard, Search, RefreshCw, Minus, Plus, ShoppingCart, FlaskConical, X,
    CheckCircle2, AlertTriangle, Loader2, Circle, Wallet, History, Tags,
} from 'lucide-react';
import { useColumnSort, sortRows } from '../../components/draupnir/columnSort';
import SortHeader from '../../components/ratatoskr/SortHeader';
import { failureMessage, readJsonOrThrow } from '../../utils/readJson';
import { postCommand, followJob, refusedBeforeStart } from '../../utils/moveCommands';

// Ratatoskr "Buy Storage Units" — and any other in-game store item (/store-catalogue/buy/<entry>,
// the Store Catalogue's Buy button): pick any account from the dashboard, choose how many,
// review the order with freshly read wallets, pay. Backend: /api/ratatoskr/storage-shop*
// with the price sheet entry as "item" (the Storage Unit is "casket"). The account list shows what is last known (nothing is read
// from Steam until "Review & buy" or "Check wallets"); every purchase is re-checked
// against Steam's own approval request before anything is paid.
// The backend decides, this page displays (storage_shop_service.py): each account row says whether
// it can be ticked and how many it may order; the plan (POST …/plan → GET …/plans/<id>) answers the
// lines, totals and its state; "Test without paying" is "execute plan <id>" as a dry run, and Pay
// (the same command, once) is offered only after a dry run of that plan passed (Ivan's rule): both
// are jobs followed at /api/jobs/<id> that carry their own orders, steps and each account's outcome.

const SHOP = '/api/ratatoskr/storage-shop';

// What each sortable column sorts on. Wallets are compared in US dollars (they are in
// different currencies); an unknown value (never checked, currency not sold) sorts last.
const SORT_VALUES = {
    account: (a) => a.account_name,
    wallet: (a) => a.balance_usd,
    storage_units: (a) => a.storage_units,
    can_buy: (a) => a.can_buy_count,
};
const CAN_BUY_COLOUR = { up_to: 'text-emerald-300', unknown: 'text-slate-500', currency_not_sold: 'text-amber-300', balance_too_low: 'text-amber-300' };
const OUTCOME_COLOUR = { bought: 'text-emerald-300', test_passed: 'text-sky-300', in_progress: 'text-amber-300', failed: 'text-red-400' };
const LOG_RESULT = { bought: 'Bought', test_passed: 'Test passed (nothing paid)', in_progress: 'In progress…' };

const money = (minorUnits, currency) => {
    if (minorUnits === null || minorUnits === undefined || !currency) return '—';
    return currency === 'USD' ? `$${(minorUnits / 100).toFixed(2)}` : `${(minorUnits / 100).toFixed(2)} ${currency}`;
};
// The backend's totals per currency, written out: "$3.98 + 35.00 EUR".
const totalsText = (byCurrency) => Object.entries(byCurrency || {}).map(([c, v]) => money(v, c)).join(' + ') || '—';
const ago = (epochSeconds) => {
    if (!epochSeconds) return null;
    const minutes = Math.round((Date.now() / 1000 - epochSeconds) / 60);
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.round(minutes / 60);
    return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} days ago`;
};
const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '');
const selectionOf = (cart) => Object.entries(cart).map(([steamid, quantity]) => ({ steamid, quantity }));
const fetchPlan = (planId) => fetch(`${SHOP}/plans/${planId}`).then(readJsonOrThrow);

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

// ---- the page ------------------------------------------------------------------------------

const PURCHASE_LOST = 'The backend restarted during the purchase, so this view lost it. Look at the purchase log (and the account) before trying again.';
const lostTrack = (reason) => `Lost track of the purchase (${reason}). It keeps running in the backend: look at the purchase log.`;

const StorageShop = ({ item }) => {
    const query = `item=${encodeURIComponent(item)}`;
    const [state, setState] = useState(null);
    const [error, setError] = useState(null);
    const [search, setSearch] = useState('');
    const [filter, setFilter] = useState('all');          // all | buyable | selected
    const [cart, setCart] = useState({});                 // {steamid: quantity}
    const [quote, setQuote] = useState(null);             // the order panel's totals (backend)
    const sort = useColumnSort();                         // {sortKey, sortDir, toggle}
    // The order dialog: {stage: 'checking', count, job} → {stage: 'review', plan} or {stage: 'failed',
    // message}; then {stage: 'running' | 'results', jobId, job, lost}. A purchase is followed by its
    // job id (its own orders and outcomes) and shown until closed.
    const [order, setOrder] = useState(null);
    const [submitting, setSubmitting] = useState(false);
    const paying = useRef(false);          // one Pay request at a time, even on a double click
    const loads = useRef(0);               // only the newest status answer is kept
    const quotes = useRef(0);              // only the newest quote is kept
    const mounted = useRef(true);
    const adopted = useRef(new Set());     // purchase jobs already shown (never opened twice)
    const openOrder = useRef(null);        // the dialog as of the last render, for the status poll

    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; };
    }, []);
    useEffect(() => { openOrder.current = order; }, [order]);

    // Follow a purchase or delivery job by its id; the dialog shows it until it is closed. A dry
    // run started from a review (`reviewPlanId`) goes back to that review when it ends: its lines
    // then say how each account's test went, and Pay is offered when the test passed.
    const follow = useCallback((job, reviewPlanId = null) => {
        adopted.current.add(job.id);
        setOrder({ stage: 'running', jobId: job.id, job });
        const ours = (update) => setOrder((o) => (o?.jobId === job.id ? update(o) : o));
        followJob(job.id, {
            onProgress: (current) => ours((o) => ({ ...o, job: current })),
            isActive: () => mounted.current, lostMessage: PURCHASE_LOST, lostTrackMessage: lostTrack,
        })
            .then((finished) => {
                if (!reviewPlanId || !finished.dry_run) {
                    ours((o) => ({ ...o, stage: 'results', job: finished }));
                    return;
                }
                fetchPlan(reviewPlanId)
                    .then((plan) => ours(() => ({ stage: 'review', plan })))
                    .catch(() => ours((o) => ({ ...o, stage: 'results', job: finished })));
            })
            .catch((err) => { if (!err.cancelled) ours((o) => ({ ...o, stage: 'results', lost: err.message })); });
    }, []);

    const load = useCallback(() => {
        const number = ++loads.current;
        return fetch(`${SHOP}?${query}`)
            .then(readJsonOrThrow)
            .then((d) => {
                if (number !== loads.current) return;
                setState(d);
                // A purchase running with no dialog open here (another tab, or this page reloaded):
                // shown all the same, from its job.
                const job = d.job || {};
                if (job.running && job.id && ['purchase', 'delivery'].includes(job.kind)
                    && !openOrder.current && !adopted.current.has(job.id)) follow(job);
            })
            .catch((error) => { if (number === loads.current) setError(failureMessage(error)); });
    }, [query, follow]);
    const running = Boolean(state?.job?.running);
    // The full status (it reads every account) is asked every 1.5 s only while a shop job runs with no
    // dialog here (the "Checking 3/21" counter, picking up a purchase from elsewhere); an open dialog
    // follows its own job and plan by id, so the status is then asked every 30 s and at each new stage.
    const dialogStage = order?.stage || null;
    useEffect(() => {
        load();
        const timer = setInterval(load, running && !dialogStage ? 1500 : 30000);
        return () => clearInterval(timer);
    }, [load, running, dialogStage]);

    const accounts = useMemo(() => state?.accounts || [], [state]);
    const byId = useMemo(() => Object.fromEntries(accounts.map((a) => [a.steamid, a])), [accounts]);
    // When a wallet was read again the order panel's totals change: asked again then.
    const walletsRead = accounts.map((a) => `${a.steamid}:${a.checked_at}`).join();

    useEffect(() => {
        const number = ++quotes.current;
        const selection = selectionOf(cart);
        if (!selection.length) return;
        postCommand(`${SHOP}/quote`, { item, selection })
            .then((answer) => { if (number === quotes.current) setQuote(answer); })
            .catch((err) => { if (number === quotes.current) setError(failureMessage(err)); });
    }, [cart, item, walletsRead]);

    // A review's plan is read again while it is open: it can expire or be replaced elsewhere.
    const reviewedPlan = order?.stage === 'review' ? order.plan.plan_id : null;
    useEffect(() => {
        if (!reviewedPlan) return undefined;
        const timer = setInterval(() => {
            fetchPlan(reviewedPlan)
                .then((plan) => setOrder((o) => (o?.stage === 'review' && o.plan.plan_id === reviewedPlan ? { ...o, plan } : o)))
                .catch((err) => console.warn('Plan refresh failed', err));
        }, 5000);
        return () => clearInterval(timer);
    }, [reviewedPlan]);

    const visible = useMemo(() => {
        const needle = search.trim().toLowerCase();
        const matching = accounts.filter((a) => (!needle || a.account_name.toLowerCase().includes(needle))
            && (filter === 'all' || (filter === 'selected' ? cart[a.steamid] : a.selectable)));
        return sortRows(matching, sort.sortKey, sort.sortDir, SORT_VALUES);
    }, [accounts, search, filter, cart, sort.sortKey, sort.sortDir]);

    const toggle = (steamid) => setCart((current) => {
        const next = { ...current };
        if (next[steamid]) delete next[steamid]; else next[steamid] = 1;
        return next;
    });
    const setQuantity = (steamid, quantity) => setCart((current) => ({
        ...current, [steamid]: Math.max(1, Math.min(quantity, byId[steamid]?.max_quantity ?? 1)),
    }));
    const visibleSelectable = visible.filter((a) => a.selectable);
    const allVisibleSelected = visibleSelectable.length > 0 && visibleSelectable.every((a) => cart[a.steamid]);
    const toggleAllVisible = () => setCart((current) => {
        const next = { ...current };
        visibleSelectable.forEach((a) => {
            if (allVisibleSelected) delete next[a.steamid]; else next[a.steamid] = next[a.steamid] || 1;
        });
        return next;
    });
    const cartSize = Object.keys(cart).length;
    const cartLines = cartSize ? (quote?.lines || []).filter((l) => cart[l.steamid]) : [];

    const checkWallets = () => {
        setError(null);
        postCommand(`${SHOP}/plan`, { item })
            .catch((err) => setError(failureMessage(err)))
            .finally(load);
    };
    const review = async () => {
        setError(null);
        setOrder({ stage: 'checking', count: cartSize, job: null });
        const checking = (update) => setOrder((o) => (o?.stage === 'checking' ? update(o) : o));
        try {
            const started = await postCommand(`${SHOP}/plan`, { item, selection: selectionOf(cart) });
            load();
            await followJob(started.check_job_id, {
                onProgress: (job) => checking((o) => ({ ...o, job })), isActive: () => mounted.current,
                lostMessage: 'The backend restarted while reading the wallets: review again.',
                lostTrackMessage: (reason) => `Lost track of the wallet check (${reason}).`,
            });
            const plan = await fetchPlan(started.plan_id);
            checking(() => (plan.state === 'failed'
                ? { stage: 'failed', message: plan.error || 'The wallets could not be checked.' }
                : { stage: 'review', plan }));
        } catch (err) {
            if (err.cancelled) return;
            if (refusedBeforeStart(err)) { checking(() => null); setError(err.message); }
            else checking(() => ({ stage: 'failed', message: failureMessage(err) }));
        } finally {
            load();
        }
    };
    const pay = async (dryRun) => {
        if (paying.current || order?.stage !== 'review') return;
        paying.current = true;
        setSubmitting(true);
        setError(null);
        const planId = order.plan.plan_id;
        try {
            follow(await postCommand(`${SHOP}/plans/${planId}/execute`, { dry_run: dryRun, item }), planId);
        } catch (err) {
            if (refusedBeforeStart(err)) {
                // Refused before anything started: back to the review, with the plan as it is now.
                setError(err.message);
                fetchPlan(planId).then((plan) => setOrder((o) => (o?.stage === 'review' && o.plan.plan_id === planId ? { ...o, plan } : o)))
                    .catch(() => {});
            } else {
                // No answer, or a server error after the purchase may have started: it may be buying.
                // With the dialog closed, the next status read opens the running purchase from its job.
                setOrder(null);
                setError(`${failureMessage(err)} It may be buying: look at the purchase log before trying again.`);
            }
        } finally {
            paying.current = false;
            setSubmitting(false);
            load();
        }
    };
    const closeOrder = () => {
        if (order?.stage === 'results') {
            // Bought accounts leave the order; anything that failed stays for another try.
            const bought = new Set((order.job?.result?.accounts || []).filter((a) => a.paid).map((a) => a.steamid));
            setCart((current) => Object.fromEntries(Object.entries(current).filter(([s]) => !bought.has(s))));
        }
        if (order?.stage === 'failed') setError(order.message);
        setOrder(null);
    };
    const deliverAgain = (at) => {
        setError(null);
        postCommand(`${SHOP}/deliver-again`, { at })
            .then(follow)                 // the dialog follows the delivery
            .catch((err) => setError(failureMessage(err)))
            .finally(load);
    };

    if (!state) {
        return <div className="min-h-screen flex items-center justify-center text-slate-400"><Loader2 className="animate-spin mr-2" size={18} /> Loading…</div>;
    }
    const itemInfo = state.item || { entry: item, name: item, for_sale: true };
    const isStorageUnit = Boolean(itemInfo.is_storage_unit);
    // Not buyable from this page: the game license / Armory Pass, or an item Ratatoskr cannot name.
    const blockedItem = !itemInfo.for_sale ? 'The game license and the Armory Pass are not sold here.' : itemInfo.error;
    const stats = state.statistics || { units: 0, spent: {}, accounts: {} };
    const unclear = state.unclear_purchases || [];
    const checkingAll = running && state.job.kind === 'plan' && !order;
    // The Store Catalogue's "Read prices again" shares this page's job: nothing can start meanwhile.
    const readingPrices = running && state.job.kind === 'price sheet';

    return (
        <div className="min-h-screen p-4 md:p-8 text-sm">
            <header className="flex flex-wrap items-center gap-3 mb-6">
                <div className="p-2 bg-amber-900/30 rounded-lg border border-amber-600/30"><Archive size={22} className="text-amber-500" /></div>
                <div className="mr-auto rounded-lg">
                    <h1 className="text-2xl font-bold text-amber-100 font-serif">{isStorageUnit ? 'Buy Storage Units' : `Buy ${itemInfo.name}`}</h1>
                    <p className="text-xs text-slate-300 [text-shadow:0_1px_3px_rgb(0_0_0)]">
                        {isStorageUnit
                            ? `Buy Counter-Strike 2 Storage Units (${state.storage_unit_capacity || 1000} items each) from each account's Steam wallet`
                            : `From the Counter-Strike 2 in-game store, with each account's Steam wallet${itemInfo.usd_list_price ? ` · $${itemInfo.usd_list_price.toFixed(2)} each` : ''}${itemInfo.max_usd_per_unit ? ` · refused above $${itemInfo.max_usd_per_unit.toFixed(2)} in any currency` : ''}`}
                    </p>
                </div>
                {stats.units > 0 && (
                    <span className="text-xs text-slate-400 rounded-lg border border-white/10 px-3 py-1.5">
                        Bought so far: <span className="text-emerald-300 font-medium">{stats.units}</span> for {totalsText(stats.spent)}
                    </span>
                )}
                <Link to="/store-catalogue" className="flex items-center gap-2 px-3 py-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/5">
                    <Tags size={14} /> Store Catalogue
                </Link>
                <Link to="/" className="flex items-center gap-2 px-3 py-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/5">
                    <LayoutDashboard size={14} /> Dashboard
                </Link>
            </header>
            {blockedItem && (
                <div className="mb-4 flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-950/80 px-3 py-2 text-red-300" role="alert">
                    <AlertTriangle size={15} className="mt-0.5 shrink-0" /> <span>{blockedItem}</span>
                </div>
            )}

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
                            <span>{h.account_name}: {h.quantity} × {h.item_name} at {money(h.unit_price, h.currency)} ({ago(h.at)})</span>
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
                            {[['all', `All ${accounts.length}`], ['buyable', 'Can buy'], ['selected', `Selected ${cartSize}`]].map(([key, label]) => (
                                <button key={key} type="button" role="tab" aria-selected={filter === key} onClick={() => setFilter(key)}
                                    className={`px-2.5 py-1.5 ${filter === key ? 'bg-amber-600/20 text-amber-200' : 'text-slate-400 hover:text-white hover:bg-white/5'}`}>
                                    {label}
                                </button>
                            ))}
                        </div>
                        <button type="button" onClick={checkWallets} disabled={running || Boolean(blockedItem)}
                            title={isStorageUnit ? "Reads every account's wallet and counts its Storage Units (about 8 seconds per account); nothing is bought"
                                : "Reads every account's wallet (about 4 seconds per account); nothing is bought"}
                            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-white/15 text-slate-300 hover:bg-white/5 disabled:opacity-50 text-xs">
                            <RefreshCw size={13} className={checkingAll || readingPrices ? 'animate-spin' : ''} />
                            {checkingAll ? `Checking ${state.job.accounts_done ?? 0}/${state.job.accounts_total ?? 0}`
                                : readingPrices ? 'Reading the store prices…' : isStorageUnit ? 'Check wallets & Storage Units' : 'Check wallets'}
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
                                    {isStorageUnit && <SortHeader resetLabel="the dashboard order" column="storage_units" label="Storage Units" sort={sort} align="right"
                                        title="Storage Units the account holds now (each holds 1,000 items), from its Counter-Strike 2 inventory" />}
                                    <SortHeader resetLabel="the dashboard order" column="can_buy" label="Can buy" sort={sort} align="right" />
                                    <th className="font-medium text-right pr-3">Quantity</th>
                                </tr>
                            </thead>
                            <tbody>
                                {visible.length === 0 && (
                                    <tr><td colSpan={isStorageUnit ? 6 : 5} className="py-8 text-center text-slate-500">No account matches.</td></tr>
                                )}
                                {visible.map((a) => {
                                    const selected = Boolean(cart[a.steamid]);
                                    const blocked = !a.selectable;
                                    return (
                                        <tr key={a.steamid} onClick={() => !blocked && toggle(a.steamid)}
                                            className={`border-t border-white/5 ${blocked ? 'opacity-50' : 'cursor-pointer hover:bg-white/[0.03]'} ${selected ? 'bg-amber-500/[0.06]' : ''}`}>
                                            <td className="py-2 pl-3" onClick={(e) => e.stopPropagation()}>
                                                <input type="checkbox" className="accent-amber-500" checked={selected} disabled={blocked}
                                                    onChange={() => toggle(a.steamid)} aria-label={`Buy for ${a.account_name}`} />
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
                                            {isStorageUnit && <td className="text-right tabular-nums">
                                                {a.storage_units != null ? (
                                                    <span title={`Room for ${(a.storage_units * (state.storage_unit_capacity || 1000)).toLocaleString()} items · counted ${ago(a.storage_units_at)}`}>
                                                        <span className={a.storage_units ? 'text-slate-200' : 'text-slate-400'}>{a.storage_units}</span>
                                                        <span className="block text-[10px] text-slate-500">{ago(a.storage_units_at)}</span>
                                                    </span>
                                                ) : <span className="text-slate-500 text-xs" title="Counted by “Check wallets & Storage Units”">not counted</span>}
                                            </td>}
                                            <td className="text-right text-xs">
                                                <span className={CAN_BUY_COLOUR[a.can_buy_code] || 'text-slate-400'}
                                                    title={a.can_buy_code === 'unknown' ? 'Checked when you review the order' : undefined}>{a.can_buy}</span>
                                            </td>
                                            <td className="text-right pr-3" onClick={(e) => e.stopPropagation()}>
                                                {selected && (
                                                    <Stepper value={cart[a.steamid]} max={a.max_quantity} label={`Quantity for ${a.account_name}`}
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
                    {cartSize === 0 ? (
                        <p className="text-slate-400 text-xs leading-relaxed">Tick the accounts to buy for. Each starts at {isStorageUnit ? '1 Storage Unit' : `1 × ${itemInfo.name}`}; change the quantity in its row.</p>
                    ) : (
                        <>
                            <ul className="space-y-1.5 mb-3 max-h-64 overflow-auto custom-scrollbar">
                                {cartLines.map((l) => (
                                    <li key={l.steamid} className="flex items-center gap-2">
                                        <span className="flex-1 truncate text-slate-200">{l.account_name}</span>
                                        <span className="tabular-nums text-slate-400">× {l.quantity}</span>
                                        <span className="tabular-nums text-slate-200 w-20 text-right">{l.total != null ? money(l.total, l.currency) : `≈ $${l.usd_estimate.toFixed(2)}`}</span>
                                        <button type="button" onClick={() => toggle(l.steamid)} aria-label={`Remove ${l.account_name}`}
                                            className="text-slate-500 hover:text-red-300"><X size={13} /></button>
                                    </li>
                                ))}
                            </ul>
                            <div className="border-t border-white/10 pt-2 mb-3 space-y-0.5">
                                <div className="flex justify-between text-slate-400"><span>{isStorageUnit ? 'Storage Units' : itemInfo.name}</span><span className="tabular-nums text-slate-200">{quote?.units ?? '…'}</span></div>
                                <div className="flex justify-between text-slate-300 font-medium">
                                    <span>Total</span>
                                    <span className="tabular-nums text-amber-100">{!quote ? '…' : quote.complete ? totalsText(quote.totals_by_currency) : `≈ $${quote.usd_estimate.toFixed(2)}`}</span>
                                </div>
                            </div>
                            <button type="button" onClick={review} disabled={running || Boolean(blockedItem)}
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
                            <thead className="text-slate-500 text-left"><tr><th className="py-1">When</th><th>Account</th><th>Item</th><th className="text-right">Quantity</th><th className="text-right">Total</th><th className="text-right" title="Storage Units on the account before and after a Storage Unit purchase">Storage Units</th><th className="pl-3">Result</th></tr></thead>
                            <tbody>
                                {state.history.map((h) => (
                                    <tr key={`${h.steamid}-${h.at}`} className="border-t border-white/5">
                                        <td className="py-1 text-slate-400">{ago(h.at)}</td>
                                        <td className="text-slate-300">{h.account_name}</td>
                                        <td className="text-slate-400">{h.item_name}</td>
                                        <td className="text-right tabular-nums">{h.quantity}</td>
                                        <td className="text-right tabular-nums">{money(h.expected, h.currency)}</td>
                                        <td className="text-right tabular-nums text-slate-400">{h.counts_storage_units ? `${h.storage_units_before ?? '—'} → ${h.storage_units_after ?? '—'}` : ''}</td>
                                        <td className={`pl-3 ${OUTCOME_COLOUR[h.outcome]}`}>
                                            {LOG_RESULT[h.outcome] || h.error}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </details>
            )}

            {order && <OrderDialog order={order} error={error} submitting={submitting} busy={running} onPay={pay} onClose={closeOrder}
                onDeliverAgain={deliverAgain} onReviewAgain={() => { setOrder(null); review(); }} />}
        </div>
    );
};

// ---- the order dialog: checking → review → running → results ------------------------------

const DialogError = ({ error }) => (error
    ? <p className="mb-3 flex items-start gap-2 text-red-300" role="alert"><AlertTriangle size={15} className="mt-0.5 shrink-0" /> {error}</p>
    : null);

const OrderDialog = ({ order, error, submitting, busy, onPay, onClose, onDeliverAgain, onReviewAgain }) => {
    if (order.stage === 'checking') {
        const job = order.job || {};
        return (
            <Modal title="Checking the wallets" closable={false}>
                <p className="flex items-center gap-2 text-slate-300">
                    <Loader2 size={16} className="animate-spin text-amber-400" />
                    Reading {order.count} wallet{order.count > 1 ? 's' : ''} and the game store price…
                    {job.running && job.accounts_total ? <span className="tabular-nums text-slate-500">{job.accounts_done}/{job.accounts_total}</span> : null}
                </p>
                <p className="mt-2 text-xs text-slate-500">About 4 seconds per account. Nothing is bought yet.</p>
            </Modal>
        );
    }

    if (order.stage === 'failed') {
        return (
            <Modal title="The wallets could not be checked" onClose={onClose}>
                <p className="text-red-300">{order.message}</p>
                <div className="flex justify-end mt-4">
                    <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-medium">Close</button>
                </div>
            </Modal>
        );
    }

    if (order.stage === 'review' && order.plan.state === 'replaced') {
        return (
            <Modal title="The wallets were checked again elsewhere" onClose={onClose}>
                <p className="text-slate-300">Another check replaced this one (another tab or “Check wallets”), so this review is out of date.</p>
                <div className="flex justify-end gap-2 mt-4">
                    <button type="button" onClick={onClose} className="px-3 py-2 rounded-lg text-slate-300 hover:bg-white/5">Cancel</button>
                    <button type="button" onClick={onReviewAgain} disabled={busy}
                        className="px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-medium disabled:opacity-50">Review again</button>
                </div>
            </Modal>
        );
    }

    if (order.stage === 'review') {
        const { plan } = order;
        const expired = plan.state === 'expired';
        const otherCurrency = Object.keys(plan.totals_by_currency || {}).some((c) => c !== 'USD');
        return (
            <Modal title="Review your order" onClose={onClose}>
                <DialogError error={error} />
                <div className="overflow-auto custom-scrollbar">
                    <table className="w-full text-sm mb-3">
                        <thead className="text-[11px] uppercase tracking-wider text-slate-500 text-left">
                            <tr><th className="pb-1 font-medium">Account</th><th className="text-right font-medium">Quantity</th><th className="text-right font-medium">Each</th><th className="text-right font-medium">Total</th><th className="text-right font-medium">Wallet now → after</th></tr>
                        </thead>
                        <tbody>
                            {plan.lines.map((l) => (
                                <tr key={l.steamid} className={`border-t border-white/5 ${!l.payable ? 'opacity-60' : ''}`}>
                                    <td className="py-1.5 text-slate-100">
                                        {l.account_name}
                                        {l.problem && <span className={`block text-xs ${l.payable ? 'text-amber-300' : 'text-red-300'}`}>{l.problem}{!l.payable && ' — skipped'}</span>}
                                        {l.test && <span className={`block text-xs ${l.test === 'passed' ? 'text-sky-300' : 'text-red-300'}`}>
                                            {l.test === 'passed' ? 'Test passed' : `Test failed: ${l.test_result} — not paid`}
                                        </span>}
                                    </td>
                                    <td className="text-right tabular-nums">{l.quantity || '—'}</td>
                                    <td className="text-right tabular-nums text-slate-400">{money(l.unit_price, l.currency)}</td>
                                    <td className="text-right tabular-nums text-slate-100">{money(l.total, l.currency)}</td>
                                    <td className="text-right tabular-nums text-slate-400 whitespace-nowrap">
                                        {money(l.balance, l.currency)}{l.balance_after != null && <> → <span className="text-slate-200">{money(l.balance_after, l.currency)}</span></>}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                {plan.accounts_payable === 0 ? (
                    <p className="text-red-300 mb-3">Nothing can be bought with these wallets.</p>
                ) : (
                    <div className="rounded-lg border border-amber-500/30 bg-amber-500/[0.06] px-3 py-2 mb-4 text-slate-200">
                        <span className="font-medium">{plan.units_text}</span> on {plan.accounts_payable} account{plan.accounts_payable > 1 ? 's' : ''} for{' '}
                        <span className="font-medium text-amber-100">{totalsText(plan.totals_by_currency)}</span>
                        {otherCurrency && plan.usd_total > 0 && <span className="text-slate-400"> (≈ ${plan.usd_total.toFixed(2)})</span>}
                        {plan.skipped > 0 && <span className="block text-xs text-slate-400 mt-0.5">{plan.skipped} account{plan.skipped > 1 ? 's' : ''} skipped.</span>}
                        {!expired && plan.expires_at && <span className="block text-xs text-slate-500 mt-0.5">This review is valid until {clock(plan.expires_at)}.</span>}
                    </div>
                )}
                {!expired && plan.accounts_payable > 0 && (
                    <p className={`mb-4 text-xs ${plan.executable ? 'text-sky-300' : 'text-slate-400'}`} role="status">
                        {plan.executable
                            ? `The test passed: Pay buys ${plan.to_pay.units_text} on ${plan.to_pay.accounts_payable} account${plan.to_pay.accounts_payable > 1 ? 's' : ''} (only those whose test passed).`
                            : plan.dry_run.state === 'failed'
                                ? `The test did not pass${plan.dry_run.error ? ` (${plan.dry_run.error})` : ''}: nothing can be paid. Test again, or close and review again.`
                                : 'Test without paying first: Pay is offered once the test of this order has passed.'}
                    </p>
                )}
                {expired && (
                    <p className="mb-4 flex items-start gap-2 text-amber-300" role="status">
                        <AlertTriangle size={15} className="mt-0.5 shrink-0" /> This review is older than 30 minutes: the wallets must be read again before paying.
                    </p>
                )}
                <div className="flex flex-wrap items-center justify-end gap-2">
                    <button type="button" onClick={onClose} className="px-3 py-2 rounded-lg text-slate-300 hover:bg-white/5">Cancel</button>
                    {expired ? (
                        <button type="button" onClick={onReviewAgain} disabled={busy}
                            className="px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-medium disabled:opacity-50">Review again</button>
                    ) : (
                        <>
                            <button type="button" onClick={() => onPay(true)} disabled={!plan.can_test || submitting}
                                title="Goes through every check with Steam and cancels before approving: nothing is paid"
                                className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-white/15 text-slate-200 hover:bg-white/5 disabled:opacity-50">
                                <FlaskConical size={14} /> Test without paying
                            </button>
                            <button type="button" onClick={() => onPay(false)} disabled={!plan.executable || submitting}
                                title={plan.executable ? undefined : 'Test without paying first'}
                                className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium disabled:opacity-50">
                                <ShoppingCart size={14} /> Pay {totalsText(plan.executable ? plan.to_pay.totals_by_currency : plan.totals_by_currency)}
                            </button>
                        </>
                    )}
                </div>
            </Modal>
        );
    }

    // running / results: one row per order of the job, with its live step or its outcome
    const job = order.job || {};
    const done = order.stage === 'results';
    const dryRun = Boolean(job.dry_run);
    const outcomes = Object.fromEntries((job.result?.accounts || []).map((a) => [a.steamid, a]));
    const steps = job.steps || [];
    const at = steps.findIndex((step) => step.key === job.step);
    return (
        <Modal title={done ? (dryRun ? 'Test finished' : 'Purchase finished') : (dryRun ? 'Testing (nothing is paid)' : 'Buying…')}
            onClose={onClose} closable={done}>
            {!done && !dryRun && <p className="mb-3 text-xs text-slate-400">Keep this page open or come back later: the purchase runs on the server either way.</p>}
            {done && !dryRun && !order.lost && (
                <p className={`mb-3 flex items-center gap-2 ${job.result?.paid_units ? 'text-emerald-300' : 'text-amber-300'}`}>
                    {job.result?.paid_units ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
                    {job.result?.paid_units ? `Bought ${job.result.paid_text}.` : 'Nothing was bought.'}
                </p>
            )}
            {done && (order.lost || job.error) && <p className="mb-3 text-red-300">{order.lost || job.error}</p>}
            <DialogError error={error} />
            <ul className="space-y-3">
                {(job.orders || []).map((line) => {
                    const result = outcomes[line.steamid];
                    const current = job.running && job.account === line.steamid;
                    const good = result && (result.outcome === 'bought' || result.outcome === 'test_passed');
                    return (
                        <li key={line.steamid} className="rounded-lg border border-white/10 bg-black/20 p-3">
                            <div className="flex items-center gap-2">
                                {result ? (good ? <CheckCircle2 size={16} className="text-emerald-400" /> : <AlertTriangle size={16} className="text-red-400" />)
                                    : current ? <Loader2 size={16} className="animate-spin text-amber-400" /> : <Circle size={16} className="text-slate-600" />}
                                <span className="flex-1 text-slate-100">{line.account_name}</span>
                                <span className="tabular-nums text-slate-400">{line.quantity} × {money(line.unit_price, line.currency)}</span>
                            </div>
                            {current && !result && (
                                <ol className="mt-2 ml-6 flex flex-wrap gap-x-3 gap-y-1 text-xs" aria-label="Steps">
                                    {steps.map((step, index) => (
                                        <li key={step.key} className={index < at ? 'text-emerald-300' : index === at ? 'text-amber-200' : 'text-slate-600'}>
                                            {index < at ? '✓ ' : ''}{step.label}
                                        </li>
                                    ))}
                                </ol>
                            )}
                            {!current && !result && (
                                <p className="mt-1 ml-6 text-xs text-slate-500">
                                    {!done ? 'Waiting' : order.lost ? 'Unknown: look at the purchase log' : 'Not started'}
                                </p>
                            )}
                            {result && (
                                <p className={`mt-1 ml-6 text-xs ${result.outcome === 'failed' ? 'text-red-300' : OUTCOME_COLOUR[result.outcome]}`}>
                                    {result.summary}
                                </p>
                            )}
                            {result?.can_deliver_again && (
                                <button type="button" onClick={() => onDeliverAgain(result.at)} disabled={busy}
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

// The item comes from the address (/store-catalogue/buy/<entry>); /buy-storage-units has none.
// Keyed by it, so another item starts with a fresh page.
const StorePurchasePage = () => {
    const { item = 'casket' } = useParams();         // the price sheet's name for the Storage Unit
    return <StorageShop key={item} item={item} />;
};

export default StorePurchasePage;
