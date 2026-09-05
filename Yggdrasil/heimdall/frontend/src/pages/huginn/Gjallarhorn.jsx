import { useState, useEffect, useCallback, useRef } from 'react';
import { Link } from 'react-router-dom';
import { LayoutDashboard, RefreshCw, Siren, Filter, HelpCircle, Phone } from 'lucide-react';
import RotationTable from '../../components/gjallarhorn/RotationTable';
import InfoTip from '../../components/gjallarhorn/InfoTip';
import HelpModal from '../../components/gjallarhorn/HelpModal';
import MarketHoldEditor from '../../components/gjallarhorn/MarketHoldEditor';
import TargetBasket from '../../components/gjallarhorn/TargetBasket';
import ReadinessPanel from '../../components/gjallarhorn/ReadinessPanel';

// Reference markets the "current price" can be read on (mirrors the backend
// _PRICE_MARKETS plus the virtual 'lowest').
const MARKETS = [
    { id: 'steam', label: 'Steam' },
    { id: 'buff', label: 'Buff163' },
    { id: 'csfloat', label: 'CSFloat' },
    { id: 'dmarket', label: 'DMarket' },
    { id: 'lisskins', label: 'LisSkins' },
    { id: 'lowest', label: 'Lowest' },
];

const money = (v) => `$${Number(v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const Stat = ({ label, value, tone }) => (
    <div className="px-4 py-2.5 rounded-xl bg-odin-blue/40 border border-white/10">
        <p className="text-[10px] uppercase tracking-wider text-slate-500">{label}</p>
        <p className={`text-lg font-bold tabular-nums ${tone || 'text-slate-200'}`}>{value}</p>
    </div>
);

const Gjallarhorn = () => {
    const [portfolios, setPortfolios] = useState([]);
    const [accounts, setAccounts] = useState([]);
    const [portfolioId, setPortfolioId] = useState('combined');
    const [steamid, setSteamid] = useState('');
    const [market, setMarket] = useState('steam');
    const [deflatedOnly, setDeflatedOnly] = useState(false);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [showHelp, setShowHelp] = useState(false);
    const [ringMsg, setRingMsg] = useState(null);
    const [ringing, setRinging] = useState(false);
    const pollRef = useRef(null);

    // Ring the Telegram target (test the event alarm). Event handler, so setState here is fine.
    const ringTest = () => {
        setRinging(true);
        setRingMsg('Ringing your Telegram…');
        fetch('/api/huginn/gjallarhorn/ring', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ seconds: 8 }),
        })
            .then((r) => r.json())
            .then((d) => setRingMsg(d.ok ? '📞 Ringing — check your phone.' : `Ring failed: ${d.error || 'error'}`))
            .catch(() => setRingMsg('Ring failed: could not reach backend.'))
            .finally(() => setRinging(false));
    };

    useEffect(() => {
        fetch('/api/draupnir/portfolios')
            .then((r) => (r.ok ? r.json() : null))
            .then((d) => { if (d && Array.isArray(d.portfolios)) setPortfolios(d.portfolios); })
            .catch(() => {});
        fetch('/api/huginn/gjallarhorn/accounts')
            .then((r) => (r.ok ? r.json() : null))
            .then((d) => { if (Array.isArray(d)) setAccounts(d); })
            .catch(() => {});
    }, []);

    const fetchRotation = useCallback(() => {
        const params = new URLSearchParams({ portfolio: portfolioId, market });
        if (steamid) params.set('steamid', steamid);
        return fetch(`/api/huginn/gjallarhorn/rotation?${params.toString()}`)
            .then((r) => (r.ok ? r.json() : null))
            .then((d) => { if (d && !d.error) setData(d); })
            .catch(() => {})
            .finally(() => setLoading(false));
    }, [portfolioId, market, steamid]);

    // Refresh button (event handler — spinner state is fine to set here).
    const refresh = () => { setLoading(true); fetchRotation(); };

    useEffect(() => { fetchRotation(); }, [fetchRotation]);

    // Auto-poll while prices/liquidity are still warming so the table fills in
    // without the user re-clicking. Re-armed each time data changes.
    useEffect(() => {
        clearTimeout(pollRef.current);
        const warming = data && (data.pricing === 'refreshing' || data.liquidity === 'warming');
        if (warming) pollRef.current = setTimeout(() => fetchRotation(), 8000);
        return () => clearTimeout(pollRef.current);
    }, [data, fetchRotation]);

    const summary = data?.summary;
    const rows = data?.rows || [];
    const overlayActive = !!steamid;
    const selectedAccount = accounts.find((a) => a.steamid === steamid);

    const statusNote = () => {
        if (!data) return null;
        const bits = [];
        if (data.pricing === 'refreshing') bits.push('prices warming');
        else if (data.pricing === 'no_token') bits.push('no tradeon token');
        if (data.liquidity === 'warming') bits.push('Steam liquidity warming');
        return bits.length ? bits.join(' · ') : null;
    };

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
                    <Siren size={17} className="text-amber-500" /> Gjallarhorn
                </h1>
                <button
                    type="button"
                    onClick={() => setShowHelp(true)}
                    className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs text-slate-400 hover:text-amber-200 hover:bg-white/5 border border-white/10 transition-colors"
                >
                    <HelpCircle size={13} /> How to use
                </button>
                <InfoTip tip="Rings your Telegram from the burner caller account, to test the event alarm. Needs telegram_caller.json set up first (run telegram_caller_login.py).">
                    <button
                        type="button"
                        onClick={ringTest}
                        disabled={ringing}
                        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs text-slate-400 hover:text-emerald-200 hover:bg-white/5 border border-white/10 transition-colors disabled:opacity-50"
                    >
                        <Phone size={13} className={ringing ? 'animate-pulse' : ''} /> Ring test
                    </button>
                </InfoTip>

                <div className="ml-auto flex items-center gap-2 flex-wrap">
                    <select
                        value={portfolioId} onChange={(e) => setPortfolioId(e.target.value)}
                        className="bg-black/30 border border-white/10 rounded-lg px-2.5 py-2 text-sm text-slate-200 outline-none focus:border-amber-500/40"
                    >
                        <option value="combined">Combined (all portfolios)</option>
                        {portfolios.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                    <select
                        value={steamid} onChange={(e) => setSteamid(e.target.value)}
                        className="bg-black/30 border border-white/10 rounded-lg px-2.5 py-2 text-sm text-slate-200 outline-none focus:border-amber-500/40"
                        title="Overlay a connected account's tradable-now status"
                    >
                        <option value="">No tradable overlay</option>
                        {accounts.map((a) => (
                            <option key={a.steamid} value={a.steamid}>{a.account_name}{a.connected ? ' ●' : ''}</option>
                        ))}
                    </select>
                    <select
                        value={market} onChange={(e) => setMarket(e.target.value)}
                        className="bg-black/30 border border-white/10 rounded-lg px-2.5 py-2 text-sm text-slate-200 outline-none focus:border-amber-500/40"
                        title="Reference market for current price"
                    >
                        {MARKETS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                    </select>
                    <MarketHoldEditor />
                    <TargetBasket market={market} defaultCapital={summary?.liquidatableNow} />
                    <InfoTip tip="Re-fetch holdings, current prices and Steam liquidity for the selected portfolio and account. Prices and 7-day volume warm in the background and fill in over a few seconds — the table auto-refreshes while they do.">
                        <button
                            onClick={refresh} disabled={loading}
                            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-sm font-medium disabled:opacity-50 transition-colors"
                        >
                            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh
                        </button>
                    </InfoTip>
                </div>
            </div>

            <div className="flex-1 flex flex-col gap-3 p-6 overflow-hidden max-w-7xl w-full mx-auto">
                {/* Summary */}
                <div className="shrink-0 flex flex-wrap items-stretch gap-2">
                    <Stat label="Holdings" value={summary ? summary.holdings.toLocaleString() : '—'} />
                    <Stat label="Deflated" value={summary ? summary.deflated.toLocaleString() : '—'} tone="text-red-400" />
                    <Stat label="Total value" value={summary ? money(summary.totalValue) : '—'} />
                    <Stat label="Deflated value" value={summary ? money(summary.deflatedValue) : '—'} tone="text-red-300" />
                    <Stat label="Liquidatable now" value={summary ? money(summary.liquidatableNow) : '—'} tone="text-emerald-400" />
                    <div className="flex-1 min-w-[10rem]">
                        <ReadinessPanel steamid={steamid} accountName={selectedAccount?.account_name} />
                    </div>
                </div>

                {/* Controls row */}
                <div className="shrink-0 flex items-center gap-3">
                    <button
                        type="button"
                        onClick={() => setDeflatedOnly((v) => !v)}
                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${deflatedOnly ? 'bg-red-500/20 border-red-500/40 text-red-300' : 'bg-black/30 border-white/10 text-slate-400 hover:text-slate-200'}`}
                    >
                        <Filter size={12} /> Deflated only
                    </button>
                    {statusNote() && (
                        <span className="text-xs text-amber-400/70 flex items-center gap-1.5">
                            <RefreshCw size={11} className="animate-spin" /> {statusNote()}
                        </span>
                    )}
                    {!overlayActive && (
                        <span className="text-xs text-slate-600">Pick an account for the tradable-now / Steam-hold overlay.</span>
                    )}
                    {ringMsg && <span className="text-xs text-emerald-300/80 ml-auto">{ringMsg}</span>}
                </div>

                <RotationTable rows={rows} overlayActive={overlayActive} deflatedOnly={deflatedOnly} />
            </div>

            {showHelp && <HelpModal onClose={() => setShowHelp(false)} />}
        </div>
    );
};

export default Gjallarhorn;
