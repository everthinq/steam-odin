/**
 * Ratatoskr's move queue: one per account, moves sent one after another at the move delay
 * (Steam rate limits — never in parallel), each one confirmed by the Game Coordinator in the
 * background.
 *
 * A sent move is done only once the Game Coordinator answers about the item itself (seen live
 * 2026-10-31, within milliseconds of the send):
 * - deposit: the item leaves the inventory (`itemRemoved`); when this session has opened the
 *   unit's contents it also arrives inside it (`itemAcquired` with the unit's id) — either counts;
 * - withdrawal: the item arrives back in the inventory (`itemAcquired` without a unit).
 * No answer within `confirmTimeoutMs` means the Game Coordinator refused it (a trade-protected
 * item, a full unit): the item simply stays where it was. A refusal costs this wait but slows
 * nothing else.
 *
 * Every run (a batch queued while the account's queue was idle) gets a new `runId`, returned by
 * `enqueueMoves` and in every status; the status lists `doneItemIDs` (confirmed) and
 * `failedItemIDs` (refused or never sent) of that run. A client counts only those: if the run id it
 * was given is gone (a disconnect drops the queue), nothing of its batch is confirmed.
 *
 * createMoveQueues({getSession, getMoveDelayMs, confirmTimeoutMs}) returns
 * {enqueueMoves, getMoveQueueStatus, hasActiveMoveWork, dropMoveQueue}. getSession(steamID)
 * gives the account's live session ({user, csgo, lastActivity}); the tests pass a fake one.
 */
const DEFAULT_CONFIRM_TIMEOUT_MS = 15000;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

let lastRunId = 0;

const createMoveQueueState = () => ({
    runId: 0,
    jobs: [],
    running: false,
    // itemID -> {job, timer}: sent, waiting for the Game Coordinator's answer.
    awaiting: new Map(),
    done: 0,
    failed: 0,
    refused: 0,
    total: 0,
    errors: [],
    // Every item of this run the Game Coordinator confirmed, and every one that did not move
    // (refused or never sent).
    doneItemIDs: [],
    failedItemIDs: [],
    // The failed ones the Game Coordinator never answered (refused), as opposed to never sent.
    refusedItemIDs: [],
    // The failed ones whose answer never came because the session was lost meanwhile.
    unconfirmedItemIDs: [],
    currentItemID: null,
});

const executeMoveJob = (session, job) => {
    const { csgo } = session;
    if (job.source === 'inventory' && job.target === 'casket') {
        csgo.addToCasket(job.casketID, job.itemID);
        return;
    }
    if (job.source === 'casket' && job.target === 'inventory') {
        csgo.removeFromCasket(job.casketID, job.itemID);
        return;
    }
    throw new Error('Invalid move source/target');
};

const createMoveQueues = ({ getSession, getMoveDelayMs, confirmTimeoutMs = DEFAULT_CONFIRM_TIMEOUT_MS }) => {
    const queues = {};
    // One listener per Game Coordinator client (a reconnect of the same client rebuilds the
    // session entry, so the client is the key, not the entry).
    const confirmingClients = new WeakSet();

    const getMoveQueue = (steamID) => {
        if (!queues[steamID]) queues[steamID] = createMoveQueueState();
        return queues[steamID];
    };

    const getMoveQueueStatus = (steamID) => {
        const state = queues[steamID] || createMoveQueueState();
        const pending = state.jobs.length + state.awaiting.size;
        const processed = state.done + state.failed;
        return {
            runId: state.runId,
            running: state.running || state.awaiting.size > 0,
            pending,
            awaiting: state.awaiting.size,
            done: state.done,
            failed: state.failed,
            refused: state.refused,
            total: state.total || processed + pending,
            processed,
            currentItemID: state.currentItemID,
            errors: state.errors.slice(-20),
            doneItemIDs: state.doneItemIDs.slice(),
            failedItemIDs: state.failedItemIDs.slice(),
            refusedItemIDs: state.refusedItemIDs.slice(),
            unconfirmedItemIDs: state.unconfirmedItemIDs.slice(),
            delayMs: getMoveDelayMs(),
        };
    };

    const hasActiveMoveWork = (steamID) => {
        const state = queues[steamID];
        if (!state) return false;
        return state.running || state.jobs.length > 0 || state.awaiting.size > 0;
    };

    const dropMoveQueue = (steamID) => {
        queues[steamID]?.awaiting.forEach((entry) => clearTimeout(entry.timer));
        delete queues[steamID];
    };

    const failMove = (state, itemID, error, code) => {
        state.failed++;
        state.failedItemIDs.push(String(itemID));
        state.errors.push({ itemID: String(itemID), error, code });
    };

    const finishIfIdle = (steamID, state) => {
        if (state.running || state.awaiting.size > 0 || state.jobs.length > 0) return;
        console.log(`Move queue finished for ${steamID}: ${state.done} moved, ${state.refused} refused, ${state.failed - state.refused} not sent`);
    };

    // arrivedIn: null = the item left the inventory; '' = back in the inventory; else the unit's id.
    const confirmMove = (steamID, item, arrivedIn) => {
        const state = queues[steamID];
        const entry = state?.awaiting.get(String(item?.id));
        if (!entry) return;
        const confirms = entry.job.target === 'casket'
            ? arrivedIn === null || arrivedIn === String(entry.job.casketID)
            : arrivedIn === '';
        if (!confirms) return;
        clearTimeout(entry.timer);
        state.awaiting.delete(String(item.id));
        state.done++;
        state.doneItemIDs.push(String(item.id));
        finishIfIdle(steamID, state);
    };

    const listenForMoveConfirmations = (steamID, csgo) => {
        if (confirmingClients.has(csgo)) return;
        confirmingClients.add(csgo);
        csgo.on('itemRemoved', (item) => {
            if (item && !item.casket_id) confirmMove(steamID, item, null);
        });
        csgo.on('itemAcquired', (item) => {
            confirmMove(steamID, item, item?.casket_id ? String(item.casket_id) : '');
        });
    };

    const processMoveQueue = async (steamID) => {
        const state = getMoveQueue(steamID);
        if (state.running) return;

        const session = getSession(steamID);
        if (!session?.csgo?.haveGCSession) {
            const left = state.jobs.splice(0);
            left.forEach((job) => failMove(state, job.itemID, 'No active Game Coordinator session', 'not_sent'));
            return;
        }

        state.running = true;
        listenForMoveConfirmations(steamID, session.csgo);

        while (state.jobs.length > 0) {
            // The session was disconnected, replaced, or lost the Game Coordinator: nothing sent
            // now would move, so fail what is left instead of sending it blind. Compare the Steam
            // client, not the entry object: a Game Coordinator reconnect rebuilds the entry.
            const current = getSession(steamID);
            if (!current || current.user !== session.user || !session.csgo.haveGCSession) {
                const left = state.jobs.splice(0);
                left.forEach((job) => failMove(state, job.itemID, 'Game Coordinator session lost before sending', 'not_sent'));
                console.error(`Move queue stopped (${steamID}): session lost, ${left.length} moves not sent`);
                break;
            }
            const job = state.jobs.shift();
            const itemID = String(job.itemID);
            state.currentItemID = itemID;

            try {
                const timer = setTimeout(() => {
                    if (!state.awaiting.delete(itemID)) return;
                    // No answer is a refusal only while the Game Coordinator was there to answer;
                    // a session lost meanwhile leaves the move unknown, not refused.
                    const current = getSession(steamID);
                    if (!current || current.user !== session.user || !session.csgo.haveGCSession) {
                        state.unconfirmedItemIDs.push(itemID);
                        failMove(state, itemID, 'Game Coordinator session lost before its answer', 'unconfirmed');
                    } else {
                        state.refused++;
                        state.refusedItemIDs.push(itemID);
                        failMove(state, itemID, 'Refused by the Game Coordinator (no answer)', 'refused');
                    }
                    finishIfIdle(steamID, state);
                }, confirmTimeoutMs);
                state.awaiting.set(itemID, { job, timer });
                executeMoveJob(session, job);
                session.lastActivity = Date.now();
            } catch (err) {
                const entry = state.awaiting.get(itemID);
                if (entry) clearTimeout(entry.timer);
                state.awaiting.delete(itemID);
                failMove(state, itemID, err.message, 'not_sent');
                console.error(`Move queue error (${steamID}):`, err);
            }

            if (state.jobs.length > 0) {
                await sleep(getMoveDelayMs());
            }
        }

        state.running = false;
        state.currentItemID = null;
        finishIfIdle(steamID, state);
    };

    const enqueueMoves = (steamID, jobs, resetCounters) => {
        const state = getMoveQueue(steamID);

        if (resetCounters && !state.running && state.awaiting.size === 0) {
            state.done = 0;
            state.failed = 0;
            state.refused = 0;
            state.errors = [];
            state.doneItemIDs = [];
            state.failedItemIDs = [];
            state.refusedItemIDs = [];
            state.unconfirmedItemIDs = [];
            state.total = 0;
            state.runId = ++lastRunId;
        }

        // An item already queued or waiting for its answer is not sent twice (a second
        // entry would replace the first one's confirmation and its timer would misfire).
        const queued = new Set([...state.jobs.map((job) => String(job.itemID)), ...state.awaiting.keys()]);
        for (const job of jobs) {
            if (queued.has(String(job.itemID))) continue;
            queued.add(String(job.itemID));
            state.jobs.push(job);
        }
        state.total = state.done + state.failed + state.awaiting.size + state.jobs.length;

        processMoveQueue(steamID).catch((err) => {
            console.error(`Move queue processor crashed (${steamID}):`, err);
            state.running = false;
        });

        return getMoveQueueStatus(steamID);
    };

    return { enqueueMoves, getMoveQueueStatus, hasActiveMoveWork, dropMoveQueue };
};

module.exports = { createMoveQueues };
