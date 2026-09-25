import { Pause, Play, RotateCcw, Zap } from 'lucide-react';

// ASF card farming, one row per account. Backend: /api/huginn/card-deals/farming.
// The service never sends secrets here: no config, password or Steam Guard code.
const STATES = {
    farming: { label: 'Farming', tone: 'text-emerald-300' },
    waiting: { label: 'Waiting for drops', tone: 'text-sky-300' },
    done: { label: 'Nothing to farm', tone: 'text-slate-400' },
    paused: { label: 'Paused', tone: 'text-amber-300' },
    paused_for_ratatoskr: { label: 'Paused for Ratatoskr', tone: 'text-amber-300' },
    logging_in: { label: 'Logging in', tone: 'text-sky-300' },
    connecting: { label: 'Connecting', tone: 'text-sky-300' },
    needs_attention: { label: 'Needs you', tone: 'text-red-400' },
    stopped: { label: 'Stopped', tone: 'text-slate-500' },
    off: { label: 'Off: nothing to farm', tone: 'text-slate-500' },
    queued: { label: 'Queued: 10 already running', tone: 'text-sky-300/70' },
    not_added: { label: 'Not added yet', tone: 'text-slate-500' },
};

const duration = (seconds) => {
    if (!seconds) return '';
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.round((seconds % 3600) / 60);
    return hours ? `${hours} h ${minutes} min` : `${minutes} min`;
};

const FarmingPanel = ({ farming, onAction, busy }) => {
    if (!farming) return <div className="text-xs text-slate-500 px-3 py-2">Loading card farming…</div>;
    if (!farming.enabled) {
        return (
            <div className="rounded-xl border border-white/10 bg-black/20 px-4 py-3 text-xs text-slate-400">
                {farming.message}
            </div>
        );
    }
    return (
        <div className="rounded-xl border border-white/10 bg-black/20 p-3 text-xs">
            {!farming.reachable && (
                <p className="text-red-400 mb-2">ASF is not answering: {farming.error}</p>
            )}
            <p className="text-slate-500 mb-2">
                {farming.totals?.running ?? 0} of {farming.totals?.max_running ?? 10} bots running. A bot runs only
                while its account has cards to farm (ASF recommends at most 10). Bought a game? Press
                <Zap size={11} className="inline mx-1 text-amber-300" />on that account so ASF checks it now.
            </p>
            <div className="max-h-64 overflow-auto custom-scrollbar grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
                {farming.accounts.map((a) => {
                    const state = STATES[a.state] || { label: a.state, tone: 'text-slate-400' };
                    const current = (a.current_games || []).map((g) => g.name).join(', ');
                    const attention = a.state === 'needs_attention'
                        ? (a.last_error || (a.required_input ? `ASF needs the ${a.required_input}` : 'login failed'))
                        : null;
                    return (
                        <div key={a.steamid} className="px-2 py-1.5 rounded bg-white/5 flex items-start justify-between gap-2">
                            <div className="min-w-0">
                                <p className="text-slate-300 truncate">{a.account_name}</p>
                                <p className={state.tone}>
                                    {state.label}
                                    {a.cards_remaining ? <span className="text-amber-300"> · {a.cards_remaining} cards in {a.games_to_farm} games</span> : null}
                                    {a.time_remaining_seconds ? <span className="text-slate-500"> · ~{duration(a.time_remaining_seconds)} left</span> : null}
                                </p>
                                {current && <p className="text-slate-500 truncate" title={current}>{current}</p>}
                                {attention && <p className="text-red-400/90 truncate" title={attention}>{attention}</p>}
                            </div>
                            <div className="flex gap-1 shrink-0">
                                {['off', 'stopped', 'not_added'].includes(a.state) && (
                                    <button type="button" disabled={busy} title="Farm now: switch this account's bot on so ASF checks for cards (it switches off again if there is nothing)"
                                        onClick={() => onAction(a.steamid, 'farm-now')}
                                        className="p-1 rounded hover:bg-white/10 text-amber-300/80 hover:text-amber-200 disabled:opacity-40">
                                        <Zap size={12} />
                                    </button>
                                )}
                                {a.state === 'needs_attention' && (
                                    <button type="button" disabled={busy} title="Try logging in again"
                                        onClick={() => onAction(a.steamid, 'retry-login')}
                                        className="p-1 rounded hover:bg-white/10 text-slate-400 hover:text-white disabled:opacity-40">
                                        <RotateCcw size={12} />
                                    </button>
                                )}
                                {['farming', 'waiting'].includes(a.state) && (
                                    <button type="button" disabled={busy} title="Pause farming on this account"
                                        onClick={() => onAction(a.steamid, 'pause')}
                                        className="p-1 rounded hover:bg-white/10 text-slate-400 hover:text-white disabled:opacity-40">
                                        <Pause size={12} />
                                    </button>
                                )}
                                {['paused', 'paused_for_ratatoskr'].includes(a.state) && (
                                    <button type="button" disabled={busy} title="Resume farming on this account"
                                        onClick={() => onAction(a.steamid, 'resume')}
                                        className="p-1 rounded hover:bg-white/10 text-slate-400 hover:text-white disabled:opacity-40">
                                        <Play size={12} />
                                    </button>
                                )}
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
};

export default FarmingPanel;
