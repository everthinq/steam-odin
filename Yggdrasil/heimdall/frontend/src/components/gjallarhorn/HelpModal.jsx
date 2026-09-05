import { useEffect } from 'react';
import { X, Siren } from 'lucide-react';

// Plain-language, KISS guide to the page. Opened from the "How to use" button.
const Step = ({ n, children }) => (
    <div className="flex gap-3">
        <div className="shrink-0 w-6 h-6 rounded-full bg-amber-600/80 text-white text-xs font-bold flex items-center justify-center">{n}</div>
        <p className="text-sm text-slate-300 leading-relaxed">{children}</p>
    </div>
);

const Term = ({ name, children }) => (
    <div className="flex flex-wrap gap-x-2 text-sm">
        <span className="text-amber-200 font-medium">{name}</span>
        <span className="text-slate-400">— {children}</span>
    </div>
);

const HelpModal = ({ onClose }) => {
    useEffect(() => {
        const onKey = (e) => { if (e.key === 'Escape') onClose(); };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [onClose]);

    return (
        <div className="fixed inset-0 z-[200] bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
            <div
                onClick={(e) => e.stopPropagation()}
                className="w-full max-w-2xl max-h-[85vh] overflow-y-auto custom-scrollbar bg-[#0d1520] border border-white/10 rounded-2xl shadow-2xl shadow-black/60"
            >
                <div className="sticky top-0 z-10 flex items-center gap-2 px-5 py-4 border-b border-white/10 bg-[#0d1520]">
                    <Siren size={18} className="text-amber-500" />
                    <h2 className="text-lg font-bold font-serif text-amber-100">How to use Gjallarhorn</h2>
                    <button onClick={onClose} className="ml-auto text-slate-500 hover:text-white"><X size={18} /></button>
                </div>

                <div className="px-5 py-4 space-y-5">
                    {/* What it's for */}
                    <section className="space-y-2">
                        <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500">What this page is for</h3>
                        <p className="text-sm text-slate-300 leading-relaxed">
                            Sometimes Valve makes a CS2 case <span className="text-amber-200">limited</span>. When that
                            happens, its price jumps fast. This page helps you <span className="text-amber-200">sell your
                            dead items</span> (ones worth less than you paid) and <span className="text-amber-200">buy the
                            new hot case</span> quickly, before the price runs away.
                        </p>
                    </section>

                    {/* Steps */}
                    <section className="space-y-3">
                        <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500">Do this, in order</h3>
                        <Step n="1">Pick your portfolio at the top. <span className="text-slate-200">Combined</span> means all your accounts together.</Step>
                        <Step n="2">Optional: pick an account. Then the table shows what you can sell <span className="text-slate-200">right now</span> and what is still locked, plus your free storage space.</Step>
                        <Step n="3">Read the table. The best items to sell are at the top (highest <span className="text-slate-200">Score</span>). Click <span className="text-slate-200">Deflated only</span> to see just the items now worth less than you paid.</Step>
                        <Step n="4">Open <span className="text-emerald-300">Redeploy markets</span> and write down the sites that let you spend your money right after selling (no multi-day hold), like CS.Money trade. Add each one as you check it.</Step>
                        <Step n="5">Open <span className="text-amber-200">Target basket</span>, type the new case name, and type how much money you have. It shows how many you can buy.</Step>
                        <Step n="6">Hit <span className="text-slate-200">Refresh</span> to update prices. Prices and Steam numbers fill in on their own after a few seconds.</Step>
                    </section>

                    {/* Columns */}
                    <section className="space-y-1.5">
                        <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500">What the columns mean</h3>
                        <Term name="Bought on">where you bought it.</Term>
                        <Term name="Held by">which account has it (in Combined view).</Term>
                        <Term name="Δ vs cost">up or down vs what you paid. Red = cheaper now (deflated).</Term>
                        <Term name="P/L if sold">money you win or lose if you sell the whole lot now.</Term>
                        <Term name="7d sold">how many sold on Steam in the last week. Higher = easier to sell fast.</Term>
                        <Term name="Spread">gap between the cheapest and the middle price. Small = easy to sell.</Term>
                        <Term name="7d trend">is the Steam price going up or down this week.</Term>
                        <Term name="Tradable">how many you can sell right now vs locked. Needs a connected account.</Term>
                        <Term name="Score">how good this item is to sell and swap. Higher = better. Hover it for the details.</Term>
                    </section>

                    {/* Good to know */}
                    <section className="space-y-2">
                        <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500">Good to know</h3>
                        <ul className="text-sm text-slate-300 leading-relaxed list-disc pl-5 space-y-1">
                            <li>Steam can hold your money for a few days if you sell something at an <span className="text-amber-200">unusually high</span> price, or if the account is new. Selling normal items at the normal price is usually instant.</li>
                            <li><span className="text-slate-200">Tradable</span> and <span className="text-slate-200">Space</span> only work when the account is connected (Confirmations → Connect).</li>
                            <li>This page tells you what to do — it does <span className="text-amber-200">not</span> buy or sell for you. You do that yourself on each site.</li>
                        </ul>
                    </section>
                </div>
            </div>
        </div>
    );
};

export default HelpModal;
