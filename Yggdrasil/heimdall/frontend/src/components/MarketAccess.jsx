import React from 'react';
import { Store, RefreshCw, AlertTriangle } from 'lucide-react';

// Steam Community Market access per account (backend: market_access_service.py). The check
// runs from Mímir (button, banner, a column); the Dashboard shows the result on each card.
// Steam keeps the Market open while the account has a purchase between 7 days and one
// year old (store or in-game); Market transactions and free promotional licenses do not count.

const formatDate = (iso) => {
    if (!iso) return null;
    const [year, month, day] = iso.split('-').map(Number);
    return new Date(year, month - 1, day).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
};

const STATE_STYLES = {
    ok: 'text-emerald-400',
    warn: 'text-amber-400',
    lapsed: 'text-red-400',
    unknown: 'text-slate-500',
};

const describe = (result) => {
    const summary = result.summary || {};
    const steam = result.steam || {};
    if (result.error && !result.summary) return `Not checked: ${result.error}`;
    if (steam.allowed === false) {
        return steam.allowed_at
            ? `No Market access (Steam) until ${new Date(steam.allowed_at * 1000).toLocaleDateString('en-GB')}`
            : 'No Market access (Steam says so)';
    }
    if (summary.opens_on) return `Market opens ${formatDate(summary.opens_on)}`;
    if (!summary.access_until) return 'No counted purchase on the first page of Purchase History';
    if (summary.days_left < 0) return `Market access lapsed ${formatDate(summary.access_until)}`;
    return `Market until ${formatDate(summary.access_until)} · ${summary.days_left} days`;
};

const tooltip = (result) => {
    const summary = result.summary || {};
    const steam = result.steam || {};
    const lines = [];
    const purchase = summary.last_purchase;
    lines.push(purchase
        ? `Last counted purchase: ${formatDate(purchase.date)} · ${purchase.items.join(', ')} · ${purchase.total} (${purchase.payment})`
        : 'No counted purchase (game, DLC, wallet top-up or in-game purchase) on the first page of Purchase History');
    lines.push(`Steam's check: ${steam.allowed === true ? 'Market allowed' : steam.allowed === false ? 'Market NOT allowed' : 'no answer'}`
        + (steam.reason ? ` (reason ${steam.reason})` : ''));
    if (result.error) lines.push(`Error: ${result.error}`);
    if (result.checked_at) lines.push(`Checked ${new Date(result.checked_at * 1000).toLocaleString('en-GB')}`);
    return lines.join('\n');
};

/** One line on an account card; *wrap* lets it take two lines (Mímir's table column). */
export const MarketAccessLine = ({ result, wrap = false }) => {
    if (!result) return null;
    return (
        <div
            className={`flex ${wrap ? 'items-start' : 'items-center'} gap-1.5 text-xs ${STATE_STYLES[result.state] || STATE_STYLES.unknown}`}
            title={tooltip(result)}
        >
            <Store size={12} className={`shrink-0 ${wrap ? 'mt-0.5' : ''}`} />
            <span className={wrap ? '' : 'truncate'}>{describe(result)}</span>
        </div>
    );
};

/** Header button: checks every account again; shows progress while it runs. */
export const MarketAccessButton = ({ status, onCheck, starting }) => {
    const job = status?.job || {};
    const running = job.running || starting;
    return (
        <button
            onClick={onCheck}
            disabled={running}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-violet-500/10 border border-violet-500/30 hover:border-violet-400/60 hover:bg-violet-500/20 text-violet-100 text-sm transition-colors disabled:opacity-60"
            title="Read each account's Purchase History (first page) and Steam's Market eligibility check, one account at a time. Steam keeps the Market open while the account has a store or in-game purchase between 7 days and one year old; Market transactions and free licenses do not count."
        >
            {running ? <RefreshCw size={14} className="animate-spin" /> : <Store size={14} />}
            {running
                ? `${job.kind === 'unlock' ? 'Unlocker' : 'Market access'} ${job.done || 0}/${job.total || '…'}`
                : 'Market access'}
        </button>
    );
};

/** Accounts that lose (or lost) the Market, and job errors. *accounts*: [{steamid, account_name}]. */
export const MarketAccessBanner = ({ status, accounts, startError = null }) => {
    if (!status) return null;
    const job = status.job || {};
    const names = Object.fromEntries((accounts || []).map((account) => [String(account.steamid), account.account_name]));
    const results = Object.entries(status.accounts || {}).filter(([steamid]) => names[steamid]);
    const flagged = results
        .filter(([, result]) => result.state === 'warn' || result.state === 'lapsed')
        .sort(([, a], [, b]) => (a.summary?.days_left ?? -9999) - (b.summary?.days_left ?? -9999));
    const unknown = results.filter(([, result]) => result.state === 'unknown');
    if (!flagged.length && !unknown.length && !job.error && !startError) return null;
    return (
        <div className="mb-6 rounded-lg border border-amber-500/30 bg-amber-950/20 p-4 text-sm space-y-1">
            <div className="flex items-center gap-2 font-semibold text-amber-200">
                <AlertTriangle size={16} />
                Steam Community Market access
            </div>
            {startError && <p className="text-red-300">Could not start the check: {startError}</p>}
            {job.error && <p className="text-red-300">{job.kind === 'unlock' ? 'Unlocker' : 'Check'} stopped: {job.error}</p>}
            {flagged.map(([steamid, result]) => (
                <p key={steamid} className={STATE_STYLES[result.state]}>
                    {names[steamid]}: {describe(result)}
                    {result.state === 'warn' ? ' — buy something (a game, a wallet top-up or an in-game item) to keep it open' : ''}
                </p>
            ))}
            {unknown.length > 0 && (
                <p className="text-slate-400">
                    Not checked: {unknown.map(([steamid, result]) => `${names[steamid]} (${result.error || 'no answer'})`).join(', ')}
                </p>
            )}
            <p className="text-xs text-slate-500">
                Steam keeps the Market open while an account has a store or in-game purchase between 7 days and one year old.
                Market transactions, free promotional licenses and refunded purchases do not count. Warns {status.warn_days} days ahead.
            </p>
        </div>
    );
};
