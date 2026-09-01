// "Name (sub)" market label used in the arbitrage profile picker. From Arbitrage.jsx.
const MarketBadge = ({ name, sub, dim }) => (
    <span className={`flex items-baseline gap-1 ${dim ? 'opacity-50' : ''}`}>
        <span className="font-semibold text-white">{name}</span>
        <span className="text-[10px] text-slate-500">({sub})</span>
    </span>
);

export default MarketBadge;
