import { getSteamMarketListingUrl } from '../utils/steamMarket';
import { getTradeonShortLink } from '../utils/tradeonShortLink';
import steamIcon from '../assets/markets/steam.png';

// The Steam icon next to an item. `viaPulse` opens it through pulse's short link (which
// redirects to the Community Market listing), as the price links do.
export default function SteamMarketLink({ itemName, className = '', viaPulse = false }) {
    const href = viaPulse ? getTradeonShortLink('Steam', itemName) : getSteamMarketListingUrl(itemName);
    if (!href) return null;

    return (
        <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            title="View on Steam Community Market"
            aria-label="View on Steam Community Market"
            className={`inline-flex shrink-0 items-center justify-center rounded p-0.5 opacity-70 hover:opacity-100 hover:bg-white/5 transition-all ${className}`}
            onClick={(e) => e.stopPropagation()}
        >
            <img src={steamIcon} alt="Steam" className="w-3.5 h-3.5" />
        </a>
    );
}
