const express = require('express');
const SteamUser = require('steam-user');
const SteamTotp = require('steam-totp');
const GlobalOffensive = require('globaloffensive');
const bodyParser = require('body-parser');
const Items = require('./items');
const Store = require('./store');

const app = express();
const port = process.env.PORT || 3030;

// No CORS: only the Heimdall backend calls Ratatoskr (server to server). Open CORS
// let any website open in the browser read inventories and trigger moves.
// Browsers add an Origin header to cross-site requests (even ones CORS cannot
// stop, like a plain POST); server-to-server calls never send one — so refuse it.
app.use((req, res, next) => {
    if (req.headers.origin) {
        return res.status(403).json({ error: 'browser requests are not accepted' });
    }
    return next();
});
app.use(bodyParser.json());

// Initialize Item Processor
const itemsProcessor = new Items();

// Store active sessions: { steamID: { user: SteamUser, csgo: GlobalOffensive, accountName, ... } }
const sessions = {};
// Account names with a login running right now. A second login for the same
// account (UI button + Mímir test + scheduler at once) would start a second
// Steam client that kicks the first one off ("logged in elsewhere").
const loginsInProgress = new Set();

const MIN_MOVE_DELAY_MS = 100;
const MAX_MOVE_DELAY_MS = 5000;
const DEFAULT_MOVE_DELAY_MS = parseInt(process.env.MOVE_DELAY_MS || '400', 10);

let moveDelayMs = Math.min(
    MAX_MOVE_DELAY_MS,
    Math.max(MIN_MOVE_DELAY_MS, DEFAULT_MOVE_DELAY_MS)
);

/** Auto-disconnect after inactivity (0 = never). Default 1 hour; was hardcoded 10 min. */
const MIN_SESSION_IDLE_MS = 5 * 60 * 1000;
const MAX_SESSION_IDLE_MS = 24 * 60 * 60 * 1000;
const DEFAULT_SESSION_IDLE_MS = parseInt(
    process.env.SESSION_IDLE_TIMEOUT_MS || String(60 * 60 * 1000),
    10
);

let sessionIdleTimeoutMs = DEFAULT_SESSION_IDLE_MS === 0
    ? 0
    : Math.min(
        MAX_SESSION_IDLE_MS,
        Math.max(MIN_SESSION_IDLE_MS, DEFAULT_SESSION_IDLE_MS)
    );

const getMoveDelayMs = () => moveDelayMs;

// SteamIDs exempt from idle auto-disconnect (e.g. the auto-store watcher's
// accounts). Heimdall keeps this in sync — see POST /config/protected-accounts.
let protectedSteamIds = new Set();

const getSessionIdleTimeoutMs = () => sessionIdleTimeoutMs;

const setSessionIdleTimeoutMs = (value) => {
    const parsed = parseInt(value, 10);
    if (Number.isNaN(parsed) || parsed <= 0) {
        sessionIdleTimeoutMs = 0;
        return sessionIdleTimeoutMs;
    }
    sessionIdleTimeoutMs = Math.min(
        MAX_SESSION_IDLE_MS,
        Math.max(MIN_SESSION_IDLE_MS, parsed)
    );
    return sessionIdleTimeoutMs;
};

const isSessionIdleExpired = (session) => {
    const idleMs = getSessionIdleTimeoutMs();
    if (idleMs === 0 || !session?.lastActivity) return false;
    return Date.now() - session.lastActivity > idleMs;
};

const hasActiveMoveWork = (steamID) => {
    const state = moveQueues[steamID];
    if (!state) return false;
    return state.running || state.jobs.length > 0;
};

const setMoveDelayMs = (value) => {
    const parsed = parseInt(value, 10);
    if (Number.isNaN(parsed)) {
        throw new Error('delayMs must be a number');
    }
    moveDelayMs = Math.min(MAX_MOVE_DELAY_MS, Math.max(MIN_MOVE_DELAY_MS, parsed));
    return moveDelayMs;
};

const moveQueues = {};

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const CASKET_NAME_MAX_LENGTH = 20;

// Helper to get session
const getSession = (steamID) => sessions[steamID];

const renameCasket = (session, casketId, name) => {
    const { csgo } = session;
    const trimmed = String(name ?? '').trim();
    if (trimmed.length > CASKET_NAME_MAX_LENGTH) {
        return Promise.reject(
            new Error(`Name must be ${CASKET_NAME_MAX_LENGTH} characters or less`)
        );
    }

    const idStr = String(casketId);
    const casket = csgo.inventory?.find(
        (item) => String(item.id) === idStr && item.def_index === 1201
    );
    if (!casket) {
        return Promise.reject(new Error('Storage unit not found in inventory'));
    }

    return new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
            cleanup();
            reject(new Error('Rename timed out waiting for GC'));
        }, 15000);

        const doneTypes = new Set([
            GlobalOffensive.ItemCustomizationNotification.NameItem,
            GlobalOffensive.ItemCustomizationNotification.NameBaseItem,
            GlobalOffensive.ItemCustomizationNotification.RemoveItemName,
        ]);

        const finish = () => {
            const updated = csgo.inventory?.find((item) => String(item.id) === idStr);
            cleanup();
            resolve({
                item_id: idStr,
                custom_name: updated?.custom_name ?? (trimmed || null),
            });
        };

        const onChanged = (oldItem, item) => {
            if (String(item?.id) !== idStr) return;
            finish();
        };

        const onNotif = (itemIds, notificationType) => {
            if (!itemIds?.some((id) => String(id) === idStr)) return;
            if (!doneTypes.has(notificationType)) return;
            finish();
        };

        const cleanup = () => {
            clearTimeout(timeout);
            csgo.removeListener('itemChanged', onChanged);
            csgo.removeListener('itemCustomizationNotification', onNotif);
        };

        csgo.on('itemChanged', onChanged);
        csgo.on('itemCustomizationNotification', onNotif);

        try {
            csgo.nameItem('0', idStr, trimmed);
            session.lastActivity = Date.now();
        } catch (err) {
            cleanup();
            reject(err);
        }
    });
};

const createMoveQueueState = () => ({
    jobs: [],
    running: false,
    done: 0,
    failed: 0,
    total: 0,
    errors: [],
    currentItemID: null,
});

const getMoveQueue = (steamID) => {
    if (!moveQueues[steamID]) {
        moveQueues[steamID] = createMoveQueueState();
    }
    return moveQueues[steamID];
};

const getMoveQueueStatus = (steamID) => {
    const state = moveQueues[steamID] || createMoveQueueState();
    const pending = state.jobs.length;
    const processed = state.done + state.failed;
    return {
        running: state.running,
        pending,
        done: state.done,
        failed: state.failed,
        total: state.total || processed + pending,
        processed,
        currentItemID: state.currentItemID,
        errors: state.errors.slice(-20),
        delayMs: getMoveDelayMs(),
    };
};

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

const processMoveQueue = async (steamID) => {
    const state = getMoveQueue(steamID);
    if (state.running) return;

    const session = getSession(steamID);
    if (!session?.csgo?.haveGCSession) {
        state.errors.push({ error: 'No active GC session' });
        return;
    }

    state.running = true;

    while (state.jobs.length > 0) {
        // The session was disconnected, replaced, or lost the Game Coordinator:
        // nothing sent now would move, so fail what is left instead of counting it done.
        // Compare the Steam client, not the entry object: a Game Coordinator
        // reconnect of the same client rebuilds sessions[steamID].
        if (!sessions[steamID] || sessions[steamID].user !== session.user || !session.csgo.haveGCSession) {
            const left = state.jobs.splice(0);
            state.failed += left.length;
            state.errors.push({ error: `Game Coordinator session lost: ${left.length} moves not sent` });
            console.error(`Move queue stopped (${steamID}): session lost, ${left.length} moves not sent`);
            break;
        }
        const job = state.jobs.shift();
        state.currentItemID = job.itemID;

        try {
            executeMoveJob(session, job);
            state.done++;
            session.lastActivity = Date.now();
        } catch (err) {
            state.failed++;
            state.errors.push({ itemID: job.itemID, error: err.message });
            console.error(`Move queue error (${steamID}):`, err);
        }

        if (state.jobs.length > 0) {
            await sleep(getMoveDelayMs());
        }
    }

    state.running = false;
    state.currentItemID = null;
    console.log(`Move queue finished for ${steamID}: ${state.done} ok, ${state.failed} failed`);
};

const enqueueMoves = (steamID, jobs, resetCounters) => {
    const state = getMoveQueue(steamID);

    if (resetCounters && !state.running) {
        state.done = 0;
        state.failed = 0;
        state.errors = [];
        state.total = 0;
    }

    state.jobs.push(...jobs);
    state.total = state.done + state.failed + state.jobs.length;

    processMoveQueue(steamID).catch((err) => {
        console.error(`Move queue processor crashed (${steamID}):`, err);
        state.running = false;
    });

    return getMoveQueueStatus(steamID);
};

// Steam Guard codes are timed by Steam's clock, never this container's alone: Docker's
// clock on a Mac falls behind while the Mac sleeps, and a code made on a wrong clock is
// rejected for every account (2026-10-16: 20 of 21 logins refused after a night's sleep).
// The offset is asked again after a minute, and always when Steam rejects a code.
const STEAM_TIME_MAX_AGE_MS = 60 * 1000;
let steamTimeOffset = null; // seconds to add to this container's clock
let steamTimeCheckedAt = 0;

const STEAM_TIME_TIMEOUT_MS = 5000;

// Steam's time from ITwoFactorService/QueryTime. Not SteamTotp.getTimeOffset: it has no
// timeout (a login would wait forever, its account stuck at 409) and on a malformed
// answer it calls back twice or throws inside the response handler.
const querySteamTime = () => new Promise((resolve, reject) => {
    const req = require('https').request({
        hostname: 'api.steampowered.com',
        path: '/ITwoFactorService/QueryTime/v1/',
        method: 'POST',
        headers: { 'Content-Length': 0 },
        timeout: STEAM_TIME_TIMEOUT_MS,
    }, (res) => {
        let body = '';
        res.on('data', (chunk) => { body += chunk; });
        res.on('end', () => {
            try {
                const serverTime = Number(JSON.parse(body).response.server_time);
                if (res.statusCode !== 200 || !Number.isFinite(serverTime) || serverTime <= 0) {
                    throw new Error(`HTTP ${res.statusCode}`);
                }
                resolve(serverTime);
            } catch (err) {
                reject(new Error(`malformed answer (${err.message})`));
            }
        });
    });
    req.on('timeout', () => req.destroy(new Error('timed out')));
    req.on('error', reject);
    req.end();
});

const steamTimeOffsetSeconds = async (force = false) => {
    if (!force && steamTimeOffset !== null && Math.abs(Date.now() - steamTimeCheckedAt) < STEAM_TIME_MAX_AGE_MS) {
        return steamTimeOffset;
    }
    try {
        const offset = (await querySteamTime()) - SteamTotp.time();
        if (steamTimeOffset === null || Math.abs(offset - steamTimeOffset) >= 2) {
            console.log(`[LOGIN] Steam's clock is ${offset} s from this container's`);
        }
        steamTimeOffset = offset;
        steamTimeCheckedAt = Date.now();
    } catch (err) {
        // Steam unreachable: keep the last offset we had (or this clock).
        console.error(`[LOGIN] Could not ask Steam for its time: ${err.message}`);
    }
    return steamTimeOffset || 0;
};

const steamGuardCode = async (sharedSecret, force = false) =>
    SteamTotp.generateAuthCode(sharedSecret, await steamTimeOffsetSeconds(force));

// Login Endpoint
app.post('/login', async (req, res) => {
    const { accountName, password, twoFactorCode, sharedSecret } = req.body;

    if (!accountName || !password) {
        return res.status(400).json({ error: 'Missing credentials' });
    }

    const key = String(accountName).toLowerCase();
    if (loginsInProgress.has(key)) {
        return res.status(409).json({ error: 'A login for this account is already running' });
    }
    // Already logged in: reuse a healthy session, replace a broken one.
    const existingID = Object.keys(sessions).find((id) => sessions[id].accountName === key);
    if (existingID) {
        const existing = sessions[existingID];
        if (existing.csgo && existing.csgo.haveGCSession) {
            existing.lastActivity = Date.now();
            return res.json({ success: true, steamID: existingID, reused: true, message: 'Already connected to Steam and GC' });
        }
        disconnectSession(existingID);
    }
    loginsInProgress.add(key);

    const user = new SteamUser();
    const csgo = new GlobalOffensive(user);

    const logOnOptions = { accountName, password };
    if (twoFactorCode) {
        logOnOptions.twoFactorCode = twoFactorCode;
    } else if (sharedSecret) {
        try {
            logOnOptions.twoFactorCode = await steamGuardCode(sharedSecret);
        } catch (err) {
            loginsInProgress.delete(key);
            return res.status(400).json({ error: 'Could not make a Steam Guard code', details: err.message });
        }
    }

    let isResponded = false;
    let loginTimer = null;
    const respond = (status, body) => {
        if (isResponded) return;
        isResponded = true;
        loginsInProgress.delete(key);
        clearTimeout(loginTimer);
        res.status(status).json(body);
    };

    // Steam wants a Steam Guard code we did not have (or rejected ours). Without
    // this listener steam-user would wait for a console prompt that never comes.
    // A rejected code made here from the shared secret is tried once more on Steam's
    // clock asked again right now; a code that comes out the same was not a clock problem.
    let guardRetried = false;
    user.on('steamGuard', async (domain, callback, lastCodeWrong) => {
        const reason = lastCodeWrong ? 'Steam Guard code rejected' : 'Steam Guard code required';
        if (sharedSecret && !twoFactorCode && !guardRetried && !isResponded) {
            guardRetried = true;
            try {
                const code = await steamGuardCode(sharedSecret, true);
                if (code !== logOnOptions.twoFactorCode && !isResponded) {
                    console.warn(`[LOGIN] ${key}: ${reason}; trying again on Steam's clock`);
                    logOnOptions.twoFactorCode = code;
                    return callback(code);
                }
            } catch (err) {
                console.error(`[LOGIN] ${key}: could not make a new Steam Guard code: ${err.message}`);
            }
        }
        console.error(`[LOGIN] ${key}: ${reason}`);
        respond(401, { error: reason });
        user.logOff();
    });

    user.logOn(logOnOptions);

    user.on('loggedOn', (details) => {
        console.log(`Logged into Steam as ${user.steamID.getSteamID64()}`);
        user.setPersona(SteamUser.EPersonaState.Online);
        user.gamesPlayed([730]); // Launch CS2
    });

    user.on('webSession', (sessionID, cookies) => {
        console.log(`[DEBUG] Got web session for ${user.steamID.getSteamID64()}`);
        // Never log the cookies: steamLoginSecure is a live Steam web token.
        console.log(`[SESSION] Web session cookies received (${cookies.length})`);

        // Send cookies to Heimdall
        const heimdallUrl = process.env.HEIMDALL_API_URL || 'http://localhost:5000';
        // Check if we are in docker, maybe use container name 'heimdall-backend' if needed, 
        // but robust setup uses env var.

        // cookies is an array of strings: [ 'sessionid=...', 'steamLoginSecure=...' ]

        // Use axios to post to heimdall. 
        // We need to require axios at top of file, or just use fetch if node 18+. 
        // package.json has axios.
        const axios = require('axios');

        console.log(`[DEBUG] Sending session to Heimdall: ${heimdallUrl}/api/accounts/update-session`);

        axios.post(`${heimdallUrl}/api/accounts/update-session`, {
            steamID: user.steamID.getSteamID64(),
            cookies: cookies
        }).then(() => {
            console.log(`[DEBUG] Synced session with Heimdall for ${user.steamID.getSteamID64()}`);
        }).catch(err => {
            console.error(`[DEBUG] Failed to sync session with Heimdall: ${err.message}`);
            if (err.response) {
                console.error('[DEBUG] Response data:', err.response.data);
            }
        });
    });

    user.on('error', (err) => {
        console.error('Steam login error:', err.message || err);
        respond(401, { error: 'Login failed', details: err.message });
        // Remove the session only if it is THIS client's: an older client being
        // kicked ("logged in elsewhere") must not delete the newer, healthy one.
        const id = user.steamID && user.steamID.getSteamID64();
        if (id && sessions[id] && sessions[id].user === user) {
            delete sessions[id];
        }
    });

    csgo.on('connectedToGC', () => {
        console.log(`Connected to GC for ${user.steamID.getSteamID64()}`);

        // Store session (a Game Coordinator reconnect fires this again for the same client)
        const steamID64 = user.steamID.getSteamID64();
        const previous = sessions[steamID64];
        if (previous && previous.user !== user) {
            try { previous.user.logOff(); } catch (err) { console.error(`Error logging off old client ${steamID64}:`, err); }
        }
        sessions[steamID64] = { user, csgo, accountName: key, lastActivity: Date.now() };

        respond(200, { success: true, steamID: steamID64, message: 'Connected to Steam and GC' });
    });

    csgo.on('disconnectedFromGC', (reason) => {
        if (user.steamID) {
            console.log(`Disconnected from GC for ${user.steamID.getSteamID64()}: ${reason}`);
        }
    });

    // Timeout (if GC doesn't connect in 30s)
    loginTimer = setTimeout(() => {
        if (!isResponded) {
            respond(504, { error: 'Gateway Timeout: GC Connection took too long' });
            user.logOff();
        }
    }, 30000);
});

const notifyHeimdallWebSessionCleared = async (steamID) => {
    const heimdallUrl = process.env.HEIMDALL_API_URL || 'http://localhost:5000';
    const axios = require('axios');
    try {
        await axios.post(`${heimdallUrl}/api/accounts/clear-web-session`, { steamID });
        console.log(`[DEBUG] Cleared Heimdall web session for ${steamID}`);
    } catch (err) {
        console.error(`[DEBUG] Failed to clear Heimdall web session: ${err.message}`);
    }
};

const disconnectSession = (steamID) => {
    const session = getSession(steamID);
    if (!session) return false;

    notifyHeimdallWebSessionCleared(steamID);

    try {
        session.user.logOff();
    } catch (err) {
        console.error(`Error logging off ${steamID}:`, err);
    }

    delete sessions[steamID];
    delete moveQueues[steamID];
    return true;
};

// Status Endpoint (also acts as keep-alive when the UI polls)
app.get('/status/:steamid', (req, res) => {
    const steamID = req.params.steamid;
    const session = getSession(steamID);

    if (session) {
        session.lastActivity = Date.now();
        if (session.csgo && session.csgo.haveGCSession) {
            return res.json({
                status: 'connected',
                steamID,
                idleTimeoutMs: getSessionIdleTimeoutMs(),
                lastActivity: session.lastActivity,
            });
        }
        return res.status(503).json({
            status: 'gc_lost',
            steamID,
            error: 'Steam session exists but Game Coordinator is not connected',
            idleTimeoutMs: getSessionIdleTimeoutMs(),
        });
    }

    res.status(404).json({ status: 'disconnected', error: 'No active session found' });
});

app.post('/disconnect/:steamid', (req, res) => {
    const steamID = req.params.steamid;
    if (disconnectSession(steamID)) {
        res.json({ success: true, message: 'Disconnected from Steam and GC' });
    } else {
        res.status(404).json({ error: 'No active session found' });
    }
});

// Inventory Endpoint
app.get('/inventory/:steamid', (req, res) => {
    const steamID = req.params.steamid;
    const session = getSession(steamID);

    if (!session || !session.csgo || !session.csgo.haveGCSession) {
        return res.status(401).json({ error: 'No active GC session' });
    }

    session.lastActivity = Date.now();

    try {
        const inventory = session.csgo.inventory;
        if (!inventory) {
            return res.json({ success: true, items: [] });
        }

        const formattedItems = itemsProcessor.inventoryConverter(inventory);
        res.json({ success: true, items: formattedItems });
    } catch (err) {
        console.error('Inventory fetch error:', err);
        res.status(500).json({ error: 'Failed to fetch inventory' });
    }
});

// Storage Units (Caskets) Endpoint
app.get('/caskets/:steamid', (req, res) => {
    const steamID = req.params.steamid;
    const session = getSession(steamID);

    if (!session || !session.csgo || !session.csgo.haveGCSession) {
        return res.status(401).json({ error: 'No active GC session' });
    }

    session.lastActivity = Date.now();

    try {
        const inventory = session.csgo.inventory;
        if (!inventory) return res.json({ success: true, caskets: [] });

        // Caskets have def_index 1201
        const caskets = inventory.filter(item => item.def_index === 1201);

        const formattedCaskets = itemsProcessor.inventoryConverter(caskets);
        res.json({ success: true, caskets: formattedCaskets });
    } catch (err) {
        console.error('Casket fetch error:', err);
        res.status(500).json({ error: 'Failed to fetch caskets' });
    }
});

// Casket Contents Endpoint
app.get('/casket/:steamid/:casketid', (req, res) => {
    const { steamid, casketid } = req.params;
    const session = getSession(steamid);

    if (!session || !session.csgo || !session.csgo.haveGCSession) {
        return res.status(401).json({ error: 'No active GC session' });
    }

    session.lastActivity = Date.now();

    session.csgo.getCasketContents(casketid, (err, items) => {
        // The GC can invoke this callback more than once (it is tied to the
        // customizationNotification event). Responding twice throws
        // ERR_HTTP_HEADERS_SENT, which is uncaught here and crashes the process —
        // so respond exactly once.
        if (res.headersSent) return;
        if (err) {
            console.error('Error getting casket contents:', err);
            return res.status(500).json({ error: 'Failed to get casket contents', details: err.message });
        }

        try {
            const formattedItems = itemsProcessor.inventoryConverter(items, true);
            res.json({ success: true, items: formattedItems });
        } catch (convErr) {
            console.error('Conversion error:', convErr);
            res.status(500).json({ error: 'Failed to process casket items' });
        }
    });
});

// Rename storage unit (free — nameTagId 0)
app.post('/casket/rename', (req, res) => {
    const { steamID, casketID, name } = req.body;

    const session = getSession(steamID);
    if (!session?.csgo?.haveGCSession) {
        return res.status(401).json({ error: 'No active GC session' });
    }

    if (!casketID) {
        return res.status(400).json({ error: 'Missing casketID' });
    }

    renameCasket(session, casketID, name)
        .then((result) => {
            const raw = session.csgo.inventory?.find(
                (item) => String(item.id) === String(casketID)
            );
            const formatted = raw ? itemsProcessor.inventoryConverter([raw]) : [];
            res.json({
                success: true,
                item_id: result.item_id,
                custom_name: result.custom_name,
                casket: formatted[0] || {
                    item_id: result.item_id,
                    item_customname: result.custom_name,
                },
            });
        })
        .catch((err) => {
            console.error('Casket rename error:', err);
            res.status(500).json({ error: err.message || 'Failed to rename storage unit' });
        });
});

// Move Item Endpoint (queued)
app.post('/move', (req, res) => {
    const { steamID, source, target, itemID, casketID } = req.body;

    const session = getSession(steamID);
    if (!session?.csgo?.haveGCSession) {
        return res.status(401).json({ error: 'No active GC session' });
    }

    if (!itemID || !casketID || !source || !target) {
        return res.status(400).json({ error: 'Missing required fields' });
    }

    const status = enqueueMoves(steamID, [{ itemID, source, target, casketID }], true);
    return res.json({ success: true, message: 'Move queued', ...status });
});

// Batch move endpoint (queued with delay between each item)
app.post('/move/batch', (req, res) => {
    const { steamID, source, target, itemIDs, casketID } = req.body;

    const session = getSession(steamID);
    if (!session?.csgo?.haveGCSession) {
        return res.status(401).json({ error: 'No active GC session' });
    }

    if (!Array.isArray(itemIDs) || itemIDs.length === 0 || !casketID || !source || !target) {
        return res.status(400).json({ error: 'Missing required fields' });
    }

    const jobs = itemIDs.map((itemID) => ({ itemID, source, target, casketID }));
    const status = enqueueMoves(steamID, jobs, true);

    console.log(`Queued ${itemIDs.length} moves for ${steamID} (${getMoveDelayMs()}ms between each)`);
    return res.json({ success: true, message: 'Batch queued', queued: itemIDs.length, ...status });
});

app.get('/move/status/:steamid', (req, res) => {
    res.json(getMoveQueueStatus(req.params.steamid));
});

app.get('/config/move-delay', (req, res) => {
    res.json({
        delayMs: getMoveDelayMs(),
        min: MIN_MOVE_DELAY_MS,
        max: MAX_MOVE_DELAY_MS,
        default: DEFAULT_MOVE_DELAY_MS,
    });
});

app.post('/config/move-delay', (req, res) => {
    try {
        const delayMs = setMoveDelayMs(req.body?.delayMs);
        console.log(`Move delay set to ${delayMs}ms`);
        res.json({
            success: true,
            delayMs,
            min: MIN_MOVE_DELAY_MS,
            max: MAX_MOVE_DELAY_MS,
        });
    } catch (err) {
        res.status(400).json({ error: err.message });
    }
});

app.get('/config/session-idle', (req, res) => {
    res.json({
        idleTimeoutMs: getSessionIdleTimeoutMs(),
        min: MIN_SESSION_IDLE_MS,
        max: MAX_SESSION_IDLE_MS,
        default: sessionIdleTimeoutMs,
        presets: [
            { label: '15 minutes', idleTimeoutMs: 15 * 60 * 1000 },
            { label: '30 minutes', idleTimeoutMs: 30 * 60 * 1000 },
            { label: '1 hour', idleTimeoutMs: 60 * 60 * 1000 },
            { label: '2 hours', idleTimeoutMs: 2 * 60 * 60 * 1000 },
            { label: '4 hours', idleTimeoutMs: 4 * 60 * 60 * 1000 },
            { label: 'Never', idleTimeoutMs: 0 },
        ],
    });
});

app.post('/config/session-idle', (req, res) => {
    try {
        const idleTimeoutMs = setSessionIdleTimeoutMs(req.body?.idleTimeoutMs);
        const label =
            idleTimeoutMs === 0
                ? 'disabled (never auto-disconnect)'
                : `${Math.round(idleTimeoutMs / 60000)} minutes`;
        console.log(`Session idle timeout set to ${label}`);
        res.json({
            success: true,
            idleTimeoutMs,
            min: MIN_SESSION_IDLE_MS,
            max: MAX_SESSION_IDLE_MS,
        });
    } catch (err) {
        res.status(400).json({ error: err.message });
    }
});

// ---- Counter-Strike 2 in-game store (Storage Units) — see store.js -------------------

const connectedSession = (steamID, res) => {
    const session = getSession(String(steamID || ''));
    if (!session || !session.csgo || !session.csgo.haveGCSession) {
        res.status(401).json({ error: 'No active GC session' });
        return null;
    }
    session.lastActivity = Date.now();
    return session;
};

// The store's price sheet as the Game Coordinator sends it (exploration / parsing).
app.get('/store/user-data/:steamid', async (req, res) => {
    const session = connectedSession(req.params.steamid, res);
    if (!session) return;
    try {
        const data = await Store.getUserData(session);
        const sheet = data.price_sheet || Buffer.alloc(0);
        res.json({
            success: true,
            result: data.result,
            price_sheet_version: data.price_sheet_version,
            price_sheet_bytes: sheet.length,
            price_sheet_base64: sheet.toString('base64'),
            wallet_currency: session.user.wallet ? session.user.wallet.currency : null,
            wallet_balance: session.user.wallet ? session.user.wallet.balance : null,
            storage_units: Store.storageUnitCount(session),
            account_country: session.user.accountInfo ? session.user.accountInfo.country : null,
            store_currency: data.currency_deprecated,
            store_country: data.country_deprecated,
        });
    } catch (err) {
        if (!res.headersSent) res.status(502).json({ error: err.message });
    }
});

// The price sheet names items by their internal name ("casket", "coupon - noisia_01"):
// their definition index, English name and prefab from items_game, for the Store Catalogue.
// Read-only and local (no Steam call). Unknown names are left out.
let definitionsByName = null;
app.post('/items/store-names', (req, res) => {
    const names = (req.body || {}).names;
    if (!Array.isArray(names) || names.length > 2000) return res.status(400).json({ error: 'names must be a list (at most 2000)' });
    if (!definitionsByName) {
        definitionsByName = new Map();
        for (const [defIndex, item] of Object.entries(itemsProcessor.csgoItems.items || {})) {
            if (item && item.name) definitionsByName.set(item.name, [defIndex, item]);
        }
    }
    const english = (token) => {
        const value = token ? itemsProcessor.translation[String(token).replace(/^#/, '').toLowerCase()] : null;
        return typeof value === 'string' ? value.replace(/^"|"$/g, '') : null;
    };
    const definitions = {};
    for (const name of names) {
        const found = definitionsByName.get(String(name));
        if (!found) continue;
        const [defIndex, item] = found;
        // The item's own "cannot trade" (Storage Unit, Charm Detachment Pack, Armory Pass). Not the
        // prefab's: every store coupon inherits it, yet becomes a tradable sticker or music kit when bought.
        const cannotTrade = Boolean(item.attributes && Number(item.attributes['cannot trade']) === 1);
        definitions[name] = { defIndex: Number(defIndex), name: english(item.item_name), prefab: item.prefab || null, cannotTrade };
    }
    res.json({ success: true, definitions });
});

// What each store capsule holds: price sheet entry ("coupon - …") → the capsule it delivers → its
// supply crate series → the loot lists, down to every sticker or patch with its rarity tier (the
// list name's _rare, _mythical, _legendary or _ancient suffix). Local item files, no Steam call.
// Pins and anything else are left out (pin capsules hold items, not sticker kits).
const LOOT_TIERS = ['rare', 'mythical', 'legendary', 'ancient'];
const LOOT_PREFIX = { sticker: 'Sticker | ', patch: 'Patch | ' };
let lootIndex = null;
app.post('/items/capsule-contents', (req, res) => {
    const names = (req.body || {}).names;
    if (!Array.isArray(names) || names.length > 500) return res.status(400).json({ error: 'names must be a list (at most 500)' });
    if (!lootIndex) {
        const raw = require('./items_game.json').items_game || {};
        const itemsByName = new Map();
        for (const item of Object.values(raw.items || {})) if (item && item.name) itemsByName.set(item.name, item);
        const kitsByName = new Map();
        for (const kit of Object.values(raw.sticker_kits || {})) if (kit && kit.name) kitsByName.set(kit.name, kit);
        lootIndex = { itemsByName, kitsByName, client: raw.client_loot_lists || {}, revolving: raw.revolving_loot_lists || {} };
    }
    const { itemsByName, kitsByName, client, revolving } = lootIndex;
    const english = (token) => {
        const value = token ? itemsProcessor.translation[String(token).replace(/^#/, '').toLowerCase()] : null;
        return typeof value === 'string' ? value.replace(/^"|"$/g, '') : null;
    };
    const walk = (listName, tier, out, depth) => {
        if (depth > 6) return;
        for (const key of Object.keys(client[listName] || {})) {
            if (client[key]) {
                const own = LOOT_TIERS.find((suffix) => key.endsWith(`_${suffix}`));
                walk(key, own || tier, out, depth + 1);
                continue;
            }
            const match = /^\[([^\]]+)\](sticker|patch)$/.exec(key);
            if (!match) continue;
            const kit = kitsByName.get(match[1]);
            const name = kit && english(kit.item_name);
            if (name) out.push({ name: LOOT_PREFIX[match[2]] + name, tier });
        }
    };
    const contents = {};
    for (const entry of names) {
        const coupon = itemsByName.get(String(entry));
        const list = coupon && client[coupon.loot_list_name];
        if (!list) continue;
        for (const crateName of Object.keys(list)) {
            const crate = itemsByName.get(crateName);
            const series = crate && crate.attributes && crate.attributes['set supply crate series'];
            const lootList = series && revolving[String(series.value)];
            if (!lootList) continue;
            const items = [];
            walk(lootList, 'rare', items, 0);
            if (!items.length) continue;
            // A coupon naming several capsules: their contents together, the first capsule's name.
            const found = contents[entry] || { capsule: english(crate.item_name), items: [] };
            const known = new Set(found.items.map((item) => item.name));
            found.items.push(...items.filter((item) => !known.has(item.name)));
            contents[entry] = found;
        }
    }
    res.json({ success: true, contents });
});

// Open a wallet transaction for an item (Storage Units by default). Nothing is paid until Steam approves
// it (Heimdall does that on checkout.steampowered.com) and it is finalized below.
app.post('/store/purchase/init', async (req, res) => {
    const { steamID, country, currency, quantity, unitPrice, itemDefinitionIndex } = req.body || {};
    const session = connectedSession(steamID, res);
    if (!session) return;
    const count = parseInt(quantity, 10);
    const price = parseInt(unitPrice, 10);
    const currencyId = parseInt(currency, 10);
    // The item's definition index (Storage Unit 1201 when none is given).
    const definition = itemDefinitionIndex === undefined || itemDefinitionIndex === null
        ? Store.STORAGE_UNIT_DEFINITION_INDEX : Number(itemDefinitionIndex);
    if (!(count >= 1 && count <= 50) || !(price > 0) || !(currencyId >= 0 && currencyId <= 63) || !/^[A-Z]{2}$/.test(String(country || ''))
        || !(Number.isInteger(definition) && definition > 0 && definition < 1000000)) {
        return res.status(400).json({ error: 'quantity (1-50), unitPrice, currency (the game store 0-based currency, USD = 0), a two-letter country and a whole itemDefinitionIndex are required' });
    }
    try {
        const answer = await Store.initPurchase(session, { country, currency: currencyId, quantity: count, unitPrice: price, itemDefinitionIndex: definition });
        console.log(`[STORE] Purchase opened for ${steamID}: ${count} × item ${definition}, result ${answer.result}, transaction ${answer.txn_id}`);
        if (answer.authRequest) {
            console.log(`[STORE] Steam approval request received for ${answer.txn_id} (${answer.authRequest.hex.length / 2} bytes)`);
        }
        res.json({ success: answer.result === 1, result: answer.result, transactionId: answer.txn_id, url: answer.url, authRequest: answer.authRequest, storageUnits: Store.storageUnitCount(session) });
    } catch (err) {
        if (!res.headersSent) res.status(502).json({ error: err.message });
    }
});

// Deliver an approved transaction's items.
app.post('/store/purchase/finalize', async (req, res) => {
    const { steamID, transactionId } = req.body || {};
    const session = connectedSession(steamID, res);
    if (!session) return;
    if (!/^\d+$/.test(String(transactionId || ''))) return res.status(400).json({ error: 'transactionId is required' });
    try {
        const answer = await Store.finalizePurchase(session, transactionId);
        console.log(`[STORE] Purchase ${transactionId} finalized for ${steamID}: result ${answer.result}, ${answer.item_ids.length} item(s)`);
        res.json({ success: answer.result === 1, result: answer.result, itemIds: answer.item_ids, storageUnits: Store.storageUnitCount(session) });
    } catch (err) {
        if (!res.headersSent) res.status(502).json({ error: err.message });
    }
});

// Drop a transaction that was never approved.
app.post('/store/purchase/cancel', async (req, res) => {
    const { steamID, transactionId } = req.body || {};
    const session = connectedSession(steamID, res);
    if (!session) return;
    if (!/^\d+$/.test(String(transactionId || ''))) return res.status(400).json({ error: 'transactionId is required' });
    try {
        const answer = await Store.cancelPurchase(session, transactionId);
        console.log(`[STORE] Purchase ${transactionId} cancelled for ${steamID}: result ${answer.result}`);
        res.json({ success: answer.result === 1, result: answer.result });
    } catch (err) {
        if (!res.headersSent) res.status(502).json({ error: err.message });
    }
});

// Accounts that must never be idle-disconnected (synced from Heimdall).
app.get('/config/protected-accounts', (req, res) => {
    res.json({ steamIds: [...protectedSteamIds] });
});

app.post('/config/protected-accounts', (req, res) => {
    const ids = req.body?.steamIds;
    if (!Array.isArray(ids)) {
        return res.status(400).json({ error: 'steamIds must be an array' });
    }
    const next = new Set(ids.map(String));
    const changed = next.size !== protectedSteamIds.size || [...next].some((id) => !protectedSteamIds.has(id));
    protectedSteamIds = next;
    // Heimdall re-sends this every scheduler tick; log only real changes.
    if (changed) {
        console.log(`[SESSION] Protected accounts set: ${[...protectedSteamIds].join(', ') || '(none)'}`);
    }
    res.json({ success: true, steamIds: [...protectedSteamIds] });
});

// Cleanup inactive sessions (respects configurable idle timeout)
const SESSION_SWEEP_MS = 60 * 1000;
setInterval(() => {
    for (const [steamID, session] of Object.entries(sessions)) {
        if (!isSessionIdleExpired(session)) continue;
        if (protectedSteamIds.has(steamID)) continue; // auto-store account — keep online
        if (hasActiveMoveWork(steamID)) {
            session.lastActivity = Date.now();
            continue;
        }
        console.log(
            `[SESSION] Idle timeout (${getSessionIdleTimeoutMs()}ms) — disconnecting ${steamID}`
        );
        disconnectSession(steamID);
    }
}, SESSION_SWEEP_MS);

// A throw inside a steam-user / globaloffensive event callback would otherwise
// kill the process silently. Log it and exit so Docker restarts the container
// (restart: unless-stopped); sessions are lost either way.
process.on('uncaughtException', (err) => {
    console.error('[FATAL] Uncaught exception, exiting for a clean restart:', err);
    process.exit(1);
});
process.on('unhandledRejection', (reason) => {
    console.error('[ERROR] Unhandled promise rejection:', reason);
});

app.listen(port, () => {
    console.log(`Ratatoskr listening at http://localhost:${port}`);
});
