import { useEffect, useRef, useState } from 'react';
import { readJson } from './readJson';
import { followJob, postCommand } from './moveCommands';

// Steam Community Market access per account (backend: market_access_service.py, routes in
// routes/mimir.py). Mímir starts the check; the Dashboard only shows the saved result.
// Polls every 3 seconds while a check runs; only the newest answer is applied.
// The backend decides what to print (headline, advice, which accounts the unlocker ticks).
export default function useMarketAccess() {
    const [status, setStatus] = useState(null);
    const [starting, setStarting] = useState(false);
    const [startError, setStartError] = useState(null);
    // The Market unlocker's plan (its dry run); the outcome per account comes back in status.last_unlocks.
    const [plan, setPlan] = useState(null);
    const request = useRef(0);
    const mounted = useRef(true);
    const running = Boolean(status?.job?.running);

    useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
        };
    }, []);

    const load = async () => {
        const n = ++request.current;
        try {
            const response = await fetch('/api/mimir/market-access');
            if (!response.ok) return;
            const data = await readJson(response);
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

    // While a plan can still be bought, read it again every 30 seconds and right after it
    // expires, so Buy for real turns off when the backend says the plan is no longer buyable.
    const planId = plan?.plan_id;
    const planOpen = Boolean(plan?.executable);
    const planExpiresAt = plan?.expires_at;
    useEffect(() => {
        if (!planId || !planOpen) return undefined;
        let alive = true;
        const reread = async () => {
            try {
                const response = await fetch(`/api/mimir/market-access/plans/${planId}`);
                const body = await readJson(response).catch(() => ({}));
                if (!alive) return;
                // a read sent before Buy for real must not undo the executed plan
                if (response.ok) setPlan((previous) => (previous?.plan_id === planId && !previous.job_id ? body : previous));
                else if (response.status === 404) {
                    setPlan((previous) => (previous?.plan_id === planId
                        ? { ...previous, executable: false, expired: true, state: 'expired' } : previous));
                }
            } catch {
                // the next read tries again
            }
        };
        const timer = setInterval(reread, 30000);
        const untilExpiry = planExpiresAt ? Date.parse(planExpiresAt) - Date.now() + 1000 : null;
        const expiryTimer = untilExpiry && untilExpiry > 0 ? setTimeout(reread, untilExpiry) : null;
        return () => {
            alive = false;
            clearInterval(timer);
            clearTimeout(expiryTimer);
        };
    }, [planId, planOpen, planExpiresAt]);

    const start = async () => {
        setStarting(true);
        setStartError(null);
        try {
            const response = await fetch('/api/mimir/market-access/check', { method: 'POST' });
            const body = await readJson(response).catch(() => ({}));
            if (!response.ok) setStartError(body.error || `HTTP ${response.status}`);
        } catch (e) {
            setStartError(e.message);
        } finally {
            await load();
            setStarting(false);
        }
    };

    // Market unlocker, step 1: a dry run on the chosen accounts makes a plan (backend command,
    // followed as a job). Answers whether the dry run started.
    const planUnlock = async (steamids) => {
        setStarting(true);
        setStartError(null);
        let started = null;
        try {
            started = await postCommand('/api/mimir/market-access/unlock/plan', { steamids });
            setPlan(started);
        } catch (e) {
            setStartError(e.message);
        } finally {
            await load();
            setStarting(false);
        }
        if (!started) return false;
        followJob(started.dry_run_job_id, {
            isActive: () => mounted.current,
            lostMessage: 'The backend restarted during the dry run: run it again.',
            lostTrackMessage: (reason) => `Lost track of the dry run (${reason}). It keeps running in the backend.`,
        })
            .then((job) => {
                if (job.result) setPlan(job.result);
                else if (job.error) setStartError(job.error);
            })
            .catch((e) => {
                if (!e.cancelled) setStartError(e.message);
            })
            .finally(() => {
                if (mounted.current) load();
            });
        return true;
    };

    // Step 2: buy for real on the accounts that passed the plan's dry run. Only this sends
    // dry_run false; the backend buys nothing without a plan. Answers whether it started.
    const executePlan = async (planId) => {
        setStarting(true);
        setStartError(null);
        let job = null;
        try {
            job = await postCommand(`/api/mimir/market-access/plans/${planId}/execute`, { dry_run: false });
            setPlan((previous) => (previous && previous.plan_id === planId
                ? { ...previous, executable: false, job_id: job.id } : previous));
        } catch (e) {
            setStartError(e.message);
        } finally {
            await load();
            setStarting(false);
        }
        if (!job) return false;
        // The outcome per account comes back in status.last_unlocks, a stopped run in status.job.error.
        followJob(job.id, {
            isActive: () => mounted.current,
            lostMessage: 'The backend restarted during the purchase: an account it was buying on MAY BE PAID. Check its Purchase History before buying again.',
            lostTrackMessage: (reason) => `Lost track of the purchase (${reason}). It keeps running in the backend; the last run per account shows the outcome.`,
        })
            .catch((e) => {
                if (!e.cancelled) setStartError(e.message);
            })
            .finally(() => {
                if (mounted.current) load();
            });
        return true;
    };

    return { status, start, planUnlock, executePlan, plan, starting, startError };
}
