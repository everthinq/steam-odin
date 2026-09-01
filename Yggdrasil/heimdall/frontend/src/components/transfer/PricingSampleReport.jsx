import { X } from 'lucide-react';
import ItemThumb from './ItemThumb';
import SteamMarketLink from '../SteamMarketLink';

// "Now in your inventory" one-of-each pricing sample list. From Transfer.jsx.
const PricingSampleReport = ({ report, onDismiss }) => {
    if (!report?.items?.length) return null;

    const sorted = [...report.items].sort((a, b) =>
        (a.item_name || '').localeCompare(b.item_name || '')
    );

    return (
        <div className="mb-6 rounded-2xl border border-bifrost-cyan/25 bg-gradient-to-b from-bifrost-cyan/10 to-transparent overflow-hidden">
            <div className="flex items-start justify-between gap-3 px-4 py-3 border-b border-white/10 bg-black/20">
                <div className="min-w-0">
                    <h3 className="text-base font-semibold text-white">Now in your inventory</h3>
                    <p className="text-xs text-slate-400 mt-1 leading-relaxed">
                        {sorted.length} different skin{sorted.length === 1 ? '' : 's'} — we moved{' '}
                        <span className="text-bifrost-cyan">one of each</span> from storage so price
                        sites can see your full collection.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={onDismiss}
                    className="shrink-0 p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/10 transition-colors"
                    aria-label="Dismiss list"
                >
                    <X size={18} />
                </button>
            </div>
            <ul className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-2 p-3 max-h-80 overflow-y-auto custom-scrollbar">
                {sorted.map((item) => (
                    <li
                        key={item.item_id}
                        className="flex items-center gap-2.5 p-2.5 rounded-xl border border-white/5 bg-odin-blue/40 hover:bg-odin-blue/60 transition-colors"
                    >
                        <ItemThumb item={item.representative || item} />
                        <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-1 min-w-0">
                                <span className="text-sm text-white truncate">{item.item_name}</span>
                                <SteamMarketLink itemName={item.item_name} />
                            </div>
                            {item.item_wear_name && (
                                <p className="text-[11px] text-slate-500 truncate">{item.item_wear_name}</p>
                            )}
                        </div>
                    </li>
                ))}
            </ul>
        </div>
    );
};

export default PricingSampleReport;
