#!/usr/bin/env node
/**
 * Store purchase diagnostics against Ratatoskr.
 *
 *   node scripts/test-store-purchase.js
 *   STEAM_ID=<your_steamid64> node scripts/test-store-purchase.js --execute
 *
 * Requires Ratatoskr GC session (Connect in Heimdall). --execute charges Steam Wallet.
 */
const axios = require('axios');

const BASE = process.env.RATATOSKR_URL || 'http://localhost:3001';
const STEAM_ID = process.env.STEAM_ID || '';
const executePurchase = process.argv.includes('--execute');

const main = async () => {
    if (!STEAM_ID) {
        console.error('Set STEAM_ID to your SteamID64 (never commit real IDs in this repo).');
        process.exit(1);
    }
    console.log(`Ratatoskr ${BASE} steamID=<redacted> execute=${executePurchase}`);

    const status = await axios.get(`${BASE}/store/${STEAM_ID}`).catch((e) => {
        throw new Error(`GET /store failed: ${e.message}`);
    });
    console.log('store snapshot:', JSON.stringify(status.data, null, 2));

    const diag = await axios
        .post(`${BASE}/store/diag/run`, {
            steamID: STEAM_ID,
            executePurchase: false,
        })
        .catch((e) => {
            const msg = e.response?.data?.error || e.message;
            throw new Error(`POST /store/diag/run failed: ${msg}`);
        });
    console.log('diag report:', JSON.stringify(diag.data, null, 2));

    if (!status.data.gc_connected) {
        console.log('\nNo GC session — connect in Heimdall, then re-run.');
        process.exit(1);
    }

    if (!executePurchase) {
        console.log('\nDry run (diag only). Re-run with --execute to attempt wallet purchase.');
        return;
    }

    const begin = await axios
        .post(`${BASE}/store/purchase/begin`, {
            steamID: STEAM_ID,
            itemDefId: 1201,
            quantity: 1,
        })
        .catch((e) => {
            console.error('begin failed:', e.response?.data || e.message);
            throw e;
        });
    console.log('begin:', JSON.stringify(begin.data, null, 2));

    const finish = await axios
        .post(`${BASE}/store/purchase/finish`, {
            steamID: STEAM_ID,
            txnId: begin.data.txn_id,
        })
        .catch((e) => {
            console.error('finish failed:', e.response?.data || e.message);
            throw e;
        });
    console.log('finish:', JSON.stringify(finish.data, null, 2));
};

main().catch((err) => {
    console.error(err.message);
    process.exit(1);
});
