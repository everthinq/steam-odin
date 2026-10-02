import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { LayoutDashboard, Swords, Play, Square, RefreshCw, Newspaper, Coins, Save, AlertTriangle, ExternalLink } from 'lucide-react';
import InfoTip from '../../components/gjallarhorn/InfoTip';

// Team Fortress 2 case drops. A newly added case drops to anyone playing and is
// worth the most in its first hours, so: watch the news, play on every account
// the minute a case is added, and list the drops on the Market as they land.
// Backend: /api/huginn/team-fortress* (team_fortress_service.py + asf_service.py).

const HOW_IT_WORKS = 'Release watcher: Team Fortress 2\'s official news feed is read every few minutes. '
    + 'An update that says "Added the … Case" starts Team Fortress 2 mode on the chosen accounts, puts the case '
    + 'on the sell list, then texts Telegram and rings the phone. Team Fortress 2 mode: ASF plays the game on '
    + 'those accounts (outside the 10-bot card-farming limit; card farming waits). Item drops are capped per week, '
    + 'so leave it off until a case is added; it stops by itself after the hours set below. Auto-sell: while the '
    + 'mode is on (and two hours after), every account\'s Team Fortress 2 inventory is read in turn and each item '
    + 'on the sell list that dropped since the mode started (never what the account held before) is listed one cent '
    + 'under the lowest Market listing, in that account\'s wallet currency, and confirmed automatically. '
    + 'Every account gets the free Team Fortress 2 license automatically, new accounts included.';

// Steam wallet currency ids (ECurrencyCode) → how to show an amount.
const CURRENCIES = {
    1: ['$', ''], 2: ['£', ''], 3: ['', ' €'], 5: ['', ' ₽'], 9: ['', ' kr'], 17: ['', ' TL'],
    18: ['', ' ₴'], 23: ['¥ ', ''], 29: ['HK$ ', ''], 37: ['', ' ₸'],
};
const price = (minorUnits, currency) => {
    if (minorUnits === null || minorUnits === undefined) return '—';
    const [before, after] = CURRENCIES[currency] || ['', ` (currency ${currency})`];
    return `${before}${(minorUnits / 100).toFixed(2)}${after}`;
};

const ago = (epochSeconds) => {
    if (!epochSeconds) return 'never';
    const minutes = Math.round((Date.now() / 1000 - epochSeconds) / 60);
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 48) return `${hours} h ago`;
    return `${Math.round(hours / 24)} days ago`;
};

const Card = ({ title, children, tip }) => (
    <div className="rounded-xl border border-white/10 bg-odin-blue/40 p-4">
        <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-2 flex items-center gap-1">
            {tip ? <InfoTip tip={tip}><span className="cursor-help">{title}</span></InfoTip> : title}
        </p>
        {children}
    </div>
);

const Toggle = ({ label, checked, onChange, hint }) => (
    <label className="flex items-start gap-2 text-sm text-slate-300 cursor-pointer">
        <input type="checkbox" className="mt-1 accent-amber-500" checked={!!checked} onChange={(e) => onChange(e.target.checked)} />
        <span>{label}{hint && <span className="block text-xs text-slate-500">{hint}</span>}</span>
    </label>
);

const TeamFortress = () => {
    const [status, setStatus] = useState(null);
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);
    const [draft, setDraft] = useState(null);       // settings being edited
    const [saved, setSaved] = useState(null);

    const load = useCallback(() => fetch('/api/huginn/team-fortress')
        .then((r) => r.json())
        .then((d) => {
            setStatus(d);
            setError(null);
            setDraft((current) => current || {
                ...d.settings,
                sell_items_text: (d.settings.team_fortress_sell_items || []).join('\n'),
            });
        })
        .catch((e) => setError(String(e))), []);

    useEffect(() => {
        load();
        const timer = setInterval(load, 10000);
        return () => clearInterval(timer);
    }, [load]);

    const post = (path, body) => {
        setBusy(true);
        return fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
            .then((r) => r.json())
            .then((d) => { if (d.error) setError(d.error); return d; })
            .catch((e) => setError(String(e)))
            .finally(() => { setBusy(false); load(); });
    };

    const saveSettings = () => {
        const items = draft.sell_items_text.split('\n').map((line) => line.trim()).filter(Boolean);
        const body = {
            team_fortress_watch_enabled: draft.team_fortress_watch_enabled,
            team_fortress_play_on_release: draft.team_fortress_play_on_release,
            team_fortress_ring_on_release: draft.team_fortress_ring_on_release,
            team_fortress_auto_sell_enabled: draft.team_fortress_auto_sell_enabled,
            team_fortress_poll_minutes: Math.max(1, Number(draft.team_fortress_poll_minutes) || 2),
            team_fortress_auto_stop_hours: Math.max(0, Number(draft.team_fortress_auto_stop_hours) || 0),
            team_fortress_accounts: draft.team_fortress_accounts || [],
            team_fortress_sell_items: items,
            team_fortress_chat_id: (draft.team_fortress_chat_id || '').trim(),
        };
        setBusy(true);
        fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
            .then((r) => r.json())
            .then((d) => {
                if (d.error) { setError(d.error); return; }
                setSaved(Date.now());
                setDraft(null);
            })
            .catch((e) => setError(String(e)))
            .finally(() => { setBusy(false); load(); });
    };

    const setField = (key, value) => setDraft((current) => ({ ...current, [key]: value }));
    const chosen = new Set(draft?.team_fortress_accounts || []);
    const toggleAccount = (steamid) => {
        const next = new Set(chosen);
        if (next.has(steamid)) next.delete(steamid); else next.add(steamid);
        setField('team_fortress_accounts', [...next]);
    };

    const play = status?.play;
    const watch = status?.watch;
    const sell = status?.sell;
    const inventories = sell?.inventories || {};

    return (
        <div className="h-screen bg-odin-dark flex flex-col overflow-hidden">
            <div className="shrink-0 border-b border-white/5 bg-odin-blue/50 px-6 py-4 flex items-center gap-3 flex-wrap">
                <Link to="/" className="flex items-center gap-1.5 text-sm text-slate-400 hover:text-white transition-colors shrink-0">
                    <LayoutDashboard size={15} /> Dashboard
                </Link>
                <span className="text-white/20">/</span>
                <Link to="/huginn" className="text-sm text-slate-400 hover:text-white transition-colors">Huginn</Link>
                <span className="text-white/20">/</span>
                <h1 className="text-lg font-bold font-serif text-amber-100 flex items-center gap-2">
                    <Swords size={17} className="text-amber-500" /> Team Fortress 2
                    <span className="text-sm font-normal text-slate-400 font-sans">New case drops</span>
                </h1>
                <InfoTip tip={HOW_IT_WORKS}>
                    <span className="text-xs text-slate-500 border border-white/10 rounded-lg px-2 py-1 cursor-help">How it works</span>
                </InfoTip>
                <div className="ml-auto flex items-center gap-2 flex-wrap">
                    {play?.active ? (
                        <button type="button" disabled={busy} onClick={() => post('/api/huginn/team-fortress/play', { action: 'stop' })}
                            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-red-500/40 text-red-200 hover:bg-red-500/10 text-sm font-medium disabled:opacity-50">
                            <Square size={14} /> Stop playing
                        </button>
                    ) : (
                        <InfoTip tip="Start Team Fortress 2 mode now on the chosen accounts (all of them when none is chosen). Drops are capped per week: use it when a case has just been added.">
                            <button type="button" disabled={busy || !play} onClick={() => post('/api/huginn/team-fortress/play', { action: 'start' })}
                                className="flex items-center gap-2 px-3 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-sm font-medium disabled:opacity-50">
                                <Play size={14} /> Play Team Fortress 2 now
                            </button>
                        </InfoTip>
                    )}
                    <InfoTip tip="Read Team Fortress 2's news feed now. A newly added case found here acts exactly like one the background check finds.">
                        <button type="button" disabled={busy} onClick={() => post('/api/huginn/team-fortress/check-news')}
                            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-white/15 text-slate-200 hover:bg-white/5 text-sm disabled:opacity-50">
                            <Newspaper size={14} /> Check news
                        </button>
                    </InfoTip>
                    <InfoTip tip="Read every watched inventory now and list what is on the sell list (runs in the background; the listings appear below).">
                        <button type="button" disabled={busy} onClick={() => post('/api/huginn/team-fortress/sell-now')}
                            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-emerald-500/40 text-emerald-200 hover:bg-emerald-500/10 text-sm disabled:opacity-50">
                            <Coins size={14} /> Sell now
                        </button>
                    </InfoTip>
                </div>
            </div>

            <div className="flex-1 overflow-auto custom-scrollbar px-6 py-5 space-y-5">
                {error && (
                    <p className="text-sm text-red-400 flex items-center gap-2"><AlertTriangle size={14} /> {error}</p>
                )}
                {!status && !error && <p className="text-sm text-slate-500 animate-pulse">Loading…</p>}

                {status && (
                    <div className="grid gap-4 md:grid-cols-3">
                        <Card title="Team Fortress 2 mode">
                            {!play ? <p className="text-sm text-slate-400">ASF is not set up.</p> : (
                                <>
                                    <p className={`text-lg font-bold ${play.active ? 'text-emerald-300' : 'text-slate-400'}`}>
                                        {play.active ? `Playing on ${play.playing} accounts` : 'Off'}
                                    </p>
                                    <p className="text-xs text-slate-500">
                                        {play.active
                                            ? `Since ${ago(play.since)} · ${play.reason === 'manual' ? 'started by hand' : play.reason}`
                                            : play.stopped_at ? `Stopped ${ago(play.stopped_at)}` : 'Starts by itself when a case is added'}
                                    </p>
                                    <p className="text-xs text-slate-500 mt-1">{play.licensed} of {play.accounts.length} accounts own Team Fortress 2</p>
                                </>
                            )}
                        </Card>
                        <Card title="Release watcher">
                            <p className={`text-lg font-bold ${watch.enabled ? 'text-sky-300' : 'text-slate-400'}`}>
                                {watch.enabled ? `Every ${watch.poll_minutes} min` : 'Off'}
                            </p>
                            <p className="text-xs text-slate-500">Last check {ago(watch.checked_at)}{watch.can_ring ? ' · rings the phone' : ' · phone ringing not set up'}</p>
                            {watch.error && <p className="text-xs text-red-400 mt-1">{watch.error}</p>}
                            {watch.cases.length > 0 && (
                                <ul className="mt-2 space-y-1 text-xs">
                                    {watch.cases.slice(0, 5).map((c) => (
                                        <li key={`${c.name}-${c.detected_at}`} className="text-amber-200">
                                            {c.name} <span className="text-slate-500">· {ago(c.detected_at)}</span>
                                            {c.url && <a href={c.url} target="_blank" rel="noreferrer" className="ml-1 text-slate-400 hover:text-white"><ExternalLink size={10} className="inline" /></a>}
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </Card>
                        <Card title="Auto-sell">
                            <p className={`text-lg font-bold ${sell.enabled ? 'text-emerald-300' : 'text-slate-400'}`}>
                                {sell.enabled ? `${sell.items.length} item${sell.items.length === 1 ? '' : 's'} on the sell list` : 'Off'}
                            </p>
                            <p className="text-xs text-slate-500 truncate" title={sell.items.join(', ')}>{sell.items.join(', ') || 'A new case is added by the release watcher'}</p>
                            <p className="text-xs text-slate-500 mt-1">
                                {sell.sales.filter((s) => s.ok).length} listed recently · only drops since the mode started are sold
                            </p>
                            {sell.rate_limited_until && (
                                <p className="text-xs text-amber-300 mt-1">Steam rate-limited a read: paused until {new Date(sell.rate_limited_until * 1000).toLocaleTimeString()}</p>
                            )}
                        </Card>
                    </div>
                )}

                {play && (
                    <div className="rounded-xl border border-white/10 bg-black/20 p-3 text-xs">
                        <p className="text-slate-500 mb-2">
                            Accounts. Tick the ones that should play; none ticked means every account. Save below.
                        </p>
                        <div className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3 max-h-72 overflow-auto custom-scrollbar">
                            {play.accounts.map((a) => {
                                const inventory = inventories[a.steamid];
                                return (
                                    <label key={a.steamid} className="px-2 py-1.5 rounded bg-white/5 flex items-start gap-2 cursor-pointer">
                                        <input type="checkbox" className="mt-0.5 accent-amber-500" disabled={!draft}
                                            checked={chosen.has(a.steamid)} onChange={() => toggleAccount(a.steamid)} />
                                        <span className="min-w-0">
                                            <span className="text-slate-300 block truncate">{a.account_name}</span>
                                            <span className={a.playing ? 'text-emerald-300' : a.in_mode ? 'text-sky-300' : 'text-slate-500'}>
                                                {a.playing ? 'Playing' : a.in_mode ? (a.connected ? 'Starting to play' : 'Logging in') : 'Not playing'}
                                            </span>
                                            {!a.licensed && (
                                                <span className="block text-amber-300/80">
                                                    {a.license_checking ? 'Adding Team Fortress 2…' : a.license_error ? `License: ${a.license_error}` : 'License not checked yet'}
                                                </span>
                                            )}
                                            {inventory && (
                                                <span className={`block ${inventory.error ? 'text-red-400/90' : 'text-slate-500'}`} title={inventory.error || ''}>
                                                    Inventory {ago(inventory.at)}{inventory.error ? `: ${inventory.error}` : inventory.listed ? ` · listed ${inventory.listed}` : ''}
                                                </span>
                                            )}
                                            {inventory?.not_tradable > 0 && (
                                                <span className="block text-amber-300/90" title="Team Fortress 2 marks a free-to-play account's drops Not Tradable or Marketable. Only Premium accounts (any Mann Co. Store purchase) can sell them.">
                                                    {inventory.not_tradable} not tradable (free-to-play account?)
                                                </span>
                                            )}
                                        </span>
                                    </label>
                                );
                            })}
                        </div>
                    </div>
                )}

                {draft && (
                    <div className="rounded-xl border border-white/10 bg-odin-blue/30 p-4 grid gap-4 md:grid-cols-2">
                        <div className="space-y-3">
                            <Toggle label="Watch Team Fortress 2 news for new cases" checked={draft.team_fortress_watch_enabled}
                                onChange={(v) => setField('team_fortress_watch_enabled', v)} />
                            <Toggle label="Start playing on the chosen accounts when a case is added" checked={draft.team_fortress_play_on_release}
                                onChange={(v) => setField('team_fortress_play_on_release', v)} />
                            <Toggle label="Ring the phone when a case is added" checked={draft.team_fortress_ring_on_release}
                                onChange={(v) => setField('team_fortress_ring_on_release', v)} hint="Uses the Gjallarhorn caller (telegram_caller.json)." />
                            <Toggle label="Auto-sell the sell list as soon as it drops" checked={draft.team_fortress_auto_sell_enabled}
                                onChange={(v) => setField('team_fortress_auto_sell_enabled', v)}
                                hint="Listed one cent under the lowest Market listing and confirmed automatically." />
                            <label className="block text-sm text-slate-300">
                                Check the news every
                                <input type="number" min="1" value={draft.team_fortress_poll_minutes ?? 2}
                                    onChange={(e) => setField('team_fortress_poll_minutes', e.target.value)}
                                    className="mx-2 w-16 bg-black/30 border border-white/10 rounded px-2 py-1 text-sm" />
                                minutes
                            </label>
                            <label className="block text-sm text-slate-300">
                                Stop playing by itself after
                                <input type="number" min="0" value={draft.team_fortress_auto_stop_hours ?? 24}
                                    onChange={(e) => setField('team_fortress_auto_stop_hours', e.target.value)}
                                    className="mx-2 w-16 bg-black/30 border border-white/10 rounded px-2 py-1 text-sm" />
                                hours <span className="text-xs text-slate-500">(0 = never; card farming resumes)</span>
                            </label>
                            <label className="block text-sm text-slate-300">
                                Telegram chat for these alerts
                                <input type="text" value={draft.team_fortress_chat_id || ''} placeholder="empty = the shared chat"
                                    onChange={(e) => setField('team_fortress_chat_id', e.target.value)}
                                    className="mt-1 w-full bg-black/30 border border-white/10 rounded px-2 py-1 text-sm" />
                            </label>
                        </div>
                        <div className="space-y-2">
                            <label className="block text-sm text-slate-300">
                                Sell list (one Market name per line)
                                <textarea rows={6} value={draft.sell_items_text}
                                    onChange={(e) => setField('sell_items_text', e.target.value)}
                                    className="mt-1 w-full bg-black/30 border border-white/10 rounded px-2 py-1 text-sm font-mono" />
                            </label>
                            <div className="flex items-center gap-3">
                                <button type="button" disabled={busy} onClick={saveSettings}
                                    className="flex items-center gap-2 px-3 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-sm font-medium disabled:opacity-50">
                                    <Save size={14} /> Save settings
                                </button>
                                {saved && <span className="text-xs text-emerald-300">Saved {ago(saved / 1000)}</span>}
                            </div>
                        </div>
                    </div>
                )}

                {sell && sell.sales.length > 0 && (
                    <div className="rounded-xl border border-white/10 bg-black/20 p-3">
                        <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-2">Recent listings</p>
                        <table className="w-full text-xs">
                            <thead className="text-slate-500 text-left">
                                <tr><th className="py-1">When</th><th>Account</th><th>Item</th><th className="text-right">Buyer pays</th><th className="text-right">You receive</th><th className="pl-3">Result</th></tr>
                            </thead>
                            <tbody>
                                {sell.sales.map((s) => (
                                    <tr key={`${s.assetid}-${s.at}`} className="border-t border-white/5">
                                        <td className="py-1 text-slate-400">{ago(s.at)}</td>
                                        <td className="text-slate-300">{s.account_name || s.steamid}</td>
                                        <td className="text-slate-200">{s.name}</td>
                                        <td className="text-right tabular-nums">{price(s.buyer_pays, s.currency)}</td>
                                        <td className="text-right tabular-nums text-emerald-300">{price(s.receives, s.currency)}</td>
                                        <td className={`pl-3 ${s.ok ? 'text-emerald-300' : 'text-red-400'}`}>{s.ok ? 'Listed' : s.error}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
};

export default TeamFortress;
