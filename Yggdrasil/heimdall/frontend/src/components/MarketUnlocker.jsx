import { useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Unlock, X, Loader2, FlaskConical, ShoppingCart, AlertTriangle } from 'lucide-react';
import TypedConfirmDialog from './TypedConfirmDialog';
import { MarketAccessLine } from './MarketAccess';

// Market unlocker (Mímir): buys Zombie Hunter's one-cent "Editor Pack" on the chosen
// accounts, an in-game purchase that keeps the Steam Community Market open for a year
// (backend: market_access_service.py, start_unlock). A dry run goes as far as Steam's
// approval page and cancels; a real purchase needs the typed phrase.

const formatWhen = (seconds) => (seconds ? new Date(seconds * 1000).toLocaleString('en-GB') : '');

const lastUnlockByAccount = (unlocks) => {
    const latest = {};
    for (const entry of unlocks || []) {          // newest first
        if (!latest[entry.steamid]) latest[entry.steamid] = entry;
    }
    return latest;
};

// Ticked at first: accounts a purchase would help, amber or red with no counted purchase
// that keeps the Market open (red with one: Steam blocks it for another reason).
const needsPurchase = (result) => {
    if (!result || !['warn', 'lapsed'].includes(result.state)) return false;
    const daysLeft = result.summary?.days_left;
    return daysLeft == null || daysLeft <= 30;
};

const UnlockResult = ({ entry }) => {
    if (!entry) return <span className="text-slate-600">not tried</span>;
    const when = formatWhen(entry.at);
    if (entry.state === 'in progress') return <span className="text-sky-300">{entry.step}…</span>;
    if (entry.ok && entry.paid) return <span className="text-emerald-400" title={when}>bought {when}</span>;
    if (entry.ok) {
        return (
            <span className="text-emerald-300/80" title={when}>
                ready: dry run passed{entry.usd != null ? ` ($${entry.usd.toFixed(4)})` : ''}
            </span>
        );
    }
    return <span className={entry.approved ? 'text-red-400' : 'text-amber-300'} title={when}>{entry.error}</span>;
};

const MarketUnlocker = ({ status, accounts, onUnlock, starting, startError, onClose }) => {
    const unlocker = status?.unlocker || {};
    const job = status?.job || {};
    const running = Boolean(job.running) || starting;
    const latest = useMemo(() => lastUnlockByAccount(status?.unlocks), [status?.unlocks]);
    const [selected, setSelected] = useState(() => new Set(
        accounts
            .filter((account) => needsPurchase(status?.accounts?.[account.steamid]))
            .map((account) => account.steamid),
    ));
    const [confirmOpen, setConfirmOpen] = useState(false);
    const buying = useRef(false);

    const toggle = (steamid) => setSelected((previous) => {
        const next = new Set(previous);
        if (next.has(steamid)) next.delete(steamid); else next.add(steamid);
        return next;
    });
    const chosen = accounts.filter((account) => selected.has(account.steamid)).map((account) => account.steamid);

    const buyForReal = async () => {
        if (buying.current) return;
        buying.current = true;
        try {
            await onUnlock(chosen, false);      // a refusal shows above the table
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
                    message={`Buys ${unlocker.game || 'Zombie Hunter'}'s ${unlocker.item || 'Editor Pack'} on ${chosen.length} account${chosen.length === 1 ? '' : 's'}, from each Steam wallet (about one US cent each, refused above $${(unlocker.max_usd ?? 0.05).toFixed(2)}).`}
                    phrase={`BUY ${chosen.length}`}
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
                            Steam&apos;s approval page and cancels. Steam asks some accounts for a billing address first: enter it once in a browser.
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
                                        onChange={(event) => setSelected(new Set(event.target.checked ? accounts.map((account) => account.steamid) : []))}
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
                                    <td className="px-4 py-2 text-xs"><UnlockResult entry={latest[account.steamid]} /></td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                <div className="flex items-center gap-3 p-4 border-t border-white/5 flex-wrap">
                    <span className="text-xs text-slate-400">
                        {running && job.kind === 'unlock'
                            ? `${job.dry_run ? 'Dry run' : 'Buying'} ${job.done}/${job.total}${job.current ? ` · ${job.current}` : ''}`
                            : `${chosen.length} chosen`}
                    </span>
                    <div className="ml-auto flex items-center gap-2">
                        <button
                            onClick={() => onUnlock(chosen, true)}
                            disabled={running || chosen.length === 0}
                            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-sky-500/10 border border-sky-500/30 hover:bg-sky-500/20 text-sky-100 text-sm disabled:opacity-50"
                        >
                            {running ? <Loader2 size={14} className="animate-spin" /> : <FlaskConical size={14} />} Dry run
                        </button>
                        <button
                            onClick={() => setConfirmOpen(true)}
                            disabled={running || chosen.length === 0}
                            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-600/20 border border-red-500/40 hover:bg-red-600/30 text-red-100 text-sm disabled:opacity-50"
                        >
                            <ShoppingCart size={14} /> Buy for real ({chosen.length})
                        </button>
                    </div>
                </div>
            </div>
        </div>,
        document.body,
    );
};

export default MarketUnlocker;
