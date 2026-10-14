import { Fragment, useState, useEffect, useCallback, useMemo, useRef, useDeferredValue } from 'react';
import { Link } from 'react-router-dom';
import {
    Search, AlertTriangle, Loader2, ShoppingCart, ChevronRight, ChevronDown, TrendingUp, TrendingDown, Info,
    RefreshCw, ArrowUp, ArrowDown, Zap, Send, Clock, Tags,
} from 'lucide-react';
import { useColumnSort, sortRows } from '../draupnir/columnSort';
import InfoTip from '../gjallarhorn/InfoTip';
import ProfilePicker from '../arbitrage/ProfilePicker';
import PriceCell from '../arbitrage/PriceCell';
import FeeEditor from '../arbitrage/FeeEditor';
import ArbitrageFilters, { ActiveFilterChips } from '../arbitrage/ArbitrageFilters';
import { activeFilterCount, buildOptions, collectionByName, priceInRange, useStoredFilters } from '../../utils/arbitrageFilters';
import SteamMarketLink from '../SteamMarketLink';
import BuffMarketLink from '../BuffMarketLink';
import CSFloatMarketLink from '../CSFloatMarketLink';

// Store Catalogue → Arbitrage tab, laid out like Huginn's Arbitrage: a profile is
// "In-game store → market (autobuy or min)", default CSMoney (Trade) autobuy. The
// CSMoney (Trade) column is the indicator: what it pays at once, as a share of the store
// price; at the threshold (100% by default) the row signals BUY, meaning the item is worth
// buying in the in-game store and selling where the profile's market pays most.
// A recommendation board only: Buy opens the item's guarded buy page (dry run first).
// Backend: GET /api/ratatoskr/store-arbitrage?markets=… (non-blocking: it warms missing
// markets in the background, so the page polls while warming or refreshing),
// POST …/refresh (the Refresh button), POST …/alerts/send (Telegram "Send now"),
// GET …/options (markets, default set, indicator, alert switch). The morning routine
// refreshes everything after "Get all items" and posts the profitable items to Telegram.

const money = (v, digits = 2) => (v == null ? '—' : `${v < 0 ? '−' : ''}$${Math.abs(v).toFixed(digits)}`);
const percent = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`);
const tone = (v) => (v == null ? 'text-slate-500' : v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-slate-400');
const ago = (epochSeconds, now) => {
    if (!epochSeconds || !now) return null;
    const minutes = Math.round((now / 1000 - epochSeconds) / 60);
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.round(minutes / 60);
    return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} days ago`;
};
const imageOf = (name) => `https://api.steamapis.com/image/item/730/${encodeURIComponent(name)}`;
const SIDE_LABEL = { instant: 'sell instantly', listing: 'list' };
const REASON_ORDER = ['cannot_trade', 'keys', 'passes', 'no_price'];
const GRID = 'grid grid-cols-[minmax(0,2.4fr)_52px_84px_100px_76px_76px_128px_104px_64px] gap-2';

const DEFAULT_PROFILE = 'CsMoneyTrade|instant';
const BEST = 'best';

// Remembered per browser: the profile and the switches. Filters have their own key.
const PREFERENCES_KEY = 'ratatoskr.storeArbitrage';
const FILTERS_KEY = 'ratatoskr.storeArbitrageFilters';
const loadPreferences = () => {
    try { return JSON.parse(localStorage.getItem(PREFERENCES_KEY)) || {}; } catch { return {}; }
};
const savePreferences = (preferences) => {
    try { localStorage.setItem(PREFERENCES_KEY, JSON.stringify(preferences)); } catch { /* private window: not remembered */ }
};

// The Filters popover: "Buy" is the store price, "Sell" what the profile's market pays.
const PRICE_SIDES = [
    ['buy', 'Store price', 'What the in-game store charges, in US dollars', 'Store'],
    ['sell', 'Sell price', 'What the profile’s market pays, after its fee', 'Sell'],
];

// The offer a row is judged by under a profile: that market and side, or for a "Best
// market" profile the best real offer on that side (money only with "Cash only").
const sellOfferOf = (row, profile, cashOnly) => {
    if (!profile) return null;
    if (profile.market === BEST) {
        // Offers come sorted by what you get, best first.
        return row.offers.find((o) => o.side === profile.side && !o.suspicious && (!cashOnly || !o.balance)) || null;
    }
    return row.offers.find((o) => o.market === profile.market && o.side === profile.side) || null;
};

const Badge = ({ className, title, children }) => (
    <span title={title} className={`px-1 rounded border text-[10px] whitespace-nowrap ${className}`}>{children}</span>
);

const Sparkline = ({ series, cost }) => {
    if (!series || series.length < 2) return null;
    const low = Math.min(...series, cost);
    const high = Math.max(...series, cost);
    const span = high - low || 1;
    const x = (index) => (index / (series.length - 1)) * 56;
    const y = (value) => 16 - ((value - low) / span) * 16;
    return (
        <svg width="56" height="16" className="block" aria-hidden="true">
            <line x1="0" x2="56" y1={y(cost)} y2={y(cost)} stroke="currentColor" className="text-slate-600" strokeDasharray="2 2" />
            <polyline fill="none" strokeWidth="1.2" stroke="currentColor" className="text-amber-400"
                points={series.map((value, index) => `${x(index)},${y(value)}`).join(' ')} />
        </svg>
    );
};

// CSMoney (Trade)'s instant offer as a share of the store price, and the BUY signal.
const IndicatorCell = ({ indicator, signalPct, display }) => {
    if (!indicator) {
        return <span className="text-right text-slate-600 text-sm" title={`${display} has no buy order for this item`}>—</span>;
    }
    const near = !indicator.signal && indicator.pct_of_store >= signalPct * 0.9;
    return (
        <span className="flex flex-col items-end leading-tight"
            title={`${display} pays ${money(indicator.net, 3)} at once (${indicator.count ?? '?'} offers), ${indicator.pct_of_store}% of the store price. The signal fires at ${signalPct}%.${indicator.balance ? ` It pays in ${indicator.balance}.` : ''}`}>
            <span className="inline-flex items-center gap-1.5">
                {indicator.signal && (
                    <span className="px-1.5 rounded bg-emerald-500/20 border border-emerald-500/50 text-emerald-300 text-[10px] font-bold tracking-wider">BUY</span>
                )}
                <span className={`text-base tabular-nums font-semibold ${indicator.signal ? 'text-emerald-300' : near ? 'text-amber-300' : 'text-slate-400'}`}>
                    {indicator.pct_of_store.toFixed(0)}%
                </span>
            </span>
            <span className="text-[10px] text-slate-500 tabular-nums">{money(indicator.net)}{indicator.count != null ? ` · ${indicator.count} offers` : ''}</span>
        </span>
    );
};

const StoreArbitrage = () => {
    const [preferences] = useState(loadPreferences);
    const [options, setOptions] = useState(null);
    const [huginnMarkets, setHuginnMarkets] = useState([]);      // the Fees editor's list
    const [profileId, setProfileId] = useState(preferences.profile || DEFAULT_PROFILE);
    const [cashOnly, setCashOnly] = useState(preferences.cashOnly ?? false);
    const [profitableOnly, setProfitableOnly] = useState(preferences.profitableOnly ?? true);
    const [signalsOnly, setSignalsOnly] = useState(preferences.signalsOnly ?? false);
    const [search, setSearch] = useState('');
    const [category, setCategory] = useState('all');
    const [filters, setFilters] = useStoredFilters(FILTERS_KEY);
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [answers, setAnswers] = useState(0);   // every finished request, so a failed one still re-arms polling
    const [open, setOpen] = useState(() => new Set());
    const [showExcluded, setShowExcluded] = useState(false);
    const [byHash, setByHash] = useState(null);
    const [now, setNow] = useState(0);
    const [refreshNote, setRefreshNote] = useState(null);
    const [signalDraft, setSignalDraft] = useState(null);       // null: show the server's value
    const [alerts, setAlerts] = useState(null);
    const [alertNote, setAlertNote] = useState(null);
    const [sending, setSending] = useState(false);
    const [sheetStartedAt, setSheetStartedAt] = useState(null);  // our "Read store prices" job
    const [copiedName, setCopiedName] = useState(null);
    const deferredSearch = useDeferredValue(search);
    const sort = useColumnSort();
    const requests = useRef(0);            // only the newest answer is kept

    const refreshHuginnMarkets = useCallback(() => fetch('/api/huginn/markets')
        .then((r) => (r.ok ? r.json() : null))
        .then((list) => { if (Array.isArray(list)) setHuginnMarkets(list); })
        .catch(() => {}), []);

    useEffect(() => {
        fetch('/api/ratatoskr/store-arbitrage/options')
            .then((r) => r.json())
            .then((o) => { setOptions(o); setAlerts(o.alerts || null); })
            .catch(() => setError('Could not reach the backend.'));
        // Owned counts, accounts and collections come from Huginn's "Get all items".
        fetch('/api/huginn/scan/cache')
            .then((r) => (r.ok ? r.json() : null))
            .then((scan) => { if (scan?.by_hash) setByHash(scan.by_hash); })
            .catch(() => {});
        refreshHuginnMarkets();
    }, [refreshHuginnMarkets]);

    // A clock for "read 3 min ago" (Date.now() is never read during render).
    useEffect(() => {
        const tick = () => setNow(Date.now());
        tick();
        const timer = setInterval(tick, 30000);
        return () => clearInterval(timer);
    }, []);

    useEffect(() => { savePreferences({ profile: profileId, cashOnly, profitableOnly, signalsOnly }); },
        [profileId, cashOnly, profitableOnly, signalsOnly]);

    // Profiles: "In-game store → market", autobuy where the market has buy orders, min where it
    // has real listings, plus the two "Best market" profiles.
    const profiles = useMemo(() => {
        if (!options) return [];
        const from = { from: 'In-game store', fromSub: 'store price' };
        const list = [
            { ...from, id: `${BEST}|instant`, market: BEST, side: 'instant', to: 'Best market', toSub: 'autobuy' },
            { ...from, id: `${BEST}|listing`, market: BEST, side: 'listing', to: 'Best market', toSub: 'min' },
        ];
        // The indicator market first, the rest as the registry lists them.
        const ordered = [...options.markets].sort((a, b) => (b.id === options.indicator_market) - (a.id === options.indicator_market));
        for (const m of ordered) {
            if (m.instant) list.push({ ...from, id: `${m.id}|instant`, market: m.id, side: 'instant', to: m.display, toSub: 'autobuy', info: m });
            if (m.listings !== false) list.push({ ...from, id: `${m.id}|listing`, market: m.id, side: 'listing', to: m.display, toSub: 'min', info: m });
        }
        return list;
    }, [options]);
    // A remembered profile that no longer exists falls back to the default.
    const profile = profiles.find((p) => p.id === profileId) || profiles.find((p) => p.id === DEFAULT_PROFILE) || profiles[0] || null;

    // Every request prices the default set (every market with buy orders, the indicator, your
    // usual markets), plus the profile's market, so switching profile never waits for a pull.
    const requestMarkets = useMemo(() => {
        if (!options) return null;
        const set = [...options.default_markets];
        if (profile && profile.market !== BEST && !set.includes(profile.market)) set.push(profile.market);
        return set;
    }, [options, profile]);
    const marketsKey = requestMarkets ? requestMarkets.join(',') : null;

    const load = useCallback(() => {
        if (!marketsKey) return Promise.resolve();
        const number = ++requests.current;
        return fetch(`/api/ratatoskr/store-arbitrage?markets=${encodeURIComponent(marketsKey)}`)
            .then(async (r) => {
                const body = await r.json().catch(() => null);
                if (number !== requests.current) return;
                if (!r.ok || !body || (body.error && !body.rows)) setError(body?.error || `The board could not load (HTTP ${r.status}).`);
                else { setData(body); setError(null); }
            })
            .catch(() => { if (number === requests.current) setError('Could not reach the backend.'); })
            .finally(() => { if (number === requests.current) setAnswers((count) => count + 1); });
    }, [marketsKey]);

    useEffect(() => { load(); }, [load]);
    const warming = data?.status === 'warming' || data?.status === 'refreshing';
    // The next poll is armed after every answer, a failed one included: every 3 seconds while the
    // first prices load, every 10 while prices are refreshed (also the hourly background pull),
    // 30 after a failure, every 5 minutes otherwise.
    useEffect(() => {
        if (!answers) return undefined;
        const delay = data?.status === 'warming' ? 3000 : warming ? 10000 : error ? 30000 : 5 * 60 * 1000;
        const timer = setTimeout(load, delay);
        return () => clearTimeout(timer);
    }, [answers, warming, data?.status, error, load]);

    const refresh = () => {
        setRefreshNote(null);
        fetch('/api/ratatoskr/store-arbitrage/refresh', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ markets: requestMarkets }),
        })
            .then(async (r) => {
                const body = await r.json().catch(() => ({}));
                if (!r.ok) setRefreshNote(body.error || `Refresh failed (HTTP ${r.status})`);
                else if (!body.queued) setRefreshNote('Prices were pulled less than a minute ago.');
            })
            .catch(() => setRefreshNote('Could not reach the backend.'))
            .finally(load);
    };

    // "Read store prices": the game store's price sheet (one Ratatoskr login), followed by its
    // server start time until it finishes, then the board again.
    const readStorePrices = () => {
        setRefreshNote(null);
        fetch('/api/ratatoskr/store-catalogue/refresh', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
            .then((r) => r.json())
            .then((d) => { if (d.started) setSheetStartedAt(d.started_at); else setRefreshNote(d.error || 'The store prices could not be read.'); })
            .catch(() => setRefreshNote('Could not reach the backend.'));
    };
    useEffect(() => {
        if (sheetStartedAt == null) return undefined;
        let polls = 0;
        const timer = setInterval(() => {
            polls += 1;
            fetch('/api/ratatoskr/store-catalogue')
                .then((r) => r.json())
                .then((d) => {
                    const job = d.job || {};
                    const ours = job.started_at === sheetStartedAt;
                    // Still reading. A newer job (a purchase, the morning refresh) means ours ended;
                    // after 3 minutes we stop following it anyway.
                    if (ours && job.running && polls < 120) return;
                    setSheetStartedAt(null);
                    if (ours && job.error) setRefreshNote(`The store prices could not be read: ${job.error}`);
                    else if (ours && job.running) setRefreshNote('The store prices are still being read: look again in a minute.');
                    load();
                })
                .catch(() => {});
        }, 1500);
        return () => clearInterval(timer);
    }, [sheetStartedAt, load]);

    // Settings this tab owns (only these keys are posted).
    const saveSettings = (changes) => fetch('/api/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes),
    }).then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); });

    const commitSignal = () => {
        if (signalDraft == null) return;
        const value = parseFloat(String(signalDraft).replace(',', '.'));
        if (!Number.isFinite(value) || value <= 0 || value > 1000 || value === data?.signal_pct) { setSignalDraft(null); return; }
        saveSettings({ store_arbitrage_signal_pct: value })
            .then(() => load())
            .catch(() => setRefreshNote('The signal threshold could not be saved.'))
            .finally(() => setSignalDraft(null));
    };

    const toggleAlerts = () => {
        const enabled = !alerts?.enabled;
        saveSettings({ store_arbitrage_alerts_enabled: enabled })
            .then(() => setAlerts((a) => ({ ...(a || {}), enabled })))
            .catch(() => setAlertNote('Could not save the switch.'));
    };

    const sendNow = () => {
        if (sending) return;
        setSending(true);
        setAlertNote(null);
        fetch('/api/ratatoskr/store-arbitrage/alerts/send', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
            .then(async (r) => {
                const body = await r.json().catch(() => ({}));
                setAlertNote(body.sent ? `Sent: ${body.items} item${body.items === 1 ? '' : 's'}` : `Not sent: ${body.error || `HTTP ${r.status}`}`);
            })
            .catch(() => setAlertNote('Not sent: could not reach the backend.'))
            .finally(() => setSending(false));
    };

    const toggleOpen = (entry) => setOpen((current) => {
        const next = new Set(current);
        if (next.has(entry)) next.delete(entry); else next.add(entry);
        return next;
    });

    const copyItemName = (name) => {
        navigator.clipboard?.writeText(name).then(() => {
            setCopiedName(name);
            setTimeout(() => setCopiedName((c) => (c === name ? null : c)), 1200);
        }).catch(() => {});
    };

    // Every row priced under the current profile.
    const priced = useMemo(() => (data?.rows || []).map((r) => {
        const sell = sellOfferOf(r, profile, cashOnly);
        const real = sell && !sell.suspicious ? sell : null;
        return { ...r, sell, sellProfit: real ? real.net - r.cost : null, sellPct: real ? real.profit_pct : null,
            owned: byHash?.[r.name] || null };
    }), [data, profile, cashOnly, byHash]);

    const collections = useMemo(() => collectionByName(byHash), [byHash]);
    const accountOptions = useMemo(() => buildOptions(priced, (r) => (r.owned?.instances || []).map((i) => i.account_name),
        null, filters.accounts), [priced, filters.accounts]);
    const collectionOptions = useMemo(() => buildOptions(priced, (r) => [collections.get(r.name)],
        null, filters.collections), [priced, collections, filters.collections]);

    const categories = useMemo(() => [...new Set(priced.map((r) => r.category))], [priced]);
    const visible = useMemo(() => {
        const needle = deferredSearch.trim().toLowerCase();
        const accounts = filters.accounts.length ? new Set(filters.accounts) : null;
        const cols = filters.collections.length ? new Set(filters.collections) : null;
        const matching = priced.filter((r) => (category === 'all' || r.category === category)
            && (!needle || r.name.toLowerCase().includes(needle))
            && (!profitableOnly || (r.sellProfit ?? 0) > 0)
            && (!signalsOnly || r.indicator?.signal)
            && priceInRange(filters.priceSide === 'sell' ? r.sell?.net : r.cost, filters)
            // Account and collection must hold for the same copy.
            && (!(accounts || cols) || (r.owned?.instances || []).some((i) =>
                (!accounts || accounts.has(i.account_name)) && (!cols || cols.has(i.collection) || cols.has(collections.get(r.name))))));
        // Unconfirmed items (probably not resellable) below every confirmed one, then by profit.
        const byProfit = [...matching].sort((a, b) => (a.unconfirmed - b.unconfirmed)
            || (b.sellPct ?? -Infinity) - (a.sellPct ?? -Infinity));
        return sortRows(byProfit, sort.sortKey, sort.sortDir, {
            item: (r) => r.name,
            owned: (r) => r.owned?.count ?? null,
            store: (r) => r.cost,
            sell: (r) => r.sell?.net ?? null,
            profit: (r) => r.sellProfit,
            profit_pct: (r) => r.sellPct,
            indicator: (r) => r.indicator?.pct_of_store ?? null,
            history: (r) => (r.history.days ? r.history.profitable_days / r.history.days : null),
        });
    }, [priced, deferredSearch, category, profitableOnly, signalsOnly, filters, collections, sort.sortKey, sort.sortDir]);

    const excludedByReason = useMemo(() => {
        const groups = {};
        for (const item of data?.excluded || []) (groups[item.reason] ||= { text: item.reason_text, names: [] }).names.push(item.name);
        return REASON_ORDER.filter((reason) => groups[reason]).map((reason) => ({ reason, ...groups[reason] }));
    }, [data]);

    if (!options || !profile) {
        return <div className="p-8 flex items-center justify-center text-slate-400"><Loader2 className="animate-spin mr-2" size={18} /> Loading markets…</div>;
    }
    const indicatorDisplay = options.markets.find((m) => m.id === options.indicator_market)?.display || options.indicator_market;
    const summary = data?.summary || {};
    const profitable = priced.filter((r) => !r.unconfirmed && (r.sellProfit ?? 0) > 0).length;
    const marketInfo = profile.info;
    const signalPct = data?.signal_pct ?? options.signal_pct ?? 100;
    const columns = [
        { key: 'item', label: 'Item' },
        { key: 'owned', label: 'Owned', right: true, title: 'How many you hold (Huginn → Get all items)' },
        { key: 'store', label: 'Store', right: true, title: 'The in-game store’s US dollar price' },
        { key: 'sell', label: 'Sell', right: true, title: `What ${profile.to} pays ${profile.side === 'instant' ? 'at once (buy orders)' : 'at its lowest listing'}, after its fee` },
        { key: 'profit', label: 'Profit', right: true },
        { key: 'profit_pct', label: 'Profit %', right: true },
        { key: 'indicator', label: indicatorDisplay, right: true, title: `The indicator: what ${indicatorDisplay} pays at once, as a share of the store price. BUY at ${signalPct}% or more` },
        { key: 'history', label: '30 days', right: true, title: 'Days the best price beat the store price, of the days tracked; the line is the best price per day, the dashes the store price' },
    ];

    return (
        <div className="flex flex-col gap-3 p-3">
            {/* Profile picker: In-game store → market (autobuy / min), as on Huginn's Arbitrage */}
            <div className="flex flex-wrap items-center gap-3">
                <span className="text-[10px] font-bold tracking-widest text-slate-600 uppercase">Profile</span>
                <ProfilePicker profiles={profiles} value={profile.id} onChange={setProfileId}
                    groupLabel={() => 'Buy in the in-game store'} searchPlaceholder="Search markets…" />
                {huginnMarkets.length > 0 && <FeeEditor markets={huginnMarkets} onSaved={() => { refreshHuginnMarkets(); load(); }} />}
                <InfoTip tip={`Buy in the in-game store, sell on the profile's market. The ${indicatorDisplay} column is the indicator: what it pays at once as a share of the store price; BUY lights up at ${signalPct}% or more. Store purchases cannot be traded for ${data?.trade_hold_days ?? 7} days, so check the price still holds before you buy.`} />
            </div>

            {/* Status bar */}
            <div className="bg-odin-blue/30 border border-white/5 rounded-xl px-4 py-3">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                    <span className="text-[11px] font-bold tracking-widest text-slate-500 uppercase">In-game store ⇒ {profile.to}</span>
                    {warming ? (
                        <span className="inline-flex items-center gap-1.5 text-xs text-amber-300/90">
                            <RefreshCw size={12} className="animate-spin" /> {data?.status === 'warming' ? 'Reading market prices' : 'Refreshing prices'}
                            {data?.pulling ? `: ${data.pulling} market side${data.pulling === 1 ? '' : 's'} left` : '…'}
                        </span>
                    ) : data?.status === 'no_token' ? (
                        <span className="text-xs text-amber-400/80">No Tradeon token (tradeon_token in backend/settings.json): no market prices.</span>
                    ) : data && (
                        <span className="text-xs text-slate-400 tabular-nums">
                            <span className="text-emerald-300 font-semibold">{summary.signals || 0} BUY signal{summary.signals === 1 ? '' : 's'}</span>
                            {' · '}<span className="text-amber-300">{profitable}</span> of {summary.items} items sell above the store price on {profile.to}
                            {summary.unconfirmed > 0 && <> · <span className="text-amber-300">{summary.unconfirmed}</span> unconfirmed</>}
                        </span>
                    )}
                    <span className="ml-auto flex flex-wrap items-center gap-2">
                        <span className="text-[11px] text-slate-500 tabular-nums" title="When the market prices on this board were pulled (the oldest side)">
                            <Clock size={11} className="inline -mt-0.5 mr-1" />prices {ago(data?.prices_at, now) || '—'}
                        </span>
                        <button type="button" onClick={refresh} disabled={warming}
                            title="Pull every market on this board again now (a market pulled in the last minute is skipped)"
                            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-xs font-medium disabled:opacity-50 transition-colors">
                            <RefreshCw size={12} className={warming ? 'animate-spin' : ''} /> Refresh
                        </button>
                        <button type="button" onClick={readStorePrices} disabled={sheetStartedAt != null}
                            title="Read the in-game store's price list again: one account logs in to Ratatoskr and out (about 15 seconds); nothing is bought. It also runs every morning after Get all items."
                            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-white/15 text-slate-300 hover:bg-white/5 disabled:opacity-50 text-xs">
                            <Tags size={12} className={sheetStartedAt != null ? 'animate-pulse' : ''} />
                            {sheetStartedAt != null ? 'Reading store prices…' : `Store prices ${ago(data?.catalogue_read_at, now) || 'never read'}`}
                        </button>
                    </span>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-slate-500">
                    <label className="inline-flex items-center gap-1.5" title={`The BUY signal fires when ${indicatorDisplay} pays at least this share of the store price at once`}>
                        <Zap size={11} className="text-emerald-400/80" /> BUY signal at
                        <input type="text" inputMode="decimal" aria-label="Signal threshold, percent of the store price"
                            value={signalDraft ?? String(signalPct)}
                            onChange={(e) => setSignalDraft(e.target.value)}
                            onBlur={commitSignal}
                            onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); if (e.key === 'Escape') setSignalDraft(null); }}
                            className="w-14 bg-black/30 border border-white/10 rounded px-1.5 py-0.5 text-slate-200 text-right tabular-nums outline-none focus:border-amber-500/40" />
                        % of the store price
                    </label>
                    <span className="inline-flex items-center gap-1.5">
                        <label className="inline-flex items-center gap-1.5 cursor-pointer"
                            title="Every morning after Get all items (and hourly for items that newly turn profitable), post the store items that sell above the store price to the Huginn Telegram bot">
                            <input type="checkbox" className="accent-amber-500" checked={!!alerts?.enabled} onChange={toggleAlerts} />
                            Telegram: daily post
                        </label>
                        <button type="button" onClick={sendNow} disabled={sending}
                            title="Post today's profitable store items to the Huginn Telegram bot now"
                            className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-white/10 text-slate-300 hover:bg-white/5 disabled:opacity-50">
                            <Send size={10} /> {sending ? 'Sending…' : 'Send now'}
                        </button>
                        {alerts && !alerts.channel && <span className="text-amber-400/80">no Telegram bot set (telegram_bot_token + telegram_chat_id)</span>}
                        {alerts?.last_sent_at && !alertNote && <span>last post {ago(alerts.last_sent_at, now)}</span>}
                        {alertNote && <span className={alertNote.startsWith('Sent') ? 'text-emerald-400' : 'text-red-300'}>{alertNote}</span>}
                    </span>
                    {marketInfo?.balance && (
                        <span><span className="text-sky-300/90">{marketInfo.display} pays {marketInfo.balance}</span>, not money: it only buys items there.</span>
                    )}
                    {marketInfo && !marketInfo.fee_known && <span>{marketInfo.display}&apos;s sell fee is not confirmed ({(marketInfo.fee * 100).toFixed(1)}%): set it in Fees.</span>}
                    {refreshNote && <span className="text-amber-300">{refreshNote}</span>}
                </div>
            </div>

            {(error || data?.error) && (
                <div className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-950/80 px-3 py-2 text-red-300 text-xs" role="alert">
                    <AlertTriangle size={14} className="mt-0.5 shrink-0" /> {error || data.error}
                </div>
            )}

            {/* Results, as Huginn's table */}
            <div className="flex flex-col bg-odin-blue/30 border border-white/5 rounded-xl overflow-hidden min-w-0">
                <div className="flex items-center gap-3 px-4 py-3 border-b border-white/5 bg-black/10 flex-wrap">
                    <div className="relative flex-1 min-w-[12rem] max-w-sm">
                        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" size={14} />
                        <input type="text" placeholder="Search items" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search items"
                            className="w-full bg-black/30 border border-white/10 rounded-lg pl-8 pr-3 py-2 text-base text-white focus:outline-none focus:border-amber-500/40 placeholder:text-slate-600" />
                    </div>
                    <button type="button" onClick={() => setProfitableOnly((v) => !v)} aria-pressed={profitableOnly}
                        title={`Only items ${profile.to} pays more for than the store charges`}
                        className={`shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${profitableOnly ? 'bg-sky-500/20 border-sky-500/40 text-sky-300' : 'bg-black/20 border-white/10 text-slate-400 hover:text-white hover:border-white/20'}`}>
                        Profitable only
                    </button>
                    <button type="button" onClick={() => setSignalsOnly((v) => !v)} aria-pressed={signalsOnly}
                        title={`Only items with a BUY signal (${indicatorDisplay} pays at least ${signalPct}% of the store price)`}
                        className={`shrink-0 inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${signalsOnly ? 'bg-emerald-500/20 border-emerald-500/40 text-emerald-300' : 'bg-black/20 border-white/10 text-slate-400 hover:text-white hover:border-white/20'}`}>
                        <Zap size={11} /> BUY signals
                    </button>
                    {profile.market === BEST && (
                        <button type="button" onClick={() => setCashOnly((v) => !v)} aria-pressed={cashOnly}
                            title="Best market only among markets that pay money: no Steam wallet, no site balance"
                            className={`shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${cashOnly ? 'bg-sky-500/20 border-sky-500/40 text-sky-300' : 'bg-black/20 border-white/10 text-slate-400 hover:text-white hover:border-white/20'}`}>
                            Cash only
                        </button>
                    )}
                    <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category"
                        className="shrink-0 bg-black/30 border border-white/10 rounded-lg px-2 py-1.5 text-xs text-slate-300">
                        <option value="all">Every category</option>
                        {categories.map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                    <ArbitrageFilters filters={filters} onChange={setFilters} accountOptions={accountOptions}
                        collectionOptions={collectionOptions} hasScan={!!byHash} priceSides={PRICE_SIDES}
                        countTitle="Store items it holds"
                        scanHint='Press "Get all items" in Huginn first: accounts and collections come from the inventory scan.' />
                    <span className="text-sm text-slate-500 shrink-0">showing {visible.length} of {priced.length}</span>
                </div>
                <ActiveFilterChips filters={filters} onChange={setFilters} priceSides={PRICE_SIDES} />

                <div className="overflow-x-auto">
                    <div className="min-w-[60rem]">
                        <div className={`${GRID} px-4 py-2 border-b border-white/5 text-[11px] font-bold tracking-wider text-slate-400 uppercase bg-black/20`}>
                            {columns.map((c) => (
                                <button key={c.key} type="button" onClick={() => sort.toggle(c.key)}
                                    title={`${c.title ? `${c.title}. ` : ''}Sort by ${c.label}`}
                                    className={`inline-flex items-center gap-1 uppercase tracking-wider hover:text-slate-200 transition-colors min-w-0 ${c.right ? 'justify-end' : ''} ${sort.sortKey === c.key ? 'text-amber-400' : ''}`}>
                                    <span className="truncate">{c.label}</span>
                                    {sort.sortKey === c.key && (sort.sortDir === 'asc' ? <ArrowUp size={11} /> : <ArrowDown size={11} />)}
                                </button>
                            ))}
                            <span className="sr-only">Buy</span>
                        </div>
                        <div className="max-h-[65vh] overflow-y-auto">
                            {data && visible.length === 0 && (
                                <div className="py-10 text-center text-slate-500 text-sm">
                                    {!data.catalogue_read_at ? 'No store prices yet: press “Store prices never read” above.'
                                        : data.status === 'warming' ? 'Reading market prices…'
                                            : data.status === 'no_token' ? 'No market prices: the Tradeon token (tradeon_token in backend/settings.json) is missing.'
                                                : !priced.length ? 'No market prices could be read.'
                                                    : activeFilterCount(filters) ? 'Nothing matches these filters. Remove a chip above, or clear them all.'
                                                        : signalsOnly ? `No item has a BUY signal right now (${indicatorDisplay} at ${signalPct}% of the store price or more).`
                                                            : profitableOnly ? `Nothing sells above the store price on ${profile.to} right now. Try another profile, or turn off “Profitable only”.`
                                                                : 'No item matches.'}
                                </div>
                            )}
                            {visible.map((r) => {
                                const expanded = open.has(r.entry);
                                const sell = r.sell;
                                return (
                                    <Fragment key={r.entry}>
                                        <div className={`${GRID} items-center px-4 py-2.5 border-b border-white/5 hover:bg-white/[0.03] transition-colors cursor-pointer ${r.unconfirmed ? 'opacity-70' : ''}`}
                                            onClick={() => toggleOpen(r.entry)} aria-expanded={expanded}>
                                            <div className="flex items-center gap-2.5 min-w-0">
                                                {expanded ? <ChevronDown size={13} className="text-slate-500 shrink-0" /> : <ChevronRight size={13} className="text-slate-500 shrink-0" />}
                                                <img src={imageOf(r.name)} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer"
                                                    className="w-9 h-9 object-contain shrink-0 rounded bg-black/20" />
                                                <div className="min-w-0">
                                                    <div className="flex items-center gap-1.5 min-w-0">
                                                        <p className="text-base text-white truncate hover:text-amber-300 transition-colors"
                                                            title={`${r.name}\n(click to copy)`}
                                                            onClick={(e) => { e.stopPropagation(); copyItemName(r.name); }}>
                                                            {r.name}
                                                        </p>
                                                        {copiedName === r.name && <span className="shrink-0 text-[10px] font-medium text-emerald-400">copied</span>}
                                                        <SteamMarketLink itemName={r.name} viaPulse />
                                                        <BuffMarketLink itemName={r.name} />
                                                        <CSFloatMarketLink itemName={r.name} />
                                                    </div>
                                                    <div className="flex items-center gap-1.5 text-[11px] text-slate-500 min-w-0">
                                                        <span className="truncate">{r.category}</span>
                                                        {r.unconfirmed && <Badge className="border-amber-500/40 text-amber-300"
                                                            title={`Markets ask at least ${data.unconfirmed_ratio}× the store price now, and did on most days tracked (or there is little history yet). If store copies could be resold, traders would have closed that gap, so they probably cannot be. Buy one and look at it in the web inventory: “Tradable After …” means a ${data.trade_hold_days}-day hold, “Not Tradable” means never.`}>unconfirmed</Badge>}
                                                        {r.resold_before && <Badge className="border-emerald-500/30 text-emerald-300" title="You sold this item before (Draupnir): store copies are resellable">resold before</Badge>}
                                                        {sell?.rising && <TrendingUp size={12} className="text-emerald-400 shrink-0" aria-label="price rising" />}
                                                        {sell?.falling && <TrendingDown size={12} className="text-red-400 shrink-0" aria-label="price falling" />}
                                                    </div>
                                                </div>
                                            </div>

                                            <span className="text-base text-right font-bold text-amber-400 tabular-nums"
                                                title={r.owned ? [...new Set(r.owned.instances.map((i) => i.account_name))].join('\n') : undefined}>
                                                {r.owned ? r.owned.count : <span className="text-slate-600 font-normal">—</span>}
                                            </span>
                                            <span className="text-right tabular-nums leading-tight">
                                                <span className="block text-base text-slate-300">{money(r.cost)}</span>
                                                {r.cheapest_wallet && r.cheapest_wallet.usd < r.cost - 0.005 && (
                                                    <span className="block text-[10px] text-sky-300" title={`In ${r.cheapest_wallet.currency} the store charges about ${money(r.cheapest_wallet.usd, 3)} (today's exchange rate): ${r.cheapest_wallet.accounts.join(', ')}`}>
                                                        {money(r.cheapest_wallet.usd)} in {r.cheapest_wallet.currency}
                                                    </span>
                                                )}
                                            </span>
                                            {sell ? (
                                                <span className="flex flex-col items-end leading-tight min-w-0"
                                                    title={`${sell.display} ${SIDE_LABEL[sell.side]}: ${money(sell.gross, 3)} before its ${(sell.fee * 100).toFixed(1)}% fee${sell.balance ? `, paid in ${sell.balance}` : ''}${sell.suspicious ? '. Far above Buff163’s listing: probably not a price you can sell at' : ''}`}>
                                                    <PriceCell market={sell.market} itemName={r.name} price={sell.net}
                                                        className={`text-base tabular-nums ${sell.suspicious ? 'text-slate-500 line-through' : 'text-slate-300'}`} />
                                                    <span className="text-[10px] text-slate-500 truncate max-w-full">
                                                        {profile.market === BEST ? sell.display
                                                            : sell.fee ? `${money(sell.gross)} before fee`
                                                                : sell.count != null ? `${sell.count} offers` : ''}
                                                    </span>
                                                </span>
                                            ) : (
                                                <span className="text-right text-slate-600 text-sm" title={`${profile.to} has no ${profile.side === 'instant' ? 'buy order' : 'listing'} for this item`}>—</span>
                                            )}
                                            <span className={`text-base text-right tabular-nums ${tone(r.sellProfit)}`}>{r.sellProfit == null ? '—' : money(r.sellProfit)}</span>
                                            <span className={`text-base text-right font-semibold tabular-nums ${tone(r.sellPct)}`}>{r.sellPct == null ? '—' : `${r.sellPct.toFixed(0)}%`}</span>
                                            <IndicatorCell indicator={r.indicator} signalPct={signalPct} display={indicatorDisplay} />
                                            <span className="flex flex-col items-end text-xs text-slate-400 tabular-nums">
                                                <span>{r.history.days ? `${r.history.profitable_days} of ${r.history.days}` : '—'}</span>
                                                <Sparkline series={r.history.series} cost={r.cost} />
                                            </span>
                                            <span className="text-right" onClick={(e) => e.stopPropagation()}>
                                                {r.definition_index != null && (
                                                    <Link to={`/store-catalogue/buy/${encodeURIComponent(r.entry)}`} title={`Buy ${r.name} in the in-game store on any of your accounts (dry run first)`}
                                                        className="inline-flex items-center gap-1 px-2 py-1 rounded border border-amber-500/30 text-amber-200 text-xs hover:bg-amber-500/10">
                                                        <ShoppingCart size={11} /> Buy
                                                    </Link>
                                                )}
                                            </span>
                                        </div>
                                        {expanded && (
                                            <div className="px-12 py-3 bg-black/30 border-b border-white/5">
                                                <table className="text-xs w-full max-w-3xl">
                                                    <thead className="text-[10px] uppercase tracking-wider text-slate-500">
                                                        <tr>
                                                            <th className="text-left font-medium py-1">Market</th>
                                                            <th className="text-left font-medium">How</th>
                                                            <th className="text-right font-medium">Price</th>
                                                            <th className="text-right font-medium">Fee</th>
                                                            <th className="text-right font-medium">You get</th>
                                                            <th className="text-right font-medium">Profit</th>
                                                            <th className="text-right font-medium" title="Listings or buy orders at that market">Depth</th>
                                                            <th className="text-left font-medium pl-4">Paid as</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {r.offers.map((o) => (
                                                            <tr key={`${o.market}-${o.side}`} className={`border-t border-white/5 ${o.suspicious ? 'text-slate-500' : 'text-slate-300'} ${sell && o.market === sell.market && o.side === sell.side ? 'bg-amber-500/[0.06]' : ''}`}>
                                                                <td className="py-1">{o.display}</td>
                                                                <td>{SIDE_LABEL[o.side]}</td>
                                                                <td className="text-right"><PriceCell market={o.market} itemName={r.name} price={o.gross} className="tabular-nums" /></td>
                                                                <td className="text-right tabular-nums">{(o.fee * 100).toFixed(1)}%</td>
                                                                <td className="text-right tabular-nums">{money(o.net, 3)}</td>
                                                                <td className={`text-right tabular-nums ${o.suspicious ? '' : tone(o.profit)}`}>{money(o.profit, 3)} <span className="opacity-70">{percent(o.profit_pct)}</span></td>
                                                                <td className="text-right tabular-nums">{o.count ?? '—'}</td>
                                                                <td className="pl-4">
                                                                    {o.suspicious ? <span title="Far above Buff163's lowest listing: one pattern's buy order, or one seller asking what nobody pays">not a real price</span>
                                                                        : o.balance || 'money'}
                                                                </td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                                <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-[11px] text-slate-500">
                                                    {r.history.best_net != null && <span>Best price in the last 30 days: {money(r.history.best_net, 3)} after fees on {r.history.best_date}.</span>}
                                                    <span>
                                                        Your record: {r.ledger ? <>bought {r.ledger.bought}, sold {r.ledger.sold}
                                                            {r.ledger.sold > 0 && <> (average {money(r.ledger.received / r.ledger.sold)} after fees{Object.keys(r.ledger.sold_on || {}).length ? `, on ${Object.entries(r.ledger.sold_on).map(([platform, units]) => `${platform} ×${units}`).join(', ')}` : ''})</>}</> : 'never traded (Draupnir)'}
                                                    </span>
                                                    {r.owned && <span>Held on: {Object.entries(r.owned.instances.reduce((acc, i) => ({ ...acc, [i.account_name]: (acc[i.account_name] || 0) + 1 }), {})).map(([account, count]) => `${account} ×${count}`).join(', ')}</span>}
                                                </div>
                                            </div>
                                        )}
                                    </Fragment>
                                );
                            })}
                        </div>
                    </div>
                </div>
            </div>

            <div className="text-[11px] text-slate-500 space-y-1">
                {data && (
                    <p>
                        <button type="button" onClick={() => setShowExcluded((v) => !v)} className="hover:text-white underline decoration-dotted">
                            {summary.excluded} store items left out
                        </button>
                        {' · '}<span title="One point per day: the best price each market showed. It grows every hour, also while this page is closed.">history: {data.history_days} {data.history_days === 1 ? 'day' : 'days'}</span>
                    </p>
                )}
                {showExcluded && excludedByReason.map((group) => (
                    <p key={group.reason}><span className="text-slate-300">{group.text[0].toUpperCase() + group.text.slice(1)}:</span> {group.names.join(', ')}</p>
                ))}
                <p className="flex items-start gap-1.5"><Info size={12} className="mt-0.5 shrink-0" />
                    <span>
                        Store purchases cannot be traded for {data?.trade_hold_days ?? 7} days, so the price has to hold for a week: the 30-day column shows whether a gap is a spike or lasts.
                        Steam pays into the Steam wallet and {indicatorDisplay} into its trade balance, not money: pick a cash market (or “Best market” with Cash only) for money.
                        Prices are after each market&apos;s fee (Fees); a price far above Buff163&apos;s lowest listing is crossed out.
                        Every morning after Get all items the store prices and every market are read again, and the profitable items are posted to Telegram.
                    </span>
                </p>
            </div>
        </div>
    );
};

export default StoreArbitrage;
