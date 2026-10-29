import { useEffect, useRef, useState } from 'react';

// Steam Community Market access per account (backend: market_access_service.py, routes in
// routes/mimir.py). Mímir starts the check; the Dashboard only shows the saved result.
// Polls every 3 seconds while a check runs; only the newest answer is applied.
export default function useMarketAccess() {
    const [status, setStatus] = useState(null);
    const [starting, setStarting] = useState(false);
    const [startError, setStartError] = useState(null);
    const request = useRef(0);
    const running = Boolean(status?.job?.running);

    const load = async () => {
        const n = ++request.current;
        try {
            const response = await fetch('/api/mimir/market-access');
            if (!response.ok) return;
            const data = await response.json();
            if (n === request.current) setStatus(data);
        } catch {
            // the next poll tries again
        }
    };

    useEffect(() => {
        let alive = true;
        let timer = null;
        const tick = async () => {
            await load();
            if (alive && running) timer = setTimeout(tick, 3000);
        };
        tick();
        return () => {
            alive = false;
            clearTimeout(timer);
        };
    }, [running]);

    const start = async () => {
        setStarting(true);
        setStartError(null);
        try {
            const response = await fetch('/api/mimir/market-access/check', { method: 'POST' });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) setStartError(body.error || `HTTP ${response.status}`);
        } catch (e) {
            setStartError(e.message);
        } finally {
            await load();
            setStarting(false);
        }
    };

    // Market unlocker: Zombie Hunter's one-cent Editor Pack. Anything but dryRun === false
    // is a dry run (the backend fails closed too). Answers whether the job started.
    const unlock = async (steamids, dryRun) => {
        setStarting(true);
        setStartError(null);
        let started = false;
        try {
            const response = await fetch('/api/mimir/market-access/unlock', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ steamids, dry_run: dryRun !== false }),
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) setStartError(body.error || `HTTP ${response.status}`);
            started = response.ok;
        } catch (e) {
            setStartError(e.message);
        } finally {
            await load();
            setStarting(false);
        }
        return started;
    };

    return { status, start, unlock, starting, startError };
}
