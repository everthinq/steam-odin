// "Name (sub)" market label used in the arbitrage profile picker. From Arbitrage.jsx.
// An estimated sub ("min, estimated": SkinSwap Trade) is shown in amber.
const MarketBadge = ({ name, sub, dim }) => (
    <span className={`flex items-baseline gap-1 ${dim ? 'opacity-50' : ''}`}>
        <span className="font-semibold text-white">{name}</span>
        <span className={`text-[10px] ${sub?.includes('estimated') ? 'text-amber-400/80' : 'text-slate-500'}`}>({sub})</span>
    </span>
);

export default MarketBadge;
