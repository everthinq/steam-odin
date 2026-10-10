import { getTradeonShortLink } from '../../utils/tradeonShortLink';

const ESTIMATE_TIP = 'Estimated, not real data: what SkinSwap Trade pays, plus the markup we measured on its Trade page.';

// A right-aligned price. When `market` is set, it links to that market's pulse
// short-link for the item; otherwise it's plain text. An `estimated` price gets
// an amber "≈" and a tooltip saying so. From Arbitrage.jsx.
const PriceCell = ({ market, itemName, price, className, estimated = false }) => {
    const text = <>{estimated && <span className="text-amber-400/80" title={ESTIMATE_TIP}>≈</span>}${price?.toFixed(2)}</>;
    const href = market ? getTradeonShortLink(market, itemName) : null;
    if (!href) return <span className={className} title={estimated ? ESTIMATE_TIP : undefined}>{text}</span>;
    return (
        <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            title={`${estimated ? `${ESTIMATE_TIP} ` : ''}Open on ${market === 'TradeOnMarket' ? 'Tradeon' : market}`}
            onClick={(e) => e.stopPropagation()}
            className={`${className} hover:text-amber-300 hover:underline transition-colors`}
        >
            {text}
        </a>
    );
};

export default PriceCell;
