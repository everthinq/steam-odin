import { useState, useEffect, useMemo } from 'react';
import { Link } from 'react-router-dom';
import {
    LayoutDashboard, KeyRound, Search, Plus, Upload, Download, Eye, EyeOff, Copy, Check,
    Pencil, Trash2, ShieldCheck, ShieldOff, X, Info, LogIn, Loader2,
} from 'lucide-react';

// Mímir — the credential vault. Login / password / email / comment for every
// Steam account, encrypted at rest with the same key as the maFiles. Rows are
// split into "With maFile" (a maFile whose account_name matches this login
// exists, so Ratatoskr can log it on) and "No maFile" (credential stored only).

const empty = { login: '', password: '', email: '', comment: '' };

const fmtTime = (iso) => {
    if (!iso) return '';
    try { return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
    catch { return iso; }
};

// Login-health dot: green = last login OK, red = failed, grey = never tested.
const HealthDot = ({ c }) => {
    const s = c.last_login_status;
    const color = s === 'ok' ? 'bg-emerald-400' : s === 'failed' ? 'bg-red-400' : 'bg-slate-600';
    const title = s === 'ok'
        ? `Last login OK · ${fmtTime(c.last_login_at)}`
        : s === 'failed'
            ? `Last login FAILED · ${fmtTime(c.last_login_at)}${c.last_login_error ? ` · ${c.last_login_error}` : ''}`
            : 'Login never tested';
    return <span title={title} className={`inline-block w-2 h-2 rounded-full shrink-0 ${color}`} />;
};

// Small ⓘ sign that reveals a styled tooltip on hover. `wide` for long copy.
const InfoHint = ({ text, wide }) => (
    <span className="relative inline-flex group/hint align-middle">
        <Info size={13} className="opacity-70" />
        <span
            className={`pointer-events-none absolute top-full right-0 mt-2 ${wide ? 'w-72' : 'w-56'} p-3 rounded-lg bg-[#0b131d] border border-cyan-500/20 text-xs font-normal normal-case tracking-normal text-left text-slate-300 leading-relaxed opacity-0 group-hover/hint:opacity-100 transition-opacity duration-150 z-[60] shadow-xl shadow-black/50`}
        >
            {text}
        </span>
    </span>
);

const CopyButton = ({ value, title }) => {
    const [done, setDone] = useState(false);
    if (!value) return null;
    return (
        <button
            type="button"
            title={title || 'Copy'}
            onClick={async () => {
                try { await navigator.clipboard.writeText(value); setDone(true); setTimeout(() => setDone(false), 1200); } catch { /* clipboard blocked */ }
            }}
            className="p-1 rounded text-slate-500 hover:text-cyan-300 hover:bg-white/5 transition-colors shrink-0"
        >
            {done ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
        </button>
    );
};

const MimirVault = () => {
    const [creds, setCreds] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [search, setSearch] = useState('');
    const [revealAll, setRevealAll] = useState(false);
    const [shown, setShown] = useState({});          // per-row reveal overrides
    const [editing, setEditing] = useState(null);    // record being edited, or {new:true}
    const [form, setForm] = useState(empty);
    const [saving, setSaving] = useState(false);
    const [showImport, setShowImport] = useState(false);
    const [importText, setImportText] = useState('');
    const [importResult, setImportResult] = useState(null);
    const [importing, setImporting] = useState(false);
    const [testing, setTesting] = useState(null);    // credential id currently being test-logged

    const load = async () => {
        try {
            const res = await fetch('/api/mimir/credentials');
            if (!res.ok) throw new Error('Failed to load credentials');
            const data = await res.json();
            setCreds(data.credentials || []);
            setError(null);
        } catch (e) {
            setError(e.message);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, []);

    // Filter, then split into the two maFile groups — each sorted alphabetically.
    const { withMafile, noMafile } = useMemo(() => {
        const q = search.trim().toLowerCase();
        const rows = q
            ? creds.filter(c =>
                c.login.toLowerCase().includes(q) ||
                c.email.toLowerCase().includes(q) ||
                c.comment.toLowerCase().includes(q))
            : creds;
        const byLogin = (a, b) => a.login.localeCompare(b.login);
        return {
            withMafile: rows.filter(c => c.linked_steamid).sort(byLogin),
            noMafile: rows.filter(c => !c.linked_steamid).sort(byLogin),
        };
    }, [creds, search]);

    const linkedCount = creds.filter(c => c.linked_steamid).length;
    const totalShown = withMafile.length + noMafile.length;

    const openNew = () => { setForm(empty); setEditing({ new: true }); };
    const openEdit = (c) => { setForm({ login: c.login, password: c.password, email: c.email, comment: c.comment }); setEditing(c); };
    const closeForm = () => { setEditing(null); setForm(empty); };

    const saveForm = async () => {
        setSaving(true);
        try {
            const isNew = editing?.new;
            const res = await fetch(
                isNew ? '/api/mimir/credentials' : `/api/mimir/credentials/${editing.id}`,
                {
                    method: isNew ? 'POST' : 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(form),
                },
            );
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Save failed');
            closeForm();
            await load();
        } catch (e) {
            alert(e.message);
        } finally {
            setSaving(false);
        }
    };

    const remove = async (c) => {
        if (!confirm(`Delete credential for "${c.login}"? This cannot be undone.`)) return;
        try {
            const res = await fetch(`/api/mimir/credentials/${c.id}`, { method: 'DELETE' });
            if (!res.ok) throw new Error('Delete failed');
            await load();
        } catch (e) { alert(e.message); }
    };

    const runImport = async () => {
        setImporting(true);
        setImportResult(null);
        try {
            const res = await fetch('/api/mimir/import', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: importText }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Import failed');
            setImportResult(data);
            await load();
        } catch (e) {
            setImportResult({ error: e.message });
        } finally {
            setImporting(false);
        }
    };

    const testLogin = async (c) => {
        setTesting(c.id);
        try {
            const res = await fetch(`/api/mimir/credentials/${c.id}/test-login`, { method: 'POST' });
            const data = await res.json();
            await load();                                  // refresh so the health dot updates
            if (res.status === 409) alert(data.error);     // no maFile — can't test
            else if (data.ok === false) alert(`Login failed for ${c.login}${data.error ? `: ${data.error}` : ''}`);
        } catch (e) {
            alert(e.message);
        } finally {
            setTesting(null);
        }
    };

    const exportVault = async () => {
        try {
            const res = await fetch('/api/mimir/export');
            const data = await res.json();
            const blob = new Blob([data.text || ''], { type: 'text/plain' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `mimir-credentials-${new Date().toISOString().slice(0, 10)}.txt`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
        } catch (e) {
            alert(`Export failed: ${e.message}`);
        }
    };

    const isShown = (id) => revealAll || shown[id];

    // One credential row, reused by both groups.
    const renderRow = (c, hasMafile) => (
        <tr key={c.id} className="border-b border-white/[0.03] hover:bg-white/[0.02] transition-colors">
            <td className="px-4 py-3 align-top">
                <div className="flex items-center gap-2">
                    <HealthDot c={c} />
                    <span
                        title={hasMafile ? `maFile present (${c.linked_steamid})` : 'No maFile'}
                        className={hasMafile ? 'text-cyan-400 shrink-0' : 'text-slate-600 shrink-0'}
                    >
                        {hasMafile ? <ShieldCheck size={13} /> : <ShieldOff size={13} />}
                    </span>
                    <span className="text-slate-200 font-medium">{c.login}</span>
                    <CopyButton value={c.login} title="Copy login" />
                </div>
            </td>
            <td className="px-4 py-3 align-top">
                <div className="flex items-center gap-1.5">
                    <span className={`font-mono ${isShown(c.id) ? 'text-slate-300' : 'text-slate-600 select-none'}`}>
                        {isShown(c.id) ? (c.password || '—') : '••••••••••'}
                    </span>
                    <button
                        type="button"
                        onClick={() => setShown(s => ({ ...s, [c.id]: !s[c.id] }))}
                        className="p-1 rounded text-slate-500 hover:text-cyan-300 hover:bg-white/5 transition-colors shrink-0"
                        title={isShown(c.id) ? 'Hide' : 'Reveal'}
                    >
                        {isShown(c.id) ? <EyeOff size={13} /> : <Eye size={13} />}
                    </button>
                    <CopyButton value={c.password} title="Copy password" />
                </div>
            </td>
            <td className="px-4 py-3 align-top">
                <div className="flex items-center gap-1.5">
                    <span className="text-slate-400">{c.email || '—'}</span>
                    <CopyButton value={c.email} title="Copy email" />
                </div>
            </td>
            <td className="px-4 py-3 align-top max-w-[16rem]">
                <span className="text-slate-500">{c.comment || '—'}</span>
            </td>
            <td className="px-4 py-3 align-top">
                <div className="flex items-center justify-end gap-1">
                    {hasMafile && (
                        <button
                            onClick={() => testLogin(c)}
                            disabled={testing === c.id}
                            title="Test Steam login via Ratatoskr"
                            className="p-1.5 rounded text-slate-500 hover:text-emerald-300 hover:bg-white/5 transition-colors disabled:opacity-60"
                        >
                            {testing === c.id ? <Loader2 size={14} className="animate-spin" /> : <LogIn size={14} />}
                        </button>
                    )}
                    <button onClick={() => openEdit(c)} title="Edit" className="p-1.5 rounded text-slate-500 hover:text-cyan-300 hover:bg-white/5 transition-colors">
                        <Pencil size={14} />
                    </button>
                    <button onClick={() => remove(c)} title="Delete" className="p-1.5 rounded text-slate-500 hover:text-red-400 hover:bg-white/5 transition-colors">
                        <Trash2 size={14} />
                    </button>
                </div>
            </td>
        </tr>
    );

    const groupHeader = (icon, title, subtitle, count, accent) => (
        <tr className="bg-white/[0.03]">
            <td colSpan={5} className="px-4 py-2.5">
                <div className="flex items-center gap-2">
                    <span className={accent}>{icon}</span>
                    <span className={`text-xs font-bold uppercase tracking-wider ${accent}`}>{title}</span>
                    <span className="text-xs text-slate-600">· {count}</span>
                    <span className="text-[11px] text-slate-500 normal-case font-normal">— {subtitle}</span>
                </div>
            </td>
        </tr>
    );

    return (
        <div className="min-h-screen bg-odin-dark flex flex-col">
            {/* Header */}
            <div className="shrink-0 border-b border-white/5 bg-odin-blue/50 px-6 py-4 flex items-center gap-3 flex-wrap">
                <Link to="/" className="flex items-center gap-1.5 text-sm text-slate-400 hover:text-white transition-colors shrink-0">
                    <LayoutDashboard size={15} /> Dashboard
                </Link>
                <span className="text-white/20">/</span>
                <KeyRound size={18} className="text-cyan-400" />
                <h1 className="text-lg font-bold font-serif text-cyan-100">Mímir — The Keeper</h1>
                <span className="text-xs text-slate-500 hidden sm:inline">
                    {creds.length} credentials · {linkedCount} with maFile · {creds.length - linkedCount} without
                </span>

                <div className="ml-auto flex items-center gap-2 flex-wrap">
                    {/* 1 · Add credential (primary) */}
                    <div className="relative group/add">
                        <button
                            onClick={openNew}
                            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-medium transition-colors"
                        >
                            <Plus size={14} /> Add credential
                            <InfoHint text="Store one account's login, password, email and an optional note. Saved encrypted with your maFile key." />
                        </button>
                    </div>
                    {/* 2 · Import (secondary, themed outline) */}
                    <button
                        onClick={() => { setShowImport(true); setImportResult(null); }}
                        className="flex items-center gap-2 px-3 py-2 rounded-lg bg-cyan-500/10 border border-cyan-500/30 hover:border-cyan-400/60 hover:bg-cyan-500/20 text-cyan-100 text-sm transition-colors"
                    >
                        <Upload size={14} /> Import
                        <InfoHint
                            wide
                            text={
                                <>
                                    Bulk-add accounts from a text list. One account per line, fields
                                    separated by semicolons:
                                    <span className="block my-1 font-mono text-cyan-200">login;password;email;comment</span>
                                    The comment is optional. Passwords may safely contain <span className="font-mono">;</span> or
                                    <span className="font-mono"> @</span> — the email is found by pattern. Existing logins are
                                    updated in place; new ones are added.
                                </>
                            }
                        />
                    </button>
                    {/* 3 · Reveal all (tertiary, ghost) */}
                    <button
                        onClick={() => setRevealAll(v => !v)}
                        className="flex items-center gap-2 px-3 py-2 rounded-lg text-slate-300 hover:bg-white/10 hover:text-cyan-100 border border-transparent hover:border-white/10 text-sm transition-colors"
                    >
                        {revealAll ? <EyeOff size={14} /> : <Eye size={14} />}
                        {revealAll ? 'Hide all' : 'Reveal all'}
                        <InfoHint text="Show or hide every password in the table at once. Each row also has its own eye toggle." />
                    </button>
                    {/* 4 · Export (utility, ghost) */}
                    <button
                        onClick={exportVault}
                        className="flex items-center gap-2 px-3 py-2 rounded-lg text-slate-300 hover:bg-white/10 hover:text-cyan-100 border border-transparent hover:border-white/10 text-sm transition-colors"
                    >
                        <Download size={14} /> Export
                        <InfoHint
                            wide
                            text={
                                <>
                                    Download the whole vault as a{' '}
                                    <span className="font-mono text-cyan-200">login;password;email;comment</span> text file —
                                    the exact format Import reads, so it round-trips. Use it for an off-machine backup.
                                    <span className="block mt-1 text-amber-300/80">Note: the file is plaintext — store it somewhere safe.</span>
                                </>
                            }
                        />
                    </button>
                </div>
            </div>

            <div className="flex-1 p-4 md:p-8">
                <div className="max-w-6xl mx-auto flex flex-col gap-4">
                    {/* Search */}
                    <div className="relative max-w-md">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={16} />
                        <input
                            type="text"
                            placeholder="Search login, email, comment…"
                            value={search}
                            onChange={e => setSearch(e.target.value)}
                            className="w-full pl-10 pr-4 py-2.5 glass-panel rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-cyan-500/50 transition-all text-sm"
                        />
                    </div>

                    {error && (
                        <div className="bg-red-500/10 border border-red-500/50 text-red-300 p-3 rounded-lg text-sm">{error}</div>
                    )}
                    {loading && <div className="text-slate-500 text-sm">Loading…</div>}

                    {!loading && totalShown === 0 && (
                        <div className="text-slate-500 text-sm py-8 text-center">
                            {creds.length === 0 ? 'Vault is empty. Add a credential or Import a list.' : 'No matches.'}
                        </div>
                    )}

                    {/* Table — grouped by maFile presence */}
                    {totalShown > 0 && (
                        <div className="glass-panel rounded-xl overflow-hidden">
                            <div className="overflow-x-auto custom-scrollbar">
                                <table className="w-full text-sm">
                                    <thead>
                                        <tr className="text-[11px] uppercase tracking-wider text-slate-500 border-b border-white/5">
                                            <th className="text-left font-semibold px-4 py-3">Login</th>
                                            <th className="text-left font-semibold px-4 py-3">Password</th>
                                            <th className="text-left font-semibold px-4 py-3">Email</th>
                                            <th className="text-left font-semibold px-4 py-3">Comment</th>
                                            <th className="text-right font-semibold px-4 py-3"></th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {withMafile.length > 0 && (
                                            <>
                                                {groupHeader(<ShieldCheck size={14} />, 'With maFile',
                                                    'Steam Guard imported — Ratatoskr can log in', withMafile.length, 'text-cyan-400')}
                                                {withMafile.map(c => renderRow(c, true))}
                                            </>
                                        )}
                                        {noMafile.length > 0 && (
                                            <>
                                                {groupHeader(<ShieldOff size={14} />, 'No maFile',
                                                    'Credential stored only — no login or 2FA control', noMafile.length, 'text-slate-400')}
                                                {noMafile.map(c => renderRow(c, false))}
                                            </>
                                        )}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    )}
                </div>
            </div>

            {/* Add / Edit modal */}
            {editing && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={closeForm}>
                    <div className="glass-panel rounded-2xl w-full max-w-md p-6" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center gap-2 mb-4">
                            <KeyRound size={18} className="text-cyan-400" />
                            <h2 className="text-base font-semibold text-cyan-100">{editing.new ? 'Add credential' : `Edit ${editing.login}`}</h2>
                            <button onClick={closeForm} className="ml-auto p-1 text-slate-500 hover:text-white"><X size={18} /></button>
                        </div>
                        <div className="flex flex-col gap-3">
                            {['login', 'password', 'email', 'comment'].map(field => (
                                <label key={field} className="flex flex-col gap-1">
                                    <span className="text-[11px] uppercase tracking-wider text-slate-500">{field}</span>
                                    <input
                                        type="text"
                                        value={form[field]}
                                        onChange={e => setForm(f => ({ ...f, [field]: e.target.value }))}
                                        placeholder={field === 'comment' ? 'optional' : ''}
                                        className="px-3 py-2 rounded-lg bg-black/30 border border-white/10 text-slate-200 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-cyan-500/50"
                                    />
                                </label>
                            ))}
                        </div>
                        <div className="flex justify-end gap-2 mt-5">
                            <button onClick={closeForm} className="px-4 py-2 rounded-lg text-slate-300 hover:bg-white/5 text-sm">Cancel</button>
                            <button
                                onClick={saveForm}
                                disabled={saving || !form.login.trim()}
                                className="px-4 py-2 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-medium disabled:opacity-50"
                            >
                                {saving ? 'Saving…' : 'Save'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Import modal */}
            {showImport && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => setShowImport(false)}>
                    <div className="glass-panel rounded-2xl w-full max-w-2xl p-6" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center gap-2 mb-2">
                            <Upload size={18} className="text-cyan-400" />
                            <h2 className="text-base font-semibold text-cyan-100">Import credentials</h2>
                            <button onClick={() => setShowImport(false)} className="ml-auto p-1 text-slate-500 hover:text-white"><X size={18} /></button>
                        </div>
                        <p className="text-xs text-slate-500 mb-3">
                            One account per line: <code className="text-cyan-200">login;password;email;comment</code>. Comment is optional.
                            Passwords may contain <code className="text-cyan-200">;</code> or <code className="text-cyan-200">@</code>.
                            Existing logins are updated; new ones are added.
                        </p>
                        <textarea
                            value={importText}
                            onChange={e => setImportText(e.target.value)}
                            rows={10}
                            placeholder={'vincent_iles;SuMghnCkKEpHLf6b;irina@rambler.ru;spare gmail\n…'}
                            className="w-full px-3 py-2 rounded-lg bg-black/40 border border-white/10 text-slate-200 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-cyan-500/50 custom-scrollbar"
                        />
                        {importResult && (
                            <div className="mt-3 text-sm">
                                {importResult.error ? (
                                    <div className="text-red-300">{importResult.error}</div>
                                ) : (
                                    <div className="text-slate-300">
                                        <span className="text-emerald-400">{importResult.added} added</span>,{' '}
                                        <span className="text-cyan-300">{importResult.updated} updated</span>{' '}
                                        <span className="text-slate-500">({importResult.parsed} parsed)</span>
                                        {importResult.warnings?.length > 0 && (
                                            <ul className="mt-2 text-xs text-amber-300/80 list-disc pl-5 max-h-32 overflow-y-auto custom-scrollbar">
                                                {importResult.warnings.map((w, i) => <li key={i}>{w}</li>)}
                                            </ul>
                                        )}
                                    </div>
                                )}
                            </div>
                        )}
                        <div className="flex justify-end gap-2 mt-4">
                            <button onClick={() => setShowImport(false)} className="px-4 py-2 rounded-lg text-slate-300 hover:bg-white/5 text-sm">Close</button>
                            <button
                                onClick={runImport}
                                disabled={importing || !importText.trim()}
                                className="px-4 py-2 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-medium disabled:opacity-50"
                            >
                                {importing ? 'Importing…' : 'Import'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default MimirVault;
