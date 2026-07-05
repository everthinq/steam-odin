import { getCsfloatMarketListingUrl } from '../utils/csfloatMarket';
import csfloatIcon from '../assets/markets/csfloat.png';

export default function CSFloatMarketLink({ itemName, className = '' }) {
    const href = getCsfloatMarketListingUrl(itemName);
    if (!href) return null;

    return (
        <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            title="View on CSFloat"
            aria-label="View on CSFloat"
            className={`inline-flex shrink-0 items-center justify-center rounded p-0.5 opacity-70 hover:opacity-100 hover:bg-white/5 transition-all ${className}`}
            onClick={(e) => e.stopPropagation()}
        >
            <img src={csfloatIcon} alt="CSFloat" className="w-3.5 h-3.5" />
        </a>
    );
}
