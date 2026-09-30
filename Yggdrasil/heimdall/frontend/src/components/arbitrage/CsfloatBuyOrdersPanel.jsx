import { useState, useEffect, useCallback, useRef } from 'react';
import { RefreshCw, AlertTriangle, Coins, Radio, Copy } from 'lucide-react';

// CSFloat buy-order sweep panel, shared by the Arbitrage "=> CSFloat (autobuy)"
// profiles and Harvest. CSFloat has no bulk buy-order feed, so its autobuy prices
// come from a background sweep (POST /api/huginn/csfloat/buy-orders) that reads
// each owned item's buy orders; progress and the cache are polled from GET.
//   canFetch / cannotFetchHint  whether a sweep can start, and why not
//   includeHoldings             also sweep items held in Draupnir portfolios (Harvest)
//   onSwept                     called once a sweep finishes (to reload prices)

const formatTs = (ts) => {
    if (!ts) return null;
    const diffMin = Math.round((Date.now() - new Date(ts).getTime()) / 60000);
    if (diffMin < 1) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffH = Math.round(diffMin / 60);
    return diffH < 24 ? `${diffH}h ago` : new Date(ts).toLocaleDateString();
};

// Shows the exact public IP the user must add to the Bright Data zone allowlist,
// as a click-to-copy chip.
const WhitelistIp = ({ ip, onCopy, copied }) => {
    if (!ip) {
        return (
            <span className="block mt-1 text-red-300/80">
                (Could not auto-detect this server&apos;s public IP — find it with{' '}
                <span className="font-mono">curl api.ipify.org</span>.)
            </span>
        );
    }
    return (
        <span className="mt-1 flex items-center gap-1.5 flex-wrap">
            <span className="text-red-200/90">This server&apos;s IP to whitelist:</span>
            <button
                type="button"
                onClick={() => onCopy(ip)}
                title="Click to copy"
                className="inline-flex items-center gap-1 rounded bg-black/40 border border-red-500/40 px-1.5 py-0.5 font-mono text-red-100 hover:border-red-400 transition-colors"
            >
                {ip}
                {copied === ip
                    ? <span className="text-emerald-400">copied</span>
                    : <Copy size={10} className="opacity-70" />}
            </button>
        </span>
    );
};

const CsfloatBuyOrdersPanel = ({ canFetch = true, cannotFetchHint = '', includeHoldings = false, onSwept }) => {
    // {job:{running,done,total,found,...}, cache:{count,fetched_at,...}, keys, proxy_*}
    const [csfloatStatus, setCsfloatStatus] = useState(null);
    const csfloatJobRunning = csfloatStatus?.job?.running ?? false;
    const [startError, setStartError] = useState(null);
    // On-demand connectivity probe (direct + proxy) so the proxy / IP whitelist can be
    // verified without running a full sweep. {proxy_enabled, direct, proxy, usable}.
    const [connCheck, setConnCheck] = useState(null);
    const [connChecking, setConnChecking] = useState(false);
    const [copiedName, setCopiedName] = useState('');

    const fetchCsfloatStatus = useCallback(async () => {
        try {
            const r = await fetch('/api/huginn/csfloat/buy-orders');
            if (r.ok) setCsfloatStatus(await r.json());
        } catch { /* ignore */ }
    }, []);

    useEffect(() => { fetchCsfloatStatus(); }, [fetchCsfloatStatus]);
    // Poll while a sweep is running so the progress bar advances.
    useEffect(() => {
        if (!csfloatJobRunning) return undefined;
        const t = setInterval(fetchCsfloatStatus, 1500);
        return () => clearInterval(t);
    }, [csfloatJobRunning, fetchCsfloatStatus]);
    // Tell the parent once a sweep it watched has finished (reload its prices).
    const wasRunning = useRef(false);
    useEffect(() => {
        if (wasRunning.current && !csfloatJobRunning && onSwept) onSwept();
        wasRunning.current = csfloatJobRunning;
    }, [csfloatJobRunning, onSwept]);

    const copyItemName = (name) => {
        navigator.clipboard?.writeText(name).catch(() => {});
        setCopiedName(name);
        setTimeout(() => setCopiedName(c => (c === name ? '' : c)), 1200);
    };

    const handleTestConnection = async () => {
        if (connChecking) return;
        setConnChecking(true);
        setConnCheck(null);
        try {
            const r = await fetch('/api/huginn/csfloat/connectivity');
            const d = await r.json().catch(() => ({}));
            if (!r.ok) { setConnCheck({ error: d.error || 'Connectivity check failed' }); return; }
            setConnCheck(d);
        } catch (err) {
            setConnCheck({ error: err.message });
        } finally {
            setConnChecking(false);
        }
    };

    const handleFetchBuyOrders = async () => {
        try {
            const url = `/api/huginn/csfloat/buy-orders${includeHoldings ? '?include_holdings=1' : ''}`;
            const r = await fetch(url, { method: 'POST' });
            const d = await r.json().catch(() => ({}));
            if (!r.ok) { setStartError(d.error || 'Could not start CSFloat buy-order sweep'); return; }
            setStartError(null);
            fetchCsfloatStatus();
        } catch (err) {
            setStartError(err.message);
        }
    };

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
                    onClick={handleTestConnection}
                    disabled={connChecking || csfloatJobRunning}
                    title="Ping CSFloat direct + proxy to check the proxy / IP whitelist without running a full sweep"
                    className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-black/30 border border-white/10 hover:border-white/25 text-slate-300 hover:text-white text-xs font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors shrink-0"
                >
                    <Radio size={12} className={connChecking ? 'animate-pulse' : ''} />
                    {connChecking ? 'Testing…' : 'Test connection'}
                </button>
                <button
                    type="button"
                    onClick={handleFetchBuyOrders}
                    disabled={csfloatJobRunning || !canFetch}
                    title={!canFetch ? cannotFetchHint : 'Fetch CSFloat buy orders for your owned items'}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-purple-600/70 hover:bg-purple-500 text-white text-xs font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors shrink-0"
                >
                    <RefreshCw size={12} className={csfloatJobRunning ? 'animate-spin' : ''} />
                    {btnLabel}
                </button>
            </div>

            {/* Explicit proxy-failure banner — Bright Data ip_forbidden and the
                like are config problems the user must fix, so call them out
                plainly rather than burying them in a raw connection string. */}
            {csfloatStatus?.proxy_hint?.hint && (
                <div className="mt-2 flex items-start gap-2 rounded-lg bg-red-500/10 border border-red-500/30 px-3 py-2">
                    <AlertTriangle size={14} className="text-red-400 shrink-0 mt-0.5" />
                    <div className="text-[11px] text-red-300">
                        <span className="font-bold">
                            {csfloatStatus.proxy_hint.code === 'ip_forbidden'
                                ? 'Proxy rejected this IP (ip_forbidden).'
                                : 'Proxy authentication failed.'}
                        </span>{' '}
                        {csfloatStatus.proxy_hint.hint}
                        <WhitelistIp ip={csfloatStatus.public_ip} onCopy={copyItemName} copied={copiedName} />
                    </div>
                </div>
            )}
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
                reused by every CSFloat (autobuy) view until you refresh. If CSFloat throttles
                us the sweep pauses and Resume continues where it stopped (within 2h).
                {!canFetch && ` ${cannotFetchHint}.`}
            </p>
            {paused && cache.reason && (
                <p className="mt-1 text-[11px] text-amber-400/80">Paused: {cache.reason}</p>
            )}
            {startError && (
                <p className="mt-1 text-[11px] text-red-400 flex items-center gap-1">
                    <AlertTriangle size={11} /> {startError}
                </p>
            )}
            {job?.error && (
                <p className="mt-1 text-[11px] text-red-400 flex items-center gap-1">
                    <AlertTriangle size={11} /> {job.error}
                </p>
            )}

            {/* On-demand connectivity probe result */}
            {connCheck && (() => {
                if (connCheck.error) {
                    return (
                        <p className="mt-2 text-[11px] text-red-400 flex items-center gap-1">
                            <AlertTriangle size={11} /> {connCheck.error}
                        </p>
                    );
                }
                const dir = connCheck.direct || {};
                const prx = connCheck.proxy || {};
                const dot = (ok) => ok === true ? 'bg-emerald-400' : ok === null ? 'bg-slate-500' : 'bg-red-400';
                const word = (ok) => ok === true ? 'reachable' : ok === null ? 'not configured' : 'blocked';
                return (
                    <div className="mt-2 rounded-lg bg-black/20 border border-white/10 px-3 py-2 space-y-1">
                        <div className="flex items-center gap-2 text-[11px] text-slate-300">
                            <span className={`w-1.5 h-1.5 rounded-full ${dot(dir.ok)}`} />
                            <span className="font-medium w-14">Direct</span>
                            <span className="text-slate-400">{word(dir.ok)}{dir.rate_limited ? ' (throttled, but reachable)' : ''}</span>
                        </div>
                        <div className="flex items-center gap-2 text-[11px] text-slate-300">
                            <span className={`w-1.5 h-1.5 rounded-full ${dot(prx.ok)}`} />
                            <span className="font-medium w-14">Proxy</span>
                            <span className="text-slate-400">{word(prx.ok)}{prx.rate_limited ? ' (throttled, but reachable)' : ''}</span>
                        </div>
                        {prx.hint && (
                            <div className="flex items-start gap-1.5 text-[11px] text-red-300 pt-0.5">
                                <AlertTriangle size={11} className="shrink-0 mt-0.5" />
                                <span>
                                    <span className="font-bold">
                                        {prx.code === 'ip_forbidden' ? 'ip_forbidden — ' : ''}
                                    </span>
                                    {prx.hint}
                                    <WhitelistIp ip={connCheck.public_ip} onCopy={copyItemName} copied={copiedName} />
                                </span>
                            </div>
                        )}
                        {connCheck.usable
                            ? <p className="text-[10px] text-emerald-400/80">CSFloat is reachable — the sweep can run.</p>
                            : <p className="text-[10px] text-red-400/80">Neither path works — the sweep cannot fetch buy orders until this is fixed.</p>}
                    </div>
                );
            })()}
        </div>
    );
};

export default CsfloatBuyOrdersPanel;
