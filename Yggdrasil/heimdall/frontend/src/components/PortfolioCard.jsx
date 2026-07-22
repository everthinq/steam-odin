import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Trash2, Download, ArrowRight, Pin, GripVertical, TrendingUp, TrendingDown, Copy, Check } from 'lucide-react';

const money = (v) => v == null ? '—' : `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const plClass = (v) => v == null ? 'text-slate-400' : v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-slate-400';
const plStr = (v) => v == null ? '—' : `${v > 0 ? '+' : ''}${money(v)}`;
const pctReturn = (p) => (p.invested && p.total_pl != null) ? (p.total_pl / p.invested) * 100 : null;

// Draupnir portfolio card — deliberately mirrors components/AccountCard.jsx so
// the Hoard uses the exact same styling as the Heimdall dashboard: glass-card
// chrome, grip + pin + delete title row, a bg-black/30 hero block (holding the
// current value where the 2FA code sits on an account card), and the same
// amber/emerald action-pill row at the bottom.
const PortfolioCard = ({ portfolio, onDelete, onExport, isPinned = false, onTogglePin, draggable = false }) => {
    const navigate = useNavigate();
    const [copied, setCopied] = useState(false);
    const p = portfolio;
    const pct = pctReturn(p);
    const up = (p.total_pl ?? 0) >= 0;
    const hasValue = p.current_value != null;
    const unpriced = p.unpriced_count || 0;

    const copyName = (e) => {
        e.stopPropagation();
        navigator.clipboard?.writeText(p.name);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <div
            onClick={() => navigate(`/draupnir/${p.id}`)}
            className={`glass-card cursor-pointer rounded-lg p-4 md:p-6 w-full max-w-sm relative group mx-auto border backdrop-blur-sm transition-all ${
                isPinned
                    ? 'border-amber-500/40 bg-amber-950/20 ring-1 ring-amber-500/20'
                    : 'border-white/5 bg-odin-blue/30'
            }`}
        >
            <div className="flex justify-between items-start mb-4 gap-2">
                <div className="flex items-start gap-2 min-w-0 flex-1">
                    {draggable && (
                        <button
                            type="button"
                            className="mt-0.5 p-1 rounded text-slate-600 hover:text-slate-400 cursor-grab active:cursor-grabbing shrink-0"
                            title="Drag to reorder"
                            aria-label="Drag to reorder"
                            onMouseDown={(e) => e.stopPropagation()}
                            onClick={(e) => e.stopPropagation()}
                        >
                            <GripVertical size={16} />
                        </button>
                    )}
                    <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1 min-w-0">
                            <h3 className="min-w-0 truncate text-lg md:text-xl font-bold text-white" title={p.name}>
                                {p.name}
                            </h3>
                            <button
                                type="button"
                                onClick={copyName}
                                title={copied ? 'Copied!' : 'Copy name'}
                                aria-label="Copy portfolio name"
                                className={`shrink-0 p-1 rounded transition-all ${copied ? 'text-emerald-400 opacity-100' : 'text-slate-500 hover:text-white hover:bg-white/10 opacity-0 group-hover:opacity-100'}`}
                            >
                                {copied ? <Check size={13} /> : <Copy size={13} />}
                            </button>
                        </div>
                        <p className="text-slate-400 text-xs md:text-sm truncate">
                            {p.holdings_count} holdings
                        </p>
                    </div>
                </div>
                <div className="flex items-center gap-0.5 shrink-0">
                    {onTogglePin && (
                        <button
                            type="button"
                            onClick={onTogglePin}
                            className={`p-2 rounded-full transition-all duration-200 ${
                                isPinned
                                    ? 'text-amber-400 bg-amber-500/15 hover:bg-amber-500/25'
                                    : 'text-slate-500 hover:text-amber-400 hover:bg-amber-500/10'
                            }`}
                            title={isPinned ? 'Unpin portfolio' : 'Pin to top'}
                            aria-label={isPinned ? 'Unpin portfolio' : 'Pin to top'}
                        >
                            <Pin size={18} className={isPinned ? 'fill-amber-400/40' : ''} />
                        </button>
                    )}
                    <button
                        onClick={onDelete}
                        className="p-2 rounded-full text-slate-500 hover:text-red-500 hover:bg-red-500/10 transition-all duration-200"
                        title="Delete portfolio"
                    >
                        <Trash2 size={18} />
                    </button>
                </div>
            </div>

            {/* Hero value block — same container as the account 2FA-code block.
                Value gets its own full-width line so it never collapses; the label
                and P/L pill share the sub-row beneath it. */}
            <div className="mb-4">
                <div className="bg-black/30 rounded-lg p-3 md:p-4 border border-white/5">
                    <span className="block text-2xl md:text-3xl font-mono font-bold tracking-tight text-white tabular-nums truncate">
                        {money(hasValue ? p.current_value : p.cost_basis)}
                    </span>
                    <div className="mt-2 flex items-center justify-between gap-2">
                        <div className="min-w-0">
                            <span className="block text-[10px] font-bold tracking-widest uppercase text-slate-500">
                                {hasValue ? 'Current value' : 'Cost basis'}
                            </span>
                            {hasValue && unpriced > 0 && (
                                <span
                                    className="block text-[10px] text-slate-500 truncate"
                                    title={`${unpriced} of ${p.holdings_count} holdings aren't in the price feed — valued at cost`}
                                >
                                    {unpriced} of {p.holdings_count} at cost
                                </span>
                            )}
                        </div>
                        <span className={`shrink-0 inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs font-medium tabular-nums ${
                            up ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'
                        }`}>
                            {up ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                            {plStr(p.total_pl)}{pct != null && ` (${up ? '+' : ''}${pct.toFixed(1)}%)`}
                        </span>
                    </div>
                </div>
            </div>

            <div className="space-y-3">
                <div className="space-y-1">
                    <div className="flex justify-between text-xs text-slate-500">
                        <span>Invested</span>
                        <span className="tabular-nums text-slate-400">{money(p.invested)}</span>
                    </div>
                    <div className="flex justify-between text-xs text-slate-500">
                        <span>Unrealized</span>
                        <span className={`tabular-nums ${plClass(p.unrealized_pl)}`}>{plStr(p.unrealized_pl)}</span>
                    </div>
                    <div className="flex justify-between text-xs text-slate-500">
                        <span>Realized</span>
                        <span className={`tabular-nums ${plClass(p.realized_pl)}`}>{plStr(p.realized_pl)}</span>
                    </div>
                </div>

                <div className="flex justify-end gap-2 pt-2">
                    <button
                        onClick={onExport}
                        className="text-xs font-mono bg-amber-600/10 hover:bg-amber-600/20 text-amber-500 border border-amber-500/20 px-3 py-2 rounded-lg transition-all hover:scale-105 active:scale-95 flex items-center gap-2"
                        title="Export as CSV"
                    >
                        <Download size={14} />
                        <span>Export</span>
                    </button>
                    <button
                        onClick={() => navigate(`/draupnir/${p.id}`)}
                        className="text-xs font-mono bg-emerald-600/10 hover:bg-emerald-600/20 text-emerald-500 border border-emerald-500/20 px-3 py-2 rounded-lg transition-all hover:scale-105 active:scale-95 flex items-center gap-2"
                        title="Open portfolio"
                    >
                        <ArrowRight size={14} />
                        <span>Open</span>
                    </button>
                </div>
            </div>
        </div>
    );
};

export default PortfolioCard;
