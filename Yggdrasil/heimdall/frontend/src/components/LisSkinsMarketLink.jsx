import { getLisskinsMarketListingUrl } from '../utils/lisskinsMarket';
import lisskinsIcon from '../assets/markets/lisskins.png';

export default function LisSkinsMarketLink({ itemName, className = '' }) {
    const href = getLisskinsMarketListingUrl(itemName);
    if (!href) return null;

    return (
        <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            title="View on LisSkins"
            aria-label="View on LisSkins"
            className={`inline-flex shrink-0 items-center justify-center rounded p-0.5 opacity-70 hover:opacity-100 hover:bg-white/5 transition-all ${className}`}
            onClick={(e) => e.stopPropagation()}
        >
            <img src={lisskinsIcon} alt="LisSkins" className="w-3.5 h-3.5" />
        </a>
    );
}
