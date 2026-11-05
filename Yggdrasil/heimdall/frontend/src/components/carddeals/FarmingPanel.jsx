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
    queued: { label: 'Queued: the most bots already run', tone: 'text-sky-300/70' },
    not_added: { label: 'Not added yet', tone: 'text-slate-500' },
};

// The look of each action; which actions an account offers comes from the backend (`actions`).
const ACTIONS = {
    'farm-now': { Icon: Zap, title: 'Farm now: switch this account\'s bot on so ASF checks for cards (it switches off again if there is nothing)', tone: 'text-amber-300/80 hover:text-amber-200' },
    'retry-login': { Icon: RotateCcw, title: 'Try logging in again', tone: 'text-slate-400 hover:text-white' },
    pause: { Icon: Pause, title: 'Pause farming on this account', tone: 'text-slate-400 hover:text-white' },
    resume: { Icon: Play, title: 'Resume farming on this account', tone: 'text-slate-400 hover:text-white' },
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
            {farming.global_config_unsafe && (
                <p className="text-red-400 mb-2">All bots switched off: {farming.global_config_unsafe}</p>
            )}
            <p className="text-slate-500 mb-2">
                {farming.totals?.running ?? 0} of {farming.totals?.max_running ?? '?'} bots running. A bot runs only
                while its account has cards to farm (ASF’s FAQ recommends at most 10; set to {farming.totals?.max_running ?? '?'}). Bought a game? Press
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
                                {(a.actions || []).map((action) => {
                                    const button = ACTIONS[action];
                                    if (!button) return null;
                                    const { Icon } = button;
                                    return (
                                        <button key={action} type="button" disabled={busy} title={button.title}
                                            onClick={() => onAction(a.steamid, action)}
                                            className={`p-1 rounded hover:bg-white/10 disabled:opacity-40 ${button.tone}`}>
                                            <Icon size={12} />
                                        </button>
                                    );
                                })}
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
};

export default FarmingPanel;
