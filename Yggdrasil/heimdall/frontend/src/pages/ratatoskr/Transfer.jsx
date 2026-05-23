import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useOutletContext } from 'react-router-dom';
import {
    ArrowUp,
    ArrowDown,
    ArrowUpDown,
    Search,
    Package,
    Filter,
    Download,
    Check,
    Minus,
    ChevronDown,
    Pencil,
    X,
    Layers,
} from 'lucide-react';

const CASKET_NAME_MAX_LENGTH = 20;
import storageUnitImage from '../../assets/ratatoskr/storage-unit.png';
import TransferQueueModal from '../../components/TransferQueueModal';
import TransferProgressBar from '../../components/TransferProgressBar';
import SteamMarketLink from '../../components/SteamMarketLink';
import { getItemImageUrl, getStickerImageUrl } from '../../utils/ratatoskrImages';
import {
    tagInventoryItems,
    tagCasketItems,
    groupItemsByName,
    filterItemsByQuery,
    groupItemsByCasket,
    matchesSearchQuery,
    getGroupReserveOneQty,
    pickOneItemIdPerSkin,
} from '../../utils/transferItems';

const STORAGE_CAPACITY = 1000;
const NO_COLLECTION_LABEL = 'No collection';
const TRANSFER_HEADER_BTN =
    'inline-flex items-center justify-center gap-2 h-10 px-4 rounded-lg text-sm font-medium transition-all shrink-0';

const getCasketCount = (c) => c.item_storage_total ?? 0;

const getItemCollection = (item) => {
    const name = item.item_collection?.trim();
    return name || NO_COLLECTION_LABEL;
};

const formatTradeHold = (item) => {
    if (!item.trade_unlock) return '—';
    const unlock = new Date(item.trade_unlock);
    if (unlock <= new Date()) return '—';
    const days = Math.ceil((unlock - new Date()) / (1000 * 60 * 60 * 24));
    return `${days}d`;
};

const groupTradeHold = (items) => {
    let maxDays = 0;
    for (const item of items) {
        if (!item.trade_unlock) continue;
        const unlock = new Date(item.trade_unlock);
        if (unlock <= new Date()) continue;
        const days = Math.ceil((unlock - new Date()) / (1000 * 60 * 60 * 24));
        maxDays = Math.max(maxDays, days);
    }
    return maxDays > 0 ? `${maxDays}d` : '—';
};

const PickCheckbox = ({ checked, partial = false, onChange, label }) => (
    <button
        type="button"
        role="checkbox"
        aria-checked={checked || partial}
        aria-label={label}
        onClick={(e) => {
            e.stopPropagation();
            onChange();
        }}
        className={`p-1 rounded border transition-colors shrink-0 ${checked
            ? 'bg-emerald-600/30 border-emerald-500/50 text-emerald-400'
            : partial
              ? 'bg-amber-600/20 border-amber-500/40 text-amber-400'
              : 'border-white/10 text-slate-500 hover:border-white/20'
            }`}
    >
        {checked ? (
            <Check size={14} />
        ) : partial ? (
            <Minus size={14} />
        ) : (
            <span className="block w-3.5 h-3.5" />
        )}
    </button>
);

const CollapsibleSection = ({ title, icon: Icon, open, onToggle, summary, headerRight, children }) => (
    <div className="mb-3 rounded-xl border border-white/10 bg-black/20 overflow-hidden">
        <button
            type="button"
            onClick={onToggle}
            className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-white/[0.03] transition-colors"
            aria-expanded={open}
        >
            <ChevronDown
                size={16}
                className={`shrink-0 text-slate-500 transition-transform ${open ? '' : '-rotate-90'}`}
            />
            {Icon && <Icon size={14} className="text-amber-400/80 shrink-0" />}
            <span className="text-xs font-bold tracking-widest text-slate-500 shrink-0">{title}</span>
            {!open && summary && (
                <span className="text-xs text-slate-400 truncate min-w-0">{summary}</span>
            )}
            {headerRight && <div className="ml-auto flex items-center gap-2 shrink-0">{headerRight}</div>}
        </button>
        {open && <div className="px-3 pb-3 border-t border-white/5">{children}</div>}
    </div>
);

const PricingSampleReport = ({ report, onDismiss }) => {
    if (!report?.items?.length) return null;

    const sorted = [...report.items].sort((a, b) =>
        (a.item_name || '').localeCompare(b.item_name || '')
    );

    return (
        <div className="mb-6 rounded-2xl border border-bifrost-cyan/25 bg-gradient-to-b from-bifrost-cyan/10 to-transparent overflow-hidden">
            <div className="flex items-start justify-between gap-3 px-4 py-3 border-b border-white/10 bg-black/20">
                <div className="min-w-0">
                    <h3 className="text-base font-semibold text-white">Now in your inventory</h3>
                    <p className="text-xs text-slate-400 mt-1 leading-relaxed">
                        {sorted.length} different skin{sorted.length === 1 ? '' : 's'} — we moved{' '}
                        <span className="text-bifrost-cyan">one of each</span> from storage so price
                        sites can see your full collection.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={onDismiss}
                    className="shrink-0 p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/10 transition-colors"
                    aria-label="Dismiss list"
                >
                    <X size={18} />
                </button>
            </div>
            <ul className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-2 p-3 max-h-80 overflow-y-auto custom-scrollbar">
                {sorted.map((item) => (
                    <li
                        key={item.item_id}
                        className="flex items-center gap-2.5 p-2.5 rounded-xl border border-white/5 bg-odin-blue/40 hover:bg-odin-blue/60 transition-colors"
                    >
                        <ItemThumb item={item.representative || item} />
                        <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-1 min-w-0">
                                <span className="text-sm text-white truncate">{item.item_name}</span>
                                <SteamMarketLink itemName={item.item_name} />
                            </div>
                            {item.item_wear_name && (
                                <p className="text-[11px] text-slate-500 truncate">{item.item_wear_name}</p>
                            )}
                        </div>
                    </li>
                ))}
            </ul>
        </div>
    );
};

const ItemThumb = ({ item }) => {
    const [failed, setFailed] = useState(false);
    const src = getItemImageUrl(item);

    if (!src || failed) {
        return (
            <div className="w-9 h-9 shrink-0 rounded bg-black/30 flex items-center justify-center">
                <Package size={14} className="text-slate-600" />
            </div>
        );
    }

    return (
        <img
            src={src}
            alt=""
            className="w-9 h-9 object-contain shrink-0 rounded bg-black/20"
            loading="lazy"
            referrerPolicy="no-referrer"
            onError={() => setFailed(true)}
        />
    );
};

const CollectionFilterPanel = ({
    collections,
    selected,
    search,
    onSearchChange,
    onToggle,
    onSelectAll,
    onClearSelection,
}) => {
    const filtered = useMemo(() => {
        if (!search.trim()) return collections;
        return collections.filter((c) => matchesSearchQuery([c], search));
    }, [collections, search]);

    return (
        <div className="absolute top-full left-0 mt-2 z-50 w-80 flex flex-col bg-[#1a1d24] border border-white/10 rounded-xl shadow-2xl overflow-hidden">
            <div className="px-3 py-2.5 border-b border-white/10">
                <p className="text-xs font-medium text-white">Collections</p>
                <p className="text-[10px] text-slate-500 mt-0.5">
                    {selected.length === 0
                        ? 'All collections shown. Check to narrow the list.'
                        : `Showing ${selected.length} collection${selected.length === 1 ? '' : 's'}.`}
                </p>
            </div>
            <div className="px-3 py-2 border-b border-white/5">
                <input
                    type="text"
                    placeholder="Search collections"
                    value={search}
                    onChange={(e) => onSearchChange(e.target.value)}
                    className="w-full bg-black/40 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-amber-500/40 placeholder:text-slate-600"
                />
            </div>
            <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/5 text-[10px]">
                <button
                    type="button"
                    onClick={onSelectAll}
                    disabled={filtered.length === 0}
                    className="text-slate-400 hover:text-white disabled:opacity-40"
                >
                    Select all
                </button>
                <button
                    type="button"
                    onClick={onClearSelection}
                    disabled={selected.length === 0}
                    className="text-slate-400 hover:text-white disabled:opacity-40"
                >
                    Clear
                </button>
            </div>
            <div className="overflow-y-auto custom-scrollbar max-h-56 py-1">
                {filtered.length === 0 ? (
                    <p className="text-xs text-slate-500 text-center py-6 px-3">No collections match.</p>
                ) : (
                    filtered.map((name) => {
                        const checked = selected.includes(name);
                        return (
                            <label
                                key={name}
                                className={`flex items-center gap-2.5 px-3 py-2 cursor-pointer text-xs transition-colors ${checked ? 'bg-amber-500/10 text-white' : 'text-slate-300 hover:bg-white/5'}`}
                            >
                                <input
                                    type="checkbox"
                                    checked={checked}
                                    onChange={() => onToggle(name)}
                                    className="rounded border-white/20 bg-black/40 text-amber-500 focus:ring-amber-500/40 focus:ring-offset-0"
                                />
                                <span className="truncate" title={name}>{name}</span>
                            </label>
                        );
                    })
                )}
            </div>
        </div>
    );
};

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

const RatatoskrTransfer = () => {
    const { steamid } = useOutletContext();

    const [inventory, setInventory] = useState([]);
    const [caskets, setCaskets] = useState([]);
    const [casketItems, setCasketItems] = useState([]);
    const [selectedCasketIds, setSelectedCasketIds] = useState([]);
    const [allStorageSelected, setAllStorageSelected] = useState(false);
    const [transferMode, setTransferMode] = useState(null);

    const [loading, setLoading] = useState(false);
    const [unitSearch, setUnitSearch] = useState('');
    const [itemSearch, setItemSearch] = useState('');
    const [selectedInvItems, setSelectedInvItems] = useState([]);
    const [selectedCasketItems, setSelectedCasketItems] = useState([]);
    const [successMsg, setSuccessMsg] = useState(null);
    const [pricingSampleReport, setPricingSampleReport] = useState(null);
    const [pricingSampleQueued, setPricingSampleQueued] = useState(false);
    const pricingSampleMoveRef = useRef(null);
    const [moveProgress, setMoveProgress] = useState(null);
    const [moveError, setMoveError] = useState(null);
    const [moveDelayMs, setMoveDelayMs] = useState(400);
    const [moveDelayBounds, setMoveDelayBounds] = useState({ min: 100, max: 5000 });
    const [qtySortDir, setQtySortDir] = useState(null); // null = by name, 'asc' | 'desc' = by qty
    const [queueOpen, setQueueOpen] = useState(false);
    const [filtersOpen, setFiltersOpen] = useState(false);
    const [includedCollections, setIncludedCollections] = useState([]);
    const [collectionFilterSearch, setCollectionFilterSearch] = useState('');
    const [renamingCasket, setRenamingCasket] = useState(null);
    const [renameDraft, setRenameDraft] = useState('');
    const [renameLoading, setRenameLoading] = useState(false);
    const [renameError, setRenameError] = useState(null);
    const [allCasketItems, setAllCasketItems] = useState([]);
    const [allCasketLoading, setAllCasketLoading] = useState(false);
    const [allCasketLoaded, setAllCasketLoaded] = useState(false);
    const [storageSectionOpen, setStorageSectionOpen] = useState(true);
    const filterRef = useRef(null);

    const fetchMoveDelay = async () => {
        try {
            const res = await fetch('/api/ratatoskr/config/move-delay');
            const data = await res.json();
            if (res.ok && data.delayMs != null) {
                setMoveDelayMs(data.delayMs);
                setMoveDelayBounds({ min: data.min ?? 100, max: data.max ?? 5000 });
            }
        } catch (err) {
            console.error(err);
        }
    };

    const saveMoveDelay = async (value) => {
        const parsed = parseInt(value, 10);
        if (Number.isNaN(parsed)) return;
        const clamped = Math.min(moveDelayBounds.max, Math.max(moveDelayBounds.min, parsed));
        try {
            const res = await fetch('/api/ratatoskr/config/move-delay', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ delayMs: clamped }),
            });
            const data = await res.json();
            if (res.ok && data.delayMs != null) {
                setMoveDelayMs(data.delayMs);
            }
        } catch (err) {
            console.error(err);
        }
    };

    useEffect(() => {
        fetchInventory();
        fetchCaskets();
        fetchMoveDelay();
    }, [steamid]);

    useEffect(() => {
        if (transferMode !== 'from') {
            setCasketItems([]);
            return;
        }
        if (!allStorageSelected && selectedCasketIds.length === 0) {
            setCasketItems([]);
            return;
        }
        if (allStorageSelected || selectedCasketIds.length !== 1) {
            if (!allCasketLoaded) fetchAllStorageContents();
            return;
        }
        fetchCasketContents(selectedCasketIds[0]);
    }, [allStorageSelected, selectedCasketIds, transferMode]);

    useEffect(() => {
        if (transferMode === 'from' && caskets.length > 0 && !allCasketLoaded && !allCasketLoading) {
            fetchAllStorageContents();
        }
    }, [transferMode, caskets.length]);

    useEffect(() => {
        if (!filtersOpen) return undefined;
        const onPointerDown = (e) => {
            if (filterRef.current && !filterRef.current.contains(e.target)) {
                setFiltersOpen(false);
            }
        };
        document.addEventListener('mousedown', onPointerDown);
        return () => document.removeEventListener('mousedown', onPointerDown);
    }, [filtersOpen]);

    useEffect(() => {
        if (!transferMode || allStorageSelected || selectedCasketIds.length !== 1) return;
        const casket = caskets.find((c) => c.item_id === selectedCasketIds[0]);
        if (!casket) return;
        const count = getCasketCount(casket);
        const stillValid =
            transferMode === 'to'
                ? count < STORAGE_CAPACITY
                : count >= 1 && count <= STORAGE_CAPACITY;
        if (!stillValid) setSelectedCasketIds([]);
    }, [transferMode, caskets, selectedCasketIds, allStorageSelected]);

    const fetchInventory = async () => {
        try {
            const res = await fetch(`/api/ratatoskr/inventory/${steamid}`);
            const data = await res.json();
            if (data.items) setInventory(data.items);
        } catch (err) { console.error(err); }
    };

    const fetchCaskets = async () => {
        try {
            const res = await fetch(`/api/ratatoskr/caskets/${steamid}`);
            const data = await res.json();
            if (data.caskets) setCaskets(data.caskets);
        } catch (err) { console.error(err); }
    };

    const openRenameCasket = (casket) => {
        setRenamingCasket(casket);
        setRenameDraft(casket.item_customname || '');
        setRenameError(null);
    };

    const closeRenameCasket = () => {
        if (renameLoading) return;
        setRenamingCasket(null);
        setRenameDraft('');
        setRenameError(null);
    };

    const submitRenameCasket = async () => {
        if (!renamingCasket || renameLoading) return;
        const trimmed = renameDraft.trim();
        if (trimmed.length > CASKET_NAME_MAX_LENGTH) {
            setRenameError(`Name must be ${CASKET_NAME_MAX_LENGTH} characters or less`);
            return;
        }

        setRenameLoading(true);
        setRenameError(null);

        try {
            const res = await fetch('/api/ratatoskr/casket/rename', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    steamID: steamid,
                    casketID: renamingCasket.item_id,
                    name: trimmed,
                }),
            });
            const data = await res.json();
            if (!res.ok || data.error) {
                throw new Error(data.error || 'Failed to rename storage unit');
            }

            const updated = data.casket || {
                ...renamingCasket,
                item_customname: data.custom_name || trimmed || undefined,
            };

            setCaskets((prev) =>
                prev.map((c) => (c.item_id === updated.item_id ? { ...c, ...updated } : c))
            );
            setRenamingCasket(null);
            setRenameDraft('');
            setRenameError(null);
        } catch (err) {
            console.error(err);
            setRenameError(err.message || 'Rename failed');
        } finally {
            setRenameLoading(false);
        }
    };

    const fetchCasketContents = async (casketId) => {
        setLoading(true);
        try {
            const res = await fetch(`/api/ratatoskr/casket/${steamid}/${casketId}`);
            const data = await res.json();
            if (data.items) setCasketItems(data.items);
        } catch (err) { console.error(err); }
        finally { setLoading(false); }
    };

    const fetchAllStorageContents = async () => {
        setAllCasketLoading(true);
        try {
            const units = caskets.filter((c) => getCasketCount(c) > 0);
            const chunks = await Promise.all(
                units.map(async (c) => {
                    try {
                        const res = await fetch(`/api/ratatoskr/casket/${steamid}/${c.item_id}`);
                        const data = await res.json();
                        return tagCasketItems(data.items || [], c.item_id, casketDisplayName(c));
                    } catch (err) {
                        console.error(`Failed to load ${c.item_id}`, err);
                        return [];
                    }
                })
            );
            const flat = chunks.flat();
            setAllCasketItems(flat);
            setAllCasketLoaded(true);
            return flat;
        } catch (err) {
            console.error(err);
            return [];
        } finally {
            setAllCasketLoading(false);
        }
    };

    const invalidateAllStorageCache = () => {
        setAllCasketLoaded(false);
        setAllCasketItems([]);
    };

    const hasStorageSelection = allStorageSelected || selectedCasketIds.length > 0;

    const isCasketSelected = (casketId) =>
        allStorageSelected || selectedCasketIds.includes(casketId);

    const getCasketById = (casketId) => caskets.find((c) => c.item_id === casketId);

    const primarySelectedCasket =
        selectedCasketIds.length === 1 ? getCasketById(selectedCasketIds[0]) : null;

    const pickCasket = (casket) => {
        if (transferMode === 'from') {
            setAllStorageSelected(false);
            setSelectedCasketIds((prev) =>
                prev.includes(casket.item_id)
                    ? prev.filter((id) => id !== casket.item_id)
                    : [...prev, casket.item_id]
            );
            return;
        }
        setAllStorageSelected(false);
        setSelectedCasketIds([casket.item_id]);
    };

    const pickAllStorage = () => {
        if (transferMode === 'from') {
            if (allStorageSelected) {
                setAllStorageSelected(false);
                setSelectedCasketItems([]);
            } else {
                setAllStorageSelected(true);
                setSelectedCasketIds([]);
                setSelectedCasketItems([]);
            }
            return;
        }
        setAllStorageSelected(true);
        setSelectedCasketIds([]);
    };

    const selectMode = (mode) => {
        setTransferMode(mode);
        setSelectedCasketIds([]);
        setAllStorageSelected(false);
        setSelectedInvItems([]);
        setSelectedCasketItems([]);
        setPricingSampleQueued(false);
        pricingSampleMoveRef.current = null;
        setCasketItems([]);
        invalidateAllStorageCache();
        setItemSearch('');
        setQueueOpen(false);
        setIncludedCollections([]);
        setCollectionFilterSearch('');
        setFiltersOpen(false);
        setStorageSectionOpen(true);
    };

    const clearItemFilters = () => {
        setItemSearch('');
        setIncludedCollections([]);
        setCollectionFilterSearch('');
        setFiltersOpen(false);
    };

    const toggleCollectionFilter = (name) => {
        setIncludedCollections((prev) =>
            prev.includes(name) ? prev.filter((c) => c !== name) : [...prev, name]
        );
    };

    const clearQueue = () => {
        setSelectedInvItems([]);
        setSelectedCasketItems([]);
    };

    const refreshAfterMove = async () => {
        invalidateAllStorageCache();
        await fetchInventory();
        await fetchCaskets();
        if (allStorageSelected || selectedCasketIds.length !== 1) {
            await fetchAllStorageContents();
        } else if (selectedCasketIds.length === 1) {
            await fetchCasketContents(selectedCasketIds[0]);
        }
    };

    const pollMoveStatus = () =>
        new Promise((resolve, reject) => {
            const poll = async () => {
                try {
                    const res = await fetch(`/api/ratatoskr/move/status/${steamid}`);
                    const data = await res.json();
                    if (data.error) {
                        reject(new Error(data.error));
                        return;
                    }
                    setMoveProgress(data);
                    if (!data.running && data.pending === 0) {
                        resolve(data);
                        return;
                    }
                    setTimeout(poll, 250);
                } catch (err) {
                    reject(err);
                }
            };
            poll();
        });

    const queueBatchMove = async (itemIDs, casketID, source, target) => {
        const res = await fetch('/api/ratatoskr/move/batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                steamID: steamid,
                itemIDs,
                casketID,
                source,
                target,
            }),
        });
        const data = await res.json();
        if (!res.ok || data.error) {
            throw new Error(data.error || 'Failed to queue moves');
        }
        setMoveProgress(data);
        return pollMoveStatus();
    };

    const handleMove = async () => {
        if (!hasStorageSelection || !transferMode || moveProgress?.running) return;
        if (transferMode === 'to' && selectedCasketIds.length !== 1) return;
        const itemsToMove = transferMode === 'to' ? selectedInvItems : selectedCasketItems;
        if (itemsToMove.length === 0) return;

        const source = transferMode === 'to' ? 'inventory' : 'casket';
        const target = transferMode === 'to' ? 'casket' : 'inventory';

        setLoading(true);
        setMoveError(null);
        setSuccessMsg(null);
        setPricingSampleReport(null);
        setPricingSampleQueued(false);
        setMoveProgress({ running: true, done: 0, failed: 0, total: itemsToMove.length, pending: itemsToMove.length });

        try {
            let totalDone = 0;
            let totalFailed = 0;

            if (transferMode === 'from') {
                const pool =
                    allCasketItems.length > 0
                        ? allCasketItems
                        : tagCasketItems(
                              casketItems,
                              selectedCasketIds[0],
                              primarySelectedCasket
                                  ? casketDisplayName(primarySelectedCasket)
                                  : ''
                          );
                const byCasket = groupItemsByCasket(itemsToMove, pool);
                for (const [casketID, itemIDs] of byCasket) {
                    const status = await queueBatchMove(itemIDs, casketID, source, target);
                    totalDone += status.done || 0;
                    totalFailed += status.failed || 0;
                }
            } else {
                const status = await queueBatchMove(
                    itemsToMove,
                    selectedCasketIds[0],
                    source,
                    target
                );
                totalDone = status.done || 0;
                totalFailed = status.failed || 0;
            }

            const sampleItems = pricingSampleMoveRef.current;

            if (totalFailed > 0) {
                setMoveError(`${totalDone} moved, ${totalFailed} failed. Check console for details.`);
                pricingSampleMoveRef.current = null;
            } else {
                if (sampleItems?.length) {
                    setPricingSampleReport({ items: sampleItems });
                    setSuccessMsg(
                        `Moved ${totalDone} skin${totalDone === 1 ? '' : 's'} to your inventory — see the list below.`
                    );
                    pricingSampleMoveRef.current = null;
                } else {
                    setSuccessMsg(`Moved ${totalDone} items successfully!`);
                    setTimeout(() => setSuccessMsg(null), 5000);
                }
            }

            setSelectedInvItems([]);
            setSelectedCasketItems([]);
            setQueueOpen(false);

            await new Promise((r) => setTimeout(r, 1500));
            await refreshAfterMove();
        } catch (err) {
            console.error(err);
            setMoveError(err.message || 'Transfer failed');
        } finally {
            setLoading(false);
            setTimeout(() => setMoveProgress(null), 2000);
        }
    };

    const exportItems = () => {
        const rows = sortedGroupedItems.map((group) => ({
            name: group.item_name,
            storage: group.storage_unit_name || '',
            wear: group.item_wear_name || '',
            collection: group.item_collection || '',
            tradehold: groupTradeHold(group.items),
            qty: group.qty,
        }));
        const withStorage = showStorageColumn;
        const header = withStorage
            ? 'Name,Storage,Wear,Collection,Tradehold,Qty\n'
            : 'Name,Wear,Collection,Tradehold,Qty\n';
        const body = rows
            .map((r) => {
                const cols = withStorage
                    ? [r.name, r.storage, r.wear, r.collection, r.tradehold, r.qty]
                    : [r.name, r.wear, r.collection, r.tradehold, r.qty];
                return cols.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',');
            })
            .join('\n');
        const blob = new Blob([header + body], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `ratatoskr-transfer-${transferMode || 'items'}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    };

    const casketDisplayName = (c) => c.item_customname || c.item_name || 'Storage Unit';

    const matchesMode = (c) => {
        const count = getCasketCount(c);
        if (transferMode === 'to') return count < STORAGE_CAPACITY;
        if (transferMode === 'from') return count >= 1 && count <= STORAGE_CAPACITY;
        return false;
    };

    const allStorageTotal = useMemo(
        () => caskets.reduce((sum, c) => sum + getCasketCount(c), 0),
        [caskets]
    );

    const showStorageColumn =
        transferMode === 'from' &&
        (allStorageSelected || selectedCasketIds.length > 1);

    const rawItems = useMemo(() => {
        if (transferMode === 'to') {
            return tagInventoryItems(inventory.filter((i) => i.def_index !== 1201));
        }
        if (allStorageSelected) return allCasketItems;
        if (selectedCasketIds.length === 0) return [];
        if (selectedCasketIds.length === 1 && allCasketItems.length === 0) {
            const casket = getCasketById(selectedCasketIds[0]);
            return tagCasketItems(
                casketItems,
                selectedCasketIds[0],
                casket ? casketDisplayName(casket) : ''
            );
        }
        const idSet = new Set(selectedCasketIds);
        return allCasketItems.filter((item) => idSet.has(item.storage_unit_id));
    }, [
        transferMode,
        inventory,
        casketItems,
        allCasketItems,
        allStorageSelected,
        selectedCasketIds,
        caskets,
    ]);

    const availableCollections = useMemo(() => {
        const names = new Set();
        for (const item of rawItems) {
            names.add(getItemCollection(item));
        }
        return Array.from(names).sort((a, b) => a.localeCompare(b));
    }, [rawItems]);

    const displayItems = useMemo(() => {
        let items = rawItems;
        if (includedCollections.length > 0) {
            const allowed = new Set(includedCollections);
            items = items.filter((item) => allowed.has(getItemCollection(item)));
        }
        return filterItemsByQuery(items, itemSearch);
    }, [rawItems, itemSearch, includedCollections]);

    const activeFilterCount =
        (itemSearch.trim() ? 1 : 0) + (includedCollections.length > 0 ? 1 : 0);

    const groupedItems = useMemo(
        () => groupItemsByName(displayItems, { includeStorage: showStorageColumn }),
        [displayItems, showStorageColumn]
    );

    const sortedGroupedItems = useMemo(() => {
        if (!qtySortDir) return groupedItems;
        const groups = [...groupedItems];
        groups.sort((a, b) => (qtySortDir === 'asc' ? a.qty - b.qty : b.qty - a.qty));
        return groups;
    }, [groupedItems, qtySortDir]);

    const toggleQtySort = () => {
        setQtySortDir((prev) => {
            if (prev === null) return 'asc';
            return prev === 'asc' ? 'desc' : 'asc';
        });
    };

    const filteredCaskets = caskets.filter(
        (c) => matchesSearchQuery([casketDisplayName(c)], unitSearch) && matchesMode(c)
    );

    const selectedIds = transferMode === 'to' ? selectedInvItems : selectedCasketItems;
    const setSelectedIds = transferMode === 'to' ? setSelectedInvItems : setSelectedCasketItems;

    const getGroupSelectedQty = (group) =>
        group.item_ids.filter(id => selectedIds.includes(id)).length;

    /** Full qty when withdrawing from storage; keep 1 in inventory when depositing (To). */
    const getMaxSelectableQty = (group) =>
        transferMode === 'to' ? getGroupReserveOneQty(group) : group.qty;

    const setGroupSelectedQty = (group, qty) => {
        const cap = getMaxSelectableQty(group);
        const n = Math.max(0, Math.min(cap, Number.isFinite(qty) ? qty : 0));
        setSelectedIds((prev) => {
            const without = prev.filter((id) => !group.item_ids.includes(id));
            return [...without, ...group.item_ids.slice(0, n)];
        });
    };

    const toggleGroupCheckbox = (group) => {
        const current = getGroupSelectedQty(group);
        const target = getMaxSelectableQty(group);
        if (target === 0) return;
        setGroupSelectedQty(group, current >= target ? 0 : target);
    };

    const groupSelectionState = (group) => {
        const selectedCount = getGroupSelectedQty(group);
        const target = getMaxSelectableQty(group);
        if (selectedCount === 0 || target === 0) return 'none';
        if (selectedCount >= target) return 'all';
        return 'partial';
    };

    const selectOnePerSkinForArbitrage = async () => {
        setMoveError(null);
        setSuccessMsg(null);
        setTransferMode('from');
        setAllStorageSelected(true);
        setSelectedCasketIds([]);
        setSelectedInvItems([]);

        let items = allCasketItems;
        if (!allCasketLoaded || items.length === 0) {
            items = await fetchAllStorageContents();
        }

        const ids = pickOneItemIdPerSkin(items);
        if (ids.length === 0) {
            setMoveError('No movable items found in storage units.');
            return;
        }

        const idSet = new Set(ids);
        pricingSampleMoveRef.current = items
            .filter((item) => idSet.has(item.item_id))
            .map((item) => ({
                item_id: item.item_id,
                item_name: item.item_name,
                item_wear_name: item.item_wear_name,
                representative: item,
            }));

        setSelectedCasketItems(ids);
        setQueueOpen(true);
        setPricingSampleQueued(true);
        setPricingSampleReport(null);
        setSuccessMsg(null);
    };

    const queueEntries = useMemo(() => {
        const fromGroups = sortedGroupedItems
            .map((group) => ({
                key: group.key,
                item_name: group.item_name,
                representative: group.representative,
                storageName: group.storage_unit_name,
                group,
                queueQty: group.item_ids.filter((id) => selectedIds.includes(id)).length,
            }))
            .filter((e) => e.queueQty > 0);

        if (fromGroups.length > 0 || selectedIds.length === 0) {
            return fromGroups;
        }

        const pool =
            allCasketItems.length > 0
                ? allCasketItems
                : casketItems.length > 0
                  ? casketItems
                  : [];

        return selectedIds
            .map((id) => {
                const item = pool.find((i) => i.item_id === id);
                if (!item) {
                    return {
                        key: String(id),
                        item_name: 'Loading…',
                        representative: null,
                        storageName: '',
                        group: null,
                        queueQty: 1,
                    };
                }
                return {
                    key: String(id),
                    item_name: item.item_name,
                    representative: item,
                    storageName: item.storage_unit_name || '',
                    group: null,
                    queueQty: 1,
                };
            });
    }, [sortedGroupedItems, selectedIds, allCasketItems, casketItems]);

    const slotsLeft =
        primarySelectedCasket && transferMode === 'to'
            ? Math.max(0, STORAGE_CAPACITY - getCasketCount(primarySelectedCasket))
            : null;

    const listLoading =
        loading ||
        ((allStorageSelected || selectedCasketIds.length > 1) &&
            allCasketLoading &&
            rawItems.length === 0);

    const isMoving = moveProgress?.running;

    const selectedStorageLabel = useMemo(() => {
        if (allStorageSelected) return 'All storage units';
        if (selectedCasketIds.length === 0) return 'None selected';
        if (selectedCasketIds.length === 1) {
            const c = getCasketById(selectedCasketIds[0]);
            return c ? casketDisplayName(c) : '1 storage unit';
        }
        return `${selectedCasketIds.length} storage units`;
    }, [allStorageSelected, selectedCasketIds, caskets]);

    const emptyUnitsMessage = () => {
        if (transferMode === 'to') {
            return caskets.length === 0
                ? 'No storage units found in your inventory.'
                : 'All storage units are full (1,000 items).';
        }
        return caskets.length === 0
            ? 'No storage units found in your inventory.'
            : 'No storage units contain items.';
    };

    const mainEmptyMessage = () => {
        if (!transferMode) return 'Choose To or From to see available storage units';
        if (!hasStorageSelection) {
            return transferMode === 'to'
                ? 'Pick a storage unit to deposit items into'
                : 'Pick one or more storage units to withdraw items from';
        }
        if (
            (allStorageSelected || selectedCasketIds.length > 1) &&
            allCasketLoading &&
            rawItems.length === 0
        ) {
            return null;
        }
        return null;
    };

    const tableGridCols = showStorageColumn
        ? 'grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_72px_64px_72px]'
        : 'grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_72px_64px_72px]';

    const itemListPanel = (
        <div className="flex-1 flex flex-col min-h-0 bg-odin-blue/30 border border-white/5 rounded-2xl overflow-hidden">
            {/* Toolbar */}
            <div className="flex flex-wrap items-center gap-3 px-4 py-3 border-b border-white/5 bg-black/20">
                <div className="relative" ref={filterRef}>
                    <button
                        type="button"
                        onClick={() => setFiltersOpen((open) => !open)}
                        disabled={availableCollections.length === 0}
                        className={`flex items-center gap-1.5 text-xs border rounded-lg px-2.5 py-1.5 transition-colors disabled:opacity-40 ${activeFilterCount > 0
                            ? 'border-amber-500/40 text-amber-300 bg-amber-500/10 hover:bg-amber-500/15'
                            : 'text-slate-400 border-white/10 hover:text-white hover:bg-white/5'
                            }`}
                        title="Filter by collection"
                        aria-expanded={filtersOpen}
                    >
                        <Filter size={14} />
                        {activeFilterCount} Filter{activeFilterCount === 1 ? '' : 's'}
                    </button>
                    {filtersOpen && availableCollections.length > 0 && (
                        <CollectionFilterPanel
                            collections={availableCollections}
                            selected={includedCollections}
                            search={collectionFilterSearch}
                            onSearchChange={setCollectionFilterSearch}
                            onToggle={toggleCollectionFilter}
                            onSelectAll={() => {
                                const visible = collectionFilterSearch.trim()
                                    ? availableCollections.filter((c) =>
                                          matchesSearchQuery([c], collectionFilterSearch)
                                      )
                                    : availableCollections;
                                setIncludedCollections((prev) => Array.from(new Set([...prev, ...visible])));
                            }}
                            onClearSelection={() => setIncludedCollections([])}
                        />
                    )}
                </div>
                <button
                    type="button"
                    onClick={clearItemFilters}
                    className="text-xs text-slate-400 hover:text-white transition-colors"
                >
                    Clear All
                </button>
                <div className="relative flex-1 min-w-[140px] max-w-xs">
                    <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" size={14} />
                    <input
                        type="text"
                        placeholder="Search items (e.g. famas half sleeve fn)"
                        value={itemSearch}
                        onChange={(e) => setItemSearch(e.target.value)}
                        className="w-full bg-black/30 border border-white/10 rounded-lg pl-8 pr-3 py-1.5 text-sm text-white focus:outline-none focus:border-amber-500/40"
                    />
                </div>

                <div className="flex items-center gap-4 ml-auto text-xs">
                    {slotsLeft !== null && transferMode === 'to' && (
                        <span className="text-amber-400/90 font-medium whitespace-nowrap">
                            {slotsLeft} LEFT
                        </span>
                    )}
                    <button
                        type="button"
                        onClick={() => setQueueOpen(true)}
                        disabled={selectedIds.length === 0}
                        className="text-slate-400 hover:text-white whitespace-nowrap disabled:opacity-40 transition-colors"
                        title="Open transfer queue"
                    >
                        <span className="text-white font-medium">{selectedIds.length}</span> ITEMS
                    </button>
                    <button
                        type="button"
                        onClick={exportItems}
                        disabled={sortedGroupedItems.length === 0}
                        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-white/10 text-slate-300 hover:bg-white/5 disabled:opacity-30 text-xs"
                    >
                        <Download size={14} />
                        Export
                    </button>
                </div>
            </div>

            {/* Table header */}
            <div className={`grid ${tableGridCols} gap-2 px-4 py-2 border-b border-white/5 text-[10px] font-bold tracking-wider text-slate-500 uppercase bg-black/10`}>
                <span>Name</span>
                {showStorageColumn && <span>Storage</span>}
                <span>Stickers</span>
                <span>Collection</span>
                <span>Tradehold</span>
                <button
                    type="button"
                    onClick={toggleQtySort}
                    className={`flex items-center justify-center gap-0.5 mx-auto hover:text-white transition-colors ${qtySortDir ? 'text-amber-400' : ''}`}
                    title={qtySortDir === 'asc' ? 'Sorted by qty (low → high). Click for high → low.' : qtySortDir === 'desc' ? 'Sorted by qty (high → low). Click for low → high.' : 'Sort by quantity'}
                >
                    Qty
                    {qtySortDir === 'asc' ? (
                        <ArrowUp size={12} />
                    ) : qtySortDir === 'desc' ? (
                        <ArrowDown size={12} />
                    ) : (
                        <ArrowUpDown size={12} className="opacity-50" />
                    )}
                </button>
                <span
                    className="text-right"
                    title={
                        transferMode === 'to'
                            ? 'Select all but one (keeps 1 in inventory for arbitrage)'
                            : 'Select all items in this row'
                    }
                >
                    {transferMode === 'to' ? 'Select (−1)' : 'Select'}
                </span>
            </div>

            <div className="flex-1 overflow-y-auto custom-scrollbar">
                {listLoading && sortedGroupedItems.length === 0 ? (
                    <div className="flex justify-center p-12">
                        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
                    </div>
                ) : sortedGroupedItems.length === 0 ? (
                    <p className="text-sm text-slate-500 text-center py-12">
                        {activeFilterCount > 0 ? 'No items match your filters.' : 'No items to show.'}
                    </p>
                ) : (
                    sortedGroupedItems.map(group => {
                        const sel = groupSelectionState(group);
                        const selectedQty = getGroupSelectedQty(group);
                        return (
                            <div
                                key={group.key}
                                className={`grid ${tableGridCols} gap-2 items-center px-4 py-2 border-b border-white/5 transition-colors ${sel !== 'none' ? 'bg-amber-500/10' : 'hover:bg-white/5'}`}
                            >
                                <div className="flex items-center gap-2 min-w-0">
                                    <ItemThumb item={group.representative} />
                                    <div className="min-w-0">
                                        <div className="flex items-center gap-1.5 min-w-0">
                                            <span className="text-sm text-white truncate min-w-0">
                                                {group.item_name}
                                            </span>
                                            <SteamMarketLink itemName={group.item_name} />
                                        </div>
                                        {group.item_wear_name && (
                                            <p className="text-[11px] text-slate-500 truncate">{group.item_wear_name}</p>
                                        )}
                                    </div>
                                </div>

                                {showStorageColumn && (
                                    <span className="text-xs text-slate-400 truncate" title={group.storage_unit_name}>
                                        {group.storage_unit_name || '—'}
                                    </span>
                                )}

                                <div className="flex items-center gap-0.5 flex-wrap min-h-[24px]">
                                    {(group.stickers || []).slice(0, 5).map((s, i) => {
                                        const src = getStickerImageUrl(s);
                                        return src ? (
                                            <img
                                                key={i}
                                                src={src}
                                                alt={s.sticker_name}
                                                title={s.sticker_name}
                                                className="w-5 h-5 object-contain"
                                                loading="lazy"
                                                referrerPolicy="no-referrer"
                                            />
                                        ) : null;
                                    })}
                                </div>

                                <span className="text-xs text-slate-400 truncate">
                                    {group.item_collection || '—'}
                                </span>

                                <span className="text-xs text-slate-400">
                                    {groupTradeHold(group.items)}
                                </span>

                                <GroupQtyInput
                                    selectedQty={selectedQty}
                                    maxQty={getMaxSelectableQty(group)}
                                    onCommit={(n) => setGroupSelectedQty(group, n)}
                                />

                                <div className="flex justify-end">
                                    <button
                                        type="button"
                                        onClick={() => toggleGroupCheckbox(group)}
                                        disabled={getMaxSelectableQty(group) === 0}
                                        title={
                                            transferMode === 'to'
                                                ? getMaxSelectableQty(group) === 0
                                                    ? 'Only one copy in inventory'
                                                    : `Select ${getMaxSelectableQty(group)} of ${group.qty} (keep 1 in inventory)`
                                                : `Select all ${group.qty}`
                                        }
                                        className={`p-1 rounded border transition-colors disabled:opacity-30 disabled:cursor-not-allowed ${sel === 'all'
                                            ? 'bg-emerald-600/30 border-emerald-500/50 text-emerald-400'
                                            : sel === 'partial'
                                                ? 'bg-amber-600/20 border-amber-500/40 text-amber-400'
                                                : 'border-white/10 text-slate-500 hover:border-white/20'
                                            }`}
                                    >
                                        {sel === 'all' ? (
                                            <Check size={14} />
                                        ) : sel === 'partial' ? (
                                            <Minus size={14} />
                                        ) : (
                                            <span className="block w-3.5 h-3.5" />
                                        )}
                                    </button>
                                </div>
                            </div>
                        );
                    })
                )}
            </div>
        </div>
    );

    return (
        <div className="h-[calc(100vh-6rem)] flex flex-col">
            <div className="flex justify-between items-center mb-6">
                <h1 className="text-3xl font-bold text-white font-serif">Transfer</h1>
                <div className="flex items-center gap-2">
                    {selectedIds.length > 0 && hasStorageSelection && transferMode && (
                        <div className="relative">
                            <button
                                type="button"
                                onClick={() => setQueueOpen((open) => !open)}
                                className={`${TRANSFER_HEADER_BTN} ring-2 ${queueOpen
                                    ? 'bg-[#1a1d24] ring-white/30'
                                    : 'bg-[#1a1d24] ring-white/15 hover:bg-[#22262f]'
                                    }`}
                                aria-label="Open transfer queue"
                                aria-expanded={queueOpen}
                            >
                                <span className="text-white tabular-nums">
                                    {selectedIds.length}
                                </span>
                                <img
                                    src={storageUnitImage}
                                    alt=""
                                    className="h-4 w-4 shrink-0 object-contain opacity-90"
                                />
                                <ChevronDown
                                    size={16}
                                    strokeWidth={2}
                                    className={`shrink-0 text-slate-400 transition-transform ${queueOpen ? 'rotate-180' : ''}`}
                                />
                            </button>
                            <TransferQueueModal
                                anchored
                                isOpen={queueOpen}
                                onClose={() => setQueueOpen(false)}
                                transferMode={transferMode}
                                storageName={selectedStorageLabel}
                                entries={queueEntries}
                                totalItems={selectedIds.length}
                                onClearAll={clearQueue}
                                onRemoveEntry={(entry) => {
                                    if (entry.group) {
                                        setGroupSelectedQty(entry.group, 0);
                                    } else {
                                        setSelectedCasketItems((prev) =>
                                            prev.filter((id) => String(id) !== String(entry.key))
                                        );
                                    }
                                }}
                                onMove={handleMove}
                                isMoving={isMoving}
                                moveProgress={moveProgress}
                                moveDelayMs={moveDelayMs}
                                moveDelayBounds={moveDelayBounds}
                                onDelayChange={setMoveDelayMs}
                                onDelaySave={saveMoveDelay}
                            />
                        </div>
                    )}
                    <button
                        type="button"
                        onClick={() => selectMode('to')}
                        className={`${TRANSFER_HEADER_BTN} ${transferMode === 'to'
                            ? 'bg-emerald-600 text-white ring-2 ring-emerald-400/50'
                            : 'bg-emerald-600/20 text-emerald-300 hover:bg-emerald-600/40 border border-emerald-600/30'
                            }`}
                    >
                        <ArrowUp size={16} strokeWidth={2} className="shrink-0" />
                        To
                    </button>
                    <button
                        type="button"
                        onClick={() => selectMode('from')}
                        className={`${TRANSFER_HEADER_BTN} ${transferMode === 'from'
                            ? 'bg-white/20 text-white ring-2 ring-white/30 border border-white/20'
                            : 'bg-white/5 text-slate-300 hover:bg-white/10 border border-white/10'
                            }`}
                    >
                        <ArrowDown size={16} strokeWidth={2} className="shrink-0" />
                        From
                    </button>
                    {transferMode === 'from' && allStorageTotal > 0 && (
                        <button
                            type="button"
                            onClick={selectOnePerSkinForArbitrage}
                            disabled={allCasketLoading || isMoving}
                            className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-medium border border-bifrost-cyan/30 bg-bifrost-cyan/10 text-bifrost-cyan hover:bg-bifrost-cyan/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                            title="Pick one copy of every skin from all storage units and move them to your inventory so price websites can see your items"
                        >
                            <Layers size={14} className="shrink-0" />
                            Move one of each to inventory
                        </button>
                    )}
                </div>
            </div>

            {transferMode && (
                <p className="text-xs text-slate-500 -mt-4 mb-4">
                    {transferMode === 'to'
                        ? 'Showing storage units with room (0–999 items). Row select keeps 1 copy in your inventory.'
                        : 'Showing storage units with items (1–1,000 items).'}
                </p>
            )}


            {moveProgress && !queueOpen && (
                <TransferProgressBar
                    moveProgress={moveProgress}
                    className="mb-4 bg-black/30 border border-white/10 rounded-lg px-4 py-3"
                    labelDone="Transfer complete"
                />
            )}

            {moveError && (
                <div className="mb-4 bg-red-500/20 border border-red-500/30 text-red-300 px-4 py-2 rounded-lg text-sm text-center">
                    {moveError}
                </div>
            )}

            {successMsg && (
                <div className="mb-4 bg-emerald-500/20 border border-emerald-500/30 text-emerald-300 px-4 py-2 rounded-lg text-sm text-center">
                    {successMsg}
                </div>
            )}

            {pricingSampleQueued && selectedIds.length > 0 && transferMode === 'from' && (
                <div className="mb-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 px-4 py-3 rounded-xl border border-amber-500/40 bg-amber-500/10">
                    <p className="text-sm text-amber-100">
                        <span className="font-semibold text-white tabular-nums">{selectedIds.length}</span>{' '}
                        skins queued — one of each will go to your inventory.
                    </p>
                    <div className="flex items-center gap-2 shrink-0">
                        <button
                            type="button"
                            onClick={() => setQueueOpen(true)}
                            className="px-3 py-2 rounded-lg text-xs font-medium border border-white/15 text-slate-200 hover:bg-white/5 transition-colors"
                        >
                            View list
                        </button>
                        <button
                            type="button"
                            onClick={handleMove}
                            disabled={isMoving || !hasStorageSelection}
                            className="px-4 py-2 rounded-lg text-sm font-semibold bg-gradient-to-r from-amber-600 to-amber-700 hover:from-amber-500 hover:to-amber-600 text-white shadow-lg shadow-amber-900/20 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
                        >
                            {isMoving ? 'Moving…' : `Move now (${selectedIds.length})`}
                        </button>
                    </div>
                </div>
            )}

            <PricingSampleReport
                report={pricingSampleReport}
                onDismiss={() => {
                    setPricingSampleReport(null);
                    setSuccessMsg(null);
                    setPricingSampleQueued(false);
                }}
            />

            <CollapsibleSection
                title="STORAGE UNITS"
                icon={Package}
                open={storageSectionOpen}
                onToggle={() => setStorageSectionOpen((o) => !o)}
                summary={transferMode ? selectedStorageLabel : 'Select To or From above'}
            >
                {!transferMode ? (
                    <p className="text-sm text-slate-500 py-2 pt-2">Select To or From above to list storage units.</p>
                ) : (
                    <>
                <div className="flex flex-wrap items-center gap-3 pt-2 pb-2">
                    <div className="relative flex-1 min-w-[160px] max-w-sm">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={14} />
                        <input
                            type="text"
                            placeholder="Search storage units"
                            value={unitSearch}
                            onChange={(e) => setUnitSearch(e.target.value)}
                            className="w-full bg-black/30 border border-white/10 rounded-lg pl-9 pr-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500/40 placeholder:text-slate-600"
                        />
                    </div>
                    {primarySelectedCasket && !allStorageSelected && selectedCasketIds.length === 1 && (
                        <button
                            type="button"
                            onClick={() => openRenameCasket(primarySelectedCasket)}
                            className="flex items-center gap-1.5 shrink-0 px-2.5 py-2 text-xs text-slate-400 border border-white/10 rounded-lg hover:text-white hover:bg-white/5 transition-colors"
                            title="Rename selected storage unit"
                        >
                            <Pencil size={14} />
                            Rename
                        </button>
                    )}
                </div>

                {filteredCaskets.length === 0 && !(transferMode === 'from' && allStorageTotal > 0) ? (
                    <p className="text-sm text-slate-500 py-2">
                        {unitSearch ? 'No storage units match your search.' : emptyUnitsMessage()}
                    </p>
                ) : (
                    <div
                        className="max-h-56 overflow-y-auto custom-scrollbar rounded-xl border border-white/5 bg-black/10 p-2"
                        role="list"
                        aria-label="Storage units"
                    >
                        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-2">
                        {transferMode === 'from' && allStorageTotal > 0 && (
                            <div
                                role="listitem"
                                className={`flex items-center gap-1 w-full rounded-lg border transition-all col-span-full sm:col-span-2 xl:col-span-3 ${allStorageSelected
                                    ? 'border-amber-500/50 bg-amber-500/10'
                                    : 'border-white/10 bg-black/20'
                                    }`}
                            >
                                <div className="pl-2 shrink-0">
                                    <PickCheckbox
                                        checked={allStorageSelected}
                                        label="Select all storage units"
                                        onChange={pickAllStorage}
                                    />
                                </div>
                                <button
                                    type="button"
                                    onClick={pickAllStorage}
                                    className="flex items-center gap-3 flex-1 min-w-0 px-3 py-2.5 text-left hover:bg-white/5 rounded-lg transition-colors"
                                >
                                    <div className="w-10 h-10 rounded-lg bg-amber-500/10 border border-amber-500/20 flex items-center justify-center shrink-0">
                                        <Layers size={20} className="text-amber-400" />
                                    </div>
                                    <div className="min-w-0 flex-1">
                                        <p className="text-sm font-medium text-white">All storage units</p>
                                        <p className="text-xs text-slate-400">
                                            {allStorageTotal} items across {caskets.filter((c) => getCasketCount(c) > 0).length} units
                                            {allCasketLoading && ' · loading…'}
                                        </p>
                                    </div>
                                </button>
                            </div>
                        )}
                        {filteredCaskets.map(c => {
                            const isSelected = isCasketSelected(c.item_id);
                            const name = casketDisplayName(c);
                            const count = getCasketCount(c);
                            return (
                                <div
                                    key={c.item_id}
                                    role="listitem"
                                    className={`flex items-center gap-1 w-full rounded-lg border transition-all ${isSelected
                                        ? 'border-amber-500/50 bg-amber-500/10'
                                        : 'border-white/10 bg-black/20'
                                        }`}
                                >
                                    <div className="pl-2 shrink-0">
                                        <PickCheckbox
                                            checked={isSelected}
                                            label={`Select ${name}`}
                                            onChange={() => pickCasket(c)}
                                        />
                                    </div>
                                    <button
                                        type="button"
                                        onClick={() => pickCasket(c)}
                                        className="flex items-center gap-3 flex-1 min-w-0 px-3 py-2.5 text-left hover:bg-white/5 rounded-lg transition-colors"
                                    >
                                        <img
                                            src={storageUnitImage}
                                            alt=""
                                            className="w-10 h-10 object-contain shrink-0"
                                        />
                                        <div className="min-w-0 flex-1">
                                            <p className="text-sm font-medium text-white truncate">{name}</p>
                                            <p className="text-xs text-slate-400">{count} items</p>
                                        </div>
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => openRenameCasket(c)}
                                        className="shrink-0 p-2.5 text-slate-500 hover:text-white hover:bg-white/5 rounded-r-lg transition-colors"
                                        title="Rename storage unit"
                                        aria-label={`Rename ${name}`}
                                    >
                                        <Pencil size={14} />
                                    </button>
                                </div>
                            );
                        })}
                        </div>
                    </div>
                )}
                    </>
                )}
            </CollapsibleSection>

            {mainEmptyMessage() ? (
                <div className="flex-1 flex flex-col items-center justify-center rounded-2xl border border-white/5 bg-odin-blue/20 text-slate-500 min-h-0">
                    <div className="w-14 h-14 rounded-full bg-white/5 flex items-center justify-center mb-4">
                        <Package size={24} className="text-slate-600" />
                    </div>
                    <p className="text-sm">{mainEmptyMessage()}</p>
                </div>
            ) : (
                itemListPanel
            )}

            {renamingCasket && (
                <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
                    <button
                        type="button"
                        className="absolute inset-0 bg-black/70 backdrop-blur-sm"
                        onClick={closeRenameCasket}
                        aria-label="Close rename dialog"
                    />
                    <div
                        className="relative w-full max-w-sm bg-[#1a1d24] border border-white/10 rounded-xl shadow-2xl p-5"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <div className="flex items-center justify-between mb-4">
                            <h3 className="text-sm font-medium text-white">Rename storage unit</h3>
                            <button
                                type="button"
                                onClick={closeRenameCasket}
                                disabled={renameLoading}
                                className="p-1 text-slate-500 hover:text-white disabled:opacity-40"
                                aria-label="Close"
                            >
                                <X size={16} />
                            </button>
                        </div>
                        <p className="text-xs text-slate-500 mb-3">
                            Custom names are free for storage units (max {CASKET_NAME_MAX_LENGTH} characters).
                        </p>
                        <input
                            type="text"
                            value={renameDraft}
                            maxLength={CASKET_NAME_MAX_LENGTH}
                            disabled={renameLoading}
                            onChange={(e) => setRenameDraft(e.target.value)}
                            onKeyDown={(e) => {
                                if (e.key === 'Enter') submitRenameCasket();
                                if (e.key === 'Escape') closeRenameCasket();
                            }}
                            placeholder="Storage unit name"
                            className="w-full bg-black/40 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500/40 mb-2"
                            autoFocus
                        />
                        <p className="text-[10px] text-slate-600 mb-3 text-right tabular-nums">
                            {renameDraft.length}/{CASKET_NAME_MAX_LENGTH}
                        </p>
                        {renameError && (
                            <p className="text-xs text-red-400 mb-3">{renameError}</p>
                        )}
                        <div className="flex gap-2 justify-end">
                            <button
                                type="button"
                                onClick={closeRenameCasket}
                                disabled={renameLoading}
                                className="px-3 py-2 text-xs text-slate-400 hover:text-white disabled:opacity-40"
                            >
                                Cancel
                            </button>
                            <button
                                type="button"
                                onClick={submitRenameCasket}
                                disabled={renameLoading}
                                className="px-4 py-2 text-xs font-medium rounded-lg bg-amber-600 hover:bg-amber-500 text-white disabled:opacity-40"
                            >
                                {renameLoading ? 'Saving…' : 'Save'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

        </div>
    );
};

export default RatatoskrTransfer;
