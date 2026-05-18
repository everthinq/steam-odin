import React from 'react';
import { X } from 'lucide-react';
import storageUnitImage from '../assets/ratatoskr/storage-unit.png';
import SteamMarketLink from './SteamMarketLink';
import TransferProgressBar from './TransferProgressBar';
import { getItemImageUrl } from '../utils/ratatoskrImages';

const ItemThumb = ({ item }) => (
    <img
        src={getItemImageUrl(item)}
        alt=""
        className="w-10 h-10 object-contain shrink-0"
        loading="lazy"
        referrerPolicy="no-referrer"
    />
);

const TransferQueueModal = ({
    isOpen,
    anchored = false,
    transferMode,
    storageName,
    entries,
    totalItems,
    onClearAll,
    onRemoveEntry,
    onMove,
    onClose,
    isMoving,
    moveProgress,
    moveDelayMs,
    moveDelayBounds,
    onDelayChange,
    onDelaySave,
}) => {
    if (!isOpen) return null;

    const title = transferMode === 'to' ? 'Transfer to' : 'Transfer from';
    const locationLabel = transferMode === 'to' ? 'To' : 'From';

    const panel = (
        <div
            className={`bg-[#1a1d24] border border-white/10 rounded-xl shadow-2xl overflow-hidden flex flex-col max-h-[min(520px,85vh)] ${anchored ? 'w-[min(100vw-2rem,22rem)]' : 'relative w-full max-w-md'}`}
            onClick={(e) => e.stopPropagation()}
        >
            <div className="flex items-center justify-between px-4 py-3 border-b border-white/10">
                <div className="flex items-center gap-2 min-w-0">
                    <img src={storageUnitImage} alt="" className="w-5 h-5 object-contain opacity-80" />
                    <h2 className="text-sm font-medium text-white truncate">{title}</h2>
                </div>
                <button
                    type="button"
                    onClick={onClearAll}
                    disabled={isMoving || entries.length === 0}
                    className="shrink-0 px-3 py-1 text-xs text-slate-300 border border-white/20 rounded-md hover:bg-white/5 disabled:opacity-40 transition-colors"
                >
                    Clear All
                </button>
            </div>

            <div className="flex-1 overflow-y-auto custom-scrollbar min-h-[120px]">
                {entries.length === 0 ? (
                    <p className="text-sm text-slate-500 text-center py-10 px-4">
                        No items in queue. Select items from the list.
                    </p>
                ) : (
                    entries.map((entry) => (
                        <div
                            key={entry.key}
                            className="flex items-center gap-3 px-4 py-3 border-b border-white/5 bg-white/[0.02] hover:bg-white/[0.04]"
                        >
                            <ItemThumb item={entry.representative} />
                            <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-1.5 min-w-0">
                                    <span className="text-sm text-white truncate min-w-0">
                                        {entry.item_name}
                                    </span>
                                    <SteamMarketLink itemName={entry.item_name} />
                                </div>
                                <p className="text-xs text-slate-500 truncate">
                                    {locationLabel}: {entry.storageName || storageName}
                                    <span className="text-slate-400 ml-2">× {entry.queueQty}</span>
                                </p>
                            </div>
                            <button
                                type="button"
                                onClick={() => onRemoveEntry(entry)}
                                disabled={isMoving}
                                className="shrink-0 w-7 h-7 flex items-center justify-center rounded-full bg-red-500/20 text-red-400 hover:bg-red-500/30 disabled:opacity-40 transition-colors"
                                aria-label="Remove from queue"
                            >
                                <X size={14} />
                            </button>
                        </div>
                    ))
                )}
            </div>

            <TransferProgressBar
                moveProgress={moveProgress}
                className="px-4 py-2 border-t border-white/5 bg-black/20"
                trackClassName="h-1"
            />

            <div className="p-4 border-t border-white/10 space-y-3 bg-black/20">
                <label className="flex items-center justify-between text-xs text-slate-500">
                    <span>Delay between items</span>
                    <span className="flex items-center gap-1">
                        <input
                            type="number"
                            min={moveDelayBounds.min}
                            max={moveDelayBounds.max}
                            step={50}
                            value={moveDelayMs}
                            disabled={isMoving}
                            onChange={(e) => onDelayChange(parseInt(e.target.value, 10) || moveDelayBounds.min)}
                            onBlur={(e) => onDelaySave(e.target.value)}
                            className="w-14 h-7 text-center bg-black/40 border border-white/10 rounded text-white text-xs focus:outline-none focus:border-amber-500/50 disabled:opacity-40"
                        />
                        <span>ms</span>
                    </span>
                </label>
                <button
                    type="button"
                    onClick={onMove}
                    disabled={isMoving || totalItems === 0}
                    className="w-full py-3 rounded-lg bg-[#2a2d35] hover:bg-[#343842] border border-white/10 text-white text-sm font-medium disabled:opacity-40 transition-colors"
                >
                    {isMoving ? 'Moving…' : `Move${totalItems > 0 ? ` (${totalItems})` : ''}`}
                </button>
            </div>
        </div>
    );

    if (anchored) {
        return (
            <>
                <button
                    type="button"
                    className="fixed inset-0 z-40"
                    onClick={onClose}
                    aria-label="Close queue"
                />
                <div className="absolute top-full right-0 mt-2 z-50">
                    {panel}
                </div>
            </>
        );
    }

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <button
                type="button"
                className="absolute inset-0 bg-black/70 backdrop-blur-sm"
                onClick={onClose}
                aria-label="Close queue"
            />
            {panel}
        </div>
    );
};

export default TransferQueueModal;
