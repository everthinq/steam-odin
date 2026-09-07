import { useState, useCallback } from 'react';

/**
 * Ratatoskr-style clickable column sorting for the Draupnir tables.
 *
 * Clicking a column sorts it ascending; clicking the same column again flips to
 * descending; a third click clears the sort and restores the table's natural
 * order (newest-first for transactions, value-desc for holdings — the order the
 * backend already sends). Clicking a different column starts fresh at ascending.
 *
 * This is the multi-column generalization of Ratatoskr's single Qty-column sort,
 * paired with the SortTh header (amber-when-active, Arrow{Up,Down,UpDown} icons).
 */
export function useColumnSort() {
    const [sort, setSort] = useState({ key: null, dir: null });   // dir: 'asc' | 'desc' | null

    const toggle = useCallback((key) => {
        setSort((prev) => {
            if (prev.key !== key) return { key, dir: 'asc' };
            if (prev.dir === 'asc') return { key, dir: 'desc' };
            return { key: null, dir: null };   // third click clears back to natural order
        });
    }, []);

    return { sortKey: sort.key, sortDir: sort.dir, toggle };
}

// Compare two non-null values: numbers numerically, everything else as
// case-insensitive, number-aware strings (so "10" sorts after "2").
function compareValues(a, b) {
    if (typeof a === 'number' && typeof b === 'number') return a - b;
    return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
}

/**
 * Return a sorted COPY of `rows` (never mutates the input). `accessors` maps a
 * column key to a function reading that column's value from a row — e.g.
 * `{ total: r => r.qty * r.price }`. Blank/unknown values (null, undefined, '')
 * always sink to the bottom regardless of direction, so an unpriced holding or an
 * item with no collection never floats to the top. The sort is stable.
 */
export function sortRows(rows, sortKey, sortDir, accessors) {
    const get = sortKey && accessors[sortKey];
    if (!sortKey || !sortDir || !get) return rows;
    const dir = sortDir === 'asc' ? 1 : -1;
    return rows
        .map((row, i) => [row, i])
        .sort((a, b) => {
            const av = get(a[0]);
            const bv = get(b[0]);
            const aBlank = av == null || av === '';
            const bBlank = bv == null || bv === '';
            if (aBlank || bBlank) {
                if (aBlank && bBlank) return a[1] - b[1];   // both blank -> keep order
                return aBlank ? 1 : -1;                      // blanks last, both directions
            }
            const cmp = compareValues(av, bv);
            return cmp !== 0 ? cmp * dir : a[1] - b[1];       // stable tiebreak on original index
        })
        .map((pair) => pair[0]);
}
