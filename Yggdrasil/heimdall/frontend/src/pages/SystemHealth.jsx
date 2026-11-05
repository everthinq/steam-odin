import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { LayoutDashboard, Activity, RefreshCw, AlertTriangle, CheckCircle2, XCircle } from 'lucide-react';
import { readJsonOrThrow, failureMessage, isTransientFailure } from '../utils/readJson';
import { formatAge } from '../utils/priceAge';

// System health: the backend's background loops, whether it can use Ratatoskr and ASF, the last
// errors it logged and its memory use. Read only: GET /api/system/health every 10 seconds while
// the page is visible. Every time is measured against the answer's own checked_at (server clock).
const POLL_MS = 10000;

const megabytes = (bytes) => (bytes == null ? '—' : `${Math.round(bytes / 1048576).toLocaleString()} MB`);
const clock = (epochSeconds) => new Date(epochSeconds * 1000).toLocaleString([], {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
});

const Panel = ({ title, children, right }) => (
    <section className="bg-slate-950/85 backdrop-blur-sm border border-white/10 rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-white/5 flex flex-wrap items-center gap-3">
            <h2 className="text-[10px] font-bold tracking-widest text-slate-400 uppercase">{title}</h2>
            {right}
        </div>
        {children}
    </section>
);

const Stat = ({ label, value, sub, tone = 'text-slate-100' }) => (
    <div className="flex-1 min-w-[10rem] bg-slate-950/85 backdrop-blur-sm border border-white/10 rounded-xl px-4 py-3">
        <div className="text-[10px] font-bold tracking-widest text-slate-500 uppercase">{label}</div>
        <div className={`text-2xl font-semibold tabular-nums mt-0.5 ${tone}`}>{value}</div>
        {sub && <div className="text-xs text-slate-500 mt-0.5">{sub}</div>}
    </div>
);

const State = ({ ok, children }) => (
    <span className={`inline-flex items-center gap-1 text-sm ${ok ? 'text-emerald-300' : 'text-red-300'}`}>
        {ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />} {children}
    </span>
);

const ago = (at, now) => (at ? formatAge(Math.max(0, now - at)) : 'never');

const Ratatoskr = ({ health }) => (
    <div className="px-4 py-3 flex flex-col gap-2 text-sm">
        <State ok={health.reachable && health.secret_accepted && !health.error}>
            {!health.reachable ? 'Not reachable' : health.secret_accepted === false ? 'Secret refused' : health.error ? 'Answers, with a problem' : `Answers in ${health.answer_ms} ms`}
        </State>
        {health.error && <p className="text-xs text-red-300/90">{health.error}</p>}
        {health.reachable && health.secret_accepted && (
            <>
                <p className="text-slate-400">
                    {health.sessions?.length ? `${health.sessions.length} live session${health.sessions.length === 1 ? '' : 's'}` : 'No live sessions'}
                    {health.idle_timeout_ms ? ` · logged off after ${Math.round(health.idle_timeout_ms / 60000)} minutes idle` : ' · never logged off for being idle'}
                </p>
                {health.sessions?.length > 0 && (
                    <ul className="text-xs text-slate-300 flex flex-col gap-0.5">
                        {health.sessions.map(session => (
                            <li key={session.steam_id} className="flex gap-2">
                                <span className="text-slate-100">{session.account_name}</span>
                                <span className={session.game_coordinator ? 'text-emerald-300' : 'text-amber-300'}>
                                    {session.game_coordinator ? 'Game Coordinator connected' : 'Game Coordinator lost'}
                                </span>
                                <span className="text-slate-500">idle {Math.round(session.idle_seconds / 60)} minutes</span>
                            </li>
                        ))}
                    </ul>
                )}
            </>
        )}
    </div>
);

const Asf = ({ health }) => (
    <div className="px-4 py-3 flex flex-col gap-2 text-sm">
        {!health.enabled
            ? <State ok={false}>Switched off (no ASF_IPC_PASSWORD)</State>
            : <State ok={health.reachable && !health.error}>{health.reachable ? `Answers · ${health.accounts} accounts` : 'Not reachable'}</State>}
        {health.error && <p className="text-xs text-red-300/90">{health.error}</p>}
        {health.states && Object.keys(health.states).length > 0 && (
            <p className="text-slate-400">
                {Object.entries(health.states).map(([state, count]) => `${count} ${state}`).join(' · ')}
            </p>
        )}
    </div>
);

const SystemHealth = () => {
    const [health, setHealth] = useState(null);
    const [error, setError] = useState(null);
    const request = useRef(0);

    const load = useCallback(async () => {
        const n = ++request.current;
        try {
            const answer = await readJsonOrThrow(await fetch('/api/system/health'));
            if (n !== request.current) return;
            setHealth(answer);
            setError(null);
        } catch (failure) {
            if (n !== request.current) return;
            setError(isTransientFailure(failure) ? 'The backend is not answering right now (restarting?); trying again.' : failureMessage(failure));
        }
    }, []);

    useEffect(() => {
        const first = setTimeout(load, 0);
        const timer = setInterval(() => { if (!document.hidden) load(); }, POLL_MS);
        return () => { clearTimeout(first); clearInterval(timer); };
    }, [load]);

    const now = health?.checked_at || 0;
    const loops = health?.loops || [];
    const stopped = loops.filter(loop => !loop.running).length;
    const memory = health?.memory || {};
    const errors = health?.errors || [];

    return (
        <div className="h-screen bg-slate-950/40 flex flex-col overflow-hidden">
            <div className="shrink-0 border-b border-white/5 bg-slate-950/70 backdrop-blur px-6 py-4 flex items-center gap-3 flex-wrap">
                <Link to="/" className="flex items-center gap-1.5 text-sm text-slate-400 hover:text-white transition-colors shrink-0">
                    <LayoutDashboard size={15} /> Dashboard
                </Link>
                <span className="text-white/20">/</span>
                <h1 className="text-lg font-bold font-serif text-sky-100 flex items-center gap-2"><Activity size={16} className="text-sky-400" /> System health</h1>
                {health && <span className="text-xs text-slate-500">checked {clock(health.checked_at)}</span>}
                <button type="button" onClick={load} className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-white/10 text-xs text-slate-300 hover:text-white">
                    <RefreshCw size={12} /> Reload
                </button>
            </div>

            <div className="flex-1 overflow-y-auto">
                <div className="max-w-6xl mx-auto px-6 pt-6 pb-16 flex flex-col gap-6">
                    {error && (
                        <div className="bg-red-500/15 border border-red-500/30 text-red-300 px-4 py-2.5 rounded-lg text-sm flex items-center gap-2">
                            <AlertTriangle size={14} /> {error}
                        </div>
                    )}

                    <div className="flex flex-wrap gap-3">
                        <Stat label="Background loops" value={health ? `${loops.length - stopped} of ${loops.length}` : '—'}
                            sub={health ? (stopped ? `${stopped} stopped` : 'all running') : null}
                            tone={stopped ? 'text-red-300' : 'text-emerald-300'} />
                        <Stat label="Backend memory" value={megabytes(memory.process_bytes)}
                            sub={health ? `peak ${megabytes(memory.process_peak_bytes)} since start` : null} />
                        <Stat label="Container memory" value={megabytes(memory.container_bytes)}
                            sub={health ? (memory.container_limit_bytes ? `limit ${megabytes(memory.container_limit_bytes)}` : 'no limit set for this container') : null} />
                        <Stat label="Errors logged" value={health ? errors.length : '—'}
                            sub={health ? (errors.length ? `newest ${ago(errors[0].at, now)}` : 'none since the backend started') : null}
                            tone={errors.length ? 'text-amber-300' : 'text-emerald-300'} />
                    </div>

                    <div className="grid gap-4 md:grid-cols-2">
                        <Panel title="Ratatoskr">{health ? <Ratatoskr health={health.ratatoskr} /> : null}</Panel>
                        <Panel title="ArchiSteamFarm (ASF)">{health ? <Asf health={health.asf} /> : null}</Panel>
                    </div>

                    <Panel title="Background loops" right={<span className="text-xs text-slate-500">a part runs inside the thread of the loop it belongs to</span>}>
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                    <tr className="text-[11px] uppercase tracking-wider text-slate-500 bg-black/20">
                                        <th className="text-left font-semibold px-4 py-2">Loop</th>
                                        <th className="text-left font-semibold px-3 py-2">State</th>
                                        <th className="text-right font-semibold px-3 py-2">Rounds</th>
                                        <th className="text-left font-semibold px-3 py-2">Last round</th>
                                        <th className="text-left font-semibold px-3 py-2">Last error</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {loops.map(loop => (
                                        <tr key={loop.name} className="border-t border-white/5 hover:bg-white/[0.02]">
                                            <td className="px-4 py-1.5 text-slate-200 whitespace-nowrap">
                                                {loop.part_of && <span className="text-slate-500">{loop.part_of} → </span>}{loop.name}
                                            </td>
                                            <td className="px-3 py-1.5"><State ok={loop.running}>{loop.running ? 'running' : 'stopped'}</State></td>
                                            <td className="px-3 py-1.5 text-right tabular-nums text-slate-300">{loop.rounds}</td>
                                            <td className="px-3 py-1.5 text-slate-300 whitespace-nowrap" title={loop.last_round_at ? clock(loop.last_round_at) : ''}>
                                                {ago(loop.last_round_at, now)}
                                            </td>
                                            <td className="px-3 py-1.5 text-xs text-red-300/80 max-w-[24rem] truncate" title={loop.last_error || ''}>
                                                {loop.last_error ? `${loop.last_error} (${ago(loop.last_error_at, now)})` : ''}
                                            </td>
                                        </tr>
                                    ))}
                                    {health && loops.length === 0 && (
                                        <tr><td colSpan={5} className="px-4 py-6 text-center text-sm text-slate-500">No loop has started yet.</td></tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </Panel>

                    <Panel title="Last errors" right={<span className="text-xs text-slate-500">newest first, in memory since the backend started; the full log is logs/heimdall.log</span>}>
                        {errors.length === 0
                            ? <p className="px-4 py-4 text-sm text-slate-500">{health ? 'No errors logged since the backend started.' : ''}</p>
                            : (
                                <ul className="divide-y divide-white/5">
                                    {errors.map((entry, index) => (
                                        <li key={`${entry.at}-${index}`} className="px-4 py-2 text-sm">
                                            <div className="flex flex-wrap gap-x-3 text-xs text-slate-500">
                                                <span title={clock(entry.at)}>{ago(entry.at, now)}</span>
                                                <span>{entry.source}</span>
                                                <span>thread {entry.thread}</span>
                                                {entry.level !== 'ERROR' && <span className="text-red-300">{entry.level}</span>}
                                            </div>
                                            <div className="text-red-200/90 break-words">{entry.message}</div>
                                        </li>
                                    ))}
                                </ul>
                            )}
                    </Panel>
                </div>
            </div>
        </div>
    );
};

export default SystemHealth;
