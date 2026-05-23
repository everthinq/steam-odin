const express = require('express');
const SteamUser = require('steam-user');
const SteamTotp = require('steam-totp');
const GlobalOffensive = require('globaloffensive');
const bodyParser = require('body-parser');
const cors = require('cors');
const Items = require('./items');

const app = express();
const port = process.env.PORT || 3030;

app.use(cors());
app.use(bodyParser.json());

// Initialize Item Processor
const itemsProcessor = new Items();

// Store active sessions: { steamID: { user: SteamUser, csgo: GlobalOffensive, ... } }
const sessions = {};

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

// Login Endpoint
app.post('/login', (req, res) => {
    const { accountName, password, twoFactorCode, sharedSecret } = req.body;

    if (!accountName || !password) {
        return res.status(400).json({ error: 'Missing credentials' });
    }

    // Check if we already have a session for this accountName
    // Ideally we map accountName -> steamID, but for now let's just create a new one
    // In a real app we'd check if `sessions` has a user with this accountName.

    const user = new SteamUser();
    const csgo = new GlobalOffensive(user);

    const logOnOptions = {
        accountName,
        password,
    };

    if (twoFactorCode) {
        logOnOptions.twoFactorCode = twoFactorCode;
    } else if (sharedSecret) {
        logOnOptions.twoFactorCode = SteamTotp.generateAuthCode(sharedSecret);
    }

    user.logOn(logOnOptions);

    let isResponded = false;

    user.on('loggedOn', (details) => {
        console.log(`Logged into Steam as ${user.steamID.getSteamID64()}`);
        user.setPersona(SteamUser.EPersonaState.Online);
        user.gamesPlayed([730]); // Launch CS2
    });

    user.on('webSession', (sessionID, cookies) => {
        console.log(`[DEBUG] Got web session for ${user.steamID.getSteamID64()}`);
        console.log('[DEBUG] Cookies received:', cookies);

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
        console.error('Steam login error:', err);
        if (!isResponded) {
            res.status(401).json({ error: 'Login failed', details: err.message });
            isResponded = true;
        }
        // Cleanup if we have a steamID
        if (user.steamID) {
            delete sessions[user.steamID.getSteamID64()];
        }
    });

    csgo.on('connectedToGC', () => {
        console.log(`Connected to GC for ${user.steamID.getSteamID64()}`);

        // Store session
        const steamID64 = user.steamID.getSteamID64();
        sessions[steamID64] = { user, csgo, lastActivity: Date.now() };

        if (!isResponded) {
            res.json({ success: true, steamID: steamID64, message: 'Connected to Steam and GC' });
            isResponded = true;
        }
    });

    csgo.on('disconnectedFromGC', (reason) => {
        if (user.steamID) {
            console.log(`Disconnected from GC for ${user.steamID.getSteamID64()}: ${reason}`);
        }
    });

    // Timeout (if GC doesn't connect in 30s)
    setTimeout(() => {
        if (!isResponded) {
            res.status(504).json({ error: 'Gateway Timeout: GC Connection took too long' });
            isResponded = true;
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

// Cleanup inactive sessions (respects configurable idle timeout)
const SESSION_SWEEP_MS = 60 * 1000;
setInterval(() => {
    for (const [steamID, session] of Object.entries(sessions)) {
        if (!isSessionIdleExpired(session)) continue;
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

app.listen(port, () => {
    console.log(`Ratatoskr listening at http://localhost:${port}`);
});
