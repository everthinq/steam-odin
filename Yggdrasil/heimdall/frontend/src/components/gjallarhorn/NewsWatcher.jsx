import { useState, useEffect, useCallback } from 'react';
import { Newspaper, RefreshCw, Bell, BellOff, PhoneCall } from 'lucide-react';
import InfoTip from './InfoTip';

// Bullet 4: the CS2 news watcher. It polls the official Counter-Strike 2 update
// feed and RINGS + texts the moment Valve adds or removes a case / collection /
// capsule / souvenir (a supply-shock limiting event). This panel lets Ivan arm
// it, poll on demand, test detection against pasted post text, and see the last
// events it caught. Backend: /api/huginn/gjallarhorn/news/{status,check,test}.
const NewsWatcher = () => {
    const [status, setStatus] = useState(null);
    const [checking, setChecking] = useState(false);
    const [saving, setSaving] = useState(false);
    const [chatId, setChatId] = useState(null);   // null until first load seeds it
    const [savingChat, setSavingChat] = useState(false);
    const [testText, setTestText] = useState('');
    const [testHits, setTestHits] = useState(null);
    const [testing, setTesting] = useState(false);

    const load = useCallback(() => (
        fetch('/api/huginn/gjallarhorn/news/status')
            .then((r) => (r.ok ? r.json() : null))
            .then((d) => {
                setStatus(d);
                // Seed the chat-id field once; don't clobber an in-progress edit.
                if (d) setChatId((prev) => (prev === null ? (d.chat_id || '') : prev));
            })
            .catch(() => setStatus(null))
    ), []);

    useEffect(() => { load(); }, [load]);

    const toggleArmed = () => {
        if (!status) return;
        setSaving(true);
        fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ gjallarhorn_news_armed: !status.armed }),
        })
            .then(() => load())
            .finally(() => setSaving(false));
    };

    const saveChat = () => {
        setSavingChat(true);
        fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ gjallarhorn_chat_id: (chatId || '').trim() }),
        })
            .then(() => load())
            .finally(() => setSavingChat(false));
    };

    const checkNow = () => {
        setChecking(true);
        fetch('/api/huginn/gjallarhorn/news/check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ force: true }),
        })
            .then(() => load())
            .finally(() => setChecking(false));
    };

    const runTest = () => {
        setTesting(true);
        fetch('/api/huginn/gjallarhorn/news/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: testText }),
        })
            .then((r) => (r.ok ? r.json() : null))
            .then((d) => setTestHits(d ? d.hits : []))
            .catch(() => setTestHits([]))
            .finally(() => setTesting(false));
    };

    const armed = status && status.armed;

    return (
        <div className="rounded-xl border border-white/10 bg-odin-blue/30 px-4 py-3">
            <div className="flex items-center gap-2 mb-3">
                <Newspaper size={14} className="text-amber-500/70" />
                <span className="text-xs font-semibold text-slate-300">News watcher</span>
                <InfoTip text="Watches the official Counter-Strike 2 update feed. Rings your phone + sends a Telegram message the moment Valve ADDS or REMOVES a case, collection, capsule or souvenir — the exact minute an item gets limited and starts to pump. Map-pool changes are ignored. Routine bug-fix patches stay silent." />
                <button
                    type="button"
                    onClick={load}
                    className="ml-auto text-slate-500 hover:text-slate-300"
                    title="Refresh status"
                >
                    <RefreshCw size={12} />
                </button>
            </div>

            {/* Arm toggle + check-now */}
            <div className="flex flex-wrap items-center gap-2 mb-3">
                <button
                    type="button"
                    onClick={toggleArmed}
                    disabled={saving || !status}
                    className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border transition ${
                        armed
                            ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-300 hover:bg-emerald-500/25'
                            : 'bg-slate-500/10 border-white/10 text-slate-400 hover:bg-slate-500/20'
                    }`}
                    title={armed ? 'Armed — will ring on a limiting event. Click to disarm.' : 'Disarmed — watching but silent. Click to arm.'}
                >
                    {armed ? <Bell size={12} /> : <BellOff size={12} />}
                    {armed ? 'Armed' : 'Disarmed'}
                </button>
                <button
                    type="button"
                    onClick={checkNow}
                    disabled={checking}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-odin-blue/60 border border-white/10 text-slate-300 hover:bg-odin-blue"
                    title="Poll the feed right now (re-checks the newest post; won't re-ring old ones)"
                >
                    <RefreshCw size={12} className={checking ? 'animate-spin' : ''} />
                    Check now
                </button>
                {status && (
                    <span className={`inline-flex items-center gap-1 text-[11px] ${status.can_ring ? 'text-slate-500' : 'text-amber-400/80'}`}>
                        <PhoneCall size={11} />
                        {status.can_ring ? 'Ring ready' : 'Ring not set up'}
                    </span>
                )}
                {status && !status.running && (
                    <span className="text-[11px] text-amber-400/80">watcher not running</span>
                )}
                {status && status.last_error && (
                    <span className="text-[11px] text-red-400/80" title={status.last_error}>feed error</span>
                )}
            </div>

            {/* Dedicated alert chat — keeps Gjallarhorn out of the Case Arbitrage board */}
            <div className="flex flex-wrap items-center gap-2 mb-3">
                <span className="text-[11px] text-slate-500 inline-flex items-center gap-1">
                    Alert chat id
                    <InfoTip text="Telegram chat id that Gjallarhorn alerts are sent to (same bot as Case Arbitrage, but a separate conversation so they never mix). Leave blank to fall back to the shared Case-Arbitrage chat." />
                </span>
                <input
                    type="text"
                    value={chatId === null ? '' : chatId}
                    onChange={(e) => setChatId(e.target.value)}
                    placeholder={status && status.shared_chat_id ? `shared: ${status.shared_chat_id}` : 'e.g. -1001234567890'}
                    className="text-xs rounded-lg bg-odin-blue/60 border border-white/10 px-2 py-1 text-slate-300 placeholder:text-slate-600 focus:outline-none focus:border-amber-500/40 w-52"
                />
                <button
                    type="button"
                    onClick={saveChat}
                    disabled={savingChat}
                    className="px-2.5 py-1 rounded-lg text-xs font-medium bg-odin-blue/60 border border-white/10 text-slate-300 hover:bg-odin-blue disabled:opacity-50"
                >
                    {savingChat ? 'Saving…' : 'Save'}
                </button>
                {status && !chatId && (
                    <span className="text-[10px] text-slate-600">using shared chat</span>
                )}
            </div>

            {/* Recent detected events */}
            <div className="mb-3">
                <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Recent events</p>
                {status && status.recent && status.recent.length ? (
                    <ul className="space-y-1.5">
                        {status.recent.map((ev, i) => (
                            <li key={ev.gid || i} className="text-xs">
                                <a href={ev.url} target="_blank" rel="noreferrer" className="text-sky-400/90 hover:underline">
                                    {ev.title || 'Counter-Strike 2 Update'}
                                </a>
                                <span className="text-slate-600"> · {ev.date ? new Date(ev.date * 1000).toLocaleDateString() : ''}</span>
                                <div className="mt-0.5 flex flex-wrap gap-1">
                                    {ev.hits && ev.hits.map((h, j) => (
                                        <span
                                            key={j}
                                            className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                                                h.action === 'remove'
                                                    ? 'bg-red-500/15 text-red-300'
                                                    : 'bg-emerald-500/15 text-emerald-300'
                                            }`}
                                            title={h.text}
                                        >
                                            {h.action === 'remove' ? '− ' : '+ '}{h.kind}
                                        </span>
                                    ))}
                                </div>
                            </li>
                        ))}
                    </ul>
                ) : (
                    <p className="text-xs text-slate-500">Nothing caught yet. The horn stays quiet until Valve limits something.</p>
                )}
            </div>

            {/* Detection tester */}
            <div>
                <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">
                    Test detection
                    <InfoTip text="Paste any update post (or a single line) to see exactly what would trigger a ring. No alert is sent — this is just a dry run." />
                </p>
                <textarea
                    value={testText}
                    onChange={(e) => setTestText(e.target.value)}
                    rows={3}
                    placeholder="Paste an update post here to see what would fire…"
                    className="w-full text-xs rounded-lg bg-odin-blue/60 border border-white/10 px-2 py-1.5 text-slate-300 placeholder:text-slate-600 focus:outline-none focus:border-amber-500/40"
                />
                <div className="flex items-center gap-2 mt-1.5">
                    <button
                        type="button"
                        onClick={runTest}
                        disabled={testing || !testText.trim()}
                        className="px-3 py-1 rounded-lg text-xs font-medium bg-amber-500/15 border border-amber-500/30 text-amber-300 hover:bg-amber-500/25 disabled:opacity-40"
                    >
                        {testing ? 'Testing…' : 'Test'}
                    </button>
                    {testHits != null && (
                        <span className="text-[11px] text-slate-500">
                            {testHits.length ? `${testHits.length} hit${testHits.length === 1 ? '' : 's'} → would ring` : 'no hits → silent'}
                        </span>
                    )}
                </div>
                {testHits != null && testHits.length > 0 && (
                    <ul className="mt-2 space-y-1">
                        {testHits.map((h, i) => (
                            <li key={i} className="text-[11px] flex gap-1.5">
                                <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium shrink-0 ${
                                    h.action === 'remove' ? 'bg-red-500/15 text-red-300' : 'bg-emerald-500/15 text-emerald-300'
                                }`}>
                                    {h.action === 'remove' ? '−' : '+'} {h.kind}
                                </span>
                                <span className="text-slate-400">{h.text}</span>
                            </li>
                        ))}
                    </ul>
                )}
            </div>
        </div>
    );
};

export default NewsWatcher;
