import React, { useState, useEffect } from 'react';
import { Bell, Send, RefreshCw, Check, AlertTriangle, Save } from 'lucide-react';
import { readJson } from '../utils/readJson';

// Config panel for Case Arbitrage price alerts (a buy market cheaper than CSFloat).
// Reads GET /api/huginn/cases/alerts and saves through POST /api/huginn/cases/alerts/settings,
// which checks every value. The Telegram bot token and the webhook are secrets: the backend
// only says whether each is set; a blank field keeps the saved one, "remove" deletes it.
// "remove" next to a saved secret: the next Save deletes it.
const SecretRemove = ({ removing, onToggle }) => (
    <button type="button" onClick={onToggle}
        className={`ml-2 text-[10px] ${removing ? 'text-red-400' : 'text-slate-500 hover:text-red-400'}`}>
        {removing ? 'will be removed — undo' : 'remove'}
    </button>
);

const CaseAlertsPanel = () => {
    const [enabled, setEnabled] = useState(false);
    const [tgToken, setTgToken] = useState('');
    const [removeToken, setRemoveToken] = useState(false);
    const [tgChat, setTgChat] = useState('');
    const [webhook, setWebhook] = useState('');
    const [removeWebhook, setRemoveWebhook] = useState(false);
    const [minPct, setMinPct] = useState('');
    const [pollMin, setPollMin] = useState('');
    const [status, setStatus] = useState(null);
    const [saving, setSaving] = useState(false);
    const [busy, setBusy] = useState('');   // 'test' | 'check' | ''
    const [msg, setMsg] = useState(null);    // {ok, text}

    // Fill the form from a status answer (on load and after a save).
    const showStatus = (s) => {
        setStatus(s);
        setEnabled(!!s.enabled);
        setTgChat(s.telegram_chat_id || '');
        setMinPct(String(s.min_pct ?? ''));
        setPollMin(s.poll_interval_sec ? String(s.poll_interval_sec / 60) : '');
        setTgToken('');
        setWebhook('');
        setRemoveToken(false);
        setRemoveWebhook(false);
    };

    const loadStatus = async () => {
        try {
            const r = await fetch('/api/huginn/cases/alerts');
            if (r.ok) setStatus(await readJson(r));
        } catch { /* the next load tries again */ }
    };

    useEffect(() => {
        (async () => {
            try {
                const r = await fetch('/api/huginn/cases/alerts');
                if (!r.ok) throw new Error('the alert settings could not be read');
                showStatus(await readJson(r));
            } catch (e) {
                setMsg({ ok: false, text: e.message });
            }
        })();
    }, []);

    const flash = (ok, text) => { setMsg({ ok, text }); setTimeout(() => setMsg(null), 6000); };

    const save = async () => {
        setSaving(true);
        try {
            const body = {
                enabled,
                telegram_chat_id: tgChat,
                min_pct: minPct,
                poll_interval_sec: Math.round((parseFloat(pollMin) || 0) * 60),
                telegram_bot_token: removeToken ? null : tgToken,
                notify_webhook_url: removeWebhook ? null : webhook,
            };
            const r = await fetch('/api/huginn/cases/alerts/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const d = await readJson(r).catch(() => ({}));
            if (!r.ok) throw new Error(d.error || 'save failed');
            showStatus(d);
            flash(true, 'Saved.');
        } catch (e) {
            flash(false, e.message);
        } finally {
            setSaving(false);
        }
    };

    const sendTest = async () => {
        setBusy('test');
        try {
            const r = await fetch('/api/huginn/cases/alerts/test', { method: 'POST' });
            const d = await readJson(r);
            flash(!!d.ok, d.ok ? `Test sent via ${d.channel}.` : `Test failed: ${d.error || 'unknown'}`);
        } catch (e) {
            flash(false, e.message);
        } finally { setBusy(''); loadStatus(); }
    };

    const checkNow = async (force) => {
        setBusy('check');
        try {
            const r = await fetch(`/api/huginn/cases/alerts/check${force ? '?force=1' : ''}`, { method: 'POST' });
            const d = await readJson(r);
            if (!d.ran) flash(false, `Not run: ${d.reason}`);
            else flash(!!(d.sent || d.new === 0), `Checked: ${d.active} active, ${d.new} new${d.sent ? ` · sent via ${d.channel}` : d.new ? ' · send failed' : ''}`);
        } catch (e) {
            flash(false, e.message);
        } finally { setBusy(''); loadStatus(); }
    };

    const channelLabel = status?.channel
        ? (status.channel === 'telegram' ? 'Telegram' : 'Webhook')
        : 'none configured';

    return (
        <div className="shrink-0 border-b border-white/5 bg-black/20 px-4 py-3">
            <div className="flex items-center gap-2 flex-wrap">
                <Bell size={14} className="text-amber-300 shrink-0" />
                <span className="text-xs font-bold tracking-wider text-slate-300 uppercase">Price alerts</span>
                <span className="text-[11px] text-slate-500">notify when {status?.buy_markets?.join(', ') || 'a buy market'} is cheaper than CSFloat</span>
                <label className="ml-auto flex items-center gap-2 cursor-pointer">
                    <span className="text-xs text-slate-400">enabled</span>
                    <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)}
                        className="accent-amber-500 w-4 h-4" />
                </label>
            </div>

            <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-2">
                <label className="flex flex-col gap-1">
                    <span className="text-[11px] text-slate-500">Telegram bot token
                        {status?.telegram_bot_token_set && <SecretRemove removing={removeToken} onToggle={() => setRemoveToken((v) => !v)} />}
                    </span>
                    <input type="password" value={tgToken} onChange={(e) => setTgToken(e.target.value)} disabled={removeToken}
                        placeholder={status?.telegram_bot_token_set ? 'saved — leave blank to keep it' : '123456:ABC-…  (from @BotFather)'}
                        className="bg-black/30 border border-white/10 rounded-lg px-2.5 py-1.5 text-sm text-white focus:outline-none focus:border-amber-500/40 placeholder:text-slate-600" />
                </label>
                <label className="flex flex-col gap-1">
                    <span className="text-[11px] text-slate-500">Telegram chat ID</span>
                    <input type="text" value={tgChat} onChange={(e) => setTgChat(e.target.value)}
                        placeholder="your chat id (message @userinfobot to get it)"
                        className="bg-black/30 border border-white/10 rounded-lg px-2.5 py-1.5 text-sm text-white focus:outline-none focus:border-amber-500/40 placeholder:text-slate-600" />
                </label>
                <label className="flex flex-col gap-1 md:col-span-2">
                    <span className="text-[11px] text-slate-500">…or a Discord/Slack webhook URL (used only if no Telegram)
                        {status?.webhook_set && <SecretRemove removing={removeWebhook} onToggle={() => setRemoveWebhook((v) => !v)} />}
                    </span>
                    <input type="password" value={webhook} onChange={(e) => setWebhook(e.target.value)} disabled={removeWebhook}
                        placeholder={status?.webhook_set ? 'saved — leave blank to keep it' : 'https://discord.com/api/webhooks/…  or  https://hooks.slack.com/…'}
                        className="bg-black/30 border border-white/10 rounded-lg px-2.5 py-1.5 text-sm text-white focus:outline-none focus:border-amber-500/40 placeholder:text-slate-600" />
                </label>
            </div>

            <div className="mt-3 flex items-center gap-2 flex-wrap">
                <span className="text-[11px] text-slate-500">alert when cheaper by ≥</span>
                <input type="number" step="0.5" min="0" value={minPct} onChange={(e) => setMinPct(e.target.value)}
                    className="w-16 bg-black/30 border border-white/10 rounded-lg px-2 py-1.5 text-sm text-white tabular-nums focus:outline-none focus:border-amber-500/40" />
                <span className="text-[11px] text-slate-500">% (0 = any, even $0.01)</span>
                <span className="text-[11px] text-slate-500 ml-3">check every</span>
                <input type="number" step="1" min={status ? status.poll_interval_minimum_sec / 60 : undefined} value={pollMin} onChange={(e) => setPollMin(e.target.value)}
                    className="w-16 bg-black/30 border border-white/10 rounded-lg px-2 py-1.5 text-sm text-white tabular-nums focus:outline-none focus:border-amber-500/40" />
                <span className="text-[11px] text-slate-500">min</span>

                <button type="button" onClick={save} disabled={saving}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-xs font-medium disabled:opacity-50 transition-colors">
                    <Save size={12} /> {saving ? 'Saving…' : 'Save'}
                </button>
                <button type="button" onClick={sendTest} disabled={busy === 'test'}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-black/30 border border-white/10 text-slate-300 hover:text-white hover:border-white/20 text-xs font-medium disabled:opacity-50 transition-colors">
                    <Send size={12} className={busy === 'test' ? 'animate-pulse' : ''} /> Send test
                </button>
                <button type="button" onClick={() => checkNow(false)} disabled={busy === 'check'}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-black/30 border border-white/10 text-slate-300 hover:text-white hover:border-white/20 text-xs font-medium disabled:opacity-50 transition-colors">
                    <RefreshCw size={12} className={busy === 'check' ? 'animate-spin' : ''} /> Check now
                </button>

                <span className="ml-auto text-[11px] text-slate-500">
                    channel: <span className={status?.channel ? 'text-emerald-400' : 'text-slate-500'}>{channelLabel}</span>
                    {status?.active?.length ? <span className="text-amber-300"> · {status.active.length} active deal{status.active.length !== 1 ? 's' : ''}</span> : null}
                </span>
            </div>

            {msg && (
                <div className={`mt-2 flex items-center gap-1.5 text-xs ${msg.ok ? 'text-emerald-400' : 'text-red-400'}`}>
                    {msg.ok ? <Check size={12} /> : <AlertTriangle size={12} />} {msg.text}
                </div>
            )}
        </div>
    );
};

export default CaseAlertsPanel;
