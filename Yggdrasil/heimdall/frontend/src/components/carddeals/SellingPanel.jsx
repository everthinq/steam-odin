import { useState } from 'react';
import { readJson } from '../../utils/readJson';

// Andvari card auto-sell. Backend: GET /api/huginn/card-deals/selling (status, totals and the
// Buy games returns), POST …/selling/config (switches, app ids parsed by the backend) and
// POST …/selling/include-held (asks first: it also sells cards held before). Amounts arrive in
// minor units per ISO currency code.

const amount = (minorUnits, code) => {
    try {
        return new Intl.NumberFormat(undefined, { style: 'currency', currency: code }).format(minorUnits / 100);
    } catch {
        return `${(minorUnits / 100).toFixed(2)} ${code}`;
    }
};
const amounts = (byCode) => Object.entries(byCode || {}).map(([code, value]) => amount(value, code)).join(' + ') || '—';
const RESULT_TEXT = { listed: 'Listed', buy_order: 'Listed at the buy order' };

const ago = (epochSeconds) => {
    if (!epochSeconds) return 'never';
    const minutes = Math.round((Date.now() / 1000 - epochSeconds) / 60);
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.round(minutes / 60);
    return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} days ago`;
};

const SellingPanel = ({ selling, onSaved }) => {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const [appsText, setAppsText] = useState(null);

    if (!selling) return <div className="text-xs text-slate-500 px-3 py-2">Loading card selling…</div>;

    const send = (path, body) => {
        setBusy(true);
        setError(null);
        return fetch(`/api/huginn/card-deals/selling/${path}`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        })
            .then(readJson)
            .then((d) => d)
            .catch((e) => ({ error: String(e) }))
            .finally(() => setBusy(false));
    };
    const save = (changes) => send('config', changes).then((d) => {
        if (d.error) setError(d.error);
        else setAppsText(null);
        onSaved();
    });
    // Also selling cards held before asks first; the backend's question is shown as is.
    const setIncludeHeld = (on) => send('include-held', { on }).then((d) => {
        if (d.code === 'confirm_needed') {
            if (!window.confirm(d.error)) return null;
            return send('include-held', { on, confirmed: true });
        }
        return d;
    }).then((d) => {
        if (d?.error) setError(d.error);
        onSaved();
    });
    const stats = selling.statistics || { accounts: {}, games: {}, listed: 0, failed: 0 };

    return (
        <div className="rounded-xl border border-white/10 bg-black/20 p-3 text-xs space-y-3">
            {error && <p className="text-red-400">{error}</p>}
            <div className="flex flex-wrap items-start gap-x-6 gap-y-2">
                <label className="flex items-start gap-2 text-sm text-slate-200 cursor-pointer">
                    <input type="checkbox" className="mt-1 accent-amber-500" disabled={busy} checked={selling.enabled}
                        onChange={(e) => save({ enabled: e.target.checked })} />
                    <span>
                        Sell dropped cards automatically
                        <span className="block text-xs text-slate-500">
                            At the highest price that still sells: up to what 90% of the last week’s sales fetched, with at most 3 days of sales listed cheaper; never under one cent below the lowest listing nor the highest buy order; confirmed automatically
                            {selling.enabled_at ? ` · on since ${ago(selling.enabled_at)}` : ''}
                        </span>
                    </span>
                </label>
                <label className="flex items-start gap-2 text-slate-300 cursor-pointer">
                    <input type="checkbox" className="mt-0.5 accent-amber-500" disabled={busy} checked={selling.include_held}
                        onChange={(e) => setIncludeHeld(e.target.checked)} />
                    <span>Also cards held before it was switched on<span className="block text-slate-500">Off: only new drops are sold</span></span>
                </label>
                <label className="flex items-start gap-2 text-slate-300 cursor-pointer">
                    <input type="checkbox" className="mt-0.5 accent-amber-500" disabled={busy} checked={selling.foil}
                        onChange={(e) => save({ foil: e.target.checked })} />
                    <span>Foil cards too</span>
                </label>
                <div className="text-slate-300">
                    Only these games (app ids, empty = every game):
                    <div className="flex gap-1 mt-1">
                        <input type="text" value={appsText ?? (selling.apps || []).join(', ')} placeholder="every game (app ids or store links)"
                            onChange={(e) => setAppsText(e.target.value)}
                            className="w-56 bg-black/30 border border-white/10 rounded px-2 py-1" />
                        {appsText !== null && (
                            <button type="button" disabled={busy} onClick={() => save({ apps: appsText || '' })}
                                className="px-2 py-1 rounded bg-amber-600 hover:bg-amber-500 text-white disabled:opacity-50">Save</button>
                        )}
                    </div>
                </div>
            </div>
            {selling.rate_limited_until && (
                <p className="text-amber-300">Steam rate-limited a read: paused until {new Date(selling.rate_limited_until * 1000).toLocaleTimeString()}</p>
            )}

            <div className="grid gap-3 md:grid-cols-2">
                <div>
                    <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Listed per account ({stats.listed} cards{stats.failed ? `, ${stats.failed} refused` : ''})</p>
                    <div className="max-h-40 overflow-auto custom-scrollbar">
                        {Object.entries(stats.accounts).length === 0 ? <p className="text-slate-500">Nothing listed yet.</p> : (
                            <table className="w-full">
                                <tbody>
                                    {Object.entries(stats.accounts).sort((a, b) => b[1].listed - a[1].listed).map(([name, entry]) => (
                                        <tr key={name} className="border-t border-white/5">
                                            <td className="py-0.5 text-slate-300">{name}</td>
                                            <td className="text-right tabular-nums">{entry.listed}</td>
                                            <td className="text-right tabular-nums text-emerald-300 pl-3">{amounts(entry.receives)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                    </div>
                </div>
                <div>
                    <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Listed per game</p>
                    <div className="max-h-40 overflow-auto custom-scrollbar">
                        {Object.entries(stats.games).length === 0 ? <p className="text-slate-500">Nothing listed yet.</p> : (
                            <table className="w-full">
                                <tbody>
                                    {Object.entries(stats.games).sort((a, b) => b[1].listed - a[1].listed).map(([game, entry]) => (
                                        <tr key={game} className="border-t border-white/5">
                                            <td className="py-0.5 text-slate-300">{game.replace(/^app_/, 'app ')}</td>
                                            <td className="text-right tabular-nums">{entry.listed}</td>
                                            <td className="text-right tabular-nums text-emerald-300 pl-3">{amounts(entry.receives)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                    </div>
                </div>
            </div>

            {selling.returns?.length > 0 && (
                <div>
                    <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Bought with “Buy games” vs. cards listed so far</p>
                    <table className="w-full">
                        <thead className="text-slate-500 text-left">
                            <tr><th>Game</th><th className="text-right">Copies bought</th><th className="text-right">Spent</th><th className="text-right">Cards listed</th><th className="text-right">You receive</th><th className="text-right">So far</th></tr>
                        </thead>
                        <tbody>
                            {selling.returns.map((row) => (
                                <tr key={row.game} className="border-t border-white/5">
                                    <td className="py-0.5 text-slate-300">{row.game}</td>
                                    <td className="text-right tabular-nums">{row.copies}</td>
                                    <td className="text-right tabular-nums text-amber-200">{amounts(row.spent)}</td>
                                    <td className="text-right tabular-nums">{row.listed}</td>
                                    <td className="text-right tabular-nums text-emerald-300">{amounts(row.receives)}</td>
                                    <td className={`text-right tabular-nums ${row.net == null ? 'text-slate-500' : row.net >= 0 ? 'text-emerald-300' : 'text-amber-300'}`}>
                                        {row.net == null ? 'mixed currencies' : `${row.net >= 0 ? '+' : '−'}${amount(Math.abs(row.net), row.net_currency)}`}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    <p className="text-slate-500 mt-1">“You receive” counts listed cards (after Steam’s fees); a listing earns once someone buys it.</p>
                </div>
            )}

            {selling.sales.length > 0 && (
                <div>
                    <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Latest listings</p>
                    <div className="max-h-48 overflow-auto custom-scrollbar">
                        <table className="w-full">
                            <thead className="text-slate-500 text-left">
                                <tr><th>When</th><th>Account</th><th>Card</th><th className="text-right">Buyer pays</th><th className="text-right">You receive</th><th className="pl-3">Result</th></tr>
                            </thead>
                            <tbody>
                                {selling.sales.map((s) => (
                                    <tr key={`${s.assetid}-${s.at}`} className="border-t border-white/5">
                                        <td className="py-0.5 text-slate-400">{ago(s.at)}</td>
                                        <td className="text-slate-300">{s.account_name}</td>
                                        <td className="text-slate-200">{s.card}{s.foil ? ' (foil)' : ''}</td>
                                        <td className="text-right tabular-nums">{amount(s.buyer_pays, s.currency_code)}</td>
                                        <td className="text-right tabular-nums text-emerald-300">{amount(s.receives, s.currency_code)}</td>
                                        <td className={`pl-3 ${s.ok ? 'text-emerald-300' : 'text-red-400'}`} title={s.how || ''}>{RESULT_TEXT[s.result] || s.error}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    );
};

export default SellingPanel;
