import { ArrowUp, ArrowDown, ArrowUpDown } from 'lucide-react';

/**
 * A sortable `<th>` for the Draupnir tables. Renders the label plus a sort arrow
 * (up when ascending, down when descending, a faint up/down when inactive) and
 * turns amber while active — mirroring Ratatoskr's sortable Qty column. Pair it
 * with `useColumnSort` + `sortRows` from ./columnSort.
 */
export default function SortTh({ col, label, sortKey, sortDir, onSort, align = 'left', className = '' }) {
    const active = sortKey === col;
    const alignCls = align === 'right' ? 'text-right' : 'text-left';
    const title = active
        ? (sortDir === 'asc'
            ? `Sorted by ${label} (ascending). Click for descending.`
            : `Sorted by ${label} (descending). Click to clear.`)
        : `Sort by ${label}`;
    return (
        <th className={`${alignCls} font-semibold px-3 py-2 ${className}`}>
            <button
                type="button"
                onClick={() => onSort(col)}
                title={title}
                className={`inline-flex items-center gap-1 align-middle hover:text-slate-200 transition-colors ${active ? 'text-amber-400' : ''}`}
            >
                {label}
                {active && sortDir === 'asc' ? <ArrowUp size={12} />
                    : active && sortDir === 'desc' ? <ArrowDown size={12} />
                        : <ArrowUpDown size={12} className="opacity-40" />}
            </button>
        </th>
    );
}
