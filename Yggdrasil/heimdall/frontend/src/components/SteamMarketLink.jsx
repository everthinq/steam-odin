import { getSteamMarketListingUrl } from '../utils/steamMarket';

const SteamIcon = ({ className }) => (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d="M11.979 0C5.678 0 .511 4.86.022 11.037l6.432 2.658c.545-.371 1.203-.59 1.912-.59.063 0 .125.004.188.006l2.861-4.142V8.4c0-2.485 2.015-4.5 4.5-4.5 2.485 0 4.5 2.015 4.5 4.5s-2.015 4.5-4.5 4.5h-.105l-4.148 2.861c.005.063.009.125.009.188 0 1.194-.491 2.274-1.281 3.048l2.897 4.488A11.923 11.923 0 0 0 23.98 12.001C23.98 5.386 18.594 0 11.979 0zM7.54 18.21l-1.473-.61c.262.615.612 1.17 1.036 1.643.304-.218.66-.392 1.037-.392.069 0 .137.006.204.016l-1.804-1.657zm2.85-8.854a2.857 2.857 0 0 0-2.853-2.853 2.857 2.857 0 0 0-2.853 2.853 2.857 2.857 0 0 0 2.853 2.853 2.857 2.857 0 0 0 2.853-2.853zm-1.428 0a1.429 1.429 0 1 1-2.857 0 1.429 1.429 0 0 1 2.857 0zm10.57 2.853c-1.572 0-2.853 1.28-2.853 2.853s1.281 2.853 2.853 2.853 2.853-1.28 2.853-2.853-1.281-2.853-2.853-2.853zm0 4.274c-.785 0-1.421-.636-1.421-1.421s.636-1.421 1.421-1.421 1.421.636 1.421 1.421-.636 1.421-1.421 1.421z" />
    </svg>
);

export default function SteamMarketLink({ itemName, className = '' }) {
    const href = getSteamMarketListingUrl(itemName);
    if (!href) return null;

    return (
        <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            title="View on Steam Community Market"
            aria-label="View on Steam Community Market"
            className={`inline-flex shrink-0 items-center justify-center rounded p-0.5 text-slate-500 hover:text-[#66c0f4] hover:bg-white/5 transition-colors ${className}`}
            onClick={(e) => e.stopPropagation()}
        >
            <SteamIcon className="w-3.5 h-3.5" />
        </a>
    );
}
