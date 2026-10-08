import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { LayoutDashboard, RefreshCw, Layers, Settings, Search, Users, AlertTriangle, Pickaxe, ExternalLink, BadgeCheck, Coins } from 'lucide-react';
import DealsTable from '../../components/carddeals/DealsTable';
import SettingsPanel from '../../components/carddeals/SettingsPanel';
import FarmingPanel from '../../components/carddeals/FarmingPanel';
import SellingPanel from '../../components/carddeals/SellingPanel';
import BuyPanel from '../../components/carddeals/BuyPanel';
import InfoTip from '../../components/gjallarhorn/InfoTip';

// Andvari — games whose Steam trading-card drops resell for more than the game
// costs. Two scopes: "On sale" (scanned first, where most deals are) and "Full
// price" (optional, slower, refreshed daily). Backend: /api/huginn/card-deals*.
const SCOPES = [
    { id: 'sale', label: 'On sale' },
    { id: 'full', label: 'Full price' },
    { id: 'all', label: 'All' },
];

// The card-value switch: overrides Settings → Card value for this page only,
// remembered in this browser. Without a remembered choice it starts from the
// saved setting (Listing → Listings, otherwise Buy orders).
const VALUATIONS = [
    { id: 'instant', label: 'Buy orders', activeClass: 'bg-emerald-500/20 text-emerald-300' },
    { id: 'listing', label: 'Listings', activeClass: 'bg-sky-500/20 text-sky-300' },
];
const VALUATION_STORAGE_KEY = 'andvari.valuation';

const savedValuation = () => {
    try {
        const saved = window.localStorage.getItem(VALUATION_STORAGE_KEY);
        return VALUATIONS.some((v) => v.id === saved) ? saved : null;
    } catch {
        return null;
    }
};

const money = (v) => `$${Number(v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const ago = (epochSeconds) => {
    if (!epochSeconds) return 'never';
    const minutes = Math.round((Date.now() / 1000 - epochSeconds) / 60);
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 48) return `${hours} h ago`;
    return `${Math.round(hours / 24)} days ago`;
};

const Stat = ({ label, value, tone, hint }) => (
    <InfoTip tip={hint}>
        <div className="px-4 py-2.5 rounded-xl bg-odin-blue/40 border border-white/10">
            <p className="text-[10px] uppercase tracking-wider text-slate-500">{label}</p>
            <p className={`text-lg font-bold tabular-nums ${tone || 'text-slate-200'}`}>{value}</p>
        </div>
    </InfoTip>
);

// ASF's own web UI, published on this Mac only (docker-compose.yml). It asks for
// the ASF API password once: `make asf-password` copies it to the clipboard.
const ASF_UI_URL = `${window.location.protocol}//${window.location.hostname}:1242/`;

const HOW_IT_WORKS = 'Buying a game with trading cards gives half its card set (rounded up) as drops '
    + 'while you play it. If those cards sell for more than the game costs (after Steam’s ~15% market fee), '
    + 'the game pays for itself — on every account that does not own it yet. Cards are valued at their live '
    + 'buy orders by default (what they sell for right now); the Buy orders / Listings switch above the table changes that. Caveats: drops need playtime '
    + '(idling works); free or giveaway copies drop nothing; accounts that never spent $5 on Steam get no '
    + 'drops; selling many copies of the same card pushes its price down.';

const CardDeals = () => {
    const [scope, setScope] = useState('sale');
    const [showLosing, setShowLosing] = useState(false);
    const [valuation, setValuation] = useState(savedValuation);
    const [search, setSearch] = useState('');
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [showSettings, setShowSettings] = useState(false);
    const [showAccounts, setShowAccounts] = useState(false);
    const [showFarming, setShowFarming] = useState(false);
    const [farming, setFarming] = useState(null);
    const [farmingBusy, setFarmingBusy] = useState(false);
    const [showSelling, setShowSelling] = useState(false);
    // 'deals' | 'buy'; /huginn/card-deals#buy opens the Buy games tab directly.
    const [tab, setTab] = useState(() => (window.location.hash === '#buy' ? 'buy' : 'deals'));
    const [selling, setSelling] = useState(null);
    const pollRef = useRef(null);

    // ASF card farming status: refreshed every 30 seconds (cheap: Heimdall answers
    // from its last ASF poll).
    const fetchFarming = useCallback(() => fetch('/api/huginn/card-deals/farming')
        .then(async (r) => {
            const body = await r.json().catch(() => null);
            if (!r.ok || !body) throw new Error(body?.error || `HTTP ${r.status}`);
            return body;
        })
        .then(setFarming)
        .catch((e) => setFarming({ enabled: true, reachable: false, error: e.message === 'Failed to fetch' ? 'backend unreachable' : e.message, accounts: [] })), []);
    useEffect(() => {
        fetchFarming();
        const timer = setInterval(fetchFarming, 30000);
        return () => clearInterval(timer);
    }, [fetchFarming]);
    const farmingAction = (steamid, action) => {
        setFarmingBusy(true);
        fetch(`/api/huginn/card-deals/farming/${steamid}/${action}`, { method: 'POST' })
            .then(async (r) => {
                const d = await r.json().catch(() => ({}));
                if (!r.ok || d.error) setError(`Card farming: ${d.error || `request failed (HTTP ${r.status})`}`);
            })
            .catch((e) => setError(`Card farming: ${e.message}`))
            .finally(() => { setFarmingBusy(false); fetchFarming(); });
    };
    const farmingTotals = farming?.totals;

    // Card auto-sell status: refreshed every 30 seconds.
    const fetchSelling = useCallback(() => fetch('/api/huginn/card-deals/selling')
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => { if (d) setSelling(d); })
        .catch(() => {}), []);
    useEffect(() => {
        fetchSelling();
        const timer = setInterval(fetchSelling, 30000);
        return () => clearInterval(timer);
    }, [fetchSelling]);

    const fetchDeals = useCallback(() => {
        if (!valuation) return Promise.resolve();
        const params = new URLSearchParams({ scope, valuation });
        if (showLosing) params.set('include_unprofitable', '1');
        return fetch(`/api/huginn/card-deals?${params.toString()}`)
            .then((r) => r.json().then((d) => ({ ok: r.ok, d })))
            .then(({ ok, d }) => {
                if (!ok || d.error) { setError(d.error || 'Could not load deals.'); return; }
                setError(null);
                setData(d);
            })
            .catch(() => setError('Could not reach the backend.'));
    }, [scope, showLosing, valuation]);

    useEffect(() => { fetchDeals(); }, [fetchDeals]);

    useEffect(() => {
        if (valuation) return;
        fetch('/api/huginn/card-deals/config')
            .then((r) => (r.ok ? r.json() : null))
            .then((config) => setValuation(config?.card_deals_valuation === 'listing' ? 'listing' : 'instant'))
            .catch(() => setValuation('instant'));
    }, [valuation]);

    const chooseValuation = (next) => {
        setValuation(next);
        try { window.localStorage.setItem(VALUATION_STORAGE_KEY, next); } catch { /* storage blocked: the switch still works */ }
    };

    // Poll while a scan runs so progress and new rows fill in by themselves.
    useEffect(() => {
        clearTimeout(pollRef.current);
        if (data?.job?.running) pollRef.current = setTimeout(() => fetchDeals(), 4000);
        return () => clearTimeout(pollRef.current);
    }, [data, fetchDeals]);

    const startScan = (includeFullPrice) => {
        fetch('/api/huginn/card-deals/scan', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ include_full_price: includeFullPrice }),
        })
            .then(() => fetchDeals())
            .catch(() => setError('Could not start the scan.'));
    };

    const checkBuyOrders = () => {
        fetch('/api/huginn/card-deals/buy-orders', { method: 'POST' })
            .then((r) => r.json().then((job) => ({ ok: r.ok, job })))
            .then(({ ok, job }) => {
                if (!ok) setError('Could not start the buy-order check.');
                else if (job.kind !== 'buy_orders') setError('A scan is already running; it checks buy orders too.');
                else setError(null);
                fetchDeals();
            })
            .catch(() => setError('Could not start the buy-order check.'));
    };
    const jobName = (kind) => (kind === 'buy_orders' ? 'buy-order check' : 'scan');

    const job = data?.job;
    const running = !!job?.running;
    const summary = data?.summary;
    const scopeCounts = summary?.scopes || {};

    const rows = useMemo(() => {
        const all = data?.deals || [];
        const needle = search.trim().toLowerCase();
        return needle ? all.filter((r) => r.name.toLowerCase().includes(needle) || r.app_id === needle) : all;
    }, [data, search]);

    const accountProblems = (data?.accounts || []).filter((a) => a.error);
    const progress = job && job.total ? Math.min(100, Math.round((job.done / job.total) * 100)) : null;

    return (
        <div className="h-screen bg-odin-dark flex flex-col overflow-hidden">
            {/* Header */}
            <div className="shrink-0 border-b border-white/5 bg-odin-blue/50 px-6 py-4 flex items-center gap-3 flex-wrap">
                <Link to="/" className="flex items-center gap-1.5 text-sm text-slate-400 hover:text-white transition-colors shrink-0">
                    <LayoutDashboard size={15} /> Dashboard
                </Link>
                <span className="text-white/20">/</span>
                <Link to="/huginn" className="text-sm text-slate-400 hover:text-white transition-colors">Huginn</Link>
                <span className="text-white/20">/</span>
                <h1 className="text-lg font-bold font-serif text-amber-100 flex items-center gap-2">
                    <Layers size={17} className="text-amber-500" /> Andvari
                    <span className="text-sm font-normal text-slate-400 font-sans">Trading-card deals</span>
                </h1>
                <InfoTip tip={HOW_IT_WORKS}>
                    <span className="text-xs text-slate-500 border border-white/10 rounded-lg px-2 py-1 cursor-help">How it works</span>
                </InfoTip>
                <div className="flex rounded-lg border border-white/10 overflow-hidden text-sm">
                    {[['deals', 'Deals'], ['buy', 'Buy games']].map(([id, label]) => (
                        <button key={id} type="button" onClick={() => setTab(id)}
                            className={`px-3 py-1.5 ${tab === id ? 'bg-amber-500/20 text-amber-200' : 'text-slate-400 hover:bg-white/5'}`}>
                            {label}
                        </button>
                    ))}
                </div>

                <div className="ml-auto flex items-center gap-2 flex-wrap">
                    <InfoTip tip="Quick scan: discounted games only (about a minute of store pages, then the Market check of the shortlist).">
                        <button type="button" onClick={() => startScan(false)} disabled={running}
                            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-sm font-medium disabled:opacity-50">
                            <RefreshCw size={14} className={running ? 'animate-spin' : ''} /> Scan on sale
                        </button>
                    </InfoTip>
                    <InfoTip tip="Full scan: on-sale games first, then every full-price game with cards. Takes much longer (the Steam Market is rate-limited); the table fills in as it goes.">
                        <button type="button" onClick={() => startScan(true)} disabled={running}
                            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-amber-500/40 text-amber-200 hover:bg-amber-500/10 text-sm font-medium disabled:opacity-50">
                            <RefreshCw size={14} className={running ? 'animate-spin' : ''} /> Scan sale + full price
                        </button>
                    </InfoTip>
                    <InfoTip tip="Check card prices only, for games already scanned: no store pages, regional prices or account checks. First the Steam Market check of shortlisted games not checked in the last day (up to 80 on-sale + 40 full-price games), then their buy orders: waiting games first, best listing return first, and a game is dropped as soon as its buy orders cannot pay for it (up to 200 + 120 order-book requests, retries included).">
                        <button type="button" onClick={checkBuyOrders} disabled={running || !data?.last_scan}
                            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-emerald-500/40 text-emerald-200 hover:bg-emerald-500/10 text-sm font-medium disabled:opacity-50">
                            <BadgeCheck size={14} /> Check buy orders
                        </button>
                    </InfoTip>
                    {farming?.enabled && (
                        <InfoTip tip="Open ArchiSteamFarm’s own UI (this Mac only). First time it asks for the ASF password: run “make asf-password” in the repository to copy it. Heimdall re-applies the safety settings (no trading, no chat commands, no public listing) within a minute if they are changed there.">
                            <a href={ASF_UI_URL} target="_blank" rel="noopener noreferrer"
                                className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm border border-white/10 text-slate-300 hover:bg-white/5">
                                <ExternalLink size={14} /> ASF UI
                            </a>
                        </InfoTip>
                    )}
                    <button type="button" onClick={() => setShowSettings((v) => !v)}
                        className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm border transition-colors ${showSettings ? 'border-amber-500/40 text-amber-200 bg-amber-500/10' : 'border-white/10 text-slate-300 hover:bg-white/5'}`}>
                        <Settings size={14} /> Settings
                    </button>
                </div>
            </div>

            <div className="flex-1 flex flex-col gap-3 p-6 overflow-hidden max-w-[96rem] w-full mx-auto">
                {tab === 'buy' ? <BuyPanel /> : (<>
                {showSettings && (
                    <div className="shrink-0">
                        <SettingsPanel onClose={() => setShowSettings(false)} onSaved={fetchDeals} />
                    </div>
                )}

                {/* Scan progress */}
                {running && (
                    <div className="shrink-0 rounded-xl border border-amber-500/20 bg-amber-500/5 px-4 py-2.5 text-sm">
                        <div className="flex items-center gap-2 text-amber-200">
                            <RefreshCw size={13} className="animate-spin" />
                            <span>{job.kind === 'buy_orders' ? 'Checking buy orders' : 'Scanning'}: {job.phase}{job.scope ? ` · ${job.scope === 'sale' ? 'on sale' : 'full price'}` : ''}</span>
                            {job.total ? <span className="text-amber-200/60 tabular-nums">{job.done}/{job.total}</span> : null}
                            {job.message && <span className="text-amber-400/80 text-xs ml-2">{job.message}</span>}
                        </div>
                        {progress != null && (
                            <div className="mt-2 h-1 rounded-full bg-white/10 overflow-hidden">
                                <div className="h-full bg-amber-500 transition-all" style={{ width: `${progress}%` }} />
                            </div>
                        )}
                    </div>
                )}
                {!running && job?.error && (
                    <div className="shrink-0 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-2.5 text-sm text-red-300 flex items-center gap-2">
                        <AlertTriangle size={14} /> Last {jobName(job.kind)} failed: {job.error}
                    </div>
                )}
                {!running && !job?.error && job?.message && (
                    <div className="shrink-0 rounded-xl border border-amber-500/20 bg-amber-500/5 px-4 py-2 text-xs text-amber-300/90 flex items-center gap-2">
                        <AlertTriangle size={13} /> {job.message}
                    </div>
                )}
                {error && <div className="shrink-0 text-sm text-red-400">{error}</div>}

                {/* Summary */}
                <div className="shrink-0 flex flex-wrap items-stretch gap-2">
                    <Stat label="Deals" value={summary ? summary.profitable.toLocaleString() : '—'} tone="text-emerald-400"
                        hint="Games in this view that at least one account (not owning it yet) can buy at a profit, at its own country’s price." />
                    <Stat label="Checked on Market" value={summary ? summary.verified.toLocaleString() : '—'}
                        hint="Of the deals, how many were priced card by card on the Steam Market (buy orders or listings — see the Source column); the rest are SteamCardExchange estimates." />
                    {summary?.valuation === 'both' && (
                        <Stat label="Sell-now deals" value={summary.sell_now_deals.toLocaleString()} tone="text-emerald-300"
                            hint="Deals that are profitable even selling the drops straight to buy orders. The rest are list-only: profitable only when you list the cards at the sell price and wait for buyers." />
                    )}
                    {summary?.valuation === 'instant' && (
                        <Stat label="Awaiting buy orders" value={summary.awaiting_buy_orders.toLocaleString()} tone="text-sky-300"
                            hint={`Games that look profitable at listing prices but whose buy orders have not all been checked yet. Only buy orders can confirm a deal here (listing prices were measured 5–50× too optimistic), so these are not counted until “Check buy orders” or a scan gets to them. Tick “Show losing games too” to see them.${summary.ruled_out_at_buy_orders ? ` Another ${summary.ruled_out_at_buy_orders} listing-price deals are already ruled out: their buy orders cannot pay for them.` : ''}`} />
                    )}
                    <Stat label="Profit, all accounts" value={summary ? money(summary.total_profit_all_accounts) : '—'} tone="text-emerald-300"
                        hint="If every account that does not own each profitable game buys one copy. Market impact not included." />
                    <Stat label="Last scan" value={ago(data?.last_scan?.finished_at)}
                        hint={`Card set prices (SteamCardExchange) fetched ${ago(data?.feed_fetched_at)}. Store countries: ${(summary?.countries || []).join(', ') || '—'}.`} />
                    <button type="button" onClick={() => setShowAccounts((v) => !v)}
                        className="px-4 py-2.5 rounded-xl bg-odin-blue/40 border border-white/10 text-left hover:border-white/20">
                        <p className="text-[10px] uppercase tracking-wider text-slate-500 flex items-center gap-1"><Users size={10} /> Accounts</p>
                        <p className={`text-lg font-bold tabular-nums ${accountProblems.length ? 'text-amber-400' : 'text-slate-200'}`}>
                            {(data?.accounts || []).length}{accountProblems.length ? ` (${accountProblems.length} issue${accountProblems.length > 1 ? 's' : ''})` : ''}
                        </p>
                    </button>
                    <button type="button" onClick={() => setShowFarming((v) => !v)}
                        title="ArchiSteamFarm idles the games so their cards drop (see Yggdrasil/asf/README.md)."
                        className="px-4 py-2.5 rounded-xl bg-odin-blue/40 border border-white/10 text-left hover:border-white/20">
                        <p className="text-[10px] uppercase tracking-wider text-slate-500 flex items-center gap-1"><Pickaxe size={10} /> Card farming</p>
                        <p className={`text-lg font-bold tabular-nums ${!farming?.enabled || !farming?.reachable || farmingTotals?.needs_attention ? 'text-amber-400' : 'text-slate-200'}`}>
                            {!farming ? '—'
                                : !farming.enabled ? 'off'
                                    : !farming.reachable ? 'ASF down'
                                        : `${farmingTotals.farming} farming · ${farmingTotals.cards_remaining} cards`}
                            {farmingTotals?.needs_attention ? ` (${farmingTotals.needs_attention} need you)` : ''}
                        </p>
                    </button>
                    <button type="button" onClick={() => setShowSelling((v) => !v)}
                        title="List dropped trading cards at the highest price that still sells, on every account."
                        className="px-4 py-2.5 rounded-xl bg-odin-blue/40 border border-white/10 text-left hover:border-white/20">
                        <p className="text-[10px] uppercase tracking-wider text-slate-500 flex items-center gap-1"><Coins size={10} /> Card selling</p>
                        <p className={`text-lg font-bold tabular-nums ${selling?.enabled ? 'text-emerald-300' : 'text-slate-400'}`}>
                            {!selling ? '—' : selling.enabled ? `on · ${selling.statistics?.listed ?? 0} listed` : 'off'}
                        </p>
                    </button>
                </div>

                {showFarming && (
                    <div className="shrink-0">
                        <FarmingPanel farming={farming} onAction={farmingAction} busy={farmingBusy} />
                    </div>
                )}

                {showSelling && (
                    <div className="shrink-0">
                        <SellingPanel selling={selling} onSaved={fetchSelling} />
                    </div>
                )}

                {showAccounts && (
                    <div className="shrink-0 max-h-48 overflow-auto custom-scrollbar rounded-xl border border-white/10 bg-black/20 p-3 grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3 text-xs">
                        {(data?.accounts || []).map((a) => (
                            <div key={a.account_name} className="flex items-center justify-between gap-2 px-2 py-1 rounded bg-white/5">
                                <span className="text-slate-300 truncate">{a.account_name}</span>
                                <span className="text-slate-500 shrink-0">
                                    {a.country || '??'} · {a.owned_games ?? '?'} games
                                    {a.card_drops_remaining ? <span className="text-amber-300"> · {a.card_drops_remaining} drops left</span> : null}
                                    {a.error && <span className="text-red-400" title={a.error}> · {a.error}</span>}
                                </span>
                            </div>
                        ))}
                    </div>
                )}

                {/* Scope tabs + filters */}
                <div className="shrink-0 flex items-center gap-3 flex-wrap">
                    <div className="flex rounded-lg border border-white/10 overflow-hidden">
                        {SCOPES.map((s) => {
                            const counts = s.id === 'all'
                                ? {
                                    profitable: (scopeCounts.sale?.profitable || 0) + (scopeCounts.full?.profitable || 0),
                                    awaiting_buy_orders: (scopeCounts.sale?.awaiting_buy_orders || 0) + (scopeCounts.full?.awaiting_buy_orders || 0),
                                }
                                : scopeCounts[s.id];
                            return (
                                <button key={s.id} type="button" onClick={() => setScope(s.id)}
                                    className={`px-3 py-1.5 text-xs font-medium transition-colors ${scope === s.id ? 'bg-amber-500/20 text-amber-200' : 'text-slate-400 hover:text-slate-200 hover:bg-white/5'}`}>
                                    {s.label}
                                    {counts && <span className="ml-1.5 text-emerald-400/80 tabular-nums">{counts.profitable}</span>}
                                    {counts?.awaiting_buy_orders > 0 && (
                                        <span className="ml-1 text-sky-300/80 tabular-nums" title="awaiting their buy-order check">+{counts.awaiting_buy_orders}?</span>
                                    )}
                                </button>
                            );
                        })}
                    </div>
                    <InfoTip tip="How dropped cards are valued in this view. Buy orders: sold straight into the highest buy orders — what they fetch right now. Listings: listed one cent under the lowest ask — more money, but you wait for buyers. Only this view changes; alerts keep the Settings → Card value choice.">
                        <div className="flex rounded-lg border border-white/10 overflow-hidden">
                            {VALUATIONS.map((v) => (
                                <button key={v.id} type="button" onClick={() => chooseValuation(v.id)}
                                    className={`px-3 py-1.5 text-xs font-medium transition-colors ${valuation === v.id ? v.activeClass : 'text-slate-400 hover:text-slate-200 hover:bg-white/5'}`}>
                                    {v.label}
                                </button>
                            ))}
                        </div>
                    </InfoTip>
                    {scope === 'full' && !scopeCounts.full?.scanned_at && (
                        <span className="text-xs text-slate-500">Full price not scanned yet: use “Scan sale + full price”.</span>
                    )}
                    {scope !== 'all' && scopeCounts[scope]?.scanned_at && (
                        <span className="text-xs text-slate-400">store data {ago(scopeCounts[scope].scanned_at)}</span>
                    )}
                    <label className="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer">
                        <input type="checkbox" checked={showLosing} onChange={(e) => setShowLosing(e.target.checked)} className="accent-amber-500" />
                        Show losing games too
                    </label>
                    <div className="ml-auto flex items-center gap-1.5 bg-black/30 border border-white/10 rounded-lg px-2.5 py-1.5">
                        <Search size={13} className="text-slate-500" />
                        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Find a game"
                            className="bg-transparent text-sm text-slate-200 outline-none w-44" />
                    </div>
                </div>

                <DealsTable
                    rows={rows}
                    emptyText={!data?.last_scan
                        ? 'No scan yet — press “Scan on sale” to start.'
                        : summary?.awaiting_buy_orders
                            ? `No confirmed deals in this view. ${summary.awaiting_buy_orders} games look profitable at listing prices and are waiting for their buy-order check — press “Check buy orders”, or tick “Show losing games too” to see them.`
                            : valuation === 'instant'
                                ? 'No profitable games in this view right now. At buy-order prices (what cards sell for instantly) deals are rare; switch to Listings to see what patient selling could make.'
                                : 'No profitable games in this view right now, even at listing prices.'}
                />
                </>)}
            </div>
        </div>
    );
};

export default CardDeals;
