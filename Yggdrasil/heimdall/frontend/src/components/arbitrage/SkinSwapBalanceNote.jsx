import { useState } from 'react';
import { ArrowRight, ChevronDown, ExternalLink, Info } from 'lucide-react';

// How SkinSwap's Trade balance becomes Market balance, shown under the profile
// picker whenever a SkinSwap profile is picked. There is no conversion step:
// SkinSwap keeps ONE balance and shows it two ways, "Trade balance = Market
// balance x 1.4, always" (its 40% deposit bonus; SkinSwap help centre, 2026-10).
// Huginn already shows SkinSwap (Trade) prices divided by 1.4 (huginn_service.py,
// _MARKET_PRICE_SCALE). The open/closed state is remembered per browser.

const TRADE_BONUS = 1.4;
const OPEN_KEY = 'huginn.skinswapNoteOpen';
const LINKS = [
    { label: 'Trade page', href: 'https://skinswap.com/trade' },
    { label: 'Market page', href: 'https://skinswap.com/buy' },
    { label: 'SkinSwap help: the two balances', href: 'https://intercom.help/skinswap/en/articles/17158124-understanding-your-market-and-trade-balances' },
];

const readOpen = () => {
    try { return localStorage.getItem(OPEN_KEY) === '1'; } catch { return false; }
};

const SkinSwapBalanceNote = () => {
    const [open, setOpen] = useState(readOpen);
    const [trade, setTrade] = useState('35.35');
    const tradeValue = parseFloat(trade);
    const market = Number.isFinite(tradeValue) && tradeValue >= 0 ? (tradeValue / TRADE_BONUS).toFixed(2) : '—';

    const toggle = () => {
        const next = !open;
        setOpen(next);
        try { localStorage.setItem(OPEN_KEY, next ? '1' : '0'); } catch { /* per-browser convenience only */ }
    };

    return (
        <div className="shrink-0 rounded-xl border border-sky-500/25 bg-slate-950/75 text-sm">
            <button
                type="button"
                onClick={toggle}
                className="w-full flex items-center gap-2 px-4 py-2 text-left hover:bg-white/[0.02] transition-colors rounded-xl"
            >
                <Info size={14} className="text-sky-400 shrink-0" />
                <span className="text-sky-200 font-medium">SkinSwap: Trade and Market share one balance</span>
                <span className="text-slate-400 hidden sm:inline">· the Trade page shows it 1.4 times higher, no conversion needed</span>
                <ChevronDown size={14} className={`ml-auto text-slate-500 shrink-0 transition-transform ${open ? '' : '-rotate-90'}`} />
            </button>

            {open && (
                <div className="px-4 pb-3 pt-1 border-t border-sky-500/10 space-y-3">
                    <ol className="space-y-1.5 text-slate-300 list-decimal pl-5 marker:text-sky-400/70">
                        <li>Sell your skins on SkinSwap&apos;s <b>Trade page</b>. The money lands on your balance in Trade dollars.</li>
                        <li>Open the <b>Market page</b>. The same balance is already there, divided by 1.4. There is no button and nothing to convert.</li>
                        <li>Buy on the Market. It is about 20% cheaper than buying the same skin on the Trade page.</li>
                    </ol>
                    <p className="text-xs text-slate-400">
                        Huginn already shows SkinSwap (Trade) prices divided by 1.4, so its numbers are real dollars, the same as your Market balance.
                        Spending on either page lowers both views together.
                    </p>

                    <div className="flex flex-wrap items-center gap-2 text-xs">
                        <label className="flex items-center gap-1.5 rounded-lg bg-black/30 border border-white/10 px-2 py-1">
                            <span className="text-slate-500">Trade balance $</span>
                            <input
                                type="number"
                                min="0"
                                step="0.01"
                                value={trade}
                                onChange={e => setTrade(e.target.value)}
                                className="w-20 bg-transparent text-slate-100 tabular-nums outline-none"
                            />
                        </label>
                        <ArrowRight size={12} className="text-sky-400/70" />
                        <span className="rounded-lg bg-emerald-500/10 border border-emerald-500/20 px-2 py-1 text-emerald-300 tabular-nums">
                            Market balance ${market}
                        </span>
                    </div>

                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
                        {LINKS.map(l => (
                            <a key={l.href} href={l.href} target="_blank" rel="noopener noreferrer"
                               className="inline-flex items-center gap-1 text-sky-300/80 hover:text-sky-200 hover:underline">
                                {l.label} <ExternalLink size={10} />
                            </a>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
};

export default SkinSwapBalanceNote;
