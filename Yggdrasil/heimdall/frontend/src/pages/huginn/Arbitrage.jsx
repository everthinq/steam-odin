import React, { useState, useEffect, useMemo, useDeferredValue } from 'react';
import { Link } from 'react-router-dom';
import { LayoutDashboard, RefreshCw, AlertTriangle, Search, ChevronDown, Coins, Boxes, Repeat, Gavel } from 'lucide-react';
import { matchesSearchQuery } from '../../utils/transferItems';
import SteamMarketLink from '../../components/SteamMarketLink';
import BuffMarketLink from '../../components/BuffMarketLink';
import LisSkinsMarketLink from '../../components/LisSkinsMarketLink';
import CSFloatMarketLink from '../../components/CSFloatMarketLink';
import CollectionFilter from '../../components/CollectionFilter';
import CaseArbitrage from '../../components/CaseArbitrage';
import AuctionBoard from '../../components/AuctionBoard';
import LootfarmArbitrage from '../../components/LootfarmArbitrage';
import PriceCell from '../../components/arbitrage/PriceCell';
import OverstockCell from '../../components/arbitrage/OverstockCell';
import LootfarmSellCell from '../../components/arbitrage/LootfarmSellCell';
import ProfilePicker from '../../components/arbitrage/ProfilePicker';
import FeeEditor from '../../components/arbitrage/FeeEditor';

// Render results in capped pages — the datasets are ~17k rows and painting them all
// at once freezes the page. Rows are sorted best-profit-first, so the first page is
// what matters; "Load more" reveals the rest on demand.
const PAGE_SIZE = 150;

const PROFILES = [
    // buyMarket / sellMarket are pulse short-link slugs; when set, the Buy/Sell prices
    // link to that market's page for the item. Data is fetched live on demand only.
    { id: 'tradeon-steam',           from: 'Tradeon',  fromSub: 'min', to: 'Steam',   toSub: 'autobuy', buyMarket: 'TradeOnMarket', sellMarket: 'Steam',   fetchEndpoint: '/api/huginn/tradeon/steam' },
    { id: 'tradeon-buff',            from: 'Tradeon',  fromSub: 'min', to: 'Buff163', toSub: 'autobuy', buyMarket: 'TradeOnMarket', sellMarket: 'Buff',    fetchEndpoint: '/api/huginn/tradeon/buff' },
    { id: 'tradeon-csfloat',         from: 'Tradeon',  fromSub: 'min', to: 'CSFloat', toSub: 'min',     buyMarket: 'TradeOnMarket', sellMarket: 'CsFloat', fetchEndpoint: '/api/huginn/tradeon/csfloat' },
    // "autobuy" profiles sell into CSFloat buy orders, which only exist for owned
    // items we've swept (see the CSFloat buy-orders panel). Data covers owned items.
    { id: 'tradeon-csfloat-autobuy', from: 'Tradeon',  fromSub: 'min', to: 'CSFloat', toSub: 'autobuy', buyMarket: 'TradeOnMarket', sellMarket: 'CsFloat', fetchEndpoint: '/api/huginn/tradeon/csfloat-autobuy', autobuy: true },
    { id: 'tradeon-dmarket',         from: 'Tradeon',  fromSub: 'min', to: 'DMarket', toSub: 'autobuy', buyMarket: 'TradeOnMarket', sellMarket: 'Dmarket', fetchEndpoint: '/api/huginn/tradeon/dmarket' },
    { id: 'tradeon-lootfarm',        from: 'Tradeon',  fromSub: 'min', to: 'LOOT.Farm', toSub: 'autobuy', buyMarket: 'TradeOnMarket', sellMarket: 'LootFarm', fetchEndpoint: '/api/huginn/tradeon/lootfarm' },
    { id: 'lisskins-steam',          from: 'LisSkins', fromSub: 'min', to: 'Steam',   toSub: 'autobuy', buyMarket: 'LisSkins',      sellMarket: 'Steam',   fetchEndpoint: '/api/huginn/tradeon/lisskins-steam' },
    { id: 'lisskins-buff',           from: 'LisSkins', fromSub: 'min', to: 'Buff163', toSub: 'autobuy', buyMarket: 'LisSkins',      sellMarket: 'Buff',    fetchEndpoint: '/api/huginn/tradeon/lisskins-buff' },
    { id: 'lisskins-csfloat',        from: 'LisSkins', fromSub: 'min', to: 'CSFloat', toSub: 'min',     buyMarket: 'LisSkins',      sellMarket: 'CsFloat', fetchEndpoint: '/api/huginn/tradeon/lisskins-csfloat' },
    { id: 'lisskins-csfloat-autobuy',from: 'LisSkins', fromSub: 'min', to: 'CSFloat', toSub: 'autobuy', buyMarket: 'LisSkins',      sellMarket: 'CsFloat', fetchEndpoint: '/api/huginn/tradeon/lisskins-csfloat-autobuy', autobuy: true },
    { id: 'lisskins-dmarket',        from: 'LisSkins', fromSub: 'min', to: 'DMarket', toSub: 'autobuy', buyMarket: 'LisSkins',      sellMarket: 'Dmarket', fetchEndpoint: '/api/huginn/tradeon/lisskins-dmarket' },
    { id: 'lisskins-lootfarm',       from: 'LisSkins', fromSub: 'min', to: 'LOOT.Farm', toSub: 'autobuy', buyMarket: 'LisSkins',    sellMarket: 'LootFarm', fetchEndpoint: '/api/huginn/tradeon/lisskins-lootfarm' },
    { id: 'buff-steam',              from: 'Buff163',  fromSub: 'min', to: 'Steam',   toSub: 'autobuy', buyMarket: 'Buff',          sellMarket: 'Steam',   fetchEndpoint: '/api/huginn/tradeon/buff-steam' },
    { id: 'buff-csfloat',            from: 'Buff163',  fromSub: 'min', to: 'CSFloat', toSub: 'min',     buyMarket: 'Buff',          sellMarket: 'CsFloat', fetchEndpoint: '/api/huginn/tradeon/buff-csfloat' },
    { id: 'buff-csfloat-autobuy',    from: 'Buff163',  fromSub: 'min', to: 'CSFloat', toSub: 'autobuy', buyMarket: 'Buff',          sellMarket: 'CsFloat', fetchEndpoint: '/api/huginn/tradeon/buff-csfloat-autobuy', autobuy: true },
    { id: 'buff-dmarket',            from: 'Buff163',  fromSub: 'min', to: 'DMarket', toSub: 'autobuy', buyMarket: 'Buff',          sellMarket: 'Dmarket', fetchEndpoint: '/api/huginn/tradeon/buff-dmarket' },
    { id: 'csfloat-steam',           from: 'CSFloat',  fromSub: 'min', to: 'Steam',   toSub: 'autobuy', buyMarket: 'CsFloat',       sellMarket: 'Steam',   fetchEndpoint: '/api/huginn/tradeon/csfloat-steam' },
    { id: 'csfloat-buff',            from: 'CSFloat',  fromSub: 'min', to: 'Buff163', toSub: 'autobuy', buyMarket: 'CsFloat',       sellMarket: 'Buff',    fetchEndpoint: '/api/huginn/tradeon/csfloat-buff' },
    { id: 'csfloat-dmarket',         from: 'CSFloat',  fromSub: 'min', to: 'DMarket', toSub: 'autobuy', buyMarket: 'CsFloat',       sellMarket: 'Dmarket', fetchEndpoint: '/api/huginn/tradeon/csfloat-dmarket' },
    { id: 'dmarket-steam',           from: 'DMarket',  fromSub: 'min', to: 'Steam',   toSub: 'autobuy', buyMarket: 'Dmarket',       sellMarket: 'Steam',   fetchEndpoint: '/api/huginn/tradeon/dmarket-steam' },
    { id: 'dmarket-buff',            from: 'DMarket',  fromSub: 'min', to: 'Buff163', toSub: 'autobuy', buyMarket: 'Dmarket',       sellMarket: 'Buff',    fetchEndpoint: '/api/huginn/tradeon/dmarket-buff' },
    { id: 'dmarket-csfloat',         from: 'DMarket',  fromSub: 'min', to: 'CSFloat', toSub: 'min',     buyMarket: 'Dmarket',       sellMarket: 'CsFloat', fetchEndpoint: '/api/huginn/tradeon/dmarket-csfloat' },
    { id: 'dmarket-csfloat-autobuy', from: 'DMarket',  fromSub: 'min', to: 'CSFloat', toSub: 'autobuy', buyMarket: 'Dmarket',       sellMarket: 'CsFloat', fetchEndpoint: '/api/huginn/tradeon/dmarket-csfloat-autobuy', autobuy: true },
];

const formatTs = (ts) => {
    if (!ts) return null;
    const diffMin = Math.round((Date.now() - new Date(ts).getTime()) / 60000);
    if (diffMin < 1) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffH = Math.round(diffMin / 60);
    return diffH < 24 ? `${diffH}h ago` : new Date(ts).toLocaleDateString();
};

const HuginnArbitrage = () => {
    // Top-level view: cross-market item arbitrage (default) vs the container/case tracker.
    const [view, setView] = useState('arbitrage');
    const [scanData, setScanData] = useState(null);
    const [scanning, setScanning] = useState(false);
    const [scanError, setScanError] = useState(null);

    // Fetched arbitrage data is held per-profile in memory for this session only.
    // Nothing is preloaded from disk — opening the page and switching profiles is
    // instant. Data loads only when you hit Fetch/Re-fetch (a re-fetch replaces it),
    // and it's intentionally gone on reload/leaving the page.
    const [dataByProfile, setDataByProfile] = useState({});
    const [tradeonError, setTradeonError] = useState(null);
    const [tradeonFetching, setTradeonFetching] = useState(false);
    const [uploadOpen, setUploadOpen] = useState(true);

    const [profileId, setProfileId] = useState(PROFILES[0].id);
    const tradeonData = dataByProfile[profileId] ?? null;

    // Market registry (from the backend) drives the generated buy→sell pairs below.
    const [markets, setMarkets] = useState([]);

    const [itemSearch, setItemSearch] = useState('');
    const [inventoryOnly, setInventoryOnly] = useState(false);
    const [hideUnstable, setHideUnstable] = useState(false);   // LOOT.Farm: drop max=0 (Unstable) rows
    const [includedCollections, setIncludedCollections] = useState([]);
    const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
    const [copiedName, setCopiedName] = useState('');
    // LOOT.Farm acceptance fee (3–5% by subscription; default 5). Applied to the
    // LOOT.Farm sell price server-side. Persisted per browser.
    const [lootfarmFee, setLootfarmFee] = useState(() => {
        try { const v = Number(localStorage.getItem('huginn_lootfarm_fee')); return Number.isFinite(v) && v > 0 ? v : 5; }
        catch { return 5; }
    });
    // Filtering ~17k rows on every keystroke is heavy; defer it so typing stays snappy.
    const deferredSearch = useDeferredValue(itemSearch);

    // CSFloat buy-order sweep: {job:{running,done,total,found,...}, cache:{count,fetched_at}}.
    // Shared by every "=> CSFloat (autobuy)" profile.
    const [csfloatStatus, setCsfloatStatus] = useState(null);
    const csfloatJobRunning = csfloatStatus?.job?.running ?? false;

    const fetchCsfloatStatus = async () => {
        try {
            const r = await fetch('/api/huginn/csfloat/buy-orders');
            if (r.ok) setCsfloatStatus(await r.json());
        } catch { /* ignore */ }
    };
    useEffect(() => { fetchCsfloatStatus(); }, []);
    // Poll while a sweep is running so the progress bar advances.
    useEffect(() => {
        if (!csfloatJobRunning) return undefined;
        const t = setInterval(fetchCsfloatStatus, 1500);
        return () => clearInterval(t);
    }, [csfloatJobRunning]);

    const handleFetchBuyOrders = async () => {
        try {
            const r = await fetch('/api/huginn/csfloat/buy-orders', { method: 'POST' });
            const d = await r.json().catch(() => ({}));
            if (!r.ok) { setTradeonError(d.error || 'Could not start CSFloat buy-order sweep'); return; }
            setTradeonError(null);
            fetchCsfloatStatus();
        } catch (err) {
            setTradeonError(err.message);
        }
    };

    useEffect(() => {
        fetch('/api/huginn/scan/cache')
            .then(r => r.ok ? r.json() : null)
            .then(data => { if (data) setScanData(data); })
            .catch(() => {});
    }, []);

    const refreshMarkets = () => fetch('/api/huginn/markets')
        .then(r => r.ok ? r.json() : null)
        .then(data => { if (Array.isArray(data)) setMarkets(data); })
        .catch(() => {});
    useEffect(() => { refreshMarkets(); }, []);

    // No preload. Just reflect whether this profile already has session data:
    // collapse the fetch panel if it does, open it (prompt to fetch) if it doesn't.
    useEffect(() => {
        setUploadOpen(!dataByProfile[profileId]);
    }, [profileId]); // eslint-disable-line react-hooks/exhaustive-deps

    const handleScan = async () => {
        setScanning(true);
        setScanError(null);
        try {
            const res = await fetch('/api/huginn/scan', { method: 'POST' });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Scan failed');
            setScanData(data);
        } catch (err) {
            setScanError(err.message);
        } finally {
            setScanning(false);
        }
    };

    const setProfileData = (id, data) => setDataByProfile(prev => ({ ...prev, [id]: data }));

    const handleTradeonChange = (raw) => {
        if (!raw.trim()) { setProfileData(profileId, null); setTradeonError(null); return; }
        try {
            const parsed = JSON.parse(raw);
            if (!Array.isArray(parsed)) throw new Error('Expected a JSON array');
            setProfileData(profileId, parsed);
            setTradeonError(null);
            setUploadOpen(false);
        } catch (err) {
            setProfileData(profileId, null);
            setTradeonError(err.message);
        }
    };

    const handleFileUpload = (e) => {
        const file = e.target.files?.[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = (ev) => handleTradeonChange(ev.target.result || '');
        reader.readAsText(file);
        e.target.value = '';
    };

    // Generated pairs: every registry market as a buy source × every sell target
    // (both autobuy and min where the target has buy orders), minus pairs already
    // covered by the curated PROFILES above. Each is synthesised server-side via
    // /api/huginn/tradeon/pair. Tradeon is skipped as a sell target (it's the pull's
    // own market) and LootFarm too (its selling is handled by the feed-based profiles).
    const allProfiles = useMemo(() => {
        if (!markets.length) return PROFILES;
        const covered = new Set(PROFILES.map(p => `${p.buyMarket}:${p.sellMarket}:${p.toSub}`));
        const generated = [];
        for (const buy of markets) {
            for (const sell of markets) {
                if (sell.id === buy.id || sell.id === 'TradeOnMarket' || sell.id === 'LootFarm') continue;
                const modes = sell.hasAutobuy ? ['autobuy', 'min'] : ['min'];
                for (const mode of modes) {
                    if (covered.has(`${buy.id}:${sell.id}:${mode}`)) continue;
                    // CSFloat autobuy sells into swept buy orders → show the sweep panel.
                    const csfloatAutobuy = sell.id === 'CsFloat' && mode === 'autobuy';
                    generated.push({
                        id: `gen:${buy.id}:${sell.id}:${mode}`,
                        from: buy.display, fromSub: 'min',
                        to: sell.display, toSub: mode,
                        buyMarket: buy.id, sellMarket: sell.id,
                        fetchEndpoint: `/api/huginn/tradeon/pair?buy=${buy.id}&sell=${sell.id}&mode=${mode}`,
                        ...(csfloatAutobuy ? { autobuy: true } : {}),
                    });
                }
            }
        }
        return [...PROFILES, ...generated];
    }, [markets]);

    const activeProfile = allProfiles.find(p => p.id === profileId) ?? PROFILES[0];

    const handleApiFetch = async () => {
        if (tradeonFetching) return;
        const profile = activeProfile; // capture — profile could change during the await
        setTradeonFetching(true);
        setTradeonError(null);
        try {
            // The LootFarm ?fee= (percent) belongs only to the dedicated feed-based
            // profiles (endpoints with no query string); generated pairs already carry
            // their own query and net the sell market's registry fee server-side.
            const url = (profile.sellMarket === 'LootFarm' && !profile.fetchEndpoint.includes('?'))
                ? `${profile.fetchEndpoint}?fee=${lootfarmFee || 0}`
                : profile.fetchEndpoint;
            const res = await fetch(url);
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Fetch failed');
            if (!Array.isArray(data)) throw new Error('Unexpected response format');
            setProfileData(profile.id, data);
            setTradeonError(null);
            setUploadOpen(false);
        } catch (err) {
            setTradeonError(err.message);
        } finally {
            setTradeonFetching(false);
        }
    };

    const copyItemName = (name) => {
        navigator.clipboard?.writeText(name).catch(() => {});
        setCopiedName(name);
        setTimeout(() => setCopiedName(c => (c === name ? '' : c)), 1200);
    };

    // Owned-item lookup by market_hash_name (built by the scan). Plain ref — cheap.
    const byHash = scanData?.by_hash ?? null;

    // Sort once, best profit first. Shallow copy of references only — the old
    // code cloned every one of ~24k rows here on each scan, which was the main
    // memory/GC cost. (Backend already sorts; this is a defensive re-sort.)
    const sortedResults = useMemo(() => {
        if (!tradeonData) return [];
        return [...tradeonData].sort((a, b) => (b.profitPercent ?? 0) - (a.profitPercent ?? 0));
    }, [tradeonData]);

    // Collections found in the current inventory scan (pulse data carries none),
    // for the collection filter. Empty until an inventory scan exists.
    const collections = useMemo(() => {
        if (!byHash) return [];
        const set = new Set();
        for (const entry of Object.values(byHash)) {
            for (const inst of entry.instances || []) {
                if (inst.collection) set.add(inst.collection);
            }
        }
        return [...set].sort();
    }, [byHash]);

    // How many arbitrage rows you own — memoized (was recomputed 3x per render).
    const ownedCount = useMemo(() => {
        if (!byHash) return 0;
        let n = 0;
        for (const it of sortedResults) if (byHash[it.itemName?.marketHashName]) n++;
        return n;
    }, [sortedResults, byHash]);

    const filteredResults = useMemo(() => {
        const q = deferredSearch.trim();
        const cols = includedCollections.length ? new Set(includedCollections) : null;
        if (!inventoryOnly && !cols && !q && !hideUnstable) return sortedResults;
        return sortedResults.filter(item => {
            const owned = byHash?.[item.itemName?.marketHashName];
            if (inventoryOnly && !owned) return false;
            if (hideUnstable && item.secondMarket?.overstockInfo?.limit === 0) return false;
            if (cols && !(owned?.instances || []).some(i => cols.has(i.collection))) {
                return false;
            }
            if (q && !matchesSearchQuery(
                [item.itemName?.marketHashName, item.itemName?.skinName, item.itemName?.itemTypeName],
                q
            )) return false;
            return true;
        });
    }, [sortedResults, byHash, deferredSearch, inventoryOnly, includedCollections, hideUnstable]);

    // Only the first `visibleCount` rows are painted (see PAGE_SIZE note).
    const visibleResults = useMemo(
        () => filteredResults.slice(0, visibleCount),
        [filteredResults, visibleCount]
    );

    // Reset the window whenever the result set changes so we start from the top.
    useEffect(() => {
        setVisibleCount(PAGE_SIZE);
    }, [deferredSearch, inventoryOnly, includedCollections, profileId, hideUnstable]);

    return (
        <div className="h-screen bg-odin-dark flex flex-col overflow-hidden">
            {/* Header */}
            <div className="shrink-0 border-b border-white/5 bg-odin-blue/50 px-6 py-4 flex items-center gap-3 flex-wrap">
                <Link to="/" className="flex items-center gap-1.5 text-sm text-slate-400 hover:text-white transition-colors shrink-0">
                    <LayoutDashboard size={15} />
                    Dashboard
                </Link>
                <span className="text-white/20">/</span>
                <h1 className="text-lg font-bold font-serif text-amber-100">Huginn — The Scout</h1>

                {/* View toggle: item arbitrage vs container/case tracker */}
                <div className="flex items-center gap-1 ml-3 p-0.5 rounded-lg bg-black/30 border border-white/10">
                    <button
                        type="button"
                        onClick={() => setView('arbitrage')}
                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${view === 'arbitrage' ? 'bg-amber-600 text-white' : 'text-slate-400 hover:text-white'}`}
                    >
                        <Repeat size={13} /> Arbitrage
                    </button>
                    <button
                        type="button"
                        onClick={() => setView('cases')}
                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${view === 'cases' ? 'bg-amber-600 text-white' : 'text-slate-400 hover:text-white'}`}
                    >
                        <Boxes size={13} /> Case Arbitrage
                    </button>
                    <button
                        type="button"
                        onClick={() => setView('auctions')}
                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${view === 'auctions' ? 'bg-amber-600 text-white' : 'text-slate-400 hover:text-white'}`}
                    >
                        <Gavel size={13} /> LF Auctions
                    </button>
                    <button
                        type="button"
                        onClick={() => setView('lfarb')}
                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${view === 'lfarb' ? 'bg-amber-600 text-white' : 'text-slate-400 hover:text-white'}`}
                    >
                        <Coins size={13} /> LF Arbitrage
                    </button>
                </div>

                {view === 'arbitrage' && (
                    <div className="ml-auto flex items-center gap-4">
                        {scanData && (
                            <span className="text-xs text-slate-500 tabular-nums">
                                {scanData.total_items.toLocaleString()} items · {formatTs(scanData.scan_timestamp)}
                            </span>
                        )}
                        <button
                            onClick={handleScan}
                            disabled={scanning}
                            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                        >
                            <RefreshCw size={14} className={scanning ? 'animate-spin' : ''} />
                            {scanning ? 'Scanning…' : 'Get all items'}
                        </button>
                    </div>
                )}
            </div>

            <div className="flex-1 flex flex-col gap-3 p-6 overflow-hidden max-w-7xl w-full mx-auto">
                {view === 'lfarb' ? <LootfarmArbitrage byHash={byHash} /> : view === 'auctions' ? <AuctionBoard /> : view === 'cases' ? <CaseArbitrage /> : (<>
                {scanError && (
                    <div className="shrink-0 bg-red-500/20 border border-red-500/30 text-red-300 px-4 py-3 rounded-lg text-sm">
                        {scanError}
                    </div>
                )}
                {scanning && (
                    <div className="shrink-0 bg-amber-500/10 border border-amber-500/20 text-amber-300 px-4 py-3 rounded-lg text-sm">
                        Connecting to all accounts and reading storage units — this can take up to a minute…
                    </div>
                )}

                {/* Profile picker */}
                <div className="shrink-0 flex items-center gap-3">
                    <span className="text-[10px] font-bold tracking-widest text-slate-600 uppercase">Profile</span>
                    <ProfilePicker
                        profiles={allProfiles}
                        value={profileId}
                        onChange={id => setProfileId(id)}
                    />
                    {markets.length > 0 && <FeeEditor markets={markets} onSaved={refreshMarkets} />}
                </div>

                {/* CSFloat buy-order sweep — only for "=> CSFloat (autobuy)" profiles */}
                {activeProfile.autobuy && (() => {
                    const job = csfloatStatus?.job;
                    const cache = csfloatStatus?.cache;
                    const pct = job && job.total ? Math.round((job.done / job.total) * 100) : 0;
                    // When all keys are cooling the sweep waits, then auto-resumes.
                    const waitMs = job?.waiting_until ? job.waiting_until * 1000 - Date.now() : 0;
                    const waiting = csfloatJobRunning && waitMs > 0;
                    // A prior sweep that was throttled mid-run and can be continued.
                    const paused = cache && cache.complete === false && !csfloatJobRunning;
                    const btnLabel = csfloatJobRunning ? 'Fetching…' : paused ? 'Resume' : cache ? 'Refresh' : 'Fetch buy orders';
                    return (
                        <div className="shrink-0 bg-purple-500/5 border border-purple-500/20 rounded-xl px-4 py-3">
                            <div className="flex items-center gap-3 flex-wrap">
                                <Coins size={15} className="text-purple-300 shrink-0" />
                                <span className="text-xs font-medium text-purple-200">CSFloat buy orders</span>
                                {csfloatStatus?.proxy_enabled && (
                                    <span title="Sweep routes through your rotating proxy" className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] bg-sky-500/10 border border-sky-500/30 text-sky-300">
                                        via proxy
                                    </span>
                                )}
                                {waiting ? (
                                    <span className="text-xs text-amber-400 tabular-nums">
                                        {job.done}/{job.total} · all keys cooling · auto-resuming in ~{Math.ceil(waitMs / 60000)}m
                                    </span>
                                ) : csfloatJobRunning ? (
                                    <span className="text-xs text-slate-400 tabular-nums">
                                        fetching {job.done}/{job.total} · {job.found} found
                                    </span>
                                ) : paused ? (
                                    <span className="text-xs text-amber-400 tabular-nums">
                                        paused at {cache.done}/{cache.candidates} · {cache.count} priced · {formatTs(cache.updated_at)}
                                    </span>
                                ) : cache ? (
                                    <span className="text-xs text-slate-400">
                                        {cache.count} owned items priced · {formatTs(cache.fetched_at)}
                                    </span>
                                ) : (
                                    <span className="text-xs text-slate-500">not fetched yet</span>
                                )}
                                <button
                                    type="button"
                                    onClick={handleFetchBuyOrders}
                                    disabled={csfloatJobRunning || !scanData}
                                    title={!scanData ? 'Run "Get all items" first to know which items you own' : 'Fetch CSFloat buy orders for your owned items'}
                                    className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-purple-600/70 hover:bg-purple-500 text-white text-xs font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors shrink-0"
                                >
                                    <RefreshCw size={12} className={csfloatJobRunning ? 'animate-spin' : ''} />
                                    {btnLabel}
                                </button>
                            </div>
                            {csfloatJobRunning && (
                                <div className="mt-2 h-1 w-full bg-black/30 rounded-full overflow-hidden">
                                    <div className="h-full bg-purple-500 transition-all" style={{ width: `${pct}%` }} />
                                </div>
                            )}
                            {csfloatStatus?.keys?.length > 0 && (
                                <div className="mt-2 flex items-center gap-1.5 flex-wrap">
                                    {csfloatStatus.keys.map((k) => (
                                        <span
                                            key={k.label}
                                            title={k.cooling
                                                ? `Cooling down (strike ${k.strikes}) — back in ~${Math.ceil(k.cooldown_remaining / 60)}m`
                                                : 'Available'}
                                            className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] border ${k.cooling
                                                ? 'bg-amber-500/10 border-amber-500/30 text-amber-300'
                                                : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'}`}
                                        >
                                            <span className={`w-1.5 h-1.5 rounded-full ${k.cooling ? 'bg-amber-400' : 'bg-emerald-400'}`} />
                                            {k.label}
                                            {k.cooling && ` · ${Math.ceil(k.cooldown_remaining / 60)}m`}
                                        </span>
                                    ))}
                                    <span className="text-[10px] text-slate-600 ml-1">edit keys in backend/csfloat_keys.json</span>
                                </div>
                            )}
                            <p className="mt-2 text-[11px] text-slate-500">
                                CSFloat has no bulk buy-order feed, so we price only the items you own
                                (~{cache?.candidates ?? '450'} on CSFloat) — this takes a few minutes and is
                                reused by all CSFloat (autobuy) profiles until you refresh. If CSFloat throttles
                                us the sweep pauses and Resume continues where it stopped (within 2h).
                                {!scanData && ' Run "Get all items" first.'}
                            </p>
                            {paused && cache.reason && (
                                <p className="mt-1 text-[11px] text-amber-400/80">Paused: {cache.reason}</p>
                            )}
                            {job?.error && (
                                <p className="mt-1 text-[11px] text-red-400 flex items-center gap-1">
                                    <AlertTriangle size={11} /> {job.error}
                                </p>
                            )}
                        </div>
                    );
                })()}

                {/* Collapsible upload section */}
                <div className="shrink-0 bg-odin-blue/30 border border-white/5 rounded-xl overflow-hidden">
                    <div className="flex items-center">
                        <button
                            type="button"
                            onClick={() => setUploadOpen(o => !o)}
                            className="flex-1 flex items-center gap-2 px-4 py-2.5 hover:bg-white/[0.03] transition-colors text-left"
                        >
                            <ChevronDown size={15} className={`text-slate-500 transition-transform shrink-0 ${uploadOpen ? '' : '-rotate-90'}`} />
                            <span className="text-[10px] font-bold tracking-widest text-slate-500 uppercase">pulse.tradeon JSON</span>
                            {tradeonData && !uploadOpen && (
                                <span className="ml-2 text-xs text-amber-400">
                                    {tradeonData.length} deals · {ownedCount} owned
                                </span>
                            )}
                        </button>
                        {activeProfile.sellMarket === 'LootFarm' && (
                            <div className="flex items-center gap-1.5 mr-2 shrink-0" title="Your LOOT.Farm acceptance fee (3–5% by subscription level; 5% with no subscription). Applied to the LOOT.Farm sell price. Re-fetch to apply.">
                                <span className="text-[10px] font-bold tracking-widest text-slate-500 uppercase">LF fee</span>
                                <input
                                    type="number" min="0" max="20" step="0.5" value={lootfarmFee}
                                    onChange={e => {
                                        const v = e.target.value === '' ? '' : Math.max(0, Math.min(20, Number(e.target.value)));
                                        setLootfarmFee(v);
                                        try { if (v !== '') localStorage.setItem('huginn_lootfarm_fee', String(v)); } catch { /* ignore */ }
                                    }}
                                    onBlur={e => { if (e.target.value === '') setLootfarmFee(5); }}
                                    className="w-14 bg-black/30 border border-white/10 rounded px-2 py-1.5 text-slate-200 text-xs tabular-nums focus:outline-none focus:ring-1 focus:ring-amber-500/50"
                                />
                                <span className="text-slate-500 text-xs">%</span>
                            </div>
                        )}
                        {activeProfile.fetchEndpoint && (
                            <button
                                type="button"
                                onClick={handleApiFetch}
                                disabled={tradeonFetching}
                                className="flex items-center gap-1.5 px-3 py-1.5 mr-2 rounded-lg bg-amber-600/70 hover:bg-amber-500 text-white text-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors shrink-0"
                            >
                                <RefreshCw size={12} className={tradeonFetching ? 'animate-spin' : ''} />
                                {tradeonFetching ? 'Fetching…' : tradeonData ? 'Re-fetch' : 'Fetch live data'}
                            </button>
                        )}
                    </div>

                    {uploadOpen && (
                        <div className="px-4 pb-4 border-t border-white/5">
                            {activeProfile.fetchEndpoint ? (
                                <div className="mt-3">
                                    {tradeonData && !tradeonFetching && (
                                        <span className="text-xs text-slate-500">
                                            {tradeonData.length} deals loaded
                                            {scanData ? ` · ${ownedCount} match your inventory` : ''}
                                        </span>
                                    )}
                                    {!tradeonData && !tradeonFetching && (
                                        <span className="text-xs text-slate-500">Click "Fetch live data" to load prices from Tradeon.</span>
                                    )}
                                </div>
                            ) : (
                                <label className={`mt-3 flex items-center justify-center gap-3 h-16 rounded-lg border-2 border-dashed cursor-pointer transition-colors ${tradeonData ? 'border-amber-500/40 bg-amber-500/5 hover:bg-amber-500/10' : 'border-white/10 hover:border-white/20 hover:bg-white/[0.02]'}`}>
                                    <input type="file" accept=".json,application/json" onChange={handleFileUpload} className="hidden" />
                                    {tradeonData ? (
                                        <span className="text-sm text-amber-300">
                                            {tradeonData.length} deals loaded
                                            {scanData ? ` · ${ownedCount} match your inventory` : ' · scan your inventory to see matches'}
                                            <span className="text-slate-500 ml-2 text-xs">— click to replace</span>
                                        </span>
                                    ) : (
                                        <span className="text-sm text-slate-500">Click to upload JSON file</span>
                                    )}
                                </label>
                            )}
                            {tradeonError && (
                                <p className="text-xs text-red-400 mt-2 flex items-center gap-1">
                                    <AlertTriangle size={11} /> {tradeonError}
                                </p>
                            )}
                        </div>
                    )}
                </div>

                {/* Results table — flex-1 fills all remaining space */}
                {sortedResults.length > 0 ? (
                    <div className="flex-1 flex flex-col bg-odin-blue/30 border border-white/5 rounded-xl overflow-hidden min-h-0">
                        <div className="shrink-0 flex items-center gap-3 px-4 py-3 border-b border-white/5 bg-black/10">
                            <div className="relative flex-1 max-w-sm">
                                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" size={14} />
                                <input
                                    type="text"
                                    placeholder="Search items (e.g. ak redline ft)"
                                    value={itemSearch}
                                    onChange={(e) => setItemSearch(e.target.value)}
                                    className="w-full bg-black/30 border border-white/10 rounded-lg pl-8 pr-3 py-2 text-base text-white focus:outline-none focus:border-amber-500/40 placeholder:text-slate-600"
                                />
                            </div>
                            <button
                                type="button"
                                onClick={() => setInventoryOnly(v => !v)}
                                className={`shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${inventoryOnly ? 'bg-sky-500/20 border-sky-500/40 text-sky-300' : 'bg-black/20 border-white/10 text-slate-400 hover:text-white hover:border-white/20'}`}
                            >
                                My Inventory
                            </button>
                            {activeProfile.sellMarket === 'LootFarm' && (
                                <button
                                    type="button"
                                    onClick={() => setHideUnstable(v => !v)}
                                    title="Hide items LOOT.Farm has no active buy limit for (max 0) — their prices are inflated and won't reliably be honored."
                                    className={`shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${hideUnstable ? 'bg-red-500/20 border-red-500/40 text-red-300' : 'bg-black/20 border-white/10 text-slate-400 hover:text-white hover:border-white/20'}`}
                                >
                                    Hide Unstable
                                </button>
                            )}
                            <CollectionFilter
                                collections={collections}
                                selected={includedCollections}
                                onChange={setIncludedCollections}
                                disabledHint="Scan your inventory (Get all items) to filter by collection"
                            />
                            <span className="text-sm text-slate-500 shrink-0">
                                showing {Math.min(visibleCount, filteredResults.length)} of {filteredResults.length}
                                {filteredResults.length !== sortedResults.length && ` (${sortedResults.length} total)`}
                            </span>
                        </div>
                        <div className="shrink-0 grid grid-cols-[minmax(0,2.5fr)_64px_88px_88px_84px_96px_88px_minmax(0,1fr)] gap-2 px-4 py-2 border-b border-white/5 text-[11px] font-bold tracking-wider text-slate-400 uppercase bg-black/20">
                            <span>Item</span>
                            <span className="text-right">Owned</span>
                            <span className="text-right">Buy</span>
                            <span className="text-right">Sell</span>
                            <span className="text-right">Overstock</span>
                            <span className="text-right">Profit</span>
                            <span className="text-right">Profit %</span>
                            <span>Accounts</span>
                        </div>
                        <div className="flex-1 overflow-y-auto custom-scrollbar min-h-0">
                            {visibleResults.map((item, idx) => {
                                const mhn = item.itemName.marketHashName;
                                const owned = byHash?.[mhn] ?? null;
                                const hasHold = owned?.instances.some(i => i.on_trade_hold) ?? false;
                                // Group instances per account, with a per-location breakdown for the tooltip.
                                const accountSummary = owned
                                    ? Object.values(owned.instances.reduce((acc, i) => {
                                        const a = (acc[i.account_name] ||= { name: i.account_name, count: 0, locations: {} });
                                        a.count += 1;
                                        const loc = i.storage_unit ? `${i.location}: ${i.storage_unit}` : (i.location || 'Unknown');
                                        a.locations[loc] = (a.locations[loc] || 0) + 1;
                                        return acc;
                                    }, {}))
                                    : [];

                                return (
                                    <div
                                        key={`${mhn}-${idx}`}
                                        className="grid grid-cols-[minmax(0,2.5fr)_64px_88px_88px_84px_96px_88px_minmax(0,1fr)] gap-2 items-center px-4 py-2.5 border-b border-white/5 hover:bg-white/[0.03] transition-colors"
                                    >
                                        <div className="flex items-center gap-2.5 min-w-0">
                                            {item.imageUrl && (
                                                <img
                                                    src={item.imageUrl}
                                                    alt=""
                                                    className="w-9 h-9 object-contain shrink-0 rounded bg-black/20"
                                                    loading="lazy"
                                                    decoding="async"
                                                    referrerPolicy="no-referrer"
                                                />
                                            )}
                                            <div className="min-w-0">
                                                <div className="flex items-center gap-1.5 min-w-0">
                                                    <p
                                                        className="text-base text-white truncate cursor-pointer hover:text-amber-300 transition-colors"
                                                        title={`${mhn}\n(click to copy)`}
                                                        onClick={() => copyItemName(mhn)}
                                                    >
                                                        {mhn}
                                                    </p>
                                                    {copiedName === mhn && (
                                                        <span className="shrink-0 text-[10px] font-medium text-emerald-400">copied</span>
                                                    )}
                                                    <SteamMarketLink itemName={mhn} />
                                                    <BuffMarketLink itemName={mhn} />
                                                    <LisSkinsMarketLink itemName={mhn} />
                                                    <CSFloatMarketLink itemName={mhn} />
                                                </div>
                                                {hasHold && (
                                                    <span className="text-[11px] text-orange-400">trade hold on some</span>
                                                )}
                                            </div>
                                        </div>

                                        <span className="text-base text-right font-bold text-amber-400 tabular-nums">{owned ? owned.count : <span className="text-slate-600">—</span>}</span>
                                        <PriceCell market={activeProfile.buyMarket} itemName={mhn} price={item.firstMarket?.price} className="text-base text-right text-slate-300 tabular-nums" />
                                        {activeProfile.sellMarket === 'LootFarm'
                                            ? <LootfarmSellCell itemName={mhn} price={item.secondMarket?.price} rate={item.secondMarket?.rate} />
                                            : <PriceCell market={activeProfile.sellMarket} itemName={mhn} price={item.secondMarket?.price} className="text-base text-right text-slate-300 tabular-nums" />}
                                        <OverstockCell info={item.secondMarket?.overstockInfo} className="text-sm text-right tabular-nums" />
                                        <span className={`text-base text-right tabular-nums ${(item.profit ?? 0) <= 0 ? 'text-red-400' : 'text-emerald-400'}`}>${item.profit?.toFixed(2)}</span>
                                        <span className={`text-base text-right font-semibold tabular-nums ${(item.profitPercent ?? 0) <= 0 ? 'text-red-400' : 'text-emerald-400'}`}>{item.profitPercent?.toFixed(0)}%</span>

                                        <div className="flex flex-wrap gap-1">
                                            {accountSummary.map((a) => (
                                                <span
                                                    key={a.name}
                                                    title={Object.entries(a.locations).map(([l, c]) => `${l} ×${c}`).join('\n')}
                                                    className="inline-flex items-center gap-1 rounded bg-white/5 border border-white/10 px-2 py-0.5 text-xs text-slate-300"
                                                >
                                                    <span className="truncate max-w-[9rem]">{a.name}</span>
                                                    {a.count > 1 && <span className="text-amber-400 font-medium">×{a.count}</span>}
                                                </span>
                                            ))}
                                        </div>
                                    </div>
                                );
                            })}

                            {visibleCount < filteredResults.length && (
                                <div className="flex justify-center py-4">
                                    <button
                                        type="button"
                                        onClick={() => setVisibleCount(c => c + PAGE_SIZE)}
                                        className="px-4 py-2 rounded-lg bg-white/5 border border-white/10 text-sm text-slate-300 hover:text-white hover:border-white/20 transition-colors"
                                    >
                                        Load {Math.min(PAGE_SIZE, filteredResults.length - visibleCount)} more
                                        <span className="text-slate-500 ml-2">({filteredResults.length - visibleCount} left)</span>
                                    </button>
                                </div>
                            )}
                        </div>
                    </div>
                ) : (
                    <div className="flex-1 flex items-center justify-center text-slate-600 text-sm">
                        {!tradeonData
                            ? 'Click "Fetch live data" to load deals for this profile. "Get all items" highlights the ones you own.'
                            : 'No deals to show.'}
                    </div>
                )}
                </>)}
            </div>
        </div>
    );
};

export default HuginnArbitrage;
