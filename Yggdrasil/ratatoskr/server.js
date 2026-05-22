const express = require('express');
const SteamUser = require('steam-user');
const SteamTotp = require('steam-totp');
const GlobalOffensive = require('globaloffensive');
const bodyParser = require('body-parser');
const cors = require('cors');
const Items = require('./items');
const Store = require('./store');

const app = express();
const port = process.env.PORT || 3030;

app.use(cors());
app.use(bodyParser.json());

// Initialize Item Processor
const itemsProcessor = new Items();

// Store active sessions: { steamID: { user: SteamUser, csgo: GlobalOffensive, ... } }
const sessions = {};

/** webSession often fires before connectedToGC; stash cookies until session object exists */
const pendingWebSessions = {};

const MIN_MOVE_DELAY_MS = 100;
const MAX_MOVE_DELAY_MS = 5000;
const DEFAULT_MOVE_DELAY_MS = parseInt(process.env.MOVE_DELAY_MS || '400', 10);

let moveDelayMs = Math.min(
    MAX_MOVE_DELAY_MS,
    Math.max(MIN_MOVE_DELAY_MS, DEFAULT_MOVE_DELAY_MS)
);

const getMoveDelayMs = () => moveDelayMs;

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
        user.gamesPlayed([{ game_id: 730, game_extra_info: 'Counter-Strike 2' }]);
    });

    user.on('webSession', (sessionID, cookies) => {
        console.log(`[DEBUG] Got web session for ${user.steamID.getSteamID64()}`);

        if (user.steamID) {
            const sid = user.steamID.getSteamID64();
            pendingWebSessions[sid] = { webSessionId: sessionID, webCookies: cookies };
            if (sessions[sid]) {
                sessions[sid].webSessionId = sessionID;
                sessions[sid].webCookies = cookies;
            }
        }

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
        const pending = pendingWebSessions[steamID64];
        sessions[steamID64] = {
            user,
            csgo,
            lastActivity: Date.now(),
            webSessionId: pending?.webSessionId ?? sessions[steamID64]?.webSessionId,
            webCookies: pending?.webCookies ?? sessions[steamID64]?.webCookies,
        };
        Store.attachMicroTxnHandler(sessions[steamID64]);
        Store.attachStoreGcHandlers(sessions[steamID64]);

        setTimeout(() => {
            const s = sessions[steamID64];
            if (s?.csgo?.haveGCSession) {
                Store.syncStoreAfterAccountData(s).catch((err) => {
                    console.warn(`[STORE] delayed GC store sync: ${err.message}`);
                });
            }
        }, 3000);

        if (!sessions[steamID64].webCookies?.length) {
            try {
                user.webLogOn();
            } catch (err) {
                console.warn(`[STORE] webLogOn after GC connect failed: ${err.message}`);
            }
        }

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

const disconnectSession = (steamID) => {
    const session = getSession(steamID);
    if (!session) return false;

    Store.detachMicroTxnHandler(session);

    try {
        session.user.logOff();
    } catch (err) {
        console.error(`Error logging off ${steamID}:`, err);
    }

    delete sessions[steamID];
    delete moveQueues[steamID];
    return true;
};

// Status Endpoint
app.get('/status/:steamid', (req, res) => {
    const steamID = req.params.steamid;
    const session = getSession(steamID);

    if (session && session.csgo && session.csgo.haveGCSession) {
        res.json({ status: 'connected', steamID });
    } else {
        res.status(404).json({ status: 'disconnected', error: 'No active session found' });
    }
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

// In-game store catalog (always returns items + Steam buyitem URLs)
app.get('/store/:steamid', async (req, res) => {
    const steamID = req.params.steamid;
    const session = getSession(steamID);

    if (session) {
        session.lastActivity = Date.now();
    }

    let storageUnitsOwned = 0;
    if (session?.csgo?.inventory) {
        storageUnitsOwned = session.csgo.inventory.filter((item) => item.def_index === 1201).length;
    }

    try {
        const store = await Store.getStoreUserData(session);
        res.json({
            success: true,
            ...store,
            storage_units_owned: storageUnitsOwned,
        });
    } catch (err) {
        console.error('Store catalog error:', err);
        res.json({
            success: true,
            gc_connected: false,
            warning: err.message || 'Failed to load store',
            catalog: Store.buildCatalog({}, 1, false),
            storage_units_owned: storageUnitsOwned,
        });
    }
});

// Purchase step 1: GC init → return Steam Authorize URL (SkinLedger-style)
app.post('/store/purchase/begin', async (req, res) => {
    const { steamID, itemDefId, quantity } = req.body;

    const session = getSession(steamID);
    if (!session?.csgo?.haveGCSession) {
        return res.status(401).json({ error: 'No active GC session' });
    }
    if (!itemDefId) {
        return res.status(400).json({ error: 'Missing itemDefId' });
    }

    session.lastActivity = Date.now();

    try {
        const result = await Store.beginStorePurchase(session, itemDefId, quantity);
        res.json(result);
    } catch (err) {
        console.error('Store purchase begin error:', err);
        res.status(500).json({ error: err.message || 'Purchase init failed' });
    }
});

// Purchase step 2: after user clicks Authorize on Steam checkout page
app.post('/store/purchase/finish', async (req, res) => {
    const { steamID, txnId } = req.body;

    const session = getSession(steamID);
    if (!session?.csgo?.haveGCSession) {
        return res.status(401).json({ error: 'No active GC session' });
    }
    if (!txnId) {
        return res.status(400).json({ error: 'Missing txnId' });
    }

    session.lastActivity = Date.now();

    try {
        const result = await Store.finishStorePurchase(session, txnId);
        res.json(result);
    } catch (err) {
        console.error('Store purchase finish error:', err);
        res.status(500).json({ error: err.message || 'Purchase finalize failed' });
    }
});

// Diagnostics: price sheet parse + session snapshot (+ optional full purchase)
app.post('/store/diag/run', async (req, res) => {
    const { steamID, executePurchase, accountId, currency } = req.body || {};
    const session = steamID ? getSession(steamID) : null;

    console.log(
        `[STORE] diag/run steamID=${steamID || 'none'} executePurchase=${Boolean(executePurchase)}`
    );

    try {
        const report = await Store.runStoreDiagnostics(session, {
            executePurchase: Boolean(executePurchase),
            accountId,
            currency,
        });
        res.json({ success: true, report });
    } catch (err) {
        console.error('Store diag error:', err);
        res.status(500).json({ error: err.message });
    }
});

// Full purchase (begin + finish without opening browser — may fail without Authorize click)
app.post('/store/purchase', async (req, res) => {
    const { steamID, itemDefId, quantity } = req.body;

    const session = getSession(steamID);
    if (!session?.csgo?.haveGCSession) {
        return res.status(401).json({ error: 'No active GC session' });
    }

    if (!itemDefId) {
        return res.status(400).json({ error: 'Missing itemDefId' });
    }

    session.lastActivity = Date.now();

    try {
        const result = await Store.purchaseStoreItem(session, itemDefId, quantity);
        if (result.requires_browser) {
            return res.status(402).json(result);
        }
        res.json(result);
    } catch (err) {
        console.error('Store purchase error:', err);
        res.status(500).json({ error: err.message || 'Purchase failed' });
    }
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

// Cleanup inactive sessions (every 5 mins)
setInterval(() => {
    const now = Date.now();
    for (const [steamID, session] of Object.entries(sessions)) {
        if (now - session.lastActivity > 1000 * 60 * 10) { // 10 mins inactive
            console.log(`Cleaning up inactive session for ${steamID}`);
            session.user.logOff();
            delete sessions[steamID];
            delete moveQueues[steamID];
        }
    }
}, 1000 * 60 * 5);

app.listen(port, () => {
    console.log(`Ratatoskr listening at http://localhost:${port}`);
});
