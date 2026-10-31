import React from 'react';
import { UNSTORABLE_EXPLANATION } from '../../utils/showUnstorable';

/**
 * Switch that shows or hides the items a Storage Unit cannot hold. It changes the view at once,
 * so it is a switch rather than a checkbox; the count says how many items it hides.
 */
const UnstorableSwitch = ({ checked, onChange, count }) => (
    <label
        className={`inline-flex items-center gap-2 text-xs whitespace-nowrap select-none ${count === 0 ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'}`}
        title={`${UNSTORABLE_EXPLANATION} They are hidden unless this is on, and they can never be selected.`}
    >
        <button
            type="button"
            role="switch"
            aria-checked={checked}
            disabled={count === 0}
            onClick={() => onChange(!checked)}
            className={`relative inline-flex h-4 w-7 shrink-0 items-center rounded-full border transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/50 disabled:cursor-not-allowed ${checked ? 'bg-amber-500/70 border-amber-400/60' : 'bg-white/10 border-white/15'}`}
        >
            <span
                className={`inline-block h-3 w-3 rounded-full bg-white shadow transition-transform ${checked ? 'translate-x-3.5' : 'translate-x-0.5'}`}
            />
        </button>
        <span className={checked ? 'text-amber-200' : 'text-slate-400'}>
            Show items that can't be stored
            <span className="ml-1 tabular-nums text-slate-500">({count})</span>
        </span>
    </label>
);

export default UnstorableSwitch;
