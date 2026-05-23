import React, { useState, useEffect } from 'react';
import { Outlet, Link, useParams, useLocation, useNavigate } from 'react-router-dom';
import { Package, ArrowRightLeft, Database, LayoutDashboard, Plug, Unplug, Timer } from 'lucide-react';

const STATUS_POLL_MS = 90 * 1000;

const formatIdleLabel = (idleTimeoutMs) => {
    if (idleTimeoutMs === 0) return 'Never';
    const minutes = Math.round(idleTimeoutMs / 60000);
    if (minutes < 60) return `${minutes} min`;
    const hours = minutes / 60;
    return Number.isInteger(hours) ? `${hours} hr` : `${hours.toFixed(1)} hr`;
};

const RatatoskrLayout = () => {
    const { steamid } = useParams();
    const navigate = useNavigate();
    const location = useLocation();

    const [status, setStatus] = useState('checking'); // checking, disconnected, connected
    const [account, setAccount] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [idleTimeoutMs, setIdleTimeoutMs] = useState(60 * 60 * 1000);
    const [idlePresets, setIdlePresets] = useState([]);
    const [idleSaving, setIdleSaving] = useState(false);

    const isConnected = status === 'connected';

    useEffect(() => {
        loadAccount();
        checkStatus();
        fetchSessionIdleConfig();
    }, [steamid]);

    useEffect(() => {
        if (status !== 'connected') return undefined;
        const timer = setInterval(() => {
            checkStatus({ quiet: true });
        }, STATUS_POLL_MS);
        return () => clearInterval(timer);
    }, [status, steamid]);

    const loadAccount = async () => {
        try {
            const res = await fetch('/api/accounts');
            if (!res.ok) return;
            const data = await res.json();
            const found = (data.accounts || []).find((a) => String(a.steamid) === String(steamid));
            setAccount(found || null);
        } catch (err) {
            console.error('Failed to load account', err);
        }
    };

    const fetchSessionIdleConfig = async () => {
        try {
            const res = await fetch('/api/ratatoskr/config/session-idle');
            const data = await res.json();
            if (res.ok && data.idleTimeoutMs != null) {
                setIdleTimeoutMs(data.idleTimeoutMs);
                if (Array.isArray(data.presets)) {
                    setIdlePresets(data.presets);
                }
            }
        } catch (err) {
            console.error('Failed to load session idle config', err);
        }
    };

    const saveSessionIdle = async (ms) => {
        setIdleSaving(true);
        try {
            const res = await fetch('/api/ratatoskr/config/session-idle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ idleTimeoutMs: ms }),
            });
            const data = await res.json();
            if (res.ok && data.idleTimeoutMs != null) {
                setIdleTimeoutMs(data.idleTimeoutMs);
            }
        } catch (err) {
            console.error('Failed to save session idle config', err);
        } finally {
            setIdleSaving(false);
        }
    };

    const checkStatus = async ({ quiet = false } = {}) => {
        if (!quiet) setLoading(true);
        try {
            const res = await fetch(`/api/ratatoskr/status/${steamid}`);
            const data = await res.json();
            if (res.ok && data.status === 'connected') {
                setStatus('connected');
                setError(null);
                if (data.idleTimeoutMs != null) {
                    setIdleTimeoutMs(data.idleTimeoutMs);
                }
            } else if (data.status === 'gc_lost') {
                setStatus('disconnected');
                setError('Game Coordinator connection lost. Click Connect to restore.');
            } else {
                setStatus('disconnected');
            }
        } catch (err) {
            console.error(err);
            if (!quiet) setStatus('disconnected');
        } finally {
            if (!quiet) setLoading(false);
        }
    };

    const handleConnect = async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await fetch('/api/ratatoskr/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ steam_id: steamid }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Login failed');
            setStatus('connected');
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    const handleDisconnect = async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await fetch('/api/ratatoskr/disconnect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ steam_id: steamid }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Disconnect failed');
            setStatus('disconnected');
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    const navItems = [
        { path: 'inventory', icon: Package, label: 'Inventory' },
        { path: 'transfer', icon: ArrowRightLeft, label: 'Transfer' },
    ];

    const accountLabel = account?.account_name || 'Unknown account';
    const accountSteamId = account?.steamid || steamid;

    const AccountBadge = () => (
        <div
            className={`rounded-xl border p-3 mt-4 ${isConnected
                ? 'border-emerald-500/20 bg-emerald-500/5'
                : 'border-white/10 bg-black/20'
                }`}
        >
            <p
                className={`text-[10px] uppercase tracking-wider font-medium mb-1.5 ${isConnected ? 'text-emerald-400/80' : 'text-slate-500'}`}
            >
                {isConnected ? 'Connected account' : 'Account'}
            </p>
            <p className="text-sm font-semibold text-white truncate" title={accountLabel}>
                {accountLabel}
            </p>
            <p className="text-[10px] text-slate-500 font-mono truncate mt-0.5" title={accountSteamId}>
                {accountSteamId}
            </p>
        </div>
    );

    const ConnectionControls = () => (
        <div className="mt-3 space-y-2">
            {isConnected ? (
                <button
                    type="button"
                    onClick={handleDisconnect}
                    disabled={loading}
                    className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-medium border border-red-500/30 bg-red-500/10 text-red-300 hover:bg-red-500/20 hover:text-red-200 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
                >
                    {loading ? (
                        <div className="w-4 h-4 border-2 border-red-300/30 border-t-red-200 rounded-full animate-spin" />
                    ) : (
                        <Unplug size={16} />
                    )}
                    <span>{loading ? 'Disconnecting…' : 'Disconnect'}</span>
                </button>
            ) : (
                <button
                    type="button"
                    onClick={handleConnect}
                    disabled={loading || status === 'checking'}
                    className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-medium bg-gradient-to-r from-amber-600 to-amber-700 hover:from-amber-500 hover:to-amber-600 text-white shadow-lg shadow-amber-900/20 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
                >
                    {loading ? (
                        <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    ) : (
                        <Plug size={16} />
                    )}
                    <span>{loading ? 'Connecting…' : 'Connect'}</span>
                </button>
            )}
            {error && (
                <p className="text-xs text-red-400 bg-red-900/20 border border-red-500/20 rounded-lg px-2.5 py-2 text-center">
                    {error}
                </p>
            )}
        </div>
    );

    const SessionIdleControl = () => {
        const options =
            idlePresets.length > 0
                ? idlePresets
                : [
                      { label: '15 minutes', idleTimeoutMs: 15 * 60 * 1000 },
                      { label: '30 minutes', idleTimeoutMs: 30 * 60 * 1000 },
                      { label: '1 hour', idleTimeoutMs: 60 * 60 * 1000 },
                      { label: '2 hours', idleTimeoutMs: 2 * 60 * 60 * 1000 },
                      { label: '4 hours', idleTimeoutMs: 4 * 60 * 60 * 1000 },
                      { label: 'Never', idleTimeoutMs: 0 },
                  ];

        return (
            <div className="mt-4 pt-4 border-t border-white/5">
                <label className="flex items-center gap-2 text-[10px] uppercase tracking-wider text-slate-500 font-medium mb-2">
                    <Timer size={12} />
                    Auto-disconnect when idle
                </label>
                <select
                    value={String(idleTimeoutMs)}
                    disabled={idleSaving}
                    onChange={(e) => saveSessionIdle(parseInt(e.target.value, 10))}
                    className="w-full rounded-lg bg-black/30 border border-white/10 text-sm text-slate-200 px-3 py-2 focus:outline-none focus:border-amber-500/40 disabled:opacity-50"
                >
                    {options.map((opt) => (
                        <option key={opt.idleTimeoutMs} value={opt.idleTimeoutMs}>
                            {opt.label}
                        </option>
                    ))}
                </select>
                <p className="text-[10px] text-slate-500 mt-1.5 leading-snug">
                    Currently: <span className="text-slate-400">{formatIdleLabel(idleTimeoutMs)}</span>.
                    {isConnected && idleTimeoutMs > 0
                        ? ' This page refreshes your session every ~90s while open.'
                        : idleTimeoutMs === 0
                          ? ' Session stays up until you disconnect.'
                          : null}
                </p>
            </div>
        );
    };

    if (status === 'checking') {
        return (
            <div className="min-h-screen bg-odin-dark flex items-center justify-center">
                <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-amber-500" />
            </div>
        );
    }

    return (
        <div className="min-h-screen bg-odin-dark flex">
            {/* Sidebar */}
            <div className="w-64 bg-odin-blue border-r border-white/5 flex flex-col fixed h-full z-10">
                <div className="p-6 border-b border-white/5">
                    <div className="flex items-center gap-3">
                        <div className="p-2 bg-amber-900/30 rounded-lg border border-amber-600/30">
                            <Database className="text-amber-500" size={20} />
                        </div>
                        <div>
                            <h2 className="font-bold text-amber-100 font-serif tracking-wide">Ratatoskr</h2>
                            <p
                                className={`text-[10px] font-mono uppercase tracking-wider ${isConnected ? 'text-emerald-400' : 'text-slate-500'}`}
                            >
                                {isConnected ? 'Online • Stable' : 'Disconnected'}
                            </p>
                        </div>
                    </div>
                    <AccountBadge />
                    <ConnectionControls />
                    <SessionIdleControl />
                </div>

                <nav className="flex-1 p-4 space-y-1">
                    {navItems.map((item) => {
                        const isActive = location.pathname.includes(item.path);
                        const Icon = item.icon;
                        return (
                            <Link
                                key={item.path}
                                to={item.path}
                                className={`flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-200 group ${!isConnected
                                    ? 'pointer-events-none opacity-40'
                                    : isActive
                                        ? 'bg-amber-600/10 text-amber-400 border border-amber-600/20'
                                        : 'text-slate-400 hover:text-white hover:bg-white/5'
                                    }`}
                            >
                                <Icon
                                    size={18}
                                    className={isActive ? 'text-amber-400' : 'text-slate-500 group-hover:text-white transition-colors'}
                                />
                                <span className="font-medium">{item.label}</span>
                            </Link>
                        );
                    })}
                </nav>

                <div className="p-4 border-t border-white/5">
                    <button
                        type="button"
                        onClick={() => navigate('/')}
                        className="flex items-center gap-3 px-4 py-3 rounded-xl text-slate-400 hover:text-white hover:bg-white/5 w-full transition-all"
                    >
                        <LayoutDashboard size={18} />
                        <span className="font-medium">Dashboard</span>
                    </button>
                </div>
            </div>

            {/* Main Content */}
            <div className="flex-1 ml-64 p-8">
                {isConnected ? (
                    <Outlet context={{ steamid, account }} />
                ) : (
                    <div className="flex flex-col items-center justify-center min-h-[calc(100vh-4rem)] text-center max-w-md mx-auto">
                        <div className="w-16 h-16 bg-amber-900/30 rounded-full flex items-center justify-center mb-4 border border-amber-500/30">
                            <Database className="text-amber-500" size={32} />
                        </div>
                        <h1 className="text-2xl font-bold text-amber-100 font-serif mb-2">Not connected</h1>
                        <p className="text-slate-400 text-sm mb-6">
                            Connect to the Steam Game Coordinator using the button in the sidebar to use Inventory and
                            Transfer.
                        </p>
                        <button
                            type="button"
                            onClick={handleConnect}
                            disabled={loading}
                            className="inline-flex items-center justify-center gap-2 px-6 py-3 rounded-xl text-sm font-medium bg-gradient-to-r from-amber-600 to-amber-700 hover:from-amber-500 hover:to-amber-600 text-white shadow-lg shadow-amber-900/20 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
                        >
                            {loading ? (
                                <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                            ) : (
                                <Plug size={16} />
                            )}
                            <span>{loading ? 'Connecting…' : 'Connect'}</span>
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
};

export default RatatoskrLayout;
