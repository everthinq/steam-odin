import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { LayoutDashboard, Radio, RefreshCw, AlertTriangle, Boxes, Bell, Store, Clock, Pin, Moon, Download, BookOpen } from 'lucide-react';
import { readJson } from '../../utils/readJson';
import { formatAge } from '../../utils/priceAge';
import { MB_PER_DOWNLOAD } from '../../utils/pulseTraffic';

// Huginn → Pulse feed: a read-only monitor of the master pulse feed (backend pulse_feed.py), the
// one place Heimdall downloads Tradeon pulse price tables. Only Huginn Arbitrage downloads (the
// active profiles, kept fresh by the keeper; the Buff163 reference once a day; "Fetch live data";
// "Check live" for one item); every other page is a slave that reads these tables from memory.
// The active profiles are chosen on Huginn → Arbitrage. GET /api/huginn/pulse, every 5 seconds.
const MB_IN_MEMORY = 15;        // one table in the feed's compact form
const keyOf = (market, priceType) => `${market}:${priceType}`;
const JOB_LOOK = {
    'case-arbitrage-hourly': { short: 'Case', Icon: Boxes },
    'case-arbitrage-alerts': { short: 'Alerts', Icon: Bell },
    'store-arbitrage': { short: 'Store', Icon: Store },
};
const jobLook = (job) => JOB_LOOK[job.id] || { short: job.label.split(' ')[0], Icon: Clock };
const clock = (epochSeconds) => new Date(epochSeconds * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

const Stat = ({ label, value, sub, tone = 'text-slate-100' }) => (
    <div className="flex-1 min-w-[10rem] bg-slate-950/85 backdrop-blur-sm border border-white/10 rounded-xl px-4 py-3">
        <div className="text-[10px] font-bold tracking-widest text-slate-500 uppercase">{label}</div>
        <div className={`text-2xl font-semibold tabular-nums mt-0.5 ${tone}`}>{value}</div>
        {sub && <div className="text-xs text-slate-500 mt-0.5">{sub}</div>}
    </div>
);

const Role = ({ tone, children, title }) => (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[11px] ${tone}`} title={title}>{children}</span>
);

const PulseFeed = () => {
    const [status, setStatus] = useState(null);
    const [error, setError] = useState(null);
    const request = useRef(0);

    const load = useCallback(async () => {
        const n = ++request.current;
        try {
            const response = await fetch('/api/huginn/pulse');
            const answer = await readJson(response);
            if (n !== request.current) return;
            if (!response.ok) throw new Error(answer.error || 'Could not read the pulse feed');
            setStatus(answer);
            setError(null);
        } catch (failure) {
            if (n === request.current) setError(failure.message);
        }
    }, []);

    // Memory only on the backend (cheap): every 5 seconds while the page is open and visible.
    useEffect(() => {
        const tick = () => { if (!document.hidden) load(); };
        const first = setTimeout(load, 0);
        const timer = setInterval(tick, 5000);
        return () => { clearTimeout(first); clearInterval(timer); };
    }, [load]);

    const jobsById = useMemo(() => Object.fromEntries((status?.jobs || []).map(job => [job.id, job])), [status]);
    // Which active profiles keep each table: key -> [labels].
    const profilesByKey = useMemo(() => {
        const out = {};
        for (const profile of Object.values(status?.active?.profiles || {})) {
            for (const table of profile.tables) (out[keyOf(table.market, table.price_type)] ||= []).push(profile.label);
        }
        return out;
    }, [status]);
    const rows = useMemo(() => (status?.tables || [])
        .filter(table => table.active || table.downloads || table.reads || table.once)
        .sort((first, second) => Number(second.active) - Number(first.active)
            || (second.last_download_at || 0) - (first.last_download_at || 0)), [status]);

    const keeper = status?.keeper;
    const memoryShare = status && status.reads ? Math.round((status.served_from_memory / status.reads) * 100) : null;
    const minutes = status?.active?.minutes;
    const activeCount = Object.keys(status?.active?.profiles || {}).length;
    const keeperText = !keeper ? '—' : keeper.paused ? 'paused' : keeper.behind ? 'behind' : keeper.running ? 'running' : 'stopped';
    const keeperSub = !keeper ? null
        : keeper.paused ? `active hours: resumes at ${keeper.resumes_at ? clock(keeper.resumes_at) : '—'}`
            : keeper.last_pass ? `last pass ${clock(keeper.last_pass.at)}: ${keeper.last_pass.downloads} table${keeper.last_pass.downloads === 1 ? '' : 's'} in ${Math.round(keeper.last_pass.seconds)} s`
                : 'no download yet';

    return (
        <div className="h-screen bg-odin-dark flex flex-col overflow-hidden">
            <div className="shrink-0 border-b border-white/5 bg-slate-950/70 backdrop-blur px-6 py-4 flex items-center gap-3 flex-wrap">
                <Link to="/" className="flex items-center gap-1.5 text-sm text-slate-400 hover:text-white transition-colors shrink-0">
                    <LayoutDashboard size={15} /> Dashboard
                </Link>
                <span className="text-white/20">/</span>
                <Link to="/huginn" className="text-sm text-slate-400 hover:text-white transition-colors">Huginn</Link>
                <span className="text-white/20">/</span>
                <h1 className="text-lg font-bold font-serif text-amber-100 flex items-center gap-2"><Radio size={16} className="text-amber-400" /> Pulse feed</h1>
                <button type="button" onClick={load} className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-white/10 text-xs text-slate-300 hover:text-white">
                    <RefreshCw size={12} /> Reload
                </button>
            </div>

            <div className="flex-1 overflow-y-auto custom-scrollbar">
                <div className="max-w-6xl mx-auto px-6 pt-6 pb-16 flex flex-col gap-6">
                    <div className="text-sm text-slate-300 leading-relaxed bg-slate-950/85 backdrop-blur-sm border border-white/10 rounded-xl px-4 py-3">
                        Every price Heimdall shows comes from Tradeon pulse, and <span className="text-slate-100">only Huginn Arbitrage downloads</span>:
                        the keeper keeps your active profiles' tables fresh{minutes ? ` (every ${minutes} minutes)` : ''} and Buff163's lowest
                        listing once a day, "Fetch live data" downloads a profile now, and "Check live" asks for one item. Every other page
                        (Case Arbitrage and its alerts, Store Catalogue, Store Boosts, Harvest, Cross-Profile, Price dips, Gjallarhorn,
                        Draupnir) reads these tables from memory and never downloads. This page only watches.{' '}
                        <Link to="/huginn" className="text-sky-300 hover:text-sky-200 whitespace-nowrap">Choose the active profiles on Huginn → Arbitrage</Link>.
                    </div>

                    {error && (
                        <div className="bg-red-500/15 border border-red-500/30 text-red-300 px-4 py-2.5 rounded-lg text-sm flex items-center gap-2">
                            <AlertTriangle size={14} /> {error}
                        </div>
                    )}
                    {status && !status.token_set && (
                        <div className="bg-amber-500/10 border border-amber-500/30 text-amber-200 px-4 py-2.5 rounded-lg text-sm">
                            No tradeon_token in backend/settings.json: nothing can be downloaded.
                        </div>
                    )}

                    <div className="flex flex-wrap gap-3">
                        <Stat label="Active profiles" value={status ? `${activeCount}` : '—'}
                            sub={status ? `${status.active.tables_used} of ${status.active.max_tables} tables + the reference` : null} />
                        <Stat label="Keeper" value={keeperText} sub={keeperSub}
                            tone={keeper?.behind ? 'text-amber-300' : keeper?.paused ? 'text-indigo-200' : 'text-slate-100'} />
                        <Stat label="Downloaded since start" value={status ? status.downloads.toLocaleString() : '—'}
                            sub={status ? `≈ ${((status.downloads * MB_PER_DOWNLOAD) / 1024).toFixed(1)} GB · ${status.lookups.count} single-item checks` : null} />
                        <Stat label="Read from memory" value={memoryShare == null ? '—' : `${memoryShare}%`}
                            sub={status ? `${status.reads.toLocaleString()} reads · ${status.tables_in_memory} tables, about ${status.tables_in_memory * MB_IN_MEMORY} MB` : null} />
                    </div>

                    {keeper?.sleep && (
                        <p className="text-xs text-slate-400 flex items-center gap-1.5">
                            <Moon size={12} /> The Mac slept from {clock(keeper.sleep.from)} to {clock(keeper.sleep.to)}; the keeper waited a tick for the network and caught up.
                        </p>
                    )}

                    <section className="bg-slate-950/85 backdrop-blur-sm border border-white/10 rounded-xl overflow-hidden">
                        <div className="px-4 py-3 border-b border-white/5 flex flex-wrap items-center gap-3">
                            <h2 className="text-[11px] font-bold tracking-widest text-slate-400 uppercase">Tables</h2>
                            <span className="text-xs text-slate-500">active first; the others were downloaded by "Fetch live data" and leave memory after 10 minutes</span>
                        </div>
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                    <tr className="text-[11px] uppercase tracking-wider text-slate-500 bg-black/20">
                                        <th className="text-left font-semibold px-4 py-2">Table</th>
                                        <th className="text-left font-semibold px-3 py-2">Why it is downloaded</th>
                                        <th className="text-right font-semibold px-3 py-2">Age</th>
                                        <th className="text-right font-semibold px-3 py-2">Items</th>
                                        <th className="text-right font-semibold px-3 py-2" title="Downloads since the backend started">Downloads</th>
                                        <th className="text-right font-semibold px-3 py-2" title="Reads by pages and jobs since the backend started">Reads</th>
                                        <th className="text-left font-semibold px-3 py-2">Read by</th>
                                        <th className="text-left font-semibold px-3 py-2">Problem</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {rows.map(table => {
                                        const key = keyOf(table.market, table.price_type);
                                        const profiles = profilesByKey[key] || [];
                                        const readers = Object.keys(table.jobs || {}).map(id => jobsById[id]).filter(Boolean);
                                        const seed = ['Median30d', 'Average30d'].includes(table.price_type);
                                        return (
                                            <tr key={key} className="border-t border-white/5 hover:bg-white/[0.02]">
                                                <td className="px-4 py-1.5 whitespace-nowrap text-slate-200">{status.labels?.[key] || key}</td>
                                                <td className="px-3 py-1.5">
                                                    <span className="flex flex-wrap gap-1">
                                                        {profiles.length > 0 && (
                                                            <Role tone="border-sky-400/40 text-sky-200 bg-sky-500/10" title={profiles.join('\n')}>
                                                                <Pin size={10} /> {profiles.length === 1 ? profiles[0] : `${profiles.length} active profiles`}
                                                            </Role>
                                                        )}
                                                        {table.reference && <Role tone="border-amber-500/40 text-amber-200 bg-amber-500/10" title="Off-Steam price reference: Buff163 is the main market, read once a day">reference, daily</Role>}
                                                        {seed && <Role tone="border-violet-400/40 text-violet-200 bg-violet-500/10" title="Downloaded once to seed the price history's 30-day prices">
                                                            <BookOpen size={10} /> history seed{table.once ? ', pending' : ''}</Role>}
                                                        {!table.active && !seed && table.downloads > 0 && <Role tone="border-white/10 text-slate-400" title="Downloaded by a profile's Fetch live data">
                                                            <Download size={10} /> Fetch live data</Role>}
                                                        {!table.active && !seed && !table.downloads && <Role tone="border-white/10 border-dashed text-slate-500" title="A page asked for it, but no active Huginn profile uses it: it is never downloaded (the page shows it as not active)">
                                                            not active</Role>}
                                                    </span>
                                                </td>
                                                <td className="px-3 py-1.5 text-right tabular-nums text-slate-300 whitespace-nowrap">
                                                    {table.downloading ? 'downloading…' : table.age_seconds != null ? formatAge(table.age_seconds) : '—'}
                                                </td>
                                                <td className="px-3 py-1.5 text-right tabular-nums text-slate-300">{table.items != null ? table.items.toLocaleString() : '—'}</td>
                                                <td className="px-3 py-1.5 text-right tabular-nums text-slate-300">{table.downloads}</td>
                                                <td className="px-3 py-1.5 text-right tabular-nums text-slate-300">{table.reads}</td>
                                                <td className="px-3 py-1.5">
                                                    <span className="flex flex-wrap gap-1">
                                                        {readers.map(job => {
                                                            const { short, Icon } = jobLook(job);
                                                            return <Role key={job.id} tone="border-white/10 text-slate-300" title={job.label}><Icon size={10} /> {short}</Role>;
                                                        })}
                                                    </span>
                                                </td>
                                                <td className="px-3 py-1.5 text-xs text-red-300/80 max-w-[16rem] truncate" title={table.last_error || ''}>
                                                    {table.last_error || (table.failures ? `${table.failures} failed earlier` : '')}
                                                </td>
                                            </tr>
                                        );
                                    })}
                                    {rows.length === 0 && (
                                        <tr><td colSpan={8} className="px-4 py-6 text-center text-sm text-slate-500">
                                            Nothing downloaded yet. Make a profile you trade on active on Huginn → Arbitrage.
                                        </td></tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </section>

                    <section>
                        <h2 className="text-[11px] font-bold tracking-widest text-slate-300 uppercase mb-2 drop-shadow">Slaves (read memory, never download)</h2>
                        <div className="grid gap-3 md:grid-cols-2">
                            {(status?.jobs || []).map(job => {
                                const { Icon } = jobLook(job);
                                const inMemory = job.tables.filter(([market, priceType]) => (status.tables || []).some(table => table.market === market && table.price_type === priceType && table.items != null)).length;
                                return (
                                    <div key={job.id} className="bg-slate-950/85 backdrop-blur-sm border border-white/10 rounded-xl p-4 flex items-center gap-3">
                                        <span className="p-1.5 rounded-lg bg-amber-500/15 text-amber-300"><Icon size={15} /></span>
                                        <div className="min-w-0 flex-1">
                                            <div className="text-sm font-semibold text-slate-100 truncate">{job.label}</div>
                                            <div className="text-xs text-slate-500">{job.schedule}</div>
                                        </div>
                                        <span className="text-xs text-slate-400 tabular-nums" title="Its tables in memory now (the rest belong to no active profile)">
                                            {inMemory} of {job.tables.length} tables in memory
                                        </span>
                                    </div>
                                );
                            })}
                        </div>
                    </section>
                </div>
            </div>
        </div>
    );
};

export default PulseFeed;
