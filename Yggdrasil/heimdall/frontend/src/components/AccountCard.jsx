import React, { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Copy, Check, Trash2, Package, Eye, EyeOff } from 'lucide-react';

const AccountCard = ({ account, onDelete }) => {
    const { account_name, steamid, code, time_remaining } = account;
    const navigate = useNavigate();

    const [copied, setCopied] = useState(false);
    const [deleting, setDeleting] = useState(false);
    const [revealed, setRevealed] = useState(false);

    useEffect(() => {
        setRevealed(false);
    }, [code]);

    const copyCode = () => {
        if (!revealed || !code) return;
        navigator.clipboard.writeText(code);
        setCopied(true);
        setTimeout(() => setCopied(false), 3000);
    };

    const handleDelete = async () => {
        if (!confirm(`Are you sure you want to remove ${account_name}?`)) return;

        setDeleting(true);
        try {
            const res = await fetch(`/api/accounts/${steamid}`, { method: 'DELETE' });
            if (res.ok) {
                if (onDelete) onDelete();
                else window.location.reload();
            }
        } catch (error) {
            console.error("Failed to delete", error);
        } finally {
            setDeleting(false);
        }
    };

    // Calculate progress percentage (30s cycle)
    const progress = (time_remaining / 30) * 100;

    // Color changes based on time remaining
    let progressBarColor = 'bg-blue-600'; // Default Blue
    if (time_remaining < 5) progressBarColor = 'bg-red-500';
    else if (time_remaining < 10) progressBarColor = 'bg-yellow-500';

    return (
        <div className="glass-card rounded-lg p-4 md:p-6 w-full max-w-sm relative group mx-auto border border-white/5 bg-odin-blue/30 backdrop-blur-sm">
            <div className="flex justify-between items-start mb-4">
                <div className="flex-1 min-w-0 mr-2">
                    <h3 className="text-lg md:text-xl font-bold text-white truncate" title={account_name}>
                        {account_name}
                    </h3>
                    <p className="text-slate-400 text-xs md:text-sm font-mono truncate">{steamid}</p>
                </div>
                <button
                    onClick={handleDelete}
                    disabled={deleting}
                    className="p-2 rounded-full text-slate-500 hover:text-red-500 hover:bg-red-500/10 transition-all duration-200"
                    title="Remove Account"
                >
                    <Trash2 size={18} />
                </button>
            </div>

            <div className="mb-4">
                <div className="flex justify-between items-center bg-black/30 rounded-lg p-3 md:p-4 border border-white/5">
                    <span
                        className={`text-2xl md:text-3xl font-mono tracking-widest font-bold min-w-[5.5rem] ${revealed
                            ? 'text-emerald-400 select-all'
                            : 'text-slate-600 select-none'
                            }`}
                    >
                        {revealed ? (code || '-----') : '•••••'}
                    </span>
                    <div className="flex items-center gap-1 shrink-0">
                        <button
                            type="button"
                            onClick={() => setRevealed((v) => !v)}
                            disabled={!code}
                            className="p-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/10 transition-all duration-200 disabled:opacity-40"
                            title={revealed ? 'Hide code' : 'Show code'}
                            aria-label={revealed ? 'Hide code' : 'Show code'}
                        >
                            {revealed ? <EyeOff size={20} /> : <Eye size={20} />}
                        </button>
                        <button
                            type="button"
                            onClick={copyCode}
                            disabled={!code || copied || !revealed}
                            className={`p-2 rounded-lg transition-all duration-200 ${copied
                                ? 'bg-emerald-500/20 text-emerald-400'
                                : 'hover:bg-white/10 text-slate-400 hover:text-white disabled:opacity-40'
                                }`}
                            title={copied ? 'Copied!' : 'Copy code'}
                        >
                            {copied ? <Check size={20} /> : <Copy size={20} />}
                        </button>
                    </div>
                </div>
            </div>

            <div className="space-y-3">
                <div className="space-y-1">
                    <div className="flex justify-between text-xs text-slate-500">
                        <span>Expires in</span>
                        <span>{time_remaining}s</span>
                    </div>
                    <div className="w-full bg-black/30 rounded-full h-1.5 overflow-hidden">
                        <div
                            className={`h-full rounded-full transition-all duration-1000 ease-linear ${progressBarColor}`}
                            style={{ width: `${progress}%` }}
                        />
                    </div>
                </div>

                <div className="flex justify-end gap-2 pt-2">
                    <button
                        onClick={() => navigate(`/ratatoskr/${steamid}`)}
                        className="text-xs font-mono bg-amber-600/10 hover:bg-amber-600/20 text-amber-500 border border-amber-500/20 px-3 py-2 rounded-lg transition-all hover:scale-105 active:scale-95 flex items-center gap-2"
                        title="Open Ratatoskr Dashboard"
                    >
                        <Package size={14} />
                        <span>Ratatoskr</span>
                    </button>
                    <Link
                        to={`/accounts/${steamid}/confirmations`}
                        className="text-xs font-mono bg-emerald-600/10 hover:bg-emerald-600/20 text-emerald-500 border border-emerald-500/20 px-3 py-2 rounded-lg transition-all hover:scale-105 active:scale-95 flex items-center gap-2"
                    >
                        <Check size={14} />
                        <span>Confirmations</span>
                    </Link>
                </div>
            </div>
        </div>
    );
};

export default AccountCard;
