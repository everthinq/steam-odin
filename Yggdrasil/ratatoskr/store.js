// Counter-Strike 2 in-game store over the Game Coordinator (Storage Units for now).
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
// One store request per session at a time; the answers are matched by message type.
// `currency` is the game store's own 0-based ECurrency (USD 0, EUR 2, NOK 9, HKD 27 —
// Valve's econ_store.h), not Steam's wallet currency code (USD 1): the wallet code
// makes the Game Coordinator answer result 8, "invalid parameter".

const Language = require('globaloffensive/language.js');
const Protos = require('globaloffensive/protobufs/generated/_load.js');

const COUNTER_STRIKE_APP_ID = 730;
const STORAGE_UNIT_DEFINITION_INDEX = 1201;
const GAME_COORDINATOR_TIMEOUT_MS = 20000;

const decode = (proto, payload) => proto.toObject(proto.decode(payload), { longs: String, defaults: true, bytes: Buffer });

// Ask the Game Coordinator and wait for its answer of `replyType`.
const request = (session, sendType, sendProto, body, replyType, replyProto) => new Promise((resolve, reject) => {
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
    const onMessage = (appId, messageType, payload) => {
        if (appId !== COUNTER_STRIKE_APP_ID || messageType !== replyType) return;
        cleanup();
        try {
            resolve(decode(replyProto, payload));
        } catch (err) {
            reject(err);
        }
    };
    const timer = setTimeout(() => {
        session.storeClosed = true;
        cleanup();
        reject(new Error('The Game Coordinator did not answer in time'));
    }, GAME_COORDINATOR_TIMEOUT_MS);
    const cleanup = () => {
        clearTimeout(timer);
        user.removeListener('receivedFromGC', onMessage);
    };
    user.on('receivedFromGC', onMessage);
    if (!csgo._send(sendType, sendProto, body)) {
        cleanup();
        reject(new Error('Not logged into Steam'));
    }
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

const initPurchase = (session, { country, currency, quantity, unitPrice, itemDefinitionIndex = STORAGE_UNIT_DEFINITION_INDEX }) => serial(session, () => request(
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
));

const finalizePurchase = (session, transactionId) => serial(session, () => request(
    session, Language.StorePurchaseFinalize, Protos.CMsgGCStorePurchaseFinalize, { txn_id: String(transactionId) },
    Language.StorePurchaseFinalizeResponse, Protos.CMsgGCStorePurchaseFinalizeResponse,
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
