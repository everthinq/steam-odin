import { useMemo } from 'react';
import { formatTradeHoldLabel } from './transferItems';

/**
 * Transfer's selection: which lines can be picked and how many, quantities per line and per float
 * or unlock-time variant, the select buttons' states and tooltips, and Select all.
 *
 * Full quantity in both directions (deposit all, withdraw all) except, in To mode, a line a Storage
 * Unit cannot hold (`unstorable`) or a trade-protected line (received in a trade): shown, never
 * selectable — the backend's plan would refuse them anyway. A plain trade hold (a Steam Market
 * purchase) does not block a Storage Unit.
 */
export const useTransferSelection = ({ selectedIds, setSelectedIds, transferMode }) => {
    // Membership set so the per-row selected-quantity scans are O(group), not O(group × selected).
    const selectedIdsSet = useMemo(() => new Set(selectedIds), [selectedIds]);

    const getIdsSelectedQty = (itemIds) => itemIds.filter((id) => selectedIdsSet.has(id)).length;
    const getGroupSelectedQty = (group) => getIdsSelectedQty(group.item_ids);

    const isHeldForDeposit = (group) => transferMode === 'to' && !!group.tradeProtected;

    const getMaxSelectableQty = (group) =>
        group.unstorable || isHeldForDeposit(group) ? 0 : group.qty;

    const getVariantMaxSelectableQty = (lineGroup, variant) =>
        lineGroup.unstorable || isHeldForDeposit(lineGroup) || isHeldForDeposit(variant) ? 0 : variant.qty;

    /** Select button tooltip; says why a line cannot be selected. */
    const selectTitle = (group, qty, what) => {
        if (group.unstorable) return "A Storage Unit can't hold this item";
        if (isHeldForDeposit(group)) {
            return `Trade protected (${formatTradeHoldLabel(group.items)} left, received in a trade) — a Storage Unit takes it once the protection ends`;
        }
        return `Select all ${qty}${what}`;
    };

    const setIdsSelectedQty = (itemIds, qty) => {
        const n = Math.max(0, Math.min(itemIds.length, Number.isFinite(qty) ? qty : 0));
        setSelectedIds((prev) => {
            const without = prev.filter((id) => !itemIds.includes(id));
            return [...without, ...itemIds.slice(0, n)];
        });
    };

    const setGroupSelectedQty = (group, qty) =>
        setIdsSelectedQty(group.item_ids, Math.min(getMaxSelectableQty(group), qty));

    const setFloatVariantSelectedQty = (lineGroup, variant, qty) =>
        setIdsSelectedQty(variant.item_ids, Math.min(getVariantMaxSelectableQty(lineGroup, variant), qty));

    const toggleGroupCheckbox = (group) => {
        const target = getMaxSelectableQty(group);
        if (target === 0) return;
        setGroupSelectedQty(group, getGroupSelectedQty(group) >= target ? 0 : target);
    };

    const toggleFloatVariantCheckbox = (lineGroup, variant) => {
        const target = getVariantMaxSelectableQty(lineGroup, variant);
        if (target === 0) return;
        setFloatVariantSelectedQty(lineGroup, variant, getIdsSelectedQty(variant.item_ids) >= target ? 0 : target);
    };

    const selectionState = (selectedCount, target) => {
        if (selectedCount === 0 || target === 0) return 'none';
        return selectedCount >= target ? 'all' : 'partial';
    };

    const groupSelectionState = (group) =>
        selectionState(getGroupSelectedQty(group), getMaxSelectableQty(group));

    const floatVariantSelectionState = (lineGroup, variant) =>
        selectionState(getIdsSelectedQty(variant.item_ids), getVariantMaxSelectableQty(lineGroup, variant));

    /** Select every given line at its full selectable quantity, in one action. */
    const selectAllLines = (lineGroups) => {
        const ids = [];
        for (const lineGroup of lineGroups) {
            const cap = getMaxSelectableQty(lineGroup);
            if (cap > 0) ids.push(...lineGroup.item_ids.slice(0, cap));
        }
        setSelectedIds(ids);
    };

    return {
        selectedIdsSet,
        getIdsSelectedQty,
        getGroupSelectedQty,
        isHeldForDeposit,
        getMaxSelectableQty,
        getVariantMaxSelectableQty,
        selectTitle,
        setGroupSelectedQty,
        setFloatVariantSelectedQty,
        toggleGroupCheckbox,
        toggleFloatVariantCheckbox,
        groupSelectionState,
        floatVariantSelectionState,
        selectAllLines,
    };
};
