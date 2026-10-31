// "Active" for one Arbitrage profile: a profile you trade on. Only the active profiles' pulse tables
// are downloaded (in the background, every few minutes, backend pulse_feed.py), and every other page
// of Heimdall reads its prices from them. An active profile opens at once from memory. From Arbitrage.jsx.
import { MB_PER_DOWNLOAD } from '../../utils/pulseTraffic';

const ActiveSwitch = ({ on, busy, minutes, warming, preview, onToggle }) => {
    const adds = preview?.adds ?? null;
    const gigabytes = adds ? (adds * (1440 / minutes) * MB_PER_DOWNLOAD) / 1024 : 0;
    const traffic = adds == null ? '' : adds === 0
        ? ' Its tables are already active for another profile, so it costs no extra traffic.'
        : ` That is ${adds} more table${adds === 1 ? '' : 's'}, about ${gigabytes.toFixed(1)} GB a day.`;
    const title = preview?.error ? `This profile cannot be made active: ${preview.error}`
        : on ? `Active: you trade on this profile. Its pulse tables are downloaded every ${minutes} minutes in the background, it opens at once with prices at most ${minutes} minutes old, and the other pages (Case Arbitrage, Harvest, Price dips...) read their prices from it. "Fetch live data" still downloads fresh prices. Click to make it inactive.`
            : `Make this profile active (one you trade on): its pulse tables are downloaded every ${minutes} minutes in the background, it opens at once, and the other pages can read its markets.${traffic}`;
    return (
        <button
            type="button"
            role="switch"
            aria-checked={on}
            onClick={onToggle}
            disabled={busy || (!on && !!preview?.error)}
            title={title}
            className={`flex items-center gap-2 px-2.5 py-1.5 rounded-lg border text-xs font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed shrink-0 ${on ? 'bg-sky-500/10 border-sky-400/40 text-sky-100 hover:bg-sky-500/20' : 'bg-black/20 border-white/10 text-slate-400 hover:text-white hover:border-white/20'}`}
        >
            <span className={`relative inline-flex h-4 w-7 shrink-0 rounded-full transition-colors ${on ? 'bg-sky-500' : 'bg-white/15'}`}>
                <span className={`absolute top-0.5 h-3 w-3 rounded-full bg-white shadow transition-transform ${on ? 'translate-x-3.5' : 'translate-x-0.5'}`} />
            </span>
            Active
            {on && <span className="font-normal text-sky-200/70 tabular-nums">{warming ? 'first download…' : `every ${minutes} min`}</span>}
        </button>
    );
};

export default ActiveSwitch;
