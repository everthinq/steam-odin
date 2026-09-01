import { getTradeonShortLink } from '../../utils/tradeonShortLink';

// A right-aligned price. When `market` is set, it links to that market's pulse
// short-link for the item; otherwise it's plain text. From Arbitrage.jsx.
const PriceCell = ({ market, itemName, price, className }) => {
    const text = `$${price?.toFixed(2)}`;
    const href = market ? getTradeonShortLink(market, itemName) : null;
    if (!href) return <span className={className}>{text}</span>;
    return (
        <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            title={`Open on ${market === 'TradeOnMarket' ? 'Tradeon' : market}`}
            onClick={(e) => e.stopPropagation()}
            className={`${className} hover:text-amber-300 hover:underline transition-colors`}
        >
            {text}
        </a>
    );
};

export default PriceCell;
