import { useEffect, useRef, useState } from 'react';

export const getMoveProgressPercent = (moveProgress) => {
    if (!moveProgress?.total) return 0;
    const processed =
        moveProgress.processed ??
        (moveProgress.done ?? 0) + (moveProgress.failed ?? 0);
    return Math.min(100, (processed / moveProgress.total) * 100);
};

const useSmoothProgress = (moveProgress) => {
    const isActive =
        !!moveProgress && (moveProgress.running || (moveProgress.processed ?? 0) > 0);
    const targetPercent = getMoveProgressPercent(moveProgress);
    const processed =
        moveProgress?.processed ??
        (moveProgress?.done ?? 0) + (moveProgress?.failed ?? 0);

    const [display, setDisplay] = useState(0);
    const displayRef = useRef(0);
    const creepAnchorRef = useRef({ processed: 0, at: 0 });

    useEffect(() => {
        if (!isActive) {
            displayRef.current = 0;
            setDisplay(0);
            creepAnchorRef.current = { processed: 0, at: 0 };
            return;
        }
        if (processed !== creepAnchorRef.current.processed) {
            creepAnchorRef.current = { processed, at: performance.now() };
        }
    }, [isActive, processed]);

    useEffect(() => {
        if (!isActive) return undefined;

        let raf;
        const tick = () => {
            const total = Math.max(1, moveProgress?.total ?? 1);
            const delayMs = Math.max(100, moveProgress?.delayMs ?? 400);
            const step = 100 / total;
            const base = (creepAnchorRef.current.processed / total) * 100;

            let target = targetPercent;
            if (moveProgress?.running && creepAnchorRef.current.processed < total) {
                const elapsed = performance.now() - creepAnchorRef.current.at;
                const creep = Math.min(step * 0.9, (elapsed / delayMs) * step * 0.9);
                target = Math.max(target, Math.min(100, base + creep));
            }

            const current = displayRef.current;
            const diff = target - current;
            const next = Math.abs(diff) < 0.06 ? target : current + diff * 0.12;
            displayRef.current = next;
            setDisplay(next);
            raf = requestAnimationFrame(tick);
        };

        raf = requestAnimationFrame(tick);
        return () => cancelAnimationFrame(raf);
    }, [
        isActive,
        targetPercent,
        moveProgress?.running,
        moveProgress?.total,
        moveProgress?.delayMs,
    ]);

    return { display, isActive };
};

export default function TransferProgressBar({
    moveProgress,
    className = '',
    trackClassName = 'h-1.5',
    labelRunning = 'Transferring…',
    labelDone = 'Done',
}) {
    const { display, isActive } = useSmoothProgress(moveProgress);

    if (!isActive) return null;

    const processed =
        moveProgress.processed ??
        (moveProgress.done ?? 0) + (moveProgress.failed ?? 0);

    return (
        <div className={className}>
            <div className="flex justify-between text-xs text-slate-400 mb-2">
                <span>{moveProgress.running ? labelRunning : labelDone}</span>
                <span className="tabular-nums text-white">
                    {processed} / {moveProgress.total}
                    {moveProgress.failed > 0 && (
                        <span className="text-red-400 ml-2">({moveProgress.failed} failed)</span>
                    )}
                </span>
            </div>
            <div
                className={`relative w-full ${trackClassName} bg-black/40 rounded-full overflow-hidden`}
            >
                <div
                    className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-amber-700 via-amber-500 to-amber-400 shadow-[0_0_10px_rgba(245,158,11,0.3)]"
                    style={{ width: `${display}%` }}
                />
                {moveProgress.running && display > 2 && (
                    <div
                        className="absolute inset-y-0 left-0 rounded-full overflow-hidden pointer-events-none"
                        style={{ width: `${display}%` }}
                    >
                        <div className="absolute inset-0 -translate-x-full animate-[transfer-bar-shimmer_1.8s_ease-in-out_infinite] bg-gradient-to-r from-transparent via-white/25 to-transparent" />
                    </div>
                )}
            </div>
        </div>
    );
}
