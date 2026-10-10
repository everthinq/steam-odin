import { useState } from 'react';
import { ArrowRight, ChevronDown, ExternalLink, Info } from 'lucide-react';

// SkinSwap's two balances, shown under the profile picker whenever a SkinSwap
// profile is picked. SkinSwap says "Trade balance = Market balance x 1.4, always"
// (its 40% deposit bonus; SkinSwap help centre, 2026-10). That holds for deposits,
// but on 2026-10-10 a skin sold on the Trade page gave Trade balance and
// nothing on Market (most likely escrow under Steam's 7-day trade protection; not
// confirmed). So Huginn shows SkinSwap (Trade) prices as the Trade page does, with
// the real-dollar value (divided by 1.4) under each (huginn_service.py,
// _MARKET_PRICE_SCALE, 'pagePrice'). The open/closed state is remembered per browser.

const TRADE_BONUS = 1.4;
const OPEN_KEY = 'huginn.skinswapNoteOpen';
const LINKS = [
    { label: 'Trade page', href: 'https://skinswap.com/trade' },
    { label: 'Market page', href: 'https://skinswap.com/buy' },
    { label: 'SkinSwap help: the two balances', href: 'https://intercom.help/skinswap/en/articles/17158124-understanding-your-market-and-trade-balances' },
    { label: 'SkinSwap help: trade protection and your balance', href: 'https://intercom.help/skinswap/en/articles/11841490-steam-trade-protection-what-it-means-for-your-balance' },
];

const readOpen = () => {
    try { return localStorage.getItem(OPEN_KEY) === '1'; } catch { return false; }
};

const money = (text) => {
    const value = parseFloat(text);
    return Number.isFinite(value) && value >= 0 ? value : null;
};

const MoneyInput = ({ label, value, onChange }) => (
    <label className="flex items-center gap-1.5 rounded-lg bg-black/30 border border-white/10 px-2 py-1">
        <span className="text-slate-500">{label} $</span>
        <input
            type="number"
            min="0"
            step="0.01"
            value={value}
            onChange={e => onChange(e.target.value)}
            className="w-20 bg-transparent text-slate-100 tabular-nums outline-none"
        />
    </label>
);

// `estimated`: the picked profile uses SkinSwap (Trade)'s min, which pulse does not
// have; Huginn estimates it (huginn_service.py, _SKINSWAP_TRADE_ASK_MARKUP).
const SkinSwapBalanceNote = ({ estimated = false }) => {
    const [open, setOpen] = useState(readOpen);
    const [trade, setTrade] = useState('14.00');
    const [pagePrice, setPagePrice] = useState('38.52');
    const [cashOut, setCashOut] = useState('30.00');

    const tradeValue = money(trade);
    const real = tradeValue == null ? '—' : (tradeValue / TRADE_BONUS).toFixed(2);
    const price = money(pagePrice);
    const out = money(cashOut);
    // A Trade dollar is worth 1 / 1.4 of a real dollar, so spending it pays off
    // when the cash you get back is at least that share of the Trade page price.
    const share = price && out != null ? out / price : null;
    const worthIt = share != null && share >= 1 / TRADE_BONUS;

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
                <span className="text-sky-200 font-medium">SkinSwap: Trade page prices and where your money lands</span>
                <span className="text-slate-400 hidden sm:inline">· prices as on the Trade page, real dollars in grey under them</span>
                {estimated && (
                    <span className="shrink-0 rounded-md border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[11px] text-amber-300">
                        ≈ Trade min is an estimate
                    </span>
                )}
                <ChevronDown size={14} className={`ml-auto text-slate-500 shrink-0 transition-transform ${open ? '' : '-rotate-90'}`} />
            </button>

            {open && (
                <div className="px-4 pb-3 pt-1 border-t border-sky-500/10 space-y-3">
                    {estimated && (
                        <p className="rounded-lg border border-amber-500/20 bg-amber-500/[0.06] px-3 py-2 text-xs text-amber-200/90">
                            <b>SkinSwap (Trade) min is an estimate, not real data.</b> Pulse does not have the Trade page&apos;s asking prices.
                            Huginn takes what Trade pays and adds the markup we measured on 11 skins on 2026-10-10: about 1.6 times
                            under $1 and 1.19 times from $5 (the $1 to $5 range is the least certain). Asks also differ per copy, so
                            the real price can be about 10% lower (an AK-47 | Redline asked $41.87, then $38.52 the same day). Skins
                            SkinSwap barely wants (Trade pays under 60% of its Market price, or under $0.10) get no estimate. Check the
                            Trade page before you buy.
                        </p>
                    )}

                    <p className="text-xs text-slate-400">
                        SkinSwap (Trade) prices are shown as on the <b className="text-slate-300">Trade page</b> (Trade dollars). The grey
                        value under each is real dollars (divided by 1.4, SkinSwap&apos;s 40% bonus), and every profit uses it.
                    </p>

                    <ol className="space-y-1.5 text-slate-300 list-decimal pl-5 marker:text-sky-400/70">
                        <li>
                            <b>Money you deposit</b> is one balance: the Trade page shows it 1.4 times higher than the Market page.
                        </li>
                        <li>
                            <b>Money from selling a skin on the Trade page stays on Trade.</b> Tested on 2026-10-10: the sale showed on the
                            Trade page and not on the Market page. Most likely escrow while Steam&apos;s 7-day trade protection runs;
                            whether it reaches the Market page afterwards is not confirmed (ask SkinSwap&apos;s live chat).
                        </li>
                        <li>
                            <b>To get Trade balance back out,</b> buy on the Trade page and sell to a market that pays money. It pays off
                            when what you get after fees is at least 0.714 of the Trade page price (1 divided by 1.4). Steam,
                            LOOT.Farm, TradeIt (Trade) and CSMoney (Trade) pay in their own balance, not money.
                        </li>
                    </ol>

                    <div className="flex flex-wrap items-center gap-2 text-xs">
                        <MoneyInput label="Trade balance" value={trade} onChange={setTrade} />
                        <ArrowRight size={12} className="text-sky-400/70" />
                        <span className="rounded-lg bg-emerald-500/10 border border-emerald-500/20 px-2 py-1 text-emerald-300 tabular-nums">
                            worth ${real} in real dollars
                        </span>
                    </div>

                    <div className="flex flex-wrap items-center gap-2 text-xs">
                        <MoneyInput label="Trade page price" value={pagePrice} onChange={setPagePrice} />
                        <MoneyInput label="You get after fees" value={cashOut} onChange={setCashOut} />
                        <ArrowRight size={12} className="text-sky-400/70" />
                        {share == null ? (
                            <span className="text-slate-500">enter both prices</span>
                        ) : (
                            <span className={`rounded-lg border px-2 py-1 tabular-nums ${worthIt
                                ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-300'
                                : 'bg-red-500/10 border-red-500/20 text-red-300'}`}>
                                {share.toFixed(3)} of the price · {worthIt ? 'worth it' : 'you lose money'}
                                {' '}({worthIt ? '+' : ''}{((share * TRADE_BONUS - 1) * 100).toFixed(1)}% on real dollars)
                            </span>
                        )}
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
