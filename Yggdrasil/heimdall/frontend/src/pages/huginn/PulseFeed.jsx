import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
    LayoutDashboard, Radio, RefreshCw, Download, Check, X, Plus, AlertTriangle, Search,
    ChevronDown, Clock, Boxes, Bell, Store, Pin, Power,
} from 'lucide-react';
import InfoTip from '../../components/gjallarhorn/InfoTip';
import { readJson } from '../../utils/readJson';
import { MB_PER_DOWNLOAD } from '../../utils/pulseTraffic';

// Huginn → Pulse feed: the switchboard of the master pulse feed (backend pulse_feed.py), the one
// place Heimdall downloads Tradeon pulse price tables. A table is (market, price type).
//  - Tables a recurring job fetches (Case Arbitrage hourly and its alert poll, Store Catalogue
//    Arbitrage hourly and daily) show ticked; unticking one stops those jobs downloading it
//    (settings pulse_switched_off_tables). Buttons you press still download it.
//  - Any other table can be ticked to keep it fresh (pulse_kept_tables, every pulse_keep_minutes).
//  - Tables kept fresh for Huginn Arbitrage profiles ("Keep fresh" next to the profile) show here
//    too; they are switched on and off on the Arbitrage page (huginn_kept_profiles).
// Saved with POST /api/huginn/pulse/kept {tables, minutes, switched_off}.
const MB_IN_MEMORY = 15;        // one table in the feed's compact form
const DAY = 86400;

const keyOf = (market, priceType) => `${market}:${priceType}`;
const COLUMNS = [
    { id: 'listing', label: 'Lowest listing', hint: 'What you pay to buy now (LisSkins: without trade hold)', types: ['Sell', 'SellWithoutHold'] },
    { id: 'buy', label: 'Buy orders', hint: 'What the market pays you at once (autobuy)', types: ['Buy'] },
    { id: 'median', label: '30-day median', hint: 'Price statistics: Price dips, Gjallarhorn', types: ['Median30d'] },
    { id: 'average', label: '30-day average', hint: 'Price statistics: Price dips', types: ['Average30d'] },
];
const JOB_LOOK = {
    'case-arbitrage-hourly': { short: 'Case', Icon: Boxes },
    'case-arbitrage-alerts': { short: 'Alerts', Icon: Bell },
    'store-arbitrage': { short: 'Store', Icon: Store },
};
const jobLook = (job) => JOB_LOOK[job.id] || { short: job.label.split(' ')[0], Icon: Clock };

const formatAge = (seconds) => {
    if (seconds == null) return null;
    if (seconds < 60) return `${Math.round(seconds)} s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
    return `${(seconds / 3600).toFixed(1)} h`;
};
const formatGigabytes = (gb) => (gb >= 10 ? `${Math.round(gb)} GB` : `${gb.toFixed(1)} GB`);

// Downloads a day for a set of switched-on tables: each table as often as its most frequent
// running job (or the keep-fresh interval when you keep it, or a Huginn profile does).
const downloadsPerDay = (onKeys, jobsByKey, kept, minutes, profileKept, profileMinutes, off) => {
    let total = 0;
    for (const key of onKeys) {
        // A switched-off table is not downloaded by its jobs (only by the keeper, if kept).
        const periods = off.has(key) ? [] : (jobsByKey[key] || []).filter(job => job.active).map(job => job.period_seconds);
        if (kept.has(key)) periods.push(minutes * 60);
        if (profileKept.has(key)) periods.push(profileMinutes * 60);
        if (periods.length) total += DAY / Math.min(...periods);
    }
    return total;
};

const Stat = ({ label, value, sub, tone = 'text-slate-100' }) => (
    <div className="flex-1 min-w-[10rem] bg-slate-950/85 backdrop-blur-sm border border-white/10 rounded-xl px-4 py-3">
        <div className="text-[10px] font-bold tracking-widest text-slate-500 uppercase">{label}</div>
        <div className={`text-2xl font-semibold tabular-nums mt-0.5 ${tone}`}>{value}</div>
        {sub && <div className="text-xs text-slate-500 mt-0.5">{sub}</div>}
    </div>
);

const FreshnessDot = ({ age }) => {
    if (age == null) return <span className="w-1.5 h-1.5 rounded-full bg-slate-700" title="Not in memory" />;
    const tone = age < 15 * 60 ? 'bg-emerald-400' : age < 3600 ? 'bg-amber-400' : 'bg-slate-500';
    return <span className={`w-1.5 h-1.5 rounded-full ${tone}`} title={`Downloaded ${formatAge(age)} ago`} />;
};

// One switch: a market × price type table.
const TableSwitch = ({ state, jobs, age, onToggle, disabled, profiles = [] }) => {
    const names = jobs.map(job => jobLook(job).short).join(' · ');
    const looks = {
        job: 'bg-amber-500/15 border-amber-500/40 text-amber-100 hover:bg-amber-500/25',
        kept: 'bg-sky-500/15 border-sky-400/40 text-sky-100 hover:bg-sky-500/25',
        profile: 'bg-violet-500/15 border-violet-400/40 text-violet-100 hover:bg-violet-500/25',
        off: 'bg-red-500/[0.06] border-red-500/40 border-dashed text-red-300/90 hover:bg-red-500/10',
        idle: 'bg-transparent border-white/10 text-slate-500 hover:border-white/25 hover:text-slate-300',
    };
    const icon = state === 'job' || state === 'kept' || state === 'profile' ? <Check size={13} strokeWidth={3} />
        : state === 'off' ? <X size={13} strokeWidth={3} /> : <Plus size={13} />;
    const caption = state === 'job' ? names : state === 'kept' ? 'kept by you'
        : state === 'profile' ? (profiles.length === 1 ? 'Huginn profile' : `${profiles.length} Huginn profiles`)
            : state === 'off' ? (profiles.length ? `Huginn profile · jobs off` : `off · ${names}`) : 'off';
    const forProfiles = profiles.length ? ` Also kept fresh for the Huginn profile${profiles.length === 1 ? '' : 's'} ${profiles.join(', ')}.` : '';
    const tip = state === 'job' ? `Fetched automatically by ${jobs.map(job => `${job.label} (${job.schedule})`).join(' and ')}.${forProfiles} Click to stop that.`
        : state === 'kept' ? `Kept fresh by you.${forProfiles} Click to stop.`
            : state === 'profile' ? `Kept fresh for the Huginn profile${profiles.length === 1 ? '' : 's'} ${profiles.join(', ')} (switch "Keep fresh" off next to the profile on Huginn's Arbitrage page). Click to keep it yourself too, at your own interval: it then stays kept after the profile is switched off.`
                : state === 'off' ? `Switched off: ${jobs.map(job => job.label).join(' and ')} will not download it (buttons you press still do).${forProfiles ? `${forProfiles} The keeper still downloads it for them, and the jobs read that copy.` : ''} Click to switch it back on.`
                    : disabled ? 'At most 12 tables can be kept fresh by you' : 'Not fetched automatically. Click to keep it fresh.';
    return (
        <button type="button" role="switch" aria-checked={state !== 'off' && state !== 'idle'} onClick={onToggle}
            disabled={disabled} title={tip}
            className={`w-full h-11 flex items-center gap-2 px-2.5 rounded-lg border text-left transition-colors disabled:opacity-30 disabled:cursor-not-allowed ${looks[state]}`}>
            <span className="shrink-0">{icon}</span>
            <span className="min-w-0 flex-1 text-xs leading-tight truncate">{caption}</span>
            <span className="shrink-0 flex items-center gap-1 text-[10px] text-slate-400 tabular-nums">
                {age != null && formatAge(age)}<FreshnessDot age={age} />
            </span>
        </button>
    );
};

const PulseFeed = () => {
    const [status, setStatus] = useState(null);
    const [error, setError] = useState(null);
    const [draft, setDraft] = useState(null);               // {kept: Set, off: Set, minutes} while editing
    const [saving, setSaving] = useState(false);
    const [savedAt, setSavedAt] = useState(null);
    const [query, setQuery] = useState('');
    const [showAll, setShowAll] = useState(false);
    const [detailsOpen, setDetailsOpen] = useState(false);
    const [downloading, setDownloading] = useState({});      // key -> true | error text
    const request = useRef(0);

    const load = useCallback(async () => {
        const n = ++request.current;
        try {
            const res = await fetch('/api/huginn/pulse');
            const answer = await readJson(res);
            if (n !== request.current) return;
            if (!res.ok) throw new Error(answer.error || 'Could not read the pulse feed');
            setStatus(answer);
            setError(null);
        } catch (err) {
            if (n === request.current) setError(err.message);
        }
    }, []);

    // The status is memory only (cheap): every 5 seconds while the page is open.
    useEffect(() => {
        const first = setTimeout(load, 0);
        const timer = setInterval(load, 5000);
        return () => { clearTimeout(first); clearInterval(timer); };
    }, [load]);

    const tablesByKey = useMemo(() => Object.fromEntries((status?.tables || []).map(table => [keyOf(table.market, table.price_type), table])), [status]);
    const jobs = useMemo(() => status?.jobs || [], [status]);
    const jobsByKey = useMemo(() => {
        const out = {};
        for (const job of jobs) for (const [market, priceType] of job.tables) (out[keyOf(market, priceType)] ||= []).push(job);
        return out;
    }, [jobs]);
    // Tables kept fresh for Huginn profiles: key -> [profile labels] (switched on the Arbitrage page).
    const profilesByKey = useMemo(() => {
        const out = {};
        for (const profile of Object.values(status?.kept_profiles || {})) {
            for (const table of profile.tables) (out[keyOf(table.market, table.price_type)] ||= []).push(profile.label);
        }
        return out;
    }, [status]);
    const profileKept = useMemo(() => new Set(Object.keys(profilesByKey)), [profilesByKey]);
    const profileMinutes = status?.profile_kept_minutes ?? 5;
    const saved = useMemo(() => ({
        kept: new Set((status?.tables || []).filter(table => table.kept_by_you).map(table => keyOf(table.market, table.price_type))),
        off: new Set((status?.tables || []).filter(table => table.switched_off).map(table => keyOf(table.market, table.price_type))),
        minutes: status?.kept_minutes ?? 10,
    }), [status]);
    const current = draft || saved;
    const maxKept = status?.max_kept_tables ?? 12;
    const keptByYou = [...current.kept].filter(key => !jobsByKey[key]);

    const edit = (change) => setDraft(prev => change(prev || { kept: new Set(saved.kept), off: new Set(saved.off), minutes: saved.minutes }));
    const stateOf = (key) => {
        if (jobsByKey[key]) return current.off.has(key) ? 'off' : 'job';
        return current.kept.has(key) ? 'kept' : profileKept.has(key) ? 'profile' : 'idle';
    };
    const toggle = (key) => edit(prev => {
        const next = { ...prev, kept: new Set(prev.kept), off: new Set(prev.off) };
        if (jobsByKey[key]) {
            if (next.off.has(key)) next.off.delete(key); else next.off.add(key);
        } else if (next.kept.has(key)) {
            next.kept.delete(key);
        } else if ([...next.kept].filter(kept => !jobsByKey[kept]).length < maxKept) {
            next.kept.add(key);
        }
        return next;
    });
    const setJob = (job, on) => edit(prev => {
        const next = { ...prev, off: new Set(prev.off) };
        for (const [market, priceType] of job.tables) {
            const key = keyOf(market, priceType);
            if (on) next.off.delete(key); else next.off.add(key);
        }
        return next;
    });

    // What is fetched automatically, and what it costs, for the saved choice and for the draft.
    const costOf = useCallback((choice) => {
        const onKeys = new Set([
            ...Object.keys(jobsByKey).filter(key => !choice.off.has(key) && jobsByKey[key].some(job => job.active)),
            ...[...choice.kept],
            ...profileKept,
        ]);
        const perDay = downloadsPerDay(onKeys, jobsByKey, choice.kept, choice.minutes, profileKept, profileMinutes, choice.off);
        return { tables: onKeys.size, perDay, gigabytes: (perDay * MB_PER_DOWNLOAD) / 1024 };
    }, [jobsByKey, profileKept, profileMinutes]);
    const savedCost = useMemo(() => costOf(saved), [costOf, saved]);
    const draftCost = useMemo(() => costOf(current), [costOf, current]);
    const changes = useMemo(() => {
        if (!draft) return 0;
        const diff = (before, after) => [...before].filter(key => !after.has(key)).length + [...after].filter(key => !before.has(key)).length;
        return diff(draft.kept, saved.kept) + diff(draft.off, saved.off) + (draft.minutes !== saved.minutes ? 1 : 0);
    }, [draft, saved]);

    const save = async () => {
        if (!draft) return;
        setSaving(true);
        try {
            const res = await fetch('/api/huginn/pulse/kept', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    tables: [...draft.kept].filter(key => !jobsByKey[key]),
                    switched_off: [...draft.off].filter(key => jobsByKey[key]),
                    minutes: draft.minutes,
                }),
            });
            const answer = await readJson(res);
            if (!res.ok) throw new Error(answer.error || 'Could not save');
            setDraft(null);
            setSavedAt(Date.now());
            setTimeout(() => setSavedAt(null), 4000);
            load();
        } catch (err) {
            setError(err.message);
        } finally {
            setSaving(false);
        }
    };

    // The kept profiles' interval saves at once (the profiles themselves are switched on the Arbitrage page).
    const saveProfileMinutes = async (minutes) => {
        try {
            const res = await fetch('/api/huginn/arbitrage/kept-profiles', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ minutes }),     // only the interval: the profile list stays as saved
            });
            const answer = await readJson(res);
            if (!res.ok) throw new Error(answer.error || 'Could not save');
            load();
        } catch (err) {
            setError(err.message);
        }
    };

    const downloadNow = async (market, priceType) => {
        const key = keyOf(market, priceType);
        setDownloading(prev => ({ ...prev, [key]: true }));
        try {
            const res = await fetch('/api/huginn/pulse/download', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ market, price_type: priceType }),
            });
            const answer = await readJson(res);
            if (!res.ok) throw new Error(answer.error || 'Download failed');
            setDownloading(prev => { const next = { ...prev }; delete next[key]; return next; });
            load();
        } catch (err) {
            setDownloading(prev => ({ ...prev, [key]: err.message }));
        }
    };

    // Market rows: those with anything automatic first (jobs, kept, switched off), the rest on demand.
    const rows = useMemo(() => {
        const q = query.trim().toLowerCase();
        const out = (status?.choices || []).map(choice => {
            const cells = COLUMNS.map(column => {
                const priceType = choice.price_types.find(option => column.types.includes(option.price_type))?.price_type;
                return priceType ? { priceType, key: keyOf(choice.market, priceType) } : null;
            });
            const involved = cells.some(cell => cell && (jobsByKey[cell.key] || saved.kept.has(cell.key) || current.kept.has(cell.key) || profileKept.has(cell.key)));
            return { ...choice, cells, involved };
        }).filter(row => !q || row.display.toLowerCase().includes(q) || row.market.toLowerCase().includes(q));
        return out.sort((first, second) => Number(second.involved) - Number(first.involved));
    }, [status, query, jobsByKey, saved, current, profileKept]);
    const visibleRows = showAll || query ? rows : rows.filter(row => row.involved);
    const hiddenCount = rows.length - visibleRows.length;

    const memoryShare = status && status.reads ? Math.round((status.served_from_memory / status.reads) * 100) : null;
    const labelOf = useMemo(() => {
        const out = {};
        for (const choice of status?.choices || []) {
            for (const option of choice.price_types) out[keyOf(choice.market, option.price_type)] = [choice.display, option.label];
        }
        return out;
    }, [status]);

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
                <div className="max-w-6xl mx-auto px-6 pt-6 pb-28 flex flex-col gap-6">
                    <p className="text-sm text-slate-300 leading-relaxed bg-slate-950/85 backdrop-blur-sm border border-white/10 rounded-xl px-4 py-3">
                        Every price Heimdall shows comes from Tradeon pulse, one table per market and price type
                        (about 5 MB each, compressed). <span className="text-slate-200">Ticked tables are fetched automatically</span> in
                        the background; unticked ones only when a page or a button asks. Buttons you press
                        always get prices under a minute old.
                    </p>

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

                    {/* Overview */}
                    <div className="flex flex-wrap gap-3">
                        <Stat label="Fetched automatically" value={status ? `${savedCost.tables} tables` : '—'}
                            sub={status ? `${Math.round(savedCost.perDay).toLocaleString()} downloads a day` : null} />
                        <Stat label="Traffic a day" value={status ? `≈ ${formatGigabytes(savedCost.gigabytes)}` : '—'}
                            tone={savedCost.gigabytes > 60 ? 'text-amber-300' : 'text-slate-100'}
                            sub="estimated from the schedules below" />
                        <Stat label="In memory now" value={status ? `${status.tables_in_memory} tables` : '—'}
                            sub={status ? `about ${status.tables_in_memory * MB_IN_MEMORY} MB` : null} />
                        <Stat label="Shared, not downloaded" value={memoryShare == null ? '—' : `${memoryShare}%`}
                            sub={status ? `${status.reads.toLocaleString()} reads, ${status.downloads.toLocaleString()} downloads since start` : null} />
                    </div>

                    {/* Recurring jobs */}
                    <section>
                        <h2 className="text-[11px] font-bold tracking-widest text-slate-300 uppercase mb-2 drop-shadow">Recurring jobs</h2>
                        <div className="grid gap-3 md:grid-cols-3">
                            {jobs.map(job => {
                                const { Icon } = jobLook(job);
                                const keys = job.tables.map(([market, priceType]) => keyOf(market, priceType));
                                const onCount = keys.filter(key => !current.off.has(key)).length;
                                return (
                                    <div key={job.id} className="bg-slate-950/85 backdrop-blur-sm border border-white/10 rounded-xl p-4 flex flex-col gap-2">
                                        <div className="flex items-center gap-2">
                                            <span className="p-1.5 rounded-lg bg-amber-500/15 text-amber-300"><Icon size={15} /></span>
                                            <div className="min-w-0">
                                                <div className="text-sm font-semibold text-slate-100 truncate">{job.label}</div>
                                                <div className="text-xs text-slate-500">{job.schedule}</div>
                                            </div>
                                            <span className={`ml-auto text-[10px] px-2 py-0.5 rounded-full border ${job.active ? 'border-emerald-500/40 text-emerald-300 bg-emerald-500/10' : 'border-white/10 text-slate-500'}`}
                                                title={job.active ? 'Running' : 'Not running now (no token, or its alerts are switched off)'}>
                                                {job.active ? 'running' : 'not running'}
                                            </span>
                                        </div>
                                        <div className="h-1.5 rounded-full bg-white/5 overflow-hidden" title={`${onCount} of ${keys.length} tables on`}>
                                            <div className="h-full bg-amber-500/70" style={{ width: `${keys.length ? (onCount / keys.length) * 100 : 0}%` }} />
                                        </div>
                                        <div className="flex items-center gap-2 text-xs text-slate-400">
                                            <span className="tabular-nums">{onCount} of {keys.length} tables on</span>
                                            <span className="text-slate-600">·</span>
                                            <span className="tabular-nums">≈ {formatGigabytes((onCount * (DAY / (job.period_seconds || 3600)) * MB_PER_DOWNLOAD) / 1024)} a day</span>
                                            <div className="ml-auto flex gap-1">
                                                <button type="button" onClick={() => setJob(job, true)} disabled={onCount === keys.length}
                                                    className="px-2 py-0.5 rounded border border-white/10 hover:border-white/25 hover:text-white disabled:opacity-30">All on</button>
                                                <button type="button" onClick={() => setJob(job, false)} disabled={onCount === 0}
                                                    className="px-2 py-0.5 rounded border border-white/10 hover:border-red-400/40 hover:text-red-300 disabled:opacity-30">All off</button>
                                            </div>
                                        </div>
                                    </div>
                                );
                            })}
                            <div className="bg-slate-950/85 backdrop-blur-sm border border-white/10 rounded-xl p-4 flex flex-col gap-2">
                                <div className="flex items-center gap-2">
                                    <span className="p-1.5 rounded-lg bg-sky-500/15 text-sky-300"><Pin size={15} /></span>
                                    <div className="min-w-0">
                                        <div className="text-sm font-semibold text-slate-100">Kept fresh by you</div>
                                        <div className="text-xs text-slate-500">any other table you tick, at most {maxKept}</div>
                                    </div>
                                </div>
                                <label className="flex items-center gap-2 text-xs text-slate-400">
                                    every
                                    <select value={current.minutes} onChange={event => edit(prev => ({ ...prev, minutes: Number(event.target.value) }))}
                                        className="bg-black/30 border border-white/10 rounded-md px-1.5 py-1 text-sm text-white focus:outline-none focus:border-sky-400/40">
                                        {(status?.keep_fresh_choices || [5, 10, 15, 30, 60]).map(choice => <option key={choice} value={choice}>{choice} minutes</option>)}
                                    </select>
                                    <span className="ml-auto tabular-nums">{keptByYou.length} of {maxKept}</span>
                                </label>
                                <p className="text-[11px] text-slate-500 leading-snug">
                                    Boards and jobs then read them from memory instead of downloading them.
                                </p>
                            </div>
                            {Object.keys(status?.kept_profiles || {}).length > 0 && (
                                <div className="bg-slate-950/85 backdrop-blur-sm border border-white/10 rounded-xl p-4 flex flex-col gap-2">
                                    <div className="flex items-center gap-2">
                                        <span className="p-1.5 rounded-lg bg-violet-500/15 text-violet-300"><Pin size={15} /></span>
                                        <div className="min-w-0">
                                            <div className="text-sm font-semibold text-slate-100">Kept for Huginn profiles</div>
                                            <div className="text-xs text-slate-500">"Keep fresh" next to a profile on the Arbitrage page</div>
                                        </div>
                                    </div>
                                    <ul className="text-xs text-slate-300 space-y-0.5">
                                        {Object.entries(status.kept_profiles).map(([key, profile]) => <li key={key} className="truncate">{profile.label}</li>)}
                                    </ul>
                                    <label className="flex items-center gap-2 text-xs text-slate-400">
                                        every
                                        <select value={profileMinutes} onChange={event => saveProfileMinutes(Number(event.target.value))}
                                            className="bg-black/30 border border-white/10 rounded-md px-1.5 py-1 text-sm text-white focus:outline-none focus:border-violet-400/40">
                                            {(status.keep_fresh_choices || [5, 10, 15, 30, 60]).map(choice => <option key={choice} value={choice}>{choice} minutes</option>)}
                                        </select>
                                        <span className="ml-auto tabular-nums">{profileKept.size} of {status.max_profile_tables ?? 12} tables</span>
                                    </label>
                                    <p className="text-[11px] text-slate-500 leading-snug">
                                        Saved at once. Those profiles open with prices at most this old.
                                    </p>
                                </div>
                            )}
                        </div>
                    </section>

                    {/* The switchboard */}
                    <section className="bg-slate-950/85 backdrop-blur-sm border border-white/10 rounded-xl overflow-hidden">
                        <div className="px-4 py-3 border-b border-white/5 flex flex-wrap items-center gap-3">
                            <h2 className="text-[11px] font-bold tracking-widest text-slate-400 uppercase">Markets</h2>
                            <div className="relative">
                                <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
                                <input value={query} onChange={event => setQuery(event.target.value)} placeholder="Find a market"
                                    className="w-48 bg-black/30 border border-white/10 rounded-lg pl-8 pr-3 py-1.5 text-sm text-white placeholder:text-slate-600 focus:outline-none focus:border-amber-500/40" />
                            </div>
                            <div className="flex flex-wrap items-center gap-3 text-[11px] text-slate-400 ml-auto">
                                <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded border bg-amber-500/20 border-amber-500/50" /> on (a job)</span>
                                <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded border bg-sky-500/20 border-sky-400/50" /> kept by you</span>
                                <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded border bg-violet-500/20 border-violet-400/50" /> Huginn profile</span>
                                <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded border border-dashed border-red-500/50 bg-red-500/10" /> switched off</span>
                                <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded border border-white/15" /> only when asked</span>
                                <InfoTip tip="Green dot: downloaded in the last 15 minutes; amber: within the hour; grey: older or not in memory.">
                                    <span className="flex items-center gap-1 cursor-help"><span className="w-1.5 h-1.5 rounded-full bg-emerald-400" /> fresh</span>
                                </InfoTip>
                            </div>
                        </div>
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm table-fixed">
                                <thead>
                                    <tr className="text-[11px] uppercase tracking-wider text-slate-500 bg-black/20">
                                        <th className="text-left font-semibold px-4 py-2 w-48">Market</th>
                                        {COLUMNS.map(column => <th key={column.id} className="text-left font-semibold px-2 py-2" title={column.hint}>{column.label}</th>)}
                                    </tr>
                                </thead>
                                <tbody>
                                    {visibleRows.map(row => (
                                        <tr key={row.market} className="border-t border-white/5">
                                            <td className="px-4 py-2 text-slate-200 whitespace-nowrap">
                                                {row.display}
                                                {row.premium && <span className="ml-1.5 text-[10px] text-slate-500" title="Needs pulse's Table subscription">subscription</span>}
                                            </td>
                                            {row.cells.map((cell, index) => (
                                                <td key={COLUMNS[index].id} className="px-2 py-1.5 min-w-[9.5rem]">
                                                    {cell ? (
                                                        <TableSwitch state={stateOf(cell.key)} jobs={jobsByKey[cell.key] || []} profiles={profilesByKey[cell.key]}
                                                            age={tablesByKey[cell.key]?.age_seconds}
                                                            onToggle={() => toggle(cell.key)}
                                                            disabled={['idle', 'profile'].includes(stateOf(cell.key)) && keptByYou.length >= maxKept} />
                                                    ) : <span className="block text-center text-slate-700">—</span>}
                                                </td>
                                            ))}
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                        {hiddenCount > 0 && (
                            <button type="button" onClick={() => setShowAll(true)}
                                className="w-full px-4 py-2.5 border-t border-white/5 text-xs text-slate-400 hover:text-white hover:bg-white/[0.03] flex items-center justify-center gap-1.5">
                                <ChevronDown size={13} /> Show {hiddenCount} more markets (fetched only when asked)
                            </button>
                        )}
                        {showAll && !query && (
                            <button type="button" onClick={() => setShowAll(false)}
                                className="w-full px-4 py-2 border-t border-white/5 text-xs text-slate-500 hover:text-white">
                                Show only markets fetched automatically
                            </button>
                        )}
                    </section>

                    {/* Activity */}
                    <section className="bg-slate-950/85 backdrop-blur-sm border border-white/10 rounded-xl overflow-hidden">
                        <button type="button" onClick={() => setDetailsOpen(open => !open)}
                            className="w-full px-4 py-3 flex items-center gap-2 text-left hover:bg-white/[0.02]">
                            <ChevronDown size={14} className={`text-slate-500 transition-transform ${detailsOpen ? '' : '-rotate-90'}`} />
                            <h2 className="text-[11px] font-bold tracking-widest text-slate-400 uppercase">Activity since start</h2>
                            <span className="text-xs text-slate-500">every table read or downloaded, with a download button</span>
                        </button>
                        {detailsOpen && (
                            <table className="w-full text-sm border-t border-white/5">
                                <thead>
                                    <tr className="text-[11px] uppercase tracking-wider text-slate-500 bg-black/20">
                                        <th className="text-left font-semibold px-4 py-2">Table</th>
                                        <th className="text-right font-semibold px-3 py-2">Items</th>
                                        <th className="text-right font-semibold px-3 py-2">Age</th>
                                        <th className="text-right font-semibold px-3 py-2">Downloads</th>
                                        <th className="text-right font-semibold px-3 py-2">Reads</th>
                                        <th className="text-left font-semibold px-3 py-2">Problem</th>
                                        <th className="px-3 py-2" />
                                    </tr>
                                </thead>
                                <tbody>
                                    {(status?.tables || []).filter(table => table.reads || table.downloads)
                                        .sort((first, second) => (second.last_download_at || 0) - (first.last_download_at || 0)).map(table => {
                                            const key = keyOf(table.market, table.price_type);
                                            const busy = downloading[key] === true || table.downloading;
                                            const failed = typeof downloading[key] === 'string' ? downloading[key] : table.last_error;
                                            const [market, label] = labelOf[key] || [table.market, table.price_type];
                                            return (
                                                <tr key={key} className="border-t border-white/5 hover:bg-white/[0.02]">
                                                    <td className="px-4 py-1.5 whitespace-nowrap"><span className="text-slate-200">{market}</span> <span className="text-slate-500">· {label}</span></td>
                                                    <td className="px-3 py-1.5 text-right tabular-nums text-slate-300">{table.items != null ? table.items.toLocaleString() : '—'}</td>
                                                    <td className="px-3 py-1.5 text-right tabular-nums text-slate-300">{busy ? 'downloading…' : (formatAge(table.age_seconds) || '—')}</td>
                                                    <td className="px-3 py-1.5 text-right tabular-nums text-slate-300">{table.downloads}</td>
                                                    <td className="px-3 py-1.5 text-right tabular-nums text-slate-300">{table.reads}</td>
                                                    <td className="px-3 py-1.5 text-xs text-red-300/80 max-w-[18rem] truncate" title={failed || ''}>{failed || (table.failures ? `${table.failures} failed earlier` : '')}</td>
                                                    <td className="px-3 py-1.5 text-right">
                                                        <button type="button" onClick={() => downloadNow(table.market, table.price_type)} disabled={busy || !status?.token_set}
                                                            title="Download this table now (unless it is under a minute old)"
                                                            className="p-1 rounded text-slate-400 hover:text-amber-300 disabled:opacity-40">
                                                            <Download size={13} className={busy ? 'animate-pulse' : ''} />
                                                        </button>
                                                    </td>
                                                </tr>
                                            );
                                        })}
                                </tbody>
                            </table>
                        )}
                    </section>
                </div>
            </div>

            {/* Save bar: only while something changed */}
            {(changes > 0 || savedAt) && (
                <div className="shrink-0 border-t border-white/10 bg-slate-950/90 backdrop-blur px-6 py-3">
                    <div className="max-w-6xl mx-auto flex items-center gap-4">
                        {changes > 0 ? (
                            <>
                                <Power size={15} className="text-amber-400" />
                                <span className="text-sm text-slate-200">{changes} change{changes === 1 ? '' : 's'} not saved</span>
                                <span className="text-sm text-slate-400 tabular-nums">
                                    {savedCost.tables} → <span className="text-slate-100">{draftCost.tables} tables</span>
                                    {' · '}≈ {formatGigabytes(savedCost.gigabytes)} → <span className={draftCost.gigabytes < savedCost.gigabytes ? 'text-emerald-300' : draftCost.gigabytes > savedCost.gigabytes ? 'text-amber-300' : 'text-slate-100'}>{formatGigabytes(draftCost.gigabytes)} a day</span>
                                </span>
                                <div className="ml-auto flex items-center gap-2">
                                    <button type="button" onClick={() => setDraft(null)} className="px-3 py-1.5 rounded-lg text-sm text-slate-400 hover:text-white">Discard</button>
                                    <button type="button" onClick={save} disabled={saving}
                                        className="px-4 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-sm font-medium disabled:opacity-50">
                                        {saving ? 'Saving…' : 'Save'}
                                    </button>
                                </div>
                            </>
                        ) : (
                            <span className="text-sm text-emerald-400 flex items-center gap-1.5"><Check size={14} /> Saved. The jobs use it from their next run.</span>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
};

export default PulseFeed;
