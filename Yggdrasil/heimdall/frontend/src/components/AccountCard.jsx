import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { Copy, Clock, Check, Trash2 } from 'lucide-react';

const AccountCard = ({ account, onDelete }) => {
    const { account_name, steamid, code, time_remaining } = account;

    const [copied, setCopied] = useState(false);
    const [deleting, setDeleting] = useState(false);

    const copyCode = () => {
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
    // Color changes based on time remaining
    let progressBarColor = 'bg-blue-800'; // Dark Blue
    if (time_remaining < 5) progressBarColor = 'bg-[#4a040b]'; // Burgundy
    else if (time_remaining < 10) progressBarColor = 'bg-amber-700'; // Dark Yellow-Orange

    return (
        <div className="glass-card rounded-lg p-6 w-full max-w-sm relative group">
            <div className="flex justify-between items-start mb-4">
                <div>
                    <h3 className="text-xl font-bold text-white max-w-[180px] truncate" title={account_name}>
                        {account_name}
                    </h3>
                    {<p className="text-slate-400 text-sm font-mono">{steamid}</p>}
                </div>
                <button
                    onClick={handleDelete}
                    disabled={deleting}
                    className="p-2 rounded-full hover:bg-red-500/20 text-slate-500 hover:text-red-500 transition-colors"
                    title="Remove Account"
                >
                    <Trash2 size={20} />
                </button>
            </div>

            <div className="mb-4">
                <div className="flex justify-between items-center bg-odin-dark/50 rounded-md p-4 border border-white/10 shadow-inner">
                    <span className="text-3xl font-mono tracking-widest text-asgard-gold font-bold select-all drop-shadow-md">
                        {code || '-----'}
                    </span>
                    <button
                        onClick={copyCode}
                        disabled={!code || copied}
                        className={`p-2 rounded-md transition-all duration-300 ${copied
                            ? 'bg-bifrost-cyan/20 text-bifrost-cyan scale-110'
                            : 'hover:bg-odin-blue text-frost-white/60 hover:text-frost-white'
                            }`}
                        title={copied ? "Copied!" : "Copy Code"}
                    >
                        {copied ? <Check size={20} /> : <Copy size={20} />}
                    </button>
                </div>
            </div>

            <div className="space-y-3">
                <div className="space-y-1">
                    <div className="flex justify-between text-xs text-frost-white/40">
                        <span>Expires in</span>
                        <span>{time_remaining}s</span>
                    </div>
                    <div className="w-full bg-odin-dark/50 rounded-full h-2 overflow-hidden border border-white/5">
                        <div
                            className={`h-2 rounded-full transition-all duration-1000 ease-linear ${progressBarColor}`}
                            style={{ width: `${progress}%` }}
                        />
                    </div>
                </div>

                <div className="flex justify-end">
                    <Link
                        to={`/accounts/${steamid}/confirmations`}
                        className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-odin-blue hover:bg-odin-blue/80 text-frost-white/80 hover:text-frost-white transition-colors border border-white/5"
                    >
                        View Confirmations
                    </Link>
                </div>
            </div>
        </div>
    );
};

export default AccountCard;
