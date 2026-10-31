import { useState, useEffect } from 'react';

// Numeric "N / max" quantity input for a group of items, clamped to [0, max]. From Transfer.jsx.
// `totalQty` (default `maxQty`) is the count shown after the slash: a line that cannot be selected
// (maxQty 0, e.g. trade protected in To mode) still shows how many copies it has, the input locked.
const GroupQtyInput = ({ selectedQty, maxQty, onCommit, totalQty = maxQty }) => {
    const locked = maxQty === 0 && totalQty > 0;
    const [draft, setDraft] = useState(String(selectedQty));

    useEffect(() => {
        setDraft(String(selectedQty));
    }, [selectedQty]);

    const commit = (raw) => {
        const parsed = raw === '' ? 0 : parseInt(raw, 10);
        const n = Math.max(0, Math.min(maxQty, Number.isNaN(parsed) ? 0 : parsed));
        onCommit(n);
        setDraft(String(n));
    };

    return (
        <div className="flex items-center justify-center gap-1 text-xs tabular-nums">
            <input
                type="text"
                inputMode="numeric"
                value={draft}
                onChange={(e) => {
                    const v = e.target.value;
                    if (v === '' || /^\d+$/.test(v)) {
                        setDraft(v);
                        if (v !== '') {
                            const parsed = parseInt(v, 10);
                            if (!Number.isNaN(parsed)) onCommit(parsed);
                        }
                    }
                }}
                onBlur={() => commit(draft)}
                onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                        commit(draft);
                        e.currentTarget.blur();
                    }
                }}
                disabled={locked}
                className="w-10 h-7 text-center bg-black/40 border border-white/10 rounded text-white focus:outline-none focus:border-amber-500/50 disabled:opacity-30 disabled:cursor-not-allowed"
                aria-label={`Quantity to move out of ${maxQty}`}
            />
            <span className="text-slate-400 whitespace-nowrap font-medium">/ {totalQty}</span>
        </div>
    );
};

export default GroupQtyInput;
