import { getTradeonShortLink } from '../../utils/tradeonShortLink';

const ESTIMATE_TIP = 'Estimated, not real data: what SkinSwap Trade pays, plus the markup we measured on its Trade page.';

// A right-aligned price. When `market` is set, it links to that market's pulse
// short-link for the item; otherwise it's plain text. An `estimated` price gets
// an amber "≈" and a tooltip saying so. With `pagePrice` (SkinSwap Trade) the
// price is shown as that market's own page shows it (`pageLabel`), and `price`,
// the real-dollar value every profit uses, goes in grey under it. From Arbitrage.jsx.
const PriceCell = ({ market, itemName, price, className, estimated = false, pagePrice, pageLabel }) => {
    const onPage = pagePrice != null && pageLabel;
    const pageTip = onPage
        ? `$${pagePrice.toFixed(2)} as the ${pageLabel} shows it (Trade dollars); $${price?.toFixed(2)} in real dollars (divided by 1.4), which profit uses.`
        : '';
    const tip = [estimated ? ESTIMATE_TIP : '', pageTip].filter(Boolean).join(' ');
    const shown = onPage ? pagePrice : price;
    const amount = <>{estimated && <span className="text-amber-400/80">≈</span>}${shown?.toFixed(2)}</>;
    const text = onPage
        ? (
            <span className="inline-flex flex-col items-end leading-tight">
                <span>{amount}</span>
                <span className="text-[10px] text-slate-500">${price?.toFixed(2)} real</span>
            </span>
        )
        : amount;
    const href = market ? getTradeonShortLink(market, itemName) : null;
    if (!href) return <span className={className} title={tip || undefined}>{text}</span>;
    return (
        <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            title={`${tip ? `${tip} ` : ''}Open on ${market === 'TradeOnMarket' ? 'Tradeon' : market}`}
            onClick={(e) => e.stopPropagation()}
            className={`${className} hover:text-amber-300 hover:underline transition-colors`}
        >
            {text}
        </a>
    );
};

export default PriceCell;
