import React from 'react';
import { Link } from 'react-router-dom';
import { Archive } from 'lucide-react';
import storageUnitImage from '../../assets/ratatoskr/storage-unit.png';

const formatCount = (n) => n.toLocaleString('en-US');

/**
 * To mode: every Storage Unit with room counts as one storage. Shows the free space, how much of it
 * the selection takes and what is left after the move; red when the selection does not fit.
 */
const StorageSpacePanel = ({ free, unitCount, unitsWithRoom, selected, loading }) => {
    const left = free - selected;
    const overflow = Math.max(0, -left);
    const usedPercent = free > 0 ? Math.min(100, (selected / free) * 100) : selected > 0 ? 100 : 0;

    return (
        <section
            aria-label="Storage space"
            className="mb-4 rounded-2xl border border-white/5 bg-odin-blue/30 px-4 py-3"
        >
            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 mb-2">
                <div className="flex items-center gap-2">
                    <img src={storageUnitImage} alt="" className="w-5 h-5 object-contain" />
                    <span className="text-[10px] font-bold tracking-widest uppercase text-slate-400">
                        Storage space
                    </span>
                    <span
                        className="text-xs text-slate-500"
                        title="Items go into the fullest Storage Unit with room first, then the next — you never pick one"
                    >
                        {loading
                            ? 'loading…'
                            : `${unitsWithRoom} of ${unitCount} Storage Unit${unitCount === 1 ? '' : 's'} ${unitsWithRoom === 1 ? 'has' : 'have'} room`}
                    </span>
                </div>
                <Link
                    to="/buy-storage-units"
                    className="inline-flex items-center gap-1.5 text-xs text-slate-400 hover:text-amber-300 transition-colors"
                >
                    <Archive size={13} />
                    Buy Storage Units
                </Link>
            </div>

            <div
                className="h-2 rounded-full bg-white/5 overflow-hidden"
                role="meter"
                aria-valuemin={0}
                aria-valuemax={free}
                aria-valuenow={Math.min(selected, free)}
                aria-label="Selected items out of the free space"
            >
                <div
                    className={`h-full rounded-full transition-all ${overflow > 0 ? 'bg-red-500/80' : 'bg-amber-500/80'}`}
                    style={{ width: `${usedPercent}%` }}
                />
            </div>

            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 mt-2 text-xs tabular-nums">
                <span className="text-slate-300">
                    <span className="text-white font-semibold">{formatCount(free)}</span> free
                </span>
                {selected > 0 && (
                    overflow > 0 ? (
                        <span className="text-red-300 font-medium">
                            {formatCount(selected)} selected — {formatCount(overflow)} more than fits. Remove some or buy Storage Units.
                        </span>
                    ) : (
                        <span className="text-slate-400">
                            <span className="text-amber-300 font-medium">{formatCount(selected)}</span> selected ·{' '}
                            <span className="text-white font-medium">{formatCount(left)}</span> left after the move
                        </span>
                    )
                )}
            </div>
        </section>
    );
};

export default StorageSpacePanel;
