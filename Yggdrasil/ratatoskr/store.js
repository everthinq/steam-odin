// Counter-Strike 2 in-game store over the Game Coordinator (any item; Storage Units by default).
//
// A purchase has three steps, the same ones the game client takes:
//   1. StorePurchaseInit     — the Game Coordinator opens a Steam wallet transaction
//                              and answers with its id (nothing is paid yet);
//   2. approval              — Steam must approve the transaction (the game shows the
//                              Steam overlay; Heimdall uses the account's web session on
//                              checkout.steampowered.com/checkout/approvetxn/<id>/);
//   3. StorePurchaseFinalize — the Game Coordinator delivers the items and answers with
//                              their item ids.
// StorePurchaseCancel drops a transaction that was never approved.
//
// One store request per session at a time. Each goes out as a Game Coordinator job (a
// source job id), as Valve's client does (BYldSendMessageAndGetReply): Finalize is
// answered only to a job (seen live: without one, no answer in 60 s). The answer comes
// back to that job; a plain message of the reply type is still accepted.
// `currency` is the game store's own 0-based ECurrency (USD 0, EUR 2, NOK 9, HKD 27 —
// Valve's econ_store.h), not Steam's wallet currency code (USD 1): the wallet code
// makes the Game Coordinator answer result 8, "invalid parameter".

const SteamUser = require('steam-user');
const Language = require('globaloffensive/language.js');
const Protos = require('globaloffensive/protobufs/generated/_load.js');

const COUNTER_STRIKE_APP_ID = 730;
// Steam's own approval request for a wallet transaction (what makes the game open the
// Steam overlay at checkout/approvetxn/<Steam transaction id>). steam-user does not
// handle it, so it is caught here and re-emitted on the client as 'microTxnAuthRequest'.
const CLIENT_MICRO_TRANSACTION_AUTH_REQUEST = SteamUser.EMsg.ClientMicroTxnAuthRequest;   // 5504
const AUTH_REQUEST_WAIT_MS = 10000;
SteamUser.prototype._handlerManager.add(CLIENT_MICRO_TRANSACTION_AUTH_REQUEST, function onMicroTransactionAuthRequest(body, header) {
    const raw = body && typeof body.toBuffer === 'function' ? body.toBuffer() : Buffer.from(body || []);
    this.emit('microTxnAuthRequest', raw, header);
});
const STORAGE_UNIT_DEFINITION_INDEX = 1201;
const GAME_COORDINATOR_TIMEOUT_MS = 20000;
// Finalize makes the Game Coordinator charge the wallet through Steam: slower.
const FINALIZE_TIMEOUT_MS = 60000;

const decode = (proto, payload) => proto.toObject(proto.decode(payload), { longs: String, defaults: true, bytes: Buffer });

// Ask the Game Coordinator and wait for its answer of `replyType`.
const request = (session, sendType, sendProto, body, replyType, replyProto, timeoutMs = GAME_COORDINATOR_TIMEOUT_MS) => new Promise((resolve, reject) => {
    const { user, csgo } = session;
    if (!csgo || !csgo.haveGCSession) {
        reject(new Error('No active Game Coordinator session'));
        return;
    }
    // A request that timed out may still be answered later, and that late answer would
    // be taken for the next request's (another transaction id). After a timeout this
    // session's store is closed until a fresh login (a new session object).
    if (session.storeClosed) {
        reject(new Error('An earlier store request timed out: log in again before buying'));
        return;
    }
    const others = [];    // other Game Coordinator messages meanwhile, named in a timeout
    let finished = false;
    const onMessage = (appId, messageType, payload) => {
        if (finished || appId !== COUNTER_STRIKE_APP_ID) return;
        if (messageType !== replyType) {
            if (others.length < 20) others.push(messageType);
            return;
        }
        finished = true;
        cleanup();
        try {
            resolve(decode(replyProto, payload));
        } catch (err) {
            reject(err);
        }
    };
    const timer = setTimeout(() => {
        finished = true;
        session.storeClosed = true;
        cleanup();
        console.log(`[STORE] No answer of type ${replyType} in ${timeoutMs} ms; other Game Coordinator messages meanwhile: ${others.join(', ') || 'none'}`);
        reject(new Error('The Game Coordinator did not answer in time'));
    }, timeoutMs);
    const cleanup = () => {
        clearTimeout(timer);
        user.removeListener('receivedFromGC', onMessage);
    };
    user.on('receivedFromGC', onMessage);
    if (!user.steamID) {
        finished = true;
        cleanup();
        reject(new Error('Not logged into Steam'));
        return;
    }
    // The job's answer arrives here (steam-user routes it by job id, not as receivedFromGC).
    user.sendToGC(COUNTER_STRIKE_APP_ID, sendType, {}, Buffer.from(sendProto.encode(body).finish()), onMessage);
});

// One store request at a time per session: two answers of the same type would mix.
const queues = new WeakMap();
const serial = (session, work) => {
    const previous = queues.get(session) || Promise.resolve();
    const next = previous.catch(() => {}).then(work);
    queues.set(session, next);
    return next;
};

const getUserData = (session) => serial(session, () => request(
    session, Language.StoreGetUserData, Protos.CMsgStoreGetUserData, {},
    Language.StoreGetUserDataResponse, Protos.CMsgStoreGetUserDataResponse,
));

// Steam's approval request that follows an opened transaction, or null when none came
// in time. Its body is binary KeyValues ("MessageObject": transid — Steam's transaction
// id for the approval page —, orderid — the Game Coordinator's —, appid, line items,
// total, currency); Heimdall decodes and checks it.
const waitForAuthRequest = (session) => new Promise((resolve) => {
    const onRequest = (raw, header) => {
        clearTimeout(timer);
        resolve({ protobuf: Boolean(header && header.proto), hex: raw.toString('hex') });
    };
    const timer = setTimeout(() => {
        session.user.removeListener('microTxnAuthRequest', onRequest);
        resolve(null);
    }, AUTH_REQUEST_WAIT_MS);
    session.user.once('microTxnAuthRequest', onRequest);
});

const initPurchase = (session, { country, currency, quantity, unitPrice, itemDefinitionIndex = STORAGE_UNIT_DEFINITION_INDEX }) => serial(session, async () => {
    const authRequest = waitForAuthRequest(session);
    const answer = await request(
    session, Language.StorePurchaseInit, Protos.CMsgGCStorePurchaseInit,
    {
        country,
        language: 0,
        currency,
        line_items: [{
            item_def_id: itemDefinitionIndex,
            quantity,
            cost_in_local_currency: unitPrice,
            purchase_type: 0,
        }],
    },
    Language.StorePurchaseInitResponse, Protos.CMsgGCStorePurchaseInitResponse,
    );
    answer.authRequest = answer.result === 1 ? await authRequest : null;
    return answer;
});

const finalizePurchase = (session, transactionId) => serial(session, () => request(
    session, Language.StorePurchaseFinalize, Protos.CMsgGCStorePurchaseFinalize, { txn_id: String(transactionId) },
    Language.StorePurchaseFinalizeResponse, Protos.CMsgGCStorePurchaseFinalizeResponse, FINALIZE_TIMEOUT_MS,
));

const cancelPurchase = (session, transactionId) => serial(session, () => request(
    session, Language.StorePurchaseCancel, Protos.CMsgGCStorePurchaseCancel, { txn_id: String(transactionId) },
    Language.StorePurchaseCancelResponse, Protos.CMsgGCStorePurchaseCancelResponse,
));

const storageUnitCount = (session) => (session.csgo.inventory || [])
    .filter((item) => item.def_index === STORAGE_UNIT_DEFINITION_INDEX).length;

module.exports = {
    STORAGE_UNIT_DEFINITION_INDEX,
    getUserData,
    initPurchase,
    finalizePurchase,
    cancelPurchase,
    storageUnitCount,
};
