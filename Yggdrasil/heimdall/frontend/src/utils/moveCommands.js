import { readJson } from './readJson';

// Backend commands: POST a command, get a plan or a job; follow the job with GET /api/jobs/<id>.
// The backend does the work (storage_deposit.py, storage_shop_service.py); closing the page never stops it.

/** POST a command; answers its JSON, or throws the backend's `error` with its `code` and HTTP `status`
 * when the answer is not OK. Only the status counts: a job answered again (202) may carry its own
 * `error`. A 4xx is a refusal (nothing started); a 5xx or no answer at all may come after it started. */
export const postCommand = async (url, body) => {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
    });
    const data = await readJson(res);
    if (!res.ok) {
        throw Object.assign(new Error(data.error || `HTTP ${res.status}`), { code: data.code, status: res.status });
    }
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

const fetchJob = async (jobId, lostMessage = JOB_LOST_MESSAGE) => {
    const res = await fetch(`/api/jobs/${jobId}`);
    const job = await readJson(res);
    if (res.status === 404 && job.code === 'job_unknown') {
        throw Object.assign(new Error(lostMessage), { jobLost: true });
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
 * polls it rejects with `jobId` set: the job keeps running in the backend. `lostMessage` and
 * `lostTrackMessage(reason)` replace the move wording (a purchase follows its job the same way).
 */
/** Whether a failed command was refused before it started anything (a 4xx answer with a code). */
export const refusedBeforeStart = (error) => Boolean(error?.code) && error.status >= 400 && error.status < 500;

export const followJob = (jobId, {
    onProgress, isActive = () => true, lostMessage = JOB_LOST_MESSAGE,
    lostTrackMessage = (reason) => `Lost track of the move (${reason}). It keeps running in the backend — the Move button re-enables once it has finished.`,
} = {}) =>
    new Promise((resolve, reject) => {
        let failures = 0;
        const poll = async () => {
            if (!isActive()) {
                reject(Object.assign(new Error('Page closed'), { cancelled: true }));
                return;
            }
            try {
                const job = await fetchJob(jobId, lostMessage);
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
                    reject(Object.assign(new Error(lostTrackMessage(err.message)), { jobId }));
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
