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

// Helper to get session
const getSession = (steamID) => sessions[steamID];

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

// Move Item Endpoint
app.post('/move', (req, res) => {
    const { steamID, source, target, itemID, casketID } = req.body;

    const session = getSession(steamID);
    if (!session || !session.csgo || !session.csgo.haveGCSession) {
        return res.status(401).json({ error: 'No active GC session' });
    }

    session.lastActivity = Date.now();
    const { csgo } = session;

    try {
        if (source === 'inventory' && target === 'casket') {
            console.log(`Moving item ${itemID} to casket ${casketID}`);
            csgo.addToCasket(casketID, itemID);
            return res.json({ success: true, message: 'Move command sent' });

        } else if (source === 'casket' && target === 'inventory') {
            console.log(`Moving item ${itemID} from casket ${casketID}`);
            csgo.removeFromCasket(casketID, itemID);
            return res.json({ success: true, message: 'Move command sent' });
        } else {
            return res.status(400).json({ error: 'Invalid move source/target' });
        }
    } catch (err) {
        console.error('Move error:', err);
        return res.status(500).json({ error: 'Move failed', details: err.message });
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
        }
    }
}, 1000 * 60 * 5);

app.listen(port, () => {
    console.log(`Ratatoskr listening at http://localhost:${port}`);
});
