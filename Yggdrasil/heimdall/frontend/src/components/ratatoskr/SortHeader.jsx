import { ArrowUp, ArrowDown, ArrowUpDown } from 'lucide-react';

// A column header that sorts with useColumnSort: ascending, then descending, then back to
// the table's own order (`resetLabel` names it in the tooltip).
const SortHeader = ({ column, label, sort, align = 'left', className = '', title, resetLabel = 'the original order' }) => {
    const active = sort.sortKey === column;
    const Icon = !active ? ArrowUpDown : sort.sortDir === 'asc' ? ArrowUp : ArrowDown;
    const next = !active ? 'sort ascending' : sort.sortDir === 'asc' ? 'sort descending' : `go back to ${resetLabel}`;
    return (
        <th className={`font-medium ${align === 'right' ? 'text-right' : 'text-left'} ${className}`}
            aria-sort={active ? (sort.sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}>
            <button type="button" onClick={() => sort.toggle(column)} title={`${title ? `${title}. ` : ''}Click to ${next}.`}
                className={`inline-flex items-center gap-1 hover:text-slate-200 ${active ? 'text-amber-300' : ''}`}>
                {label}
                <Icon size={11} className={active ? '' : 'opacity-40'} aria-hidden="true" />
            </button>
        </th>
    );
};

export default SortHeader;
