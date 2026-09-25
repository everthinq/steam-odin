import { useEffect, useState } from 'react';
import { Save, Send, X } from 'lucide-react';

// Andvari settings: which scopes to scan, the price/discount filters, the auto
// scan schedule and the Telegram alert thresholds. Backed by
// GET/POST /api/huginn/card-deals/config (validated server-side, saved to
// settings.json). Filters apply to the cached scan immediately; a lower maximum
// price than the last scan used triggers a fresh store scan next time.
const Field = ({ label, hint, children }) => (
    <label className="flex flex-col gap-1">
        <span className="text-[11px] uppercase tracking-wider text-slate-500">{label}</span>
        {children}
        {hint && <span className="text-[11px] text-slate-600 leading-snug">{hint}</span>}
    </label>
);

const Checkbox = ({ checked, onChange, label, hint }) => (
    <label className="flex items-start gap-2 cursor-pointer">
        <input type="checkbox" checked={!!checked} onChange={(e) => onChange(e.target.checked)} className="mt-0.5 accent-amber-500" />
        <span>
            <span className="text-sm text-slate-200">{label}</span>
            {hint && <span className="block text-[11px] text-slate-500 leading-snug">{hint}</span>}
        </span>
    </label>
);

const inputClass = 'bg-black/30 border border-white/10 rounded-lg px-2.5 py-1.5 text-sm text-slate-200 outline-none focus:border-amber-500/40 w-full';

const SettingsPanel = ({ onClose, onSaved }) => {
    const [draft, setDraft] = useState(null);
    const [message, setMessage] = useState(null);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        fetch('/api/huginn/card-deals/config')
            .then((r) => (r.ok ? r.json() : null))
            .then((d) => { if (d) setDraft(d); })
            .catch(() => setMessage('Could not load settings.'));
    }, []);

    const set = (key) => (value) => setDraft((d) => ({ ...d, [key]: value }));

    const save = () => {
        setSaving(true);
        setMessage(null);
        fetch('/api/huginn/card-deals/config', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft),
        })
            .then((r) => r.json().then((d) => ({ ok: r.ok, d })))
            .then(({ ok, d }) => {
                if (!ok) { setMessage(d.error || 'Save failed.'); return; }
                setDraft(d);
                setMessage('Saved.');
                onSaved?.();
            })
            .catch(() => setMessage('Save failed: could not reach backend.'))
            .finally(() => setSaving(false));
    };

    const testAlert = () => {
        setMessage('Sending test message…');
        fetch('/api/huginn/card-deals/alerts/test', { method: 'POST' })
            .then((r) => r.json())
            .then((d) => setMessage(d.ok ? 'Test message sent — check Telegram.' : `Test failed: ${d.error || 'error'}`))
            .catch(() => setMessage('Test failed: could not reach backend.'));
    };

    return (
        <div className="rounded-xl border border-white/10 bg-odin-blue/40 p-4">
            <div className="flex items-center justify-between mb-3">
                <p className="text-sm font-semibold text-amber-100 font-serif">Andvari settings</p>
                <button type="button" onClick={onClose} className="text-slate-500 hover:text-white"><X size={16} /></button>
            </div>
            {!draft ? (
                <p className="text-sm text-slate-500">{message || 'Loading…'}</p>
            ) : (
                <div className="grid gap-5 md:grid-cols-3">
                    <div className="flex flex-col gap-3">
                        <p className="text-[10px] font-bold tracking-widest text-slate-500 uppercase">Scan</p>
                        <Checkbox
                            checked={draft.card_deals_include_full_price}
                            onChange={set('card_deals_include_full_price')}
                            label="Include full-price games"
                            hint="Also scan every non-discounted game with cards. Runs AFTER the on-sale scan, refreshes once a day, and gets a smaller share of the rate-limited Market check."
                        />
                        <Checkbox
                            checked={draft.card_deals_auto_scan_enabled}
                            onChange={set('card_deals_auto_scan_enabled')}
                            label="Scan automatically"
                        />
                        <Field label="Every (hours)">
                            <input type="number" min="1" max="168" className={inputClass}
                                value={draft.card_deals_scan_interval_hours ?? ''}
                                onChange={(e) => set('card_deals_scan_interval_hours')(e.target.value)} />
                        </Field>
                    </div>
                    <div className="flex flex-col gap-3">
                        <p className="text-[10px] font-bold tracking-widest text-slate-500 uppercase">Filters</p>
                        <Field label="Maximum game price ($)" hint="Games above this are not scanned. Steam's store only filters in steps ($5, 10, 15, 20, 25, 30, 40, 50, 60); the exact cut is applied afterwards.">
                            <input type="number" min="0.1" step="0.5" className={inputClass}
                                value={draft.card_deals_max_price ?? ''}
                                onChange={(e) => set('card_deals_max_price')(e.target.value)} />
                        </Field>
                        <Field label="Card value" hint="Both: every game shows the sell-now value (buy orders) and the list value (sell price); it is a deal if either is profitable, sell-now deals first. Instant: buy orders only — for the all-accounts total, the order book is walked for every account's copies together. Listing: one cent under the lowest ask only (odd one-off asks capped) — more money, but you wait for buyers.">
                            <select className={inputClass}
                                value={draft.card_deals_valuation || 'both'}
                                onChange={(e) => set('card_deals_valuation')(e.target.value)}>
                                <option value="both">Both — sell now and list (default)</option>
                                <option value="instant">Instant — sell to buy orders only</option>
                                <option value="listing">Listing — undercut the lowest ask only</option>
                            </select>
                        </Field>
                        <Field label="Minimum discount (%)" hint="On-sale games only. 0 = any discount; the profit math decides.">
                            <input type="number" min="0" max="100" className={inputClass}
                                value={draft.card_deals_min_discount ?? ''}
                                onChange={(e) => set('card_deals_min_discount')(e.target.value)} />
                        </Field>
                        <Field label="Fallback store country" hint="Priced when no account's store country is known yet (two letters, e.g. TR).">
                            <input type="text" maxLength={2} className={inputClass}
                                value={draft.card_deals_fallback_country ?? ''}
                                onChange={(e) => set('card_deals_fallback_country')(e.target.value.toUpperCase())} />
                        </Field>
                    </div>
                    <div className="flex flex-col gap-3">
                        <p className="text-[10px] font-bold tracking-widest text-slate-500 uppercase">Telegram alerts</p>
                        <Checkbox
                            checked={draft.card_deals_alerts_enabled}
                            onChange={set('card_deals_alerts_enabled')}
                            label="Alert on new deals"
                            hint="One message per scope after each scan, listing only deals not sent before at that price."
                        />
                        <div className="grid grid-cols-2 gap-2">
                            <Field label="Minimum return (%)">
                                <input type="number" min="0" className={inputClass}
                                    value={draft.card_deals_alert_min_return_percent ?? ''}
                                    onChange={(e) => set('card_deals_alert_min_return_percent')(e.target.value)} />
                            </Field>
                            <Field label="Minimum profit ($)">
                                <input type="number" min="0" step="0.05" className={inputClass}
                                    value={draft.card_deals_alert_min_profit ?? ''}
                                    onChange={(e) => set('card_deals_alert_min_profit')(e.target.value)} />
                            </Field>
                        </div>
                        <Field label="Chat id (optional)" hint="Send card deals to their own chat. Empty = the shared Telegram chat.">
                            <input type="text" className={inputClass}
                                value={draft.card_deals_chat_id ?? ''}
                                onChange={(e) => set('card_deals_chat_id')(e.target.value)} />
                        </Field>
                    </div>
                </div>
            )}
            {draft && (
                <div className="flex items-center gap-2 mt-4">
                    <button type="button" onClick={save} disabled={saving}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-sm font-medium disabled:opacity-50">
                        <Save size={13} /> Save
                    </button>
                    <button type="button" onClick={testAlert}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm text-slate-300 border border-white/10 hover:bg-white/5">
                        <Send size={13} /> Test alert
                    </button>
                    {message && <span className="text-xs text-slate-400">{message}</span>}
                </div>
            )}
        </div>
    );
};

export default SettingsPanel;
