import { useEffect, useState } from 'react';
import { readJson } from './readJson';

/**
 * Ratatoskr's pause between two moves (`/api/ratatoskr/config/move-delay`): read when `steamid`
 * changes; `setMoveDelayMs` follows the input while typing, `saveMoveDelay(value)` clamps to the
 * bounds Ratatoskr reports and saves.
 */
export const useMoveDelay = (steamid) => {
    const [moveDelayMs, setMoveDelayMs] = useState(200);
    const [moveDelayBounds, setMoveDelayBounds] = useState({ min: 100, max: 5000 });

    useEffect(() => {
        let active = true;
        (async () => {
            try {
                const res = await fetch('/api/ratatoskr/config/move-delay');
                const data = await readJson(res);
                if (active && res.ok && data.delayMs != null) {
                    setMoveDelayMs(data.delayMs);
                    setMoveDelayBounds({ min: data.min ?? 100, max: data.max ?? 5000 });
                }
            } catch (err) {
                console.error(err);
            }
        })();
        return () => { active = false; };
    }, [steamid]);

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
            const data = await readJson(res);
            if (res.ok && data.delayMs != null) setMoveDelayMs(data.delayMs);
        } catch (err) {
            console.error(err);
        }
    };

    return { moveDelayMs, setMoveDelayMs, moveDelayBounds, saveMoveDelay };
};
