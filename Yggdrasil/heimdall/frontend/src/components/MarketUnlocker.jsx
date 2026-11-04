import { useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Unlock, X, Loader2, FlaskConical, ShoppingCart, AlertTriangle } from 'lucide-react';
import TypedConfirmDialog from './TypedConfirmDialog';
import { MarketAccessLine } from './MarketAccess';

// Market unlocker (Mímir): buys Zombie Hunter's one-cent "Editor Pack" on the chosen
// accounts, an in-game purchase that keeps the Steam Community Market open for a year
// (backend: market_access_service.py). Two backend commands: Dry run makes a plan (it goes
// as far as Steam's approval page and cancels); Buy for real executes that plan once, only
// on the accounts that passed it, after the typed phrase. The backend decides which accounts
// start ticked (purchase_would_help) and words the last run per account (last_unlocks).

const formatWhen = (seconds) => (seconds ? new Date(seconds * 1000).toLocaleString('en-GB') : '');
const formatTime = (iso) => (iso ? new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '');

const UNLOCK_STYLES = {
    running: 'text-sky-300',
    bought: 'text-emerald-400',
    passed: 'text-emerald-300/80',
    failed: 'text-amber-300',
    may_be_paid: 'text-red-400',
};

const UnlockResult = ({ last }) => {
    if (!last) return <span className="text-slate-600">not tried</span>;
    const when = formatWhen(last.at);
    return (
        <span className={UNLOCK_STYLES[last.kind] || 'text-slate-400'} title={when}>
            {last.text}{last.kind === 'bought' ? ` ${when}` : ''}
        </span>
    );
};

const PlanSummary = ({ plan }) => {
    if (!plan) return null;
    if (plan.state === 'dry run') return <p className="text-sky-300">Dry run running…</p>;
    const passed = plan.accounts.filter((account) => account.passed);
    return (
        <div className="space-y-0.5">
            {plan.error && <p className="text-red-300">Dry run stopped: {plan.error}</p>}
            <p className={passed.length ? 'text-emerald-300' : 'text-amber-300'}>
                Dry run passed on {passed.length} of {plan.accounts.length}
                {passed.length ? `: ${passed.map((account) => account.account_name).join(', ')}` : ''}.
                {plan.job_id
                    ? ' This plan was executed: the last run per account is in the table.'
                    : plan.executable
                        ? ` Buy for real buys only these, until ${formatTime(plan.expires_at)}.`
                        : plan.state === 'replaced'
                            ? ' A newer dry run covers these accounts: this plan can no longer be bought.'
                            : plan.state === 'failed'
                                ? ' This plan cannot be bought: run the dry run again.'
                                : plan.expired
                                    ? ' The plan expired: run the dry run again.' : ''}
            </p>
        </div>
    );
};

const MarketUnlocker = ({ status, accounts, plan, onDryRun, onExecute, starting, startError, onClose }) => {
    const unlocker = status?.unlocker || {};
    const job = status?.job || {};
    const running = Boolean(job.running) || starting;
    const lastUnlocks = status?.last_unlocks || {};
    // null until the first tick: the backend's choice, also when the status loads after the dialog opened.
    const [picked, setPicked] = useState(null);
    const selected = picked ?? new Set(
        accounts
            .filter((account) => status?.accounts?.[account.steamid]?.purchase_would_help)
            .map((account) => account.steamid),
    );
    const [confirmOpen, setConfirmOpen] = useState(false);
    const buying = useRef(false);

    const toggle = (steamid) => {
        const next = new Set(selected);
        if (next.has(steamid)) next.delete(steamid); else next.add(steamid);
        setPicked(next);
    };
    const chosen = accounts.filter((account) => selected.has(account.steamid)).map((account) => account.steamid);
    const passedNames = (plan?.accounts || []).filter((account) => account.passed).map((account) => account.account_name);
    const canBuy = Boolean(plan?.executable) && !running;

    const buyForReal = async () => {
        if (buying.current) return;
        buying.current = true;
        try {
            await onExecute(plan.plan_id);      // a refusal shows above the table
            setConfirmOpen(false);
        } finally {
            buying.current = false;
        }
    };

    return createPortal(
        <div className="fixed inset-0 bg-slate-950/85 flex items-center justify-center z-50 backdrop-blur-sm p-4" role="dialog" aria-modal="true">
            {confirmOpen && (
                <TypedConfirmDialog
                    title="Buy the Market unlocker for real"
                    message={`Buys ${unlocker.game || 'Zombie Hunter'}'s ${unlocker.item || 'Editor Pack'} on the ${passedNames.length} account${passedNames.length === 1 ? '' : 's'} that passed the dry run (${passedNames.join(', ')}), from each Steam wallet (about one US cent each, refused above $${(plan?.max_usd ?? unlocker.max_usd ?? 0.05).toFixed(2)}).`}
                    phrase={`BUY ${passedNames.length}`}
                    confirmLabel="Buy"
                    busy={running}
                    onConfirm={buyForReal}
                    onCancel={() => setConfirmOpen(false)}
                />
            )}
            <div className="bg-slate-900/95 border border-violet-500/30 rounded-2xl w-full max-w-4xl max-h-[90vh] flex flex-col shadow-2xl">
                <div className="flex items-start gap-3 p-5 border-b border-white/5">
                    <Unlock size={18} className="text-violet-300 mt-0.5" />
                    <div className="flex-1">
                        <h2 className="text-base font-semibold text-violet-100">Market unlocker</h2>
                        <p className="text-xs text-slate-400 mt-1">
                            Buys {unlocker.game || 'Zombie Hunter'}&apos;s one-cent &quot;{unlocker.item || 'Editor Pack'}&quot; (an in-game
                            purchase) so the account keeps the Steam Community Market for another year. The free game is
                            claimed first when the account does not own it (a dry run claims it too). A dry run stops on
                            Steam&apos;s approval page and cancels. Buy for real buys only on the accounts that passed the last dry
                            run, within 10 minutes of it. Steam asks some accounts for a billing address first: enter it once in a browser.
                        </p>
                    </div>
                    <button onClick={onClose} className="p-1 text-slate-500 hover:text-white" aria-label="Close"><X size={18} /></button>
                </div>
                {startError && (
                    <div className="mx-5 mt-3 flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
                        <AlertTriangle size={14} /> Not started: {startError}
                    </div>
                )}
                <div className="overflow-auto flex-1">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="text-[11px] uppercase tracking-wider text-slate-500 border-b border-white/5">
                                <th className="px-4 py-2 w-8">
                                    <input
                                        type="checkbox"
                                        aria-label="Choose every account"
                                        checked={chosen.length === accounts.length && accounts.length > 0}
                                        onChange={(event) => setPicked(new Set(event.target.checked ? accounts.map((account) => account.steamid) : []))}
                                    />
                                </th>
                                <th className="text-left font-semibold px-4 py-2">Account</th>
                                <th className="text-left font-semibold px-4 py-2">Market access</th>
                                <th className="text-left font-semibold px-4 py-2">Last unlocker run</th>
                            </tr>
                        </thead>
                        <tbody>
                            {accounts.map((account) => (
                                <tr key={account.steamid} className="border-b border-white/[0.03]">
                                    <td className="px-4 py-2">
                                        <input type="checkbox" checked={selected.has(account.steamid)} onChange={() => toggle(account.steamid)}
                                            aria-label={`Choose ${account.account_name}`} />
                                    </td>
                                    <td className="px-4 py-2 text-slate-200">{account.account_name}</td>
                                    <td className="px-4 py-2"><MarketAccessLine result={status?.accounts?.[account.steamid]} wrap /></td>
                                    <td className="px-4 py-2 text-xs"><UnlockResult last={lastUnlocks[account.steamid]} /></td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                {plan && (
                    <div className="px-5 py-2 border-t border-white/5 text-xs">
                        <PlanSummary plan={plan} />
                    </div>
                )}
                <div className="flex items-center gap-3 p-4 border-t border-white/5 flex-wrap">
                    <span className="text-xs text-slate-400">
                        {running && job.kind === 'unlock'
                            ? `${job.dry_run ? 'Dry run' : 'Buying'} ${job.done}/${job.total}${job.current ? ` · ${job.current}` : ''}`
                            : `${chosen.length} chosen`}
                    </span>
                    <div className="ml-auto flex items-center gap-2">
                        <button
                            onClick={() => onDryRun(chosen)}
                            disabled={running || chosen.length === 0}
                            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-sky-500/10 border border-sky-500/30 hover:bg-sky-500/20 text-sky-100 text-sm disabled:opacity-50"
                        >
                            {running ? <Loader2 size={14} className="animate-spin" /> : <FlaskConical size={14} />} Dry run
                        </button>
                        <button
                            onClick={() => setConfirmOpen(true)}
                            disabled={!canBuy}
                            title={plan?.executable ? '' : 'Run the dry run first: only the accounts that pass it can be bought'}
                            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-600/20 border border-red-500/40 hover:bg-red-600/30 text-red-100 text-sm disabled:opacity-50"
                        >
                            <ShoppingCart size={14} /> Buy for real ({plan?.executable ? passedNames.length : 0})
                        </button>
                    </div>
                </div>
            </div>
        </div>,
        document.body,
    );
};

export default MarketUnlocker;
