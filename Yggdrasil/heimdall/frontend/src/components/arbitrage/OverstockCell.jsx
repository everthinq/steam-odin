// Sell-side overstock (LOOT.Farm autobuy). info = { limit, currentCount, overstockScale },
// where scale = currentCount/limit*100 (how full the bot is). From Arbitrage.jsx.
//   • limit 0  → no active buy limit; forced scale 100, shown "Unstable" — red.
//   • currentCount >= limit (scale >= 100) → "Full", won't take more — red.
//   • otherwise show how full it is (currentCount/limit), amber when nearly full.
// Markets without overstock data (every non-LootFarm profile) render a dash.
const OverstockCell = ({ info, className }) => {
    if (!info || info.currentCount == null) return <span className={`${className} text-slate-600`}>—</span>;
    const { limit, currentCount, overstockScale } = info;
    const scale = overstockScale != null ? overstockScale : (limit > 0 ? Math.round(currentCount / limit * 100) : 100);
    if (limit === 0) {
        return <span className={`${className} text-red-400 font-medium`} title={`LOOT.Farm has no active buy limit for this item — shown as "Unstable" on the site (currently holds ${currentCount}). Acceptance/price is unreliable.`}>Unstable</span>;
    }
    if (currentCount >= limit) {
        return <span className={`${className} text-red-400`} title={`LOOT.Farm is full: holds ${currentCount} of ${limit} — won't buy more`}>Full</span>;
    }
    const cls = scale >= 80 ? 'text-amber-400' : 'text-slate-300';
    return <span className={`${className} ${cls}`} title={`LOOT.Farm holds ${currentCount} of ${limit} (${scale}% full)`}>{currentCount}/{limit}</span>;
};

export default OverstockCell;
