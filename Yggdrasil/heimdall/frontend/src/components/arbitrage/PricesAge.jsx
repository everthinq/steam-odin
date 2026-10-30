import { Clock } from 'lucide-react';
import { formatAge, useNowSeconds } from '../../utils/priceAge';

const PRICE_TYPE_NAMES = {
    Sell: 'lowest listing', SellWithoutHold: 'lowest listing, no trade hold', Buy: 'buy orders',
    Median30d: '30-day median', Average30d: '30-day average',
};

// How old a profile's prices are: the oldest pulse table behind them. `tables` is the answer's
// X-Pulse-Tables header ([[market, price type, seconds since its download]]) and `receivedAt`
// when it arrived (browser milliseconds), so the age keeps counting without trusting the clock of
// the backend container. Ticks on its own, so the table of deals does not re-render. From Arbitrage.jsx.
// `note`: where the profile's other prices come from (not a pulse table), for the tooltip.
const PricesAge = ({ tables, receivedAt, marketName, staleAfterSeconds = 600, note = null }) => {
    const now = useNowSeconds(10000);
    if (!tables?.length || !now) return null;
    const elapsed = Math.max(0, now - receivedAt / 1000);
    const ages = tables.map(([market, priceType, age]) => ({ market, priceType, age: age + elapsed }));
    const oldest = Math.max(...ages.map(table => table.age));
    const title = ['The pulse tables behind these prices:',
        ...ages.map(table => `${marketName(table.market)} · ${PRICE_TYPE_NAMES[table.priceType] || table.priceType}: downloaded ${formatAge(table.age)}`),
        ...(note ? [note] : []),
        '"Check live" re-checks one row now; "Fetch live data" downloads every table again.'].join('\n');
    return (
        <span className={`flex items-center gap-1 text-xs tabular-nums shrink-0 ${oldest > staleAfterSeconds ? 'text-amber-300' : 'text-slate-400'}`} title={title}>
            <Clock size={11} /> prices downloaded {formatAge(oldest)}
        </span>
    );
};

export default PricesAge;
