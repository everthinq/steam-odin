import { getTradeonShortLink } from '../../utils/tradeonShortLink';

// LOOT.Farm price tier = its price relative to Steam, shown on their site as the
// price-tag background color. Thresholds (on the feed `rate` %) and exact hexes are
// lifted from loot.farm's own CSS/JS: green = cheap vs Steam (best to buy), orange =
// priced above Steam. Below 80% → untiered (default).
const lootfarmTier = (rate) => {
    if (rate == null) return null;
    if (rate >= 115) return { bg: '#d19705', pct: '105%' };   // orange
    if (rate >= 110) return { bg: '#8c857c', pct: '100%' };   // gray
    if (rate >= 95)  return { bg: '#825115', pct: '85%' };    // brown
    if (rate >= 80)  return { bg: '#00b021', pct: '70%' };    // green
    return null;
};

// LOOT.Farm sell price, rendered as a tier-colored tag like their own site. From Arbitrage.jsx.
const LootfarmSellCell = ({ itemName, price, rate }) => {
    const tier = lootfarmTier(rate);
    const href = getTradeonShortLink('LootFarm', itemName);
    const text = `$${price?.toFixed(2)}`;
    const title = tier
        ? `LOOT.Farm tier ${tier.pct} of Steam${rate != null ? ` · rate ${rate}%` : ''}`
        : (rate != null ? `rate ${rate}% of Steam` : '');
    const inner = tier
        ? <span className="inline-block px-2 rounded font-semibold text-white" style={{ backgroundColor: tier.bg, textShadow: '0 0 4px #000' }}>{text}</span>
        : <span>{text}</span>;
    return (
        <div className="text-base text-right tabular-nums text-slate-300" title={title}>
            {href
                ? <a href={href} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()} className="hover:opacity-80 transition-opacity">{inner}</a>
                : inner}
        </div>
    );
};

export default LootfarmSellCell;
