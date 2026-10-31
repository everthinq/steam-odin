import { readJson } from './readJson';

// Backend commands: POST a command, get a plan or a job; follow the job with GET /api/jobs/<id>.
// The backend does the work (storage_deposit.py); closing the page never stops it.

/** POST a command; answers its JSON or throws the backend's `error`. */
export const postCommand = async (url, body) => {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
    });
    const data = await readJson(res);
    if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
};

/** A job as the move progress bar reads it. */
export const jobProgress = (job) => ({
    running: !!job.running,
    done: job.done || 0,
    failed: job.failed || 0,
    total: job.total || 0,
    processed: (job.done || 0) + (job.failed || 0),
    pending: Math.max(0, (job.total || 0) - (job.done || 0) - (job.failed || 0)),
    phase: job.phase,
});

// The backend keeps jobs in memory: after it restarts, a job it was running is unknown (404).
const JOB_LOST_MESSAGE =
    'The backend restarted during the move, so its result is unknown. Refresh the page to see where the items are.';

const fetchJob = async (jobId) => {
    const res = await fetch(`/api/jobs/${jobId}`);
    const job = await readJson(res);
    if (res.status === 404 && job.code === 'job_unknown') {
        throw Object.assign(new Error(JOB_LOST_MESSAGE), { jobLost: true });
    }
    if (!res.ok || (job.error && job.running === undefined)) {
        throw new Error(job.error || `Job status HTTP ${res.status}`);
    }
    return job;
};

const JOB_STATUS_RETRIES = 4;   // failed polls in a row before giving up (backoff 1s, 2s, 4s, 8s)

/**
 * Polls a job every half second until it finishes; resolves the finished job. `onProgress(job)` on
 * every answer; `isActive()` false (the page closed) rejects with `cancelled`. After too many failed
 * polls it rejects with `jobId` set: the job keeps running in the backend.
 */
export const followJob = (jobId, { onProgress, isActive = () => true } = {}) =>
    new Promise((resolve, reject) => {
        let failures = 0;
        const poll = async () => {
            if (!isActive()) {
                reject(Object.assign(new Error('Page closed'), { cancelled: true }));
                return;
            }
            try {
                const job = await fetchJob(jobId);
                failures = 0;
                onProgress?.(job);
                if (!job.running) {
                    resolve(job);
                    return;
                }
                setTimeout(poll, 500);
            } catch (err) {
                if (err.jobLost) {
                    reject(err);
                    return;
                }
                failures += 1;
                if (failures > JOB_STATUS_RETRIES) {
                    reject(Object.assign(
                        new Error(`Lost track of the move (${err.message}). It keeps running in the backend — the Move button re-enables once it has finished.`),
                        { jobId }
                    ));
                    return;
                }
                console.warn(`Job status poll failed (attempt ${failures}), retrying`, err);
                setTimeout(poll, 1000 * 2 ** (failures - 1));
            }
        };
        poll();
    });

/**
 * Checks a job every 5 seconds until it finishes (after `followJob` gave up); `onUpdate(job)` each
 * time. A job the backend no longer knows (it restarted) ends the watch as `{running: false}`.
 */
export const watchJobUntilDone = (jobId, { onUpdate, isActive = () => true }) => {
    const check = async () => {
        if (!isActive()) return;
        try {
            const job = await fetchJob(jobId);
            onUpdate(job);
            if (!job.running) return;
        } catch (err) {
            if (err.jobLost) {
                onUpdate({ running: false, lost: true });
                return;
            }
            console.error('Job status check failed', err);
        }
        setTimeout(check, 5000);
    };
    setTimeout(check, 5000);
};
