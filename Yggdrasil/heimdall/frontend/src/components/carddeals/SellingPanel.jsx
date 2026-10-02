import { useState, useEffect } from 'react';

// Andvari card auto-sell. Backend: /api/huginn/card-deals/selling (status) and
// /api/settings (the switches). Cards are listed one cent under the lowest Market
// listing but never below the highest buy order, in each account's wallet currency.

const CURRENCIES = { 1: ['$', ''], 3: ['', ' €'], 9: ['', ' kr'], 17: ['', ' TL'], 29: ['HK$ ', ''] };
const amount = (minorUnits, currency) => {
    const [before, after] = CURRENCIES[currency] || ['', ` (currency ${currency})`];
    return `${before}${(minorUnits / 100).toFixed(2)}${after}`;
};
const amounts = (byCurrency) => Object.entries(byCurrency || {})
    .map(([currency, value]) => amount(value, Number(currency))).join(' + ') || '—';

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
    const [purchases, setPurchases] = useState(null);   // Buy games statistics, to set against the card sales
    useEffect(() => {
        fetch('/api/huginn/card-deals/buy').then((r) => (r.ok ? r.json() : null))
            .then((d) => setPurchases(d?.statistics || null)).catch(() => {});
    }, []);

    if (!selling) return <div className="text-xs text-slate-500 px-3 py-2">Loading card selling…</div>;

    const save = (changes) => {
        setBusy(true);
        setError(null);
        fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes) })
            .then((r) => r.json())
            .then((d) => { if (d.error) setError(d.error); })
            .catch((e) => setError(String(e)))
            .finally(() => { setBusy(false); onSaved(); });
    };
    // App ids or store links ("store.steampowered.com/app/745740/Reflex/"). Text that
    // gives no app id is refused: saving [] would mean every game.
    const saveApps = () => {
        const text = (appsText || '').trim();
        const apps = [...new Set([...text.matchAll(/(?:\/app\/)?(\d{2,9})/g)].map((m) => Number(m[1])))];
        if (text && !apps.length) {
            setError('No app id found: type app ids (like 745740) or paste store links. Leave it empty for every game.');
            return;
        }
        save({ card_auto_sell_apps: apps });
        setAppsText(null);
    };
    const stats = selling.statistics || { accounts: {}, games: {}, listed: 0, failed: 0 };

    return (
        <div className="rounded-xl border border-white/10 bg-black/20 p-3 text-xs space-y-3">
            {error && <p className="text-red-400">{error}</p>}
            <div className="flex flex-wrap items-start gap-x-6 gap-y-2">
                <label className="flex items-start gap-2 text-sm text-slate-200 cursor-pointer">
                    <input type="checkbox" className="mt-1 accent-amber-500" disabled={busy} checked={selling.enabled}
                        onChange={(e) => save({ card_auto_sell_enabled: e.target.checked })} />
                    <span>
                        Sell dropped cards automatically
                        <span className="block text-xs text-slate-500">
                            One cent under the lowest listing, never below the highest buy order (where they meet: at the buy order, sells at once), confirmed automatically
                            {selling.enabled_at ? ` · on since ${ago(selling.enabled_at)}` : ''}
                        </span>
                    </span>
                </label>
                <label className="flex items-start gap-2 text-slate-300 cursor-pointer">
                    <input type="checkbox" className="mt-0.5 accent-amber-500" disabled={busy} checked={selling.include_held}
                        onChange={(e) => save({ card_auto_sell_include_held: e.target.checked })} />
                    <span>Also cards held before it was switched on<span className="block text-slate-500">Off: only new drops are sold</span></span>
                </label>
                <label className="flex items-start gap-2 text-slate-300 cursor-pointer">
                    <input type="checkbox" className="mt-0.5 accent-amber-500" disabled={busy} checked={selling.foil}
                        onChange={(e) => save({ card_auto_sell_foil: e.target.checked })} />
                    <span>Foil cards too</span>
                </label>
                <div className="text-slate-300">
                    Only these games (app ids, empty = every game):
                    <div className="flex gap-1 mt-1">
                        <input type="text" value={appsText ?? (selling.apps || []).join(', ')} placeholder="every game (app ids or store links)"
                            onChange={(e) => setAppsText(e.target.value)}
                            className="w-56 bg-black/30 border border-white/10 rounded px-2 py-1" />
                        {appsText !== null && (
                            <button type="button" disabled={busy} onClick={saveApps}
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

            {purchases?.purchases > 0 && (
                <div>
                    <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Bought with “Buy games” vs. cards listed so far</p>
                    <table className="w-full">
                        <thead className="text-slate-500 text-left">
                            <tr><th>Game</th><th className="text-right">Copies bought</th><th className="text-right">Spent</th><th className="text-right">Cards listed</th><th className="text-right">You receive</th><th className="text-right">So far</th></tr>
                        </thead>
                        <tbody>
                            {Object.entries(purchases.games).map(([name, bought]) => {
                                const listed = (stats.apps || {})[String(bought.app_id)] || { listed: 0, receives: {} };
                                const spentUsd = bought.spent.USD;
                                const receivedUsd = listed.receives['1'] || 0;
                                const onlyDollars = Object.keys(bought.spent).every((c) => c === 'USD')
                                    && Object.keys(listed.receives).every((c) => c === '1');
                                const net = onlyDollars ? receivedUsd - (spentUsd || 0) : null;
                                return (
                                    <tr key={name} className="border-t border-white/5">
                                        <td className="py-0.5 text-slate-300">{name}</td>
                                        <td className="text-right tabular-nums">{bought.copies}</td>
                                        <td className="text-right tabular-nums text-amber-200">{Object.entries(bought.spent).map(([c, v]) => (c === 'USD' ? `$${(v / 100).toFixed(2)}` : `${(v / 100).toFixed(2)} ${c}`)).join(' + ')}</td>
                                        <td className="text-right tabular-nums">{listed.listed}</td>
                                        <td className="text-right tabular-nums text-emerald-300">{amounts(listed.receives)}</td>
                                        <td className={`text-right tabular-nums ${net == null ? 'text-slate-500' : net >= 0 ? 'text-emerald-300' : 'text-amber-300'}`}>
                                            {net == null ? 'mixed currencies' : `${net >= 0 ? '+' : '−'}$${(Math.abs(net) / 100).toFixed(2)}`}
                                        </td>
                                    </tr>
                                );
                            })}
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
                                        <td className="text-right tabular-nums">{amount(s.buyer_pays, s.currency)}</td>
                                        <td className="text-right tabular-nums text-emerald-300">{amount(s.receives, s.currency)}</td>
                                        <td className={`pl-3 ${s.ok ? 'text-emerald-300' : 'text-red-400'}`} title={s.how || ''}>{s.ok ? (s.how?.includes('buy order') ? 'Listed at the buy order' : 'Listed') : s.error}</td>
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
