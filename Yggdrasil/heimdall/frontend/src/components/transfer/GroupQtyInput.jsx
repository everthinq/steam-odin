import { useState, useEffect } from 'react';

// Numeric "N / max" quantity input for a group of items, clamped to [0, max]. From Transfer.jsx.
const GroupQtyInput = ({ selectedQty, maxQty, onCommit }) => {
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
                className="w-10 h-7 text-center bg-black/40 border border-white/10 rounded text-white focus:outline-none focus:border-amber-500/50"
                aria-label={`Quantity to move out of ${maxQty}`}
            />
            <span className="text-slate-400 whitespace-nowrap font-medium">/ {maxQty}</span>
        </div>
    );
};

export default GroupQtyInput;
