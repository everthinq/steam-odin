import { getBuffMarketListingUrl } from '../utils/buffMarket';
import buffIcon from '../assets/markets/buff.png';

export default function BuffMarketLink({ itemName, className = '' }) {
    const href = getBuffMarketListingUrl(itemName);
    if (!href) return null;

    return (
        <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            title="View on Buff163"
            aria-label="View on Buff163"
            className={`inline-flex shrink-0 items-center justify-center rounded p-0.5 opacity-70 hover:opacity-100 hover:bg-white/5 transition-all ${className}`}
            onClick={(e) => e.stopPropagation()}
        >
            <img src={buffIcon} alt="Buff163" className="w-3.5 h-3.5" />
        </a>
    );
}
