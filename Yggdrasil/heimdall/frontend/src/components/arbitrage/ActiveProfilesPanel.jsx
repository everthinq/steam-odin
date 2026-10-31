import { useState } from 'react';
import { AlertTriangle, ChevronDown, Moon, Pin, X } from 'lucide-react';
import { formatAge, useNowSeconds } from '../../utils/priceAge';
import { MB_PER_DOWNLOAD } from '../../utils/pulseTraffic';

// The Active profiles panel on Huginn Arbitrage: the profiles Ivan trades on are the only ones whose
// pulse tables are downloaded (backend pulse_feed.py, the keeper); every other page reads from them.
// Shows each active profile's tables and their age, the Buff163 reference (once a day), Heimdall's
// own price history, the interval, the active hours and the keeper's state. From Arbitrage.jsx.
const OPEN_KEY = 'huginn.activePanelOpen';

const readOpen = () => {
    try { return localStorage.getItem(OPEN_KEY) !== 'false'; } catch { return true; }
};

const clock = (epochSeconds) => new Date(epochSeconds * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

// Age of a table in this answer, still counting since the answer arrived.
const ageOf = (table, status, now) => (table.age_seconds == null || !now ? null
    : table.age_seconds + Math.max(0, now - status.receivedAt / 1000));

const TableChip = ({ table, status, now, intervalSeconds }) => {
    const age = ageOf(table, status, now);
    const tone = table.downloading ? 'border-sky-400/40 text-sky-200'
        : age == null ? 'border-white/10 text-slate-500'
            : age > intervalSeconds + 120 ? 'border-amber-500/40 text-amber-200' : 'border-white/10 text-slate-300';
    return (
        <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[11px] tabular-nums ${tone}`}
            title={table.downloading ? 'Downloading now' : age == null ? 'Not downloaded yet (after a restart, or outside the active hours)' : `Downloaded ${formatAge(age)}`}>
            {table.label}
            <span className="text-slate-500">{table.downloading ? '…' : age == null ? '—' : formatAge(age)}</span>
        </span>
    );
};

// The keeper's state in one short line, worst first.
const keeperState = (keeper, now) => {
    if (!keeper) return null;
    if (keeper.paused) {
        return { tone: 'text-indigo-200 border-indigo-400/40 bg-indigo-500/10', icon: Moon,
            text: `paused until ${keeper.resumes_at ? clock(keeper.resumes_at) : 'the active hours'}`,
            tip: 'Outside the active hours nothing is downloaded: the pages show the last prices, Case Arbitrage alerts wait. The Counter-Strike 2 news watcher still rings.' };
    }
    if (keeper.behind) {
        return { tone: 'text-amber-200 border-amber-500/40 bg-amber-500/10', icon: AlertTriangle, text: 'falling behind',
            tip: `The last pass took ${Math.round(keeper.last_pass.seconds)} s, longer than the interval: the connection is too slow for this many tables. Make a profile inactive or choose a longer interval.` };
    }
    if (keeper.sleep && now && now - keeper.sleep.to < 6 * 3600) {
        return { tone: 'text-slate-300 border-white/10 bg-white/5', icon: Moon,
            text: `Mac slept ${clock(keeper.sleep.from)}–${clock(keeper.sleep.to)}`,
            tip: 'Nothing is downloaded while the Mac sleeps; afterwards the keeper waits one tick for the network and catches up.' };
    }
    return null;
};

const ActiveProfilesPanel = ({ status, profileIdOf, onOpen, onChange, busy }) => {
    const [open, setOpen] = useState(readOpen);
    const [draft, setDraft] = useState(null);          // the active hours while a time is being typed
    const now = useNowSeconds(10000);
    const hours = draft || status?.hours || { enabled: false, start: '08:00', end: '01:00' };

    if (!status) return null;
    const toggleOpen = () => setOpen(previous => {
        try { localStorage.setItem(OPEN_KEY, String(!previous)); } catch { /* private window */ }
        return !previous;
    });
    const profiles = Object.entries(status.profiles || {});
    const intervalSeconds = status.minutes * 60;
    const downloadsPerDay = status.tables_used * (1440 / status.minutes) + (status.reference?.length || 0);
    const gigabytes = (downloadsPerDay * MB_PER_DOWNLOAD) / 1024;
    const state = keeperState(status.keeper, now);
    const saveHours = (next) => {
        setDraft(null);
        if (next.start && next.end) onChange({ hours: next });
    };
    const recorded = (status.statistics || []).filter(table => table.age_seconds != null || table.days > 0);
    const longestHistory = Math.max(0, ...(status.statistics || []).map(table => table.days));
    const seeded = [...new Set((status.statistics || []).filter(table => table.seeded?.Median30d).map(table => table.label.split(' · ')[0]))];

    return (
        <section className="shrink-0 bg-odin-blue/30 border border-white/5 rounded-xl overflow-hidden">
            <button type="button" onClick={toggleOpen} aria-expanded={open}
                className="w-full flex items-center gap-2 px-4 py-2.5 hover:bg-white/[0.03] transition-colors text-left">
                <ChevronDown size={15} className={`text-slate-500 transition-transform shrink-0 ${open ? '' : '-rotate-90'}`} />
                <Pin size={13} className="text-sky-400 shrink-0" />
                <span className="text-[10px] font-bold tracking-widest text-slate-400 uppercase">Active profiles</span>
                <span className="text-xs text-slate-400 tabular-nums">
                    {profiles.length} profile{profiles.length === 1 ? '' : 's'} · {status.tables_used} of {status.max_tables} tables · every {status.minutes} min · ≈ {gigabytes.toFixed(1)} GB a day
                </span>
                {state && (
                    <span className={`ml-auto flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] ${state.tone}`} title={state.tip}>
                        <state.icon size={11} /> {state.text}
                    </span>
                )}
            </button>
            {open && (
                <div className="px-4 pb-3 pt-1 border-t border-white/5 flex flex-col gap-3">
                    <p className="text-xs text-slate-500 leading-relaxed">
                        Only the active profiles' prices are downloaded from pulse, in the background. Case Arbitrage, its alerts,
                        Harvest, Cross-Profile, Price dips, Store Catalogue and Gjallarhorn read theirs from these tables; a market
                        no active profile uses shows as "not active". "Fetch live data" downloads any profile now.
                    </p>

                    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-slate-400">
                        <label className="flex items-center gap-2">
                            Download every
                            <select value={status.minutes} disabled={busy} onChange={event => onChange({ minutes: Number(event.target.value) })}
                                className="bg-black/30 border border-white/10 rounded-md px-1.5 py-1 text-sm text-white focus:outline-none focus:border-sky-400/40">
                                {(status.choices || [5, 10, 15, 30, 60]).map(choice => <option key={choice} value={choice}>{choice} minutes</option>)}
                            </select>
                        </label>
                        <div className="flex items-center gap-2" title="Outside these hours nothing is downloaded: go to sleep, the Counter-Strike 2 news watcher still rings. Local time; an end before the start runs over midnight.">
                            <button type="button" role="switch" aria-checked={hours.enabled} disabled={busy}
                                onClick={() => saveHours({ ...hours, enabled: !hours.enabled })}
                                className="flex items-center gap-2 disabled:opacity-50">
                                <span className={`relative inline-flex h-4 w-7 shrink-0 rounded-full transition-colors ${hours.enabled ? 'bg-indigo-400' : 'bg-white/15'}`}>
                                    <span className={`absolute top-0.5 h-3 w-3 rounded-full bg-white shadow transition-transform ${hours.enabled ? 'translate-x-3.5' : 'translate-x-0.5'}`} />
                                </span>
                                Active hours
                            </button>
                            <input type="time" value={hours.start} disabled={!hours.enabled || busy} step={900}
                                onChange={event => setDraft({ ...hours, start: event.target.value })}
                                onBlur={event => saveHours({ ...hours, start: event.target.value })}
                                aria-label="Active hours start"
                                className="bg-black/30 border border-white/10 rounded-md px-1.5 py-0.5 text-sm text-white disabled:opacity-40 focus:outline-none focus:border-indigo-400/40" />
                            <span>to</span>
                            <input type="time" value={hours.end} disabled={!hours.enabled || busy} step={900}
                                onChange={event => setDraft({ ...hours, end: event.target.value })}
                                onBlur={event => saveHours({ ...hours, end: event.target.value })}
                                aria-label="Active hours end"
                                className="bg-black/30 border border-white/10 rounded-md px-1.5 py-0.5 text-sm text-white disabled:opacity-40 focus:outline-none focus:border-indigo-400/40" />
                        </div>
                    </div>

                    {profiles.length === 0 ? (
                        <p className="text-sm text-slate-400">
                            No active profile yet: nothing is downloaded by itself. Pick a profile you trade on and switch on <span className="text-sky-200">Active</span> next to it.
                        </p>
                    ) : (
                        <ul className="flex flex-col divide-y divide-white/5">
                            {profiles.map(([key, profile]) => (
                                <li key={key} className="flex flex-wrap items-center gap-2 py-1.5">
                                    <button type="button" onClick={() => onOpen(profileIdOf(key))} disabled={!profileIdOf(key)}
                                        className="text-sm text-slate-100 hover:text-amber-300 transition-colors text-left min-w-[16rem]"
                                        title="Open this profile">
                                        {profile.label}
                                    </button>
                                    <span className="flex flex-wrap gap-1">
                                        {profile.tables.map(table => <TableChip key={`${table.market}:${table.price_type}`} table={table} status={status} now={now} intervalSeconds={intervalSeconds} />)}
                                    </span>
                                    <button type="button" onClick={() => onChange({ remove: key })} disabled={busy}
                                        className="ml-auto p-1 rounded text-slate-500 hover:text-red-300 disabled:opacity-40"
                                        title="Make this profile inactive (its tables stop downloading unless another active profile uses them)"
                                        aria-label={`Make ${profile.label} inactive`}>
                                        <X size={13} />
                                    </button>
                                </li>
                            ))}
                        </ul>
                    )}

                    <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
                        <span className="text-[10px] font-bold tracking-widest text-slate-500 uppercase">Reference</span>
                        {(status.reference || []).map(table => (
                            <span key={`${table.market}:${table.price_type}`} className="flex items-center gap-1.5" title={table.note}>
                                <TableChip table={table} status={status} now={now} intervalSeconds={table.every_seconds} />
                                <span className="text-slate-500">once a day, off-Steam price reference</span>
                            </span>
                        ))}
                    </div>

                    <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400"
                        title="Every downloaded table adds one sample per item to the day's row (average, lowest, highest, last), saved for good in backend/cache/price_history. The 30-day median and average come from an item's own days once it has 7; before that from pulse's 30-day prices, downloaded once.">
                        <span className="text-[10px] font-bold tracking-widest text-slate-500 uppercase">Price history</span>
                        <span>
                            {recorded.length} table{recorded.length === 1 ? '' : 's'} recorded · {longestHistory} of 30 days so far
                            {seeded.length > 0 ? ` · seeded from pulse: ${seeded.join(', ')}` : ' · pulse seed pending'}
                        </span>
                    </div>
                </div>
            )}
        </section>
    );
};

export default ActiveProfilesPanel;
