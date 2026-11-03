import { useState, useEffect, useCallback } from 'react';
import { BellRing, Trash2, Plus } from 'lucide-react';
import { readJson } from '../../utils/readJson';

// Resell reminders: an item to resell when its trade lock ends. The backend
// sends one Telegram text (Gjallarhorn's chat, never a phone call) at that time.
const localOffset = () => {
    const minutes = -new Date().getTimezoneOffset();
    const sign = minutes >= 0 ? '+' : '-';
    const abs = Math.abs(minutes);
    return `${sign}${String(Math.floor(abs / 60)).padStart(2, '0')}:${String(abs % 60).padStart(2, '0')}`;
};

const shown = (iso) => new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });

const ResellReminders = () => {
    const [reminders, setReminders] = useState([]);
    const [name, setName] = useState('');
    const [when, setWhen] = useState('');
    const [note, setNote] = useState('');
    const [error, setError] = useState(null);

    const load = useCallback(() => fetch('/api/huginn/gjallarhorn/reminders')
        .then((r) => (r.ok ? readJson(r) : []))
        .then((data) => setReminders(Array.isArray(data) ? data : []))
        .catch(() => {}), []);

    useEffect(() => { load(); }, [load]);

    const add = async () => {
        setError(null);
        const response = await fetch('/api/huginn/gjallarhorn/reminders', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item_name: name, unlock_at: `${when}:00${localOffset()}`, note }),
        });
        if (!response.ok) {
            const data = await readJson(response).catch(() => null);
            setError(data?.error || `Could not add (HTTP ${response.status})`);
            return;
        }
        setName(''); setWhen(''); setNote('');
        load();
    };

    const remove = async (id) => {
        await fetch(`/api/huginn/gjallarhorn/reminders/${id}`, { method: 'DELETE' });
        load();
    };

    return (
        <div className="rounded-xl border border-white/10 bg-black/20 p-3 text-xs text-slate-300">
            <div className="flex items-center gap-2 mb-2 font-medium text-slate-200">
                <BellRing size={14} /> Resell reminders
                <span className="text-slate-500 font-normal">one Telegram text when the trade lock ends, no call</span>
            </div>
            {reminders.map((r) => (
                <div key={r.id} className="flex items-center gap-3 py-1 border-t border-white/5">
                    <span className="flex-1 text-slate-200">{r.item_name}</span>
                    <span>{shown(r.unlock_at)}</span>
                    <span className={r.sent_at ? 'text-emerald-400' : 'text-amber-300'}>{r.sent_at ? 'sent' : 'scheduled'}</span>
                    {r.note && <span className="text-slate-500">{r.note}</span>}
                    <button type="button" onClick={() => remove(r.id)} className="text-slate-500 hover:text-red-400" title="Remove">
                        <Trash2 size={13} />
                    </button>
                </div>
            ))}
            <div className="flex flex-wrap items-center gap-2 mt-2">
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Item name"
                    className="flex-1 min-w-[12rem] bg-black/30 border border-white/10 rounded px-2 py-1" />
                <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)}
                    className="bg-black/30 border border-white/10 rounded px-2 py-1" />
                <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)"
                    className="min-w-[10rem] bg-black/30 border border-white/10 rounded px-2 py-1" />
                <button type="button" onClick={add} disabled={!name.trim() || !when}
                    className="flex items-center gap-1 px-2 py-1 rounded bg-amber-500/20 border border-amber-500/40 text-amber-200 disabled:opacity-40">
                    <Plus size={13} /> Add
                </button>
            </div>
            {error && <div className="mt-1 text-red-400">{error}</div>}
        </div>
    );
};

export default ResellReminders;
