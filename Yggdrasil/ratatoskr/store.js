const ByteBuffer = require('bytebuffer');
const LZMA = require('lzma');
const VDF = require('@node-steam/vdf');
const {
    parseStorePriceSheetBinary,
    parseCs2StorePriceSheet,
} = require('./storeBinaryKv');
const axios = require('axios');
const SteamID = require('steamid');
const Language = require('globaloffensive/language.js');
const Protos = require('globaloffensive/protobufs/generated/_load.js');
const SteamUser = require('steam-user');
const ECurrencyCode = require('steam-user/enums/ECurrencyCode.js');

/** Wallet currency id → ISO country (for GC store + checkout). */
const CURRENCY_COUNTRY = {
    1: 'US',
    2: 'GB',
    3: 'EU',
    4: 'CH',
    5: 'RU',
    6: 'PL',
    7: 'BR',
    8: 'JP',
    9: 'NO',
    10: 'CN',
    11: 'ID',
    12: 'ZA',
    13: 'PH',
    14: 'IN',
    15: 'HK',
    16: 'TW',
    17: 'SA',
    18: 'AE',
    19: 'IL',
    20: 'KR',
    21: 'MX',
    22: 'TH',
    23: 'AU',
    24: 'NZ',
    25: 'VN',
    26: 'CA',
    27: 'TR',
    28: 'AR',
    29: 'PK',
    30: 'CL',
    31: 'PEN',
    32: 'CO',
    33: 'MY',
    34: 'UA',
    35: 'SG',
};

const STEAM_APPID = 730;
const STORE_LANGUAGE = 0;
const PURCHASE_TIMEOUT_MS = 60000;
const PURCHASE_FINALIZE_TIMEOUT_MS = 90000;

/** Structured purchase/checkout logs (set STORE_DIAG=0 to disable). */
const STORE_DIAG = process.env.STORE_DIAG !== '0';

const storeDiag = (phase, data = {}) => {
    if (!STORE_DIAG) return;
    const payload = { phase, ...data };
    try {
        console.log(`[STORE-DIAG] ${JSON.stringify(payload)}`);
    } catch {
        console.log(`[STORE-DIAG] ${phase}`, data);
    }
};

/** steam-user exposes SteamID as an object — not a string the steamid package accepts directly. */
const getSteamAccountId = (user) => {
    const sid = user?.steamID;
    if (!sid) return null;
    if (typeof sid.accountid === 'number' && sid.accountid > 0) {
        return sid.accountid;
    }
    const id64 =
        typeof sid.getSteamID64 === 'function'
            ? sid.getSteamID64()
            : typeof sid === 'string' || typeof sid === 'bigint'
              ? String(sid)
              : null;
    if (!id64) return null;
    return new SteamID(id64).accountid;
};

/**
 * In-game store items (item_def_id = schema index).
 * buyitem URLs: https://store.steampowered.com/buyitem/730/{defId}[/{qty}]
 * (same pattern as community PSAs for Copenhagen capsules, etc.)
 */
/** Minor units (cents/øre) — CS2 storage unit is ~$1.99 USD in-game. */
const STORAGE_UNIT_FALLBACK_PRICE = {
    1: 199, // USD $1.99
    2: 199,
    3: 199, // EUR
    9: 1850, // NOK kr 18.50 (in-game / SkinLedger)
    23: 299, // AUD
    26: 269, // CAD
};

/** Tried in order when GC price sheet is denied (wrong price → init error). */
const STORAGE_UNIT_PRICE_CANDIDATES = {
    1: [199, 249, 1990],
    2: [199, 249],
    3: [199, 249],
    9: [1850, 2200, 2300, 2500],
    23: [299, 399],
    26: [269, 349],
};

const STORE_PURCHASE_ERRORS = {
    1: 'Denied',
    2: 'Server error',
    3: 'Timeout',
    4: 'Invalid parameter (usually wrong price)',
    9: 'Limit exceeded',
    10: 'Commit unfinalized',
    200: 'Store unavailable (price list not loaded on GC — reconnect after GC sync)',
};

const FEATURED_ITEMS = {
    1201: {
        item_def_id: 1201,
        name: 'Storage Unit',
        description: 'Store up to 1,000 items from your CS2 inventory.',
        image: 'econ/tools/casket',
        max_quantity: 5,
        category: 'tools',
    },
};

const GC_RESULT_LABELS = {
    0: 'OK',
    1: 'Denied',
    2: 'Server error',
    3: 'Timeout',
    4: 'Invalid',
    5: 'No match',
    6: 'Unknown error',
    7: 'Not logged on',
    8: 'Failed to create',
    9: 'Limit exceeded',
    10: 'Commit unfinalized',
};

/** SkinLedger-style wallet approval after GC StorePurchaseInit. */
const buildApproveTxnUrl = (txnId) =>
    `https://checkout.steampowered.com/checkout/approvetxn/${txnId}/?returnurl=steam`;

/** Web checkout txn id (approvetxn URL); GC finalize uses gc txn_id from init. */
const resolveCheckoutTxnIds = (session, initResponse, purchaseStartedAt = 0) => {
    const gcTxnId =
        initResponse?.txn_id?.toString?.() ?? String(initResponse?.txn_id ?? '');
    const initResult = initResponse?.result ?? 0;
    let webTxnId = '';

    const url = initResponse?.url ? String(initResponse.url) : '';
    if (url.includes('approvetxn')) {
        const match = url.match(/approvetxn\/(\d+)/i);
        if (match?.[1]) webTxnId = match[1];
    }

    const microSince = session?.pendingStorePurchase?.purchaseInitSentAt || purchaseStartedAt;
    if (
        !webTxnId &&
        session?.lastMicroTxnId &&
        session.lastMicroTxnAt >= microSince &&
        isWalletMicroTxnId(session.lastMicroTxnId) &&
        !isStaleMicroTxn(session, session.lastMicroTxnId)
    ) {
        webTxnId = String(session.lastMicroTxnId);
    }

    // Successful init may return approvetxn URL with wallet id even when result != 0.
    if (!webTxnId && initResult === 0 && gcTxnId && isWalletMicroTxnId(gcTxnId)) {
        webTxnId = gcTxnId;
    }

    return {
        gcTxnId,
        webTxnId,
        authorizeUrl: webTxnId ? buildApproveTxnUrl(webTxnId) : '',
    };
};

/** GC accepted or created a pending store txn (result 0 or denied+txn_id). */
const isGcPurchaseInitUsable = (initResponse) => {
    const resultCode = initResponse?.result ?? 0;
    if (resultCode === 0) return true;
    return resultCode === 1 && hasValidPurchaseTxn(initResponse);
};

const cookiesToHeader = (cookieList, webSessionId) => {
    if (!cookieList?.length) return '';
    const parts = cookieList.map((c) => (typeof c === 'string' ? c : `${c.name}=${c.value}`));
    if (webSessionId && !parts.some((c) => c.startsWith('sessionid='))) {
        parts.push(`sessionid=${webSessionId}`);
    }
    return parts.join('; ');
};

/** Checkout often expects browserid (SkinLedger sends it on checkout.steampowered.com). */
const ensureBrowserIdCookie = (session) => {
    if (!session.webCookies) session.webCookies = [];
    const has = session.webCookies.some((c) => {
        const line = typeof c === 'string' ? c : `${c.name}=${c.value}`;
        return line.startsWith('browserid=');
    });
    if (!has) {
        const browserid = String(
            BigInt.asUintN(64, BigInt(Math.floor(Math.random() * Number.MAX_SAFE_INTEGER))) +
                BigInt(Math.floor(Math.random() * Number.MAX_SAFE_INTEGER))
        );
        session.webCookies.push(`browserid=${browserid}`);
    }
};

const sendClientMicroTxnAuthorize = (session, appId, transId, approved = 1) => {
    const { user } = session;
    if (!user) return false;
    const authBuf = ByteBuffer.allocate(13, ByteBuffer.LITTLE_ENDIAN);
    authBuf.writeUint32(appId >>> 0);
    authBuf.writeUint64(transId);
    authBuf.writeByte(approved ? 1 : 0);
    user._send(SteamUser.EMsg.ClientMicroTxnAuthorize, authBuf);
    if (approved) {
        session.microTxnAuthorizedId = String(transId);
    }
    console.log(
        `[STORE] ClientMicroTxnAuthorize(${approved ? 'approve' : 'deny'}) trans=${transId} app=${appId}`
    );
    return true;
};

const markMicroTxnStale = (session, transId) => {
    const id = String(transId || '');
    if (!id) return;
    if (!session.staleMicroTxnIds) session.staleMicroTxnIds = new Set();
    session.staleMicroTxnIds.add(id);
    if (!session.blockedMicroTxnIds) session.blockedMicroTxnIds = new Set();
    session.blockedMicroTxnIds.add(id);
};

const rememberFailedCheckoutMicroTxn = (session, transId) => {
    const id = String(transId || '');
    if (!id || !isWalletMicroTxnId(id)) return;
    if (!session.failedCheckoutMicroTxnIds) {
        session.failedCheckoutMicroTxnIds = new Set();
    }
    session.failedCheckoutMicroTxnIds.add(id);
    markMicroTxnStale(session, id);
};

const isStaleMicroTxn = (session, transId) => {
    const id = String(transId || '');
    if (!id) return true;
    return (
        session.staleMicroTxnIds?.has(id) ||
        session.blockedMicroTxnIds?.has(id) ||
        session.failedCheckoutMicroTxnIds?.has(id)
    );
};

/** Clear a stuck wallet charge so Steam issues a fresh microtxn for the next buy. */
const denyStaleMicroTxn = (session, transId, appId = null) => {
    const id = String(transId || '');
    if (!id || !isWalletMicroTxnId(id)) return;
    const app =
        appId ??
        session.pendingStorePurchase?.pendingMicroTxnAuth?.appId ??
        session.lastMicroTxnAppId ??
        STEAM_APPID;
    markMicroTxnStale(session, id);
    sendClientMicroTxnAuthorize(session, app, id, 0);
};

const cancelGcStorePurchase = async (session, gcTxnId) => {
    const txn = String(gcTxnId || '');
    if (!txn || txn === '0' || !session?.csgo?.haveGCSession) return null;
    try {
        const raw = await withStoreGcLock(session, async () => {
            const promise = waitForGcMessage(
                session,
                Language.StorePurchaseCancelResponse,
                15000
            );
            sendGc(session.csgo, Language.StorePurchaseCancel, Protos.CMsgGCStorePurchaseCancel, {
                txn_id: txn,
            });
            return promise;
        });
        const res = decodeProto(Protos.CMsgGCStorePurchaseCancelResponse, raw);
        console.log(`[STORE] StorePurchaseCancel txn=${txn} result=${res.result}`);
        return res;
    } catch (err) {
        console.warn(`[STORE] StorePurchaseCancel txn=${txn}:`, err.message);
        return null;
    }
};

const clearAbandonedStorePurchases = async (session) => {
    const prev = session.pendingStorePurchase;
    if (prev?.gcTxnId) {
        await cancelGcStorePurchase(session, prev.gcTxnId);
    } else if (prev?.txnId) {
        await cancelGcStorePurchase(session, prev.txnId);
    }

    const staleIds = new Set([
        ...(session.staleMicroTxnIds || []),
        ...(session.blockedMicroTxnIds || []),
        ...(session.failedCheckoutMicroTxnIds || []),
        session.lastMicroTxnId,
        prev?.webTxnId,
        prev?.microTxnId,
    ]);
    for (const id of staleIds) {
        if (id && isWalletMicroTxnId(id)) {
            denyStaleMicroTxn(session, id);
        }
    }
    delete session.pendingStorePurchase;
    session.lastMicroTxnId = null;
    session.lastMicroTxnAt = 0;
    session.microTxnAuthorizedId = null;
};

/** After web approvetxn succeeds, approve the pending GC microtxn in steam-user. */
const flushPendingMicroTxnAuth = (session) => {
    const pending = session.pendingStorePurchase;
    const auth = pending?.pendingMicroTxnAuth;
    if (!auth?.transId) return false;
    if (session.microTxnAuthorizedId === String(auth.transId)) {
        return true;
    }
    sendClientMicroTxnAuthorize(session, auth.appId, auth.transId, 1);
    pending.pendingMicroTxnAuth = null;
    return true;
};

const buildCheckoutHtmlHeaders = (session, cookieHeader, referer) => ({
    'User-Agent': BROWSER_UA,
    Accept:
        'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    Cookie: cookieHeader,
    Origin: 'https://checkout.steampowered.com',
    Referer: referer || 'https://checkout.steampowered.com/',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'same-origin',
    'Sec-Fetch-User': '?1',
    'Upgrade-Insecure-Requests': '1',
});

/** Hidden fields from Steam checkout authorize form. */
const extractCheckoutPostParams = (body, sessionId) => {
    const params = { sessionid: sessionId };
    if (!body || typeof body !== 'string') return params;

    const inputRe =
        /<input\b[^>]*\bname=["']([^"']+)["'][^>]*\bvalue=["']([^"']*)["'][^>]*>/gi;
    let match = inputRe.exec(body);
    while (match) {
        params[match[1]] = match[2];
        match = inputRe.exec(body);
    }
    const inputRe2 =
        /<input\b[^>]*\bvalue=["']([^"']*)["'][^>]*\bname=["']([^"']+)["'][^>]*>/gi;
    match = inputRe2.exec(body);
    while (match) {
        params[match[2]] = match[1];
        match = inputRe2.exec(body);
    }

    params.sessionid = sessionId;
    return params;
};

const postApproveTxnPage = async (url, body, cookieHeaders, sessionId) => {
    const postParams = extractCheckoutPostParams(body, sessionId);
    return axios.post(url, new URLSearchParams(postParams).toString(), {
        headers: {
            ...cookieHeaders,
            'Content-Type': 'application/x-www-form-urlencoded',
            Referer: url,
        },
        maxRedirects: 10,
        timeout: 30000,
        validateStatus: () => true,
    });
};

const ensureWebSession = (session, timeoutMs = 20000) =>
    new Promise((resolve, reject) => {
        if (session.webCookies?.length) {
            return resolve();
        }

        const { user } = session;
        if (!user?.steamID) {
            return reject(new Error('Not logged in to Steam'));
        }

        const timer = setTimeout(() => {
            user.removeListener('webSession', onWebSession);
            reject(new Error('Timed out waiting for Steam web session (cookies)'));
        }, timeoutMs);

        const onWebSession = (sessionID, cookies) => {
            clearTimeout(timer);
            session.webSessionId = sessionID;
            session.webCookies = cookies;
            resolve();
        };

        user.once('webSession', onWebSession);
        try {
            user.webLogOn();
        } catch (err) {
            clearTimeout(timer);
            user.removeListener('webSession', onWebSession);
            reject(err);
        }
    });

const waitForMicroTxnAuth = (session, txnId, timeoutMs = 8000) =>
    new Promise((resolve) => {
        const target = String(txnId);
        if (session.lastMicroTxnId === target) {
            return resolve(true);
        }

        const deadline = Date.now() + timeoutMs;
        const tick = () => {
            if (session.lastMicroTxnId === target) {
                return resolve(true);
            }
            if (Date.now() >= deadline) {
                return resolve(false);
            }
            setTimeout(tick, 100);
        };
        tick();
    });

/** Wait for a new ClientMicroTxnAuthRequest after this purchase started. */
const waitForFreshMicroTxn = (session, sinceMs, timeoutMs = 20000) =>
    new Promise((resolve) => {
        const deadline = Date.now() + timeoutMs;
        const tick = () => {
            const id = session.lastMicroTxnId;
            if (
                id &&
                session.lastMicroTxnAt >= sinceMs &&
                isWalletMicroTxnId(id) &&
                !isStaleMicroTxn(session, id)
            ) {
                return resolve(id);
            }
            if (Date.now() >= deadline) {
                return resolve(null);
            }
            setTimeout(tick, 50);
        };
        tick();
    });

const BROWSER_UA =
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';

/** Store/checkout country — must match wallet region, not always account registration country. */
const getSteamCountryCode = (session) => {
    const fromEnv = process.env.STORE_COUNTRY_CODE;
    if (fromEnv && /^[A-Za-z]{2}$/.test(fromEnv)) {
        return fromEnv.toUpperCase();
    }
    const fromStoreGc = session?.storeGcCountry;
    if (fromStoreGc && /^[A-Za-z]{2}$/.test(fromStoreGc)) {
        return fromStoreGc.toUpperCase();
    }
    const fromGcWelcome = session?.gcTxnCountryCode;
    if (fromGcWelcome && /^[A-Za-z]{2}$/.test(fromGcWelcome)) {
        return fromGcWelcome.toUpperCase();
    }
    const cur = session.user?.wallet?.currency;
    if (cur && CURRENCY_COUNTRY[cur]) {
        return CURRENCY_COUNTRY[cur];
    }
    const fromAccount = session.user?.accountInfo?.country;
    if (fromAccount && /^[A-Za-z]{2}$/.test(fromAccount)) {
        return fromAccount.toUpperCase();
    }
    return 'US';
};

const hasValidPurchaseTxn = (initResponse) => {
    const t = initResponse?.txn_id;
    if (t == null || t === 0) return false;
    return String(t) !== '0';
};

const isWalletMicroTxnId = (id) => {
    const s = String(id || '');
    return s.length >= 15 && /^\d+$/.test(s);
};

const getPurchaseMicroTxnSince = (session) =>
    session?.pendingStorePurchase?.purchaseInitSentAt ||
    session?.pendingStorePurchase?.purchaseStartedAt ||
    0;

/** GC init response may include approvetxn URL even when result=Denied. */
const captureInitCheckoutUrl = (session, initResponse) => {
    const url = initResponse?.url ? String(initResponse.url) : '';
    if (!url.includes('approvetxn')) return false;
    const match = url.match(/approvetxn\/(\d+)/i);
    if (!match?.[1] || !isWalletMicroTxnId(match[1])) return false;
    if (isStaleMicroTxn(session, match[1])) {
        console.warn(`[STORE] PurchaseInit checkout url ignored (stale) ${match[1]}`);
        return false;
    }
    session.lastMicroTxnId = match[1];
    session.lastMicroTxnAt = Date.now();
    console.log(`[STORE] PurchaseInit checkout url → microtxn ${match[1]}`);
    return true;
};

const sanitizePurchaseType = (value) => {
    const n = parseInt(value, 10) || 0;
    return n >= 0 && n <= 32 ? n : 0;
};

/** Init OK, denied+gc txn, denied+microtxn, or checkout URL on init response. */
const isPurchaseInitAccepted = (initResponse, session) => {
    const resultCode = initResponse?.result ?? 0;
    if (resultCode === 0) return true;
    captureInitCheckoutUrl(session, initResponse);
    if (resultCode === 1 && hasValidPurchaseTxn(initResponse)) {
        return true;
    }
    const since = getPurchaseMicroTxnSince(session);
    const hasMicro =
        since > 0 &&
        session?.lastMicroTxnId &&
        session.lastMicroTxnAt >= since &&
        isWalletMicroTxnId(session.lastMicroTxnId) &&
        !isStaleMicroTxn(session, session.lastMicroTxnId);
    return hasMicro && hasValidPurchaseTxn(initResponse);
};

const isFreshPurchaseMicroTxn = (session) => {
    const since = getPurchaseMicroTxnSince(session);
    return (
        since > 0 &&
        session?.lastMicroTxnId &&
        session.lastMicroTxnAt >= since &&
        isWalletMicroTxnId(session.lastMicroTxnId)
    );
};

const getAccessToken = (user) => user?._logOnDetails?.access_token || null;

/** Web API token for IStoreService (logon token often 401; steamLoginSecure JWT works). */
const getStoreWebAccessToken = (session) => {
    const fromLogon = getAccessToken(session.user);
    const cookies = session.webCookies || [];
    for (const raw of cookies) {
        const line = typeof raw === 'string' ? raw : `${raw.name}=${raw.value}`;
        if (!line.startsWith('steamLoginSecure=')) continue;
        const value = decodeURIComponent(line.slice('steamLoginSecure='.length));
        const jwt = value.includes('||') ? value.split('||').pop() : value;
        if (jwt && jwt.startsWith('ey')) return jwt;
    }
    return fromLogon;
};

/** Protobuf field 1 = country code (e.g. base64-encoded ISO country for store preferences). */
const encodeStoreCountryRequest = (countryCode) => {
    const cc = Buffer.from(String(countryCode).toUpperCase(), 'utf8');
    return Buffer.concat([Buffer.from([0x0a, cc.length]), cc]).toString('base64');
};

/** SkinLedger: warm Steam checkout session before GC StorePurchaseInit. */
const prepareSteamStoreSession = async (session) => {
    const country = getSteamCountryCode(session);
    await ensureWebSession(session);

    const accessToken = getStoreWebAccessToken(session);
    const accountId = getSteamAccountId(session.user);
    const cookieHeader = cookiesToHeader(session.webCookies, session.webSessionId);

    if (!cookieHeader) {
        return { ok: false, error: 'No Steam web session — reconnect Ratatoskr' };
    }

    const cookieHeaders = {
        'User-Agent': BROWSER_UA,
        Accept: '*/*',
        Cookie: cookieHeader,
        Origin: 'https://checkout.steampowered.com',
        Referer: 'https://store.steampowered.com/',
    };

    try {
        if (accountId) {
            const dyn = await axios.get('https://checkout.steampowered.com/dynamicstore/userdata/', {
                params: { id: accountId, cc: country },
                headers: cookieHeaders,
                timeout: 15000,
                validateStatus: () => true,
            });
            console.log(`[STORE] Pre-purchase dynamicstore HTTP ${dyn.status} cc=${country}`);
        }

        if (accessToken) {
            const prefsRes = await axios.get(
                'https://api.steampowered.com/IStoreService/GetStorePreferences/v1',
                {
                    params: {
                        access_token: accessToken,
                        origin: 'https://checkout.steampowered.com',
                        input_protobuf_encoded: encodeStoreCountryRequest(country),
                    },
                    headers: { 'User-Agent': BROWSER_UA, Cookie: cookieHeader },
                    timeout: 15000,
                    validateStatus: () => true,
                }
            );
            console.log(
                `[STORE] Pre-purchase GetStorePreferences HTTP ${prefsRes.status} cc=${country}`
            );
        } else {
            console.warn('[STORE] Pre-purchase: no web access token (GetStorePreferences skipped)');
        }
        return { ok: true, country };
    } catch (err) {
        console.warn('[STORE] Pre-purchase Steam session prep failed:', err.message);
        return { ok: false, error: err.message };
    }
};

const isCheckoutLoginWall = (body, finalUrl) =>
    /\/login\/?(\?|$)/i.test(finalUrl) ||
    (body.includes('New to Steam?') &&
        body.includes('Create an account') &&
        body.includes('Sign in to Steam'));

const isCheckoutSuccessBody = (body) =>
    /transactionfinalizing/i.test(body) ||
    /THANK YOU/i.test(body) ||
    /Thank You/i.test(body) ||
    /purchase completed/i.test(body);

const isCheckoutAuthorizePage = (body) =>
    /Review \+ Purchase/i.test(body) ||
    /btn_authorize/i.test(body) ||
    /AuthorizeTransaction/i.test(body) ||
    (body.includes('Payment Info') && body.includes('Authorize'));

const isCheckoutHardError = (body) =>
    /An unexpected error occurred while authorizing your transaction/i.test(body) ||
    /An error was encountered while processing your request/i.test(body) ||
    /cannot complete the purchase/i.test(body);

const isCheckoutAlreadyAuthorizedError = (body) =>
    isCheckoutHardError(body) &&
    /unexpected error occurred while authorizing/i.test(body);

/**
 * SkinLedger checkout: GET approvetxn page, POST confirm, then dynamicstore + GetStorePreferences.
 */
const approveCheckoutTxn = async (session, txnIdStr) => {
    const txn = String(txnIdStr);
    const url = buildApproveTxnUrl(txn);
    const country = getSteamCountryCode(session);
    const accountId = getSteamAccountId(session.user);

    await ensureWebSession(session);
    ensureBrowserIdCookie(session);

    const accessToken = getStoreWebAccessToken(session);
    if (!accessToken) {
        return {
            ok: false,
            url,
            error: 'No Steam web access token — disconnect and reconnect Ratatoskr',
        };
    }

    const cookieHeader = cookiesToHeader(session.webCookies, session.webSessionId);
    if (!cookieHeader) {
        return { ok: false, url, error: 'No web session cookies' };
    }

    const sessionId =
        session.webSessionId ||
        cookieHeader.match(/sessionid=([^;]+)/)?.[1] ||
        '';

    const cookieHeaders = buildCheckoutHtmlHeaders(
        session,
        cookieHeader,
        'https://store.steampowered.com/'
    );

    if (accountId) {
        try {
            await axios.get('https://checkout.steampowered.com/dynamicstore/userdata/', {
                params: { id: accountId, cc: country },
                headers: cookieHeaders,
                timeout: 15000,
                validateStatus: () => true,
            });
        } catch {
            /* warm checkout cookies */
        }
    }

    storeDiag('approvetxn_start', {
        txn,
        url,
        country,
        account_id: accountId,
        has_access_token: Boolean(accessToken),
        has_sessionid: Boolean(sessionId),
    });

    try {
        const getRes = await axios.get(url, {
            headers: cookieHeaders,
            maxRedirects: 10,
            timeout: 30000,
            validateStatus: () => true,
        });

        let body = typeof getRes.data === 'string' ? getRes.data : '';
        let finalUrl = getRes.request?.res?.responseUrl || url;
        storeDiag('approvetxn_get', {
            txn,
            http_status: getRes.status,
            final_url: finalUrl,
            login_wall: isCheckoutLoginWall(body, finalUrl),
            authorize_page: isCheckoutAuthorizePage(body),
            success_body: isCheckoutSuccessBody(body),
            hard_error: isCheckoutHardError(body),
            body_len: body.length,
        });

        if (isCheckoutLoginWall(body, finalUrl)) {
            return {
                ok: false,
                url,
                error: 'Steam checkout session expired — disconnect and reconnect Ratatoskr',
            };
        }

        const shouldPost =
            sessionId &&
            (isCheckoutAuthorizePage(body) ||
                isCheckoutHardError(body) ||
                /purchase\.confirm|btn_authorize|AuthorizeTransaction/i.test(body));

        if (isCheckoutSuccessBody(body)) {
            console.log(`[STORE] approvetxn ${txn} already completed`);
        } else if (shouldPost) {
            if (isCheckoutHardError(body)) {
                console.warn(
                    `[STORE] approvetxn GET ${txn} error page — trying POST (web-first checkout)`
                );
            }
            const postRes = await postApproveTxnPage(url, body, cookieHeaders, sessionId);
            body = typeof postRes.data === 'string' ? postRes.data : body;
            finalUrl = postRes.request?.res?.responseUrl || finalUrl;
            console.log(`[STORE] approvetxn POST ${txn} HTTP ${postRes.status}`);
            storeDiag('approvetxn_post', {
                txn,
                http_status: postRes.status,
                final_url: finalUrl,
                success_body: isCheckoutSuccessBody(body),
                hard_error: isCheckoutHardError(body),
            });
        } else {
            console.warn(
                `[STORE] approvetxn GET ${txn} unexpected page (HTTP ${getRes.status})`
            );
        }

        if (isCheckoutHardError(body) && !isCheckoutSuccessBody(body)) {
            rememberFailedCheckoutMicroTxn(session, txn);
            return {
                ok: false,
                url,
                error: 'Steam wallet checkout failed on approvetxn — try again',
                already_authorized: isCheckoutAlreadyAuthorizedError(body),
            };
        }

        flushPendingMicroTxnAuth(session);
        await sleep(400);

        if (accountId) {
            const dynCheckout = await axios.get(
                'https://checkout.steampowered.com/dynamicstore/userdata/',
                {
                    params: { id: accountId, cc: country },
                    headers: cookieHeaders,
                    timeout: 15000,
                    validateStatus: () => true,
                }
            );
            const dynStore = await axios.get(
                'https://store.steampowered.com/dynamicstore/userdata/',
                {
                    params: { id: accountId, cc: country },
                    headers: {
                        ...cookieHeaders,
                        Referer: 'https://checkout.steampowered.com/',
                    },
                    timeout: 15000,
                    validateStatus: () => true,
                }
            );
            storeDiag('checkout_dynamicstore', {
                txn: txn,
                country,
                account_id: accountId,
                checkout_status: dynCheckout.status,
                store_status: dynStore.status,
            });
        }

        await axios.get('https://api.steampowered.com/IStoreService/GetStorePreferences/v1', {
            params: {
                access_token: accessToken,
                origin: 'https://checkout.steampowered.com',
                input_protobuf_encoded: encodeStoreCountryRequest(country),
            },
            headers: { 'User-Agent': BROWSER_UA, Cookie: cookieHeader },
            timeout: 15000,
            validateStatus: () => true,
        });

        const ok =
            isCheckoutSuccessBody(body) ||
            (!isCheckoutLoginWall(body, finalUrl) &&
                !isCheckoutHardError(body) &&
                (isCheckoutAuthorizePage(body) || finalUrl.includes('checkout')));

        if (ok) {
            console.log(`[STORE] web checkout ${txn} OK`);
            return { ok: true, url, country };
        }

        return {
            ok: false,
            url,
            error: `Checkout did not complete (HTTP ${getRes.status})`,
        };
    } catch (err) {
        console.error('[STORE] web checkout failed:', err.message);
        return { ok: false, url, error: err.message };
    }
};

const completeWebCheckout = approveCheckoutTxn;

const buildBuyItemUrl = (itemDefId, quantity = 1) => {
    const id = parseInt(itemDefId, 10);
    const qty = Math.max(1, parseInt(quantity, 10) || 1);
    if (qty > 1) {
        return `https://store.steampowered.com/buyitem/${STEAM_APPID}/${id}/${qty}`;
    }
    return `https://store.steampowered.com/buyitem/${STEAM_APPID}/${id}`;
};

/** Same decoding as globaloffensive/handlers.js (ByteBuffer → Buffer, longs as strings). */
const decodeProto = (proto, encoded) => {
    if (ByteBuffer.isByteBuffer(encoded)) {
        encoded = encoded.toBuffer();
    } else if (encoded instanceof Uint8Array) {
        encoded = Buffer.from(encoded);
    }

    if (!encoded || !encoded.length) {
        throw new Error('Empty GC message body');
    }

    const decoded = proto.decode(encoded);
    const objNoDefaults = proto.toObject(decoded, { longs: String });
    const objWithDefaults = proto.toObject(decoded, { defaults: true, longs: String });
    return replaceProtoDefaults(objNoDefaults, objWithDefaults);
};

const replaceProtoDefaults = (noDefaults, withDefaults) => {
    if (Array.isArray(withDefaults)) {
        return withDefaults.map((val, idx) => replaceProtoDefaults(noDefaults[idx], val));
    }

    for (const i of Object.keys(withDefaults)) {
        if (
            withDefaults[i] &&
            typeof withDefaults[i] === 'object' &&
            !Buffer.isBuffer(withDefaults[i])
        ) {
            withDefaults[i] = replaceProtoDefaults(noDefaults[i], withDefaults[i]);
        } else if (
            typeof noDefaults[i] === 'undefined' &&
            isReplaceableDefaultValue(withDefaults[i])
        ) {
            withDefaults[i] = null;
        }
    }

    return withDefaults;
};

const isReplaceableDefaultValue = (val) => {
    if (Buffer.isBuffer(val) && val.length === 0) return true;
    if (Array.isArray(val)) return false;
    if (val === '0') return true;
    return !val;
};

const toBuffer = (data) => {
    if (!data) return Buffer.alloc(0);
    if (Buffer.isBuffer(data)) return data;
    if (data instanceof Uint8Array) return Buffer.from(data);
    if (ByteBuffer.isByteBuffer(data)) return data.toBuffer();
    return Buffer.from(data);
};

const decompressValveLZMA = (buffer) =>
    new Promise((resolve, reject) => {
        if (!buffer || buffer.length < 13) {
            return reject(new Error('Price sheet buffer too short'));
        }

        const magic = buffer.readUInt32BE(0);
        if (magic !== 0x4c5a4d41) {
            return reject(new Error('Price sheet is not Valve LZMA compressed'));
        }

        const uncompressedLength = buffer.readUInt32LE(4);
        const compressedLength = buffer.readUInt32LE(8);
        const expectedTotal = 12 + 5 + compressedLength;
        if (buffer.length < expectedTotal) {
            return reject(
                new Error(
                    `Price sheet truncated (expected ${expectedTotal}, got ${buffer.length})`
                )
            );
        }

        const headerEnd = 17;
        const available = buffer.length - headerEnd;
        if (compressedLength <= 0 || compressedLength > available) {
            compressedLength = available;
        }

        const props = buffer.slice(12, 17);
        const lzmaData = buffer.slice(headerEnd, headerEnd + compressedLength);

        const uncompressedSizeBuffer = Buffer.alloc(8);
        uncompressedSizeBuffer.writeUInt32LE(uncompressedLength, 0);
        uncompressedSizeBuffer.writeUInt32LE(0, 4);
        const fullStream = Buffer.concat([props, uncompressedSizeBuffer, lzmaData]);

        const finish = (result, err) => {
            if (err) return reject(err);
            const out = lzmaBytesToBuffer(result);
            if (uncompressedLength > 0 && out.length !== uncompressedLength) {
                console.warn(
                    `[STORE] LZMA decoded ${out.length} bytes (header says ${uncompressedLength})`
                );
            }
            resolve(out);
        };

        try {
            finish(LZMA.decompress(fullStream));
        } catch (syncErr) {
            LZMA.decompress(fullStream, finish);
        }
    });

/** lzma-js may return a number[] or latin1 string — normalize to Buffer for BinaryKV. */
const lzmaBytesToBuffer = (result) => {
    if (!result) return Buffer.alloc(0);
    if (Buffer.isBuffer(result)) return result;
    if (Array.isArray(result)) return Buffer.from(result);
    if (typeof result === 'string') return Buffer.from(result, 'latin1');
    return Buffer.from(result);
};

const PRICE_FIELD_NAMES = new Set([
    'price',
    'Price',
    'cost',
    'local_price',
    'cost_in_local_currency',
    'account_price',
    'account_price_in_local_currency',
    'base_price',
    'final_price',
]);

const DEF_FIELD_NAMES = new Set([
    'item_def_id',
    'item_def',
    'def_index',
    'def_id',
    'itemdef',
    'itemdefid',
]);

const KV_NAME_TO_DEF = {
    casket: 1201,
    tool_casket: 1201,
};

/** Wallet currency ids in the GC price sheet are usually 1–37 (ECurrencyCode). */
const isCurrencyBucketKey = (key) => {
    if (!/^\d{1,2}$/.test(key)) return false;
    const n = parseInt(key, 10);
    return n > 0 && n <= 37;
};

/** Store catalog keys are item def_index (e.g. 1201), not currency buckets. */
const isItemDefKey = (key, num) => {
    if (!/^\d+$/.test(key)) return false;
    if (isCurrencyBucketKey(key)) return false;
    return num >= 100 && num <= 60000;
};

const ingestPrice = (items, defId, price, purchaseType = 0, ctx = {}) => {
    const id = parseInt(defId, 10);
    const cost = parseInt(price, 10);
    const { currencyBucket, targetCurrency, explicitDef } = ctx;
    if (id <= 0 || cost <= 0) return;
    if (
        currencyBucket != null &&
        targetCurrency != null &&
        currencyBucket !== targetCurrency &&
        !explicitDef
    ) {
        return;
    }
    items[id] = {
        item_def_id: id,
        cost_in_local_currency: cost,
        purchase_type: sanitizePurchaseType(purchaseType),
        currency_bucket: currencyBucket ?? null,
        explicit: Boolean(explicitDef),
    };
};

/** Wallet currency id → ISO 4217 code used in sheet `entries.*.prices` (e.g. NOK). */
const getWalletCurrencyIso = (currencyId) => {
    const id = parseInt(currencyId, 10);
    return ECurrencyCode[id] || null;
};

/** Ingest one catalog row from CS2 `entries.{name}.prices.{ISO}`. */
const ingestStoreEntryPrices = (entryKey, entry, items, ctx = {}) => {
    if (!entry || typeof entry !== 'object' || Buffer.isBuffer(entry)) return;

    let defId = KV_NAME_TO_DEF[entryKey] ?? 0;
    if (!defId && /^\d+$/.test(entryKey)) {
        const n = parseInt(entryKey, 10);
        if (isItemDefKey(entryKey, n)) defId = n;
    }
    if (defId <= 0) return;

    const iso = getWalletCurrencyIso(ctx.targetCurrency);
    if (entry.prices && typeof entry.prices === 'object' && iso) {
        const fromIso = kvPriceToInt(entry.prices[iso]);
        if (fromIso > 0) {
            ingestPrice(items, defId, fromIso, 0, { ...ctx, explicitDef: true });
            return;
        }
    }

    for (const field of PRICE_FIELD_NAMES) {
        if (entry[field] != null) {
            const cost = kvPriceToInt(entry[field]);
            if (cost > 0) {
                ingestPrice(items, defId, cost, 0, { ...ctx, explicitDef: true });
            }
        }
    }
};

/** Normalize parse output: `{ store: { entries } }` vs flat `{ entries }`. */
const normalizeStoreKvRoot = (kvRoot) => {
    if (!kvRoot || typeof kvRoot !== 'object' || Buffer.isBuffer(kvRoot)) return kvRoot;
    if (kvRoot.store && typeof kvRoot.store === 'object' && !Buffer.isBuffer(kvRoot.store)) {
        return kvRoot;
    }
    if (
        kvRoot.entries ||
        kvRoot.store_metadata ||
        kvRoot.store_banner_layout ||
        kvRoot.currencies
    ) {
        return { store: kvRoot };
    }
    return kvRoot;
};

const getStoreSheetEntries = (kvRoot) => {
    const root = normalizeStoreKvRoot(kvRoot);
    const store =
        root?.store && typeof root.store === 'object' && !Buffer.isBuffer(root.store)
            ? root.store
            : root;
    const entries = store?.entries;
    if (!entries || typeof entries !== 'object' || Buffer.isBuffer(entries)) return null;
    return entries;
};

/** Fast path: `entries.casket.prices.NOK` (Storage Unit) without deep KV walk. */
const extractCasketPriceDirect = (kvRoot, items, ctx = {}) => {
    const entries = getStoreSheetEntries(kvRoot);
    if (!entries) return false;
    let found = false;
    for (const key of ['casket', 'tool_casket']) {
        if (!entries[key]) continue;
        ingestStoreEntryPrices(key, entries[key], items, { ...ctx, explicitDef: true });
        if (items[STORAGE_UNIT_DEF_ID]) found = true;
    }
    return found;
};

/** CS2 GC sheet layout: store → entries → { casket → { prices: { NOK: "1850", … } } }. */
const extractPricesFromStoreSheet = (kvRoot, items, ctx = {}) => {
    if (!kvRoot || typeof kvRoot !== 'object') return;
    const store =
        normalizeStoreKvRoot(kvRoot).store ||
        (kvRoot.store && typeof kvRoot.store === 'object' && !Buffer.isBuffer(kvRoot.store)
            ? kvRoot.store
            : kvRoot);
    if (store.entries && typeof store.entries === 'object' && !Buffer.isBuffer(store.entries)) {
        for (const [key, entry] of Object.entries(store.entries)) {
            ingestStoreEntryPrices(key, entry, items, ctx);
        }
        extractPricesFromKv(store.entries, items, ctx);
    }
    if (store.currencies && typeof store.currencies === 'object' && !Buffer.isBuffer(store.currencies)) {
        extractPricesFromKv(store.currencies, items, {
            ...ctx,
            currencyBucket: ctx.targetCurrency,
        });
    }
};

/** Deep-walk CS2 VBKV price sheet (currency buckets → item def_index → price). */
const extractPricesFromKv = (node, items, ctx = {}) => {
    if (node == null) return;
    if (Array.isArray(node)) {
        for (const entry of node) {
            extractPricesFromKv(entry, items, ctx);
        }
        return;
    }
    if (Buffer.isBuffer(node)) {
        if (node.length >= 16) {
            try {
                const nested = parseStorePriceSheetBinary(node);
                extractPricesFromStoreSheet(nested.tree, items, ctx);
                extractPricesFromKv(nested.tree, items, ctx);
            } catch {
                /* not nested KV */
            }
        }
        return;
    }
    if (typeof node !== 'object') return;

    let defId = ctx.defId ?? 0;
    let purchaseType = ctx.purchaseType ?? 0;
    let hadExplicitDef = false;

    for (const field of DEF_FIELD_NAMES) {
        if (node[field] != null) {
            defId = kvPriceToInt(node[field]);
            hadExplicitDef = true;
        }
    }
    if (node.purchase_type != null || node.purchaseType != null) {
        purchaseType = kvPriceToInt(node.purchase_type ?? node.purchaseType);
    }
    for (const field of PRICE_FIELD_NAMES) {
        if (node[field] != null && defId > 0) {
            const cost = kvPriceToInt(node[field]);
            if (cost > 0) {
                ingestPrice(items, defId, cost, purchaseType, {
                    ...ctx,
                    explicitDef: hadExplicitDef,
                });
            }
        }
    }

    for (const [key, value] of Object.entries(node)) {
        if (value == null) continue;

        if (KV_NAME_TO_DEF[key]) {
            const namedDef = KV_NAME_TO_DEF[key];
            if (value && typeof value === 'object' && !Buffer.isBuffer(value)) {
                extractPricesFromKv(value, items, {
                    ...ctx,
                    defId: namedDef,
                    purchaseType,
                });
            } else {
                const scalarCost = kvPriceToInt(value);
                if (scalarCost > 0) {
                    ingestPrice(items, namedDef, scalarCost, purchaseType, {
                        ...ctx,
                        explicitDef: true,
                    });
                }
            }
            continue;
        }

        if (isCurrencyBucketKey(key) && value && typeof value === 'object') {
            extractPricesFromKv(value, items, {
                ...ctx,
                currencyBucket: parseInt(key, 10),
                defId: 0,
            });
            continue;
        }

        const keyNum = /^\d+$/.test(key) ? parseInt(key, 10) : 0;
        if (isItemDefKey(key, keyNum)) {
            if (value && typeof value === 'object' && !Buffer.isBuffer(value)) {
                extractPricesFromKv(value, items, {
                    ...ctx,
                    defId: keyNum,
                    purchaseType,
                });
            } else {
                const scalarCost = kvPriceToInt(value);
                if (scalarCost > 0) {
                    ingestPrice(items, keyNum, scalarCost, purchaseType, ctx);
                }
            }
            continue;
        }

        if (value && typeof value === 'object') {
            extractPricesFromKv(value, items, { ...ctx, defId, purchaseType });
        }
    }
};

/** Scan decompressed sheet for Storage Unit (1201) + wallet price without full KV tree. */
const findStorageUnitPriceInBinary = (buf, items, ctx = {}) => {
    const defId = STORAGE_UNIT_DEF_ID;
    const currency = ctx.targetCurrency ?? 0;
    const needle = Buffer.alloc(4);
    needle.writeUInt32LE(defId, 0);
    const currencyNeedle =
        currency > 0 && currency < 256 ? Buffer.from([currency, 0, 0, 0]) : null;

    let best = null;
    let pos = 0;
    while (pos < buf.length - 12) {
        const idx = buf.indexOf(needle, pos);
        if (idx === -1) break;
        for (let off = -24; off <= 40; off += 4) {
            const priceAt = idx + off;
            if (priceAt < 0 || priceAt + 4 > buf.length) continue;
            const cost = buf.readUInt32LE(priceAt);
            if (cost < 100 || cost > 500_000) continue;
            let score = 0;
            if (currencyNeedle) {
                const winStart = Math.max(0, idx - 48);
                const winEnd = Math.min(buf.length, idx + 48);
                if (buf.indexOf(currencyNeedle, winStart, winEnd) !== -1) {
                    score += 10;
                }
            }
            if (Math.abs(priceAt - idx) <= 8) score += 5;
            if (cost >= 500 && cost <= 50_000) score += 3;
            if (!best || score > best.score) {
                best = { cost, score };
            }
        }
        pos = idx + 1;
    }

    if (best?.score >= 1) {
        ingestPrice(items, defId, best.cost, 0, { ...ctx, explicitDef: true });
        console.log(
            `[STORE] Storage Unit ${defId} price ${best.cost} from binary scan (currency ${currency}, score ${best.score})`
        );
    }
};

const logPriceSheetKvShape = (kvRoot) => {
    if (!kvRoot || typeof kvRoot !== 'object') return;
    const keys = Object.keys(kvRoot).slice(0, 12);
    console.log(`[STORE] Price sheet KV root keys: ${keys.join(', ') || '(none)'}`);
};

/** Parse decompressed sheet for def_index → price in the account wallet currency. */
const parsePriceSheet = (rawBuffer, targetCurrency = 1) => {
    const items = {};
    if (!rawBuffer?.length) return items;

    const ctx = { targetCurrency };
    let kvRoot = null;
    let parseErr = null;
    let parseMeta = null;
    try {
        const parsed = parseCs2StorePriceSheet(rawBuffer);
        kvRoot = parsed.tree;
        parseMeta = {
            parser: 'cs2',
            offset: parsed.offset,
            string_table: parsed.stringTableSize ?? 0,
            end_marker: parsed.endMarker,
        };
        const meta = [
            `kv_offset=${parsed.offset}`,
            parsed.stringTableSize ? `strtab=${parsed.stringTableSize}` : null,
            parsed.endMarker === 11 ? 'VBKV_end' : null,
        ]
            .filter(Boolean)
            .join(' ');
        if (meta) console.log(`[STORE] Price sheet parse ${meta}`);
    } catch (err) {
        parseErr = err;
        try {
            const parsed = parseStorePriceSheetBinary(rawBuffer);
            kvRoot = parsed.tree;
            parseMeta = {
                parser: 'binary',
                offset: parsed.offset,
                string_table: parsed.stringTableSize ?? 0,
                end_marker: parsed.endMarker,
            };
            console.log(
                `[STORE] Price sheet fallback parse offset=${parsed.offset} strtab=${parsed.stringTableSize ?? 0}`
            );
        } catch (fallbackErr) {
            parseErr = fallbackErr;
        }
    }

    if (kvRoot) {
        parseErr = null;
        kvRoot = normalizeStoreKvRoot(kvRoot);
        const storeRoot =
            kvRoot?.store && typeof kvRoot.store === 'object' ? kvRoot.store : kvRoot;
        const entries = getStoreSheetEntries(kvRoot);
        logPriceSheetKvShape(storeRoot);
        extractCasketPriceDirect(kvRoot, items, ctx);
        extractPricesFromStoreSheet(kvRoot, items, ctx);
        extractPricesFromKv(kvRoot, items, ctx);
        storeDiag('price_sheet_parsed', {
            currency: targetCurrency,
            parse_meta: parseMeta,
            parse_error: parseErr?.message ?? null,
            root_keys: Object.keys(storeRoot || {}).slice(0, 8),
            entries_count: entries ? Object.keys(entries).length : 0,
            has_casket: Boolean(entries?.casket),
            casket_nok: entries?.casket?.prices?.NOK ?? null,
            prices_found: Object.keys(items).length,
            storage_unit: items[STORAGE_UNIT_DEF_ID]?.cost_in_local_currency ?? null,
        });
    }
    if (parseErr && !items[STORAGE_UNIT_DEF_ID]) {
        console.warn('[STORE] Steam binary KV price sheet parse failed:', parseErr.message);
    } else if (items[STORAGE_UNIT_DEF_ID]) {
        console.log(
            `[STORE] Storage Unit sheet price=${items[STORAGE_UNIT_DEF_ID].cost_in_local_currency} ` +
                `(currency ${targetCurrency}, explicit=${items[STORAGE_UNIT_DEF_ID].explicit})`
        );
    }

    if (!items[1201]) {
        try {
            const parsed = VDF.parse(rawBuffer.toString('utf8'));
            extractPricesFromKv(parsed, items, ctx);
        } catch {
            /* not text VDF */
        }
    }

    if (!items[1201]) {
        findStorageUnitPriceInBinary(rawBuffer, items, ctx);
    }

    const count = Object.keys(items).length;
    if (count > 0) {
        const sample = Object.entries(items)
            .slice(0, 6)
            .map(([id, e]) => `${id}=${e.cost_in_local_currency}`)
            .join(', ');
        console.log(`[STORE] Parsed ${count} sheet price(s) for currency ${targetCurrency} (sample: ${sample})`);
    }

    return items;
};

const STORAGE_UNIT_DEF_ID = 1201;

const snapshotStoreSession = (session) => {
    if (!session) return { session: false };
    const user = session.user;
    const accountId = getSteamAccountId(user);
    const priceMap = session.priceMap || {};
    const storage = priceMap[STORAGE_UNIT_DEF_ID];
    return {
        account_id: accountId,
        gc_connected: Boolean(session.csgo?.haveGCSession),
        gc_pricesheet_version: session.gcPricesheetVersion ?? null,
        required_app_version: session.requiredAppIdVersion ?? null,
        last_price_sheet_version: session.lastPriceSheetVersion ?? null,
        price_map_size: Object.keys(priceMap).length,
        storage_unit_price: storage?.cost_in_local_currency ?? null,
        storage_unit_explicit: storage?.explicit ?? false,
        wallet_currency: user?.wallet?.currency ?? null,
        wallet_balance: user?.wallet?.balance ?? null,
        web_session: Boolean(session.webSessionId || session.webCookies?.length),
        last_micro_txn: session.lastMicroTxnId ?? null,
        pending_purchase: session.pendingStorePurchase
            ? {
                  phase: session.pendingStorePurchase.phase,
                  gc_txn: session.pendingStorePurchase.gcTxnId,
                  web_txn: session.pendingStorePurchase.webTxnId,
                  init_result: session.pendingStorePurchase.initResult,
              }
            : null,
        store_gc_country: session.storeGcCountry ?? null,
    };
};

/** Storage Unit purchase line — def_index 1201 is correct; sheet/fallback supplies price. */
const resolveStorageUnitStoreLine = (session, currency) => {
    const priceMap = session?.priceMap || {};
    const fromSheet = priceMap[STORAGE_UNIT_DEF_ID];
    const fallback = STORAGE_UNIT_FALLBACK_PRICE[currency];
    const cost = fromSheet?.cost_in_local_currency || fallback;
    return {
        item_def_id: STORAGE_UNIT_DEF_ID,
        name: 'Storage Unit',
        cost_in_local_currency: cost,
        purchase_type: sanitizePurchaseType(fromSheet?.purchase_type ?? 0),
        price_label: cost ? formatPrice(cost, currency) : null,
        price_source: fromSheet
            ? fromSheet.explicit
                ? 'gc_sheet_explicit'
                : 'gc_sheet'
            : fallback
              ? 'fallback'
              : 'unknown',
        inventory_def_index: STORAGE_UNIT_DEF_ID,
    };
};

const isHeadfulStoreMode = () => process.env.STORE_GC_SESSION_MODE !== 'headless';

const STORE_GC_RESPONSE_TYPES = [
    Language.StoreGetUserDataResponse,
    Language.StorePurchaseInitResponse,
    Language.StorePurchaseInitResponse_DEPRECATED,
    Language.StorePurchaseFinalizeResponse,
    Language.StorePurchaseCancelResponse,
    Language.StorePurchaseQueryTxnResponse,
];

/** One in-flight GC store RPC at a time (avoids handler races). */
const withStoreGcLock = (session, fn) => {
    const prev = session._storeGcChain || Promise.resolve();
    const next = prev.then(() => fn());
    session._storeGcChain = next.catch(() => {});
    return next;
};

const ensureStoreGcDispatcher = (session) => {
    if (!session?.csgo || session.storeGcDispatcherInstalled) return;

    if (!session.gcWaiters) {
        session.gcWaiters = {};
    }

    const { csgo } = session;
    for (const msgType of STORE_GC_RESPONSE_TYPES) {
        const prior = csgo._handlers[msgType];
        csgo._handlers[msgType] = function onStoreGcResponse(body) {
            const queue = session.gcWaiters[msgType];
            if (queue?.length) {
                const waiter = queue.shift();
                clearTimeout(waiter.timer);
                waiter.resolve(body);
            }
            if (prior) prior.call(this, body);
        };
    }

    session.storeGcDispatcherInstalled = true;
};

const waitForGcMessage = (session, msgType, timeoutMs = 15000) => {
    ensureStoreGcDispatcher(session);
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
            const queue = session.gcWaiters[msgType];
            if (queue) {
                const i = queue.findIndex((w) => w.resolve === resolve);
                if (i >= 0) queue.splice(i, 1);
            }
            reject(new Error(`GC message ${msgType} timed out`));
        }, timeoutMs);

        if (!session.gcWaiters[msgType]) {
            session.gcWaiters[msgType] = [];
        }
        session.gcWaiters[msgType].push({ resolve, reject, timer });
    });
};

const sendGc = (csgo, msgType, proto, body) => {
    csgo._send(msgType, proto, body);
};

const sendGcRaw = (csgo, msgType, buffer) => {
    if (!csgo?._steam?.steamID) return false;
    csgo._steam.sendToGC(STEAM_APPID, msgType, {}, buffer);
    return true;
};

const encodeVarint = (value) => {
    const n = BigInt(value);
    const out = [];
    let v = n;
    while (v >= 0x80n) {
        out.push(Number((v & 0x7fn) | 0x80n));
        v >>= 7n;
    }
    out.push(Number(v));
    return Buffer.from(out);
};

const encodeUint32Field = (fieldNum, value) => {
    const n = Number(value) >>> 0;
    if (!n) return Buffer.alloc(0);
    return Buffer.concat([encodeVarint((fieldNum << 3) | 0), encodeVarint(n)]);
};

const encodeUint64Field = (fieldNum, value) => {
    const n = BigInt(value ?? 0);
    if (n === 0n) return Buffer.alloc(0);
    return Buffer.concat([encodeVarint((fieldNum << 3) | 0), encodeVarint(n)]);
};

const encodeStringField = (fieldNum, str) => {
    if (!str) return Buffer.alloc(0);
    const buf = Buffer.from(String(str), 'utf8');
    return Buffer.concat([
        encodeVarint((fieldNum << 3) | 2),
        encodeVarint(buf.length),
        buf,
    ]);
};

const encodeMessageField = (fieldNum, payload) =>
    Buffer.concat([
        encodeVarint((fieldNum << 3) | 2),
        encodeVarint(payload.length),
        payload,
    ]);

/** CS2 line item uses uint64 cost + optional supplemental_data (globaloffensive still uses uint32). */
const encodeCs2PurchaseLineItem = (lineItem) => {
    const parts = [
        encodeUint32Field(1, lineItem.item_def_id),
        encodeUint32Field(2, lineItem.quantity),
        encodeUint64Field(3, lineItem.cost_in_local_currency),
        encodeUint32Field(4, lineItem.purchase_type),
        encodeUint64Field(5, lineItem.supplemental_data),
    ].filter((b) => b.length > 0);
    return Buffer.concat(parts);
};

/** Encode CMsgGCStorePurchaseInit with CS2-compatible line items. */
const encodeCs2StorePurchaseInit = ({ country, language, currency, lineItem }) => {
    const parts = [
        encodeStringField(1, country),
        encodeUint32Field(2, language),
        encodeUint32Field(3, currency),
        encodeMessageField(4, encodeCs2PurchaseLineItem(lineItem)),
    ].filter((b) => b.length > 0);
    return Buffer.concat(parts);
};

/** Version we successfully downloaded — not the GC-published version from account stats. */
const getPriceSheetVersion = (session) => {
    if (session?.lastPriceSheetVersion != null) {
        return session.lastPriceSheetVersion >>> 0;
    }
    return 0;
};

const getGcPublishedPriceSheetVersion = (session) => {
    const fromAccount = session?.csgo?.accountData?.global_stats?.pricesheet_version;
    if (fromAccount > 0) return fromAccount >>> 0;
    if (session?.gcPricesheetVersion > 0) return session.gcPricesheetVersion >>> 0;
    return 0;
};

const logStoreGetUserDataResponse = (response, reqKey) => {
    const sheetLen = response?.price_sheet?.length ?? 0;
    const parts = [`result=${response?.result}`, `req=${reqKey}`];
    if (response?.price_sheet_version) {
        parts.push(`resp_ver=${response.price_sheet_version >>> 0}`);
    }
    if (response?.country_deprecated) {
        parts.push(`country=${response.country_deprecated}`);
    }
    if (sheetLen > 0) {
        parts.push(`sheet_bytes=${sheetLen}`);
    }
    console.log(`[STORE] StoreGetUserData ${parts.join(' ')}`);
};

/** Request bodies to try when we have no local price map yet (order matters). */
const buildStoreGetUserDataVariants = (session) => {
    const walletCurrency = session.user?.wallet?.currency ?? 0;
    const hasCachedSheet = session?.priceMap && Object.keys(session.priceMap).length > 0;
    const localVersion = getPriceSheetVersion(session);
    const gcPublished = getGcPublishedPriceSheetVersion(session);
    const variants = [];

    if (!hasCachedSheet) {
        variants.push({});
        variants.push({ price_sheet_version: 0 });
        if (walletCurrency > 0) {
            variants.push({ currency: walletCurrency });
            variants.push({ price_sheet_version: 0, currency: walletCurrency });
        }
        if (gcPublished > 0) {
            variants.push({ price_sheet_version: gcPublished });
            if (walletCurrency > 0) {
                variants.push({ price_sheet_version: gcPublished, currency: walletCurrency });
            }
        }
    } else {
        const ver = localVersion || gcPublished || 0;
        variants.push({ price_sheet_version: ver });
        if (walletCurrency > 0) {
            variants.push({ price_sheet_version: ver, currency: walletCurrency });
        }
        variants.push({ price_sheet_version: 0 });
    }

    const seen = new Set();
    return variants.filter((body) => {
        const key = JSON.stringify(body);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
};

const waitForGcInventoryReady = async (session, maxMs = 15000) => {
    const start = Date.now();
    while (Date.now() - start < maxMs) {
        if (Array.isArray(session?.csgo?.inventory)) {
            return true;
        }
        await sleep(200);
    }
    return Array.isArray(session?.csgo?.inventory);
};

const warmupStoreGcSession = async (session) => {
    if (!session?.csgo?.haveGCSession) return;
    await waitForGcInventoryReady(session);
    await waitForPricesheetVersion(session, 12000);
    const ver = session.requiredAppIdVersion;
    if (ver) {
        sendGcClientHello(session, ver);
        await sleep(2500);
    }
};

const getStoreLanguage = (user) => {
    const fromLogon = user?._logOnDetails?.language;
    if (typeof fromLogon === 'number' && fromLogon >= 0) return fromLogon;
    return STORE_LANGUAGE;
};

/** Stop trying more price guesses — but result=1 with txn_id is still usable. */
const PURCHASE_FATAL_RESULTS = new Set([200, 7]);

const describePurchaseInitFailure = (code, session) => {
    if (code === 'unknown' || code == null) {
        return (
            'No response from GC (timed out). Reconnect Ratatoskr, wait ~10s after Connect, then retry.'
        );
    }
    const label = STORE_PURCHASE_ERRORS[code] || GC_RESULT_LABELS[code] || String(code);
    const hasSheet = session?.priceMap && Object.keys(session.priceMap).length > 0;
    const country = getSteamCountryCode(session);
    if (code === 200 && !hasSheet) {
        return (
            `${label}. GC price list not loaded (StoreGetUserData denied). ` +
            `Reconnect, wait ~10s after Connect, then retry (country ${country}).`
        );
    }
    if (code === 1 && !hasSheet) {
        return (
            `${label} (GC price sheet not loaded). Reconnect Ratatoskr, wait ~15s after Connect, then retry. ` +
            `Wallet checkout only works after StoreGetUserData succeeds or Steam sends a microtxn.`
        );
    }
    if (code === 1 && hasSheet) {
        return (
            `${label} — GC created a transaction but Steam did not start wallet checkout (no microtxn). ` +
            `Reconnect Ratatoskr, wait ~15s after Connect, confirm wallet balance, then try again.`
        );
    }
    if (code === 4) {
        return `${label}. Price or currency may be wrong (country ${country}) — reconnect and retry.`;
    }
    return `${label}. Disconnect and reconnect Ratatoskr, then try again.`;
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const kvPriceToInt = (val) => {
    if (val == null) return 0;
    if (typeof val === 'number') return val;
    if (typeof val === 'string') return parseInt(val, 10) || 0;
    if (val.low != null) return Number(val.low) || 0;
    return parseInt(String(val), 10) || 0;
};

const getCurrencyLabel = (code) => ECurrencyCode[code] || `Currency ${code}`;

/** GC/store prices are in minor units (øre, cents). */
const formatPriceMinor = (amountMinor, currencyCode) => {
    const label = getCurrencyLabel(currencyCode);
    const major = Number(amountMinor) / 100;
    try {
        return new Intl.NumberFormat(undefined, {
            style: 'currency',
            currency: label,
        }).format(major);
    } catch {
        return SteamUser.formatCurrency(major, currencyCode);
    }
};

/** steam-user wallet.balance is already major units (869.03), not øre. */
const formatWalletBalance = (wallet) => {
    if (!wallet) return null;
    return SteamUser.formatCurrency(Number(wallet.balance), wallet.currency);
};

const formatPrice = formatPriceMinor;

const resolveItemCost = (itemDefId, priceMap, currency) => {
    const sheet = priceMap[itemDefId];
    if (sheet?.cost_in_local_currency > 0) {
        return { cost: sheet.cost_in_local_currency, purchase_type: sheet.purchase_type ?? 0 };
    }
    if (itemDefId === 1201 && STORAGE_UNIT_FALLBACK_PRICE[currency]) {
        return {
            cost: STORAGE_UNIT_FALLBACK_PRICE[currency],
            purchase_type: 0,
            fallback: true,
        };
    }
    return null;
};

const buildCatalog = (sessionOrPriceMap, currency = 1, connected = false) => {
    const session = sessionOrPriceMap?.priceMap ? sessionOrPriceMap : null;
    const priceMap = session?.priceMap ?? sessionOrPriceMap ?? {};
    return Object.values(FEATURED_ITEMS).map((meta) => {
        const line =
            meta.item_def_id === STORAGE_UNIT_DEF_ID && session
                ? resolveStorageUnitStoreLine(session, currency)
                : null;
        const resolved = line || resolveItemCost(meta.item_def_id, priceMap, currency);
        const cost = line?.cost_in_local_currency ?? resolved?.cost ?? null;
        const gcAvailable = connected && cost != null && cost > 0;
        return {
            ...meta,
            cost_in_local_currency: cost,
            purchase_type: line?.purchase_type ?? resolved?.purchase_type ?? 0,
            price_label: cost != null ? formatPrice(cost, currency) : 'Price unavailable',
            price_fallback: line?.price_source === 'fallback' || Boolean(resolved?.fallback),
            price_source: line?.price_source ?? null,
            available: gcAvailable,
            buy_url: buildBuyItemUrl(meta.item_def_id, 1),
            purchase_via_gc: gcAvailable,
            purchase_via_browser: true,
        };
    });
};

/** All items from parsed GC price sheet (for Store UI list). */
/** Diagnostic list — only explicit sheet rows (old 192-row dump was mostly false positives). */
const buildPriceList = (priceMap = {}, currency = 1) =>
    Object.entries(priceMap || {})
        .filter(([, entry]) => entry?.explicit)
        .map(([defId, entry]) => {
            const id = parseInt(defId, 10);
            const meta = FEATURED_ITEMS[id];
            const cost = entry?.cost_in_local_currency ?? 0;
            return {
                item_def_id: id,
                name: meta?.name || (id === STORAGE_UNIT_DEF_ID ? 'Storage Unit' : `Item ${id}`),
                cost_in_local_currency: cost,
                purchase_type: entry?.purchase_type ?? 0,
                price_label: formatPrice(cost, currency),
                is_storage_unit: id === STORAGE_UNIT_DEF_ID,
            };
        })
        .filter((row) => row.item_def_id > 0 && row.cost_in_local_currency > 0)
        .sort((a, b) => a.item_def_id - b.item_def_id);

const sendGcClientHello = (session, version) => {
    const { csgo, user } = session;
    if (!csgo?.haveGCSession || !version) return;
    const headful = isHeadfulStoreMode();
    const body = {
        version: version >>> 0,
        client_session_need: headful ? 1 : 0,
        client_launcher: headful ? 1 : 0,
        steam_launcher: headful ? 1 : 0,
    };
    const sid = user?.steamID;
    if (sid) {
        const id64 =
            typeof sid.getSteamID64 === 'function' ? sid.getSteamID64() : String(sid);
        const socacheVer =
            (headful && session.inventorySocacheVersion) ||
            (headful && csgo.inventory?.length ? 1 : 0);
        if (headful) {
            body.socache_have_versions = [
                {
                    soid: { type: 1, id: id64 },
                    version: socacheVer >>> 0,
                },
            ];
        }
    }
    csgo._send(Language.ClientHello, Protos.CMsgClientHello, body);
    console.log(
        `[STORE] ClientHello mode=${headful ? 'headful' : 'headless'} version=${version >>> 0} socache=${body.socache_have_versions?.[0]?.version ?? 'n/a'}`
    );
};

const cachePriceSheetResponse = async (session, response) => {
    const sheetBuf = response?.price_sheet;
    const sheetLen = sheetBuf?.length ?? (Buffer.isBuffer(sheetBuf) ? sheetBuf.length : 0);
    if (!response || sheetLen < 16) {
        return false;
    }

    const resultCode = response.result ?? 0;
    if (resultCode !== 0) {
        console.log(
            `[STORE] StoreGetUserData result=${resultCode} but price_sheet present (${sheetLen} bytes) — parsing`
        );
    }

    const raw = toBuffer(sheetBuf);
    let priceMap = {};
    let decompressed = null;
    const magic = raw.length >= 4 ? raw.readUInt32BE(0) : 0;

    const walletCurrency = session.user?.wallet?.currency ?? 1;
    if (magic === 0x4c5a4d41) {
        try {
            decompressed = await decompressValveLZMA(raw);
            priceMap = parsePriceSheet(decompressed, walletCurrency);
        } catch (lzmaErr) {
            console.warn('[STORE] LZMA decompress failed:', lzmaErr.message);
        }
    }

    if (!priceMap[STORAGE_UNIT_DEF_ID]) {
        priceMap = { ...priceMap, ...parsePriceSheet(raw, walletCurrency) };
    }

    if (
        (process.env.STORE_DUMP_PRICESHEET === '1' || Object.keys(priceMap).length === 0) &&
        !session._priceSheetDumped
    ) {
        try {
            const fs = require('fs');
            const id = getSteamAccountId(session.user) || 'unknown';
            fs.writeFileSync(`/tmp/pricesheet-${id}.bin`, raw);
            if (decompressed?.length) {
                fs.writeFileSync(`/tmp/pricesheet-${id}-decoded.bin`, decompressed);
            }
            session._priceSheetDumped = true;
            console.log(`[STORE] Dumped price sheet to /tmp/pricesheet-${id}*.bin`);
        } catch (dumpErr) {
            console.warn('[STORE] price sheet dump failed:', dumpErr.message);
        }
    }

    if (Object.keys(priceMap).length === 0) {
        const magic = raw.length >= 4 ? `0x${raw.readUInt32BE(0).toString(16)}` : 'n/a';
        let diag = `magic=${magic} raw=${raw.length}`;
        if (decompressed?.length) {
            diag += ` decompressed=${decompressed.length} head=${decompressed.slice(0, 16).toString('hex')}`;
        }
        console.warn(`[STORE] Price sheet present but no prices parsed (${diag})`);
        return false;
    }

    session.priceMap = priceMap;
    if (response.price_sheet_version) {
        session.lastPriceSheetVersion = response.price_sheet_version >>> 0;
    }
    if (response.country_deprecated && /^[A-Za-z]{2}$/.test(response.country_deprecated)) {
        session.storeGcCountry = String(response.country_deprecated).toUpperCase();
    }
    const storageLine = resolveStorageUnitStoreLine(session, walletCurrency);
    console.log(
        `[STORE] Storage Unit def=${storageLine.item_def_id} price=${storageLine.cost_in_local_currency} ` +
            `(${storageLine.price_source}) purchase_type=${storageLine.purchase_type}`
    );
    console.log(
        `[STORE] Cached ${Object.keys(priceMap).length} prices (sheet v${session.lastPriceSheetVersion ?? '?'})`
    );
    return true;
};

const waitForPricesheetVersion = async (session, maxMs = 20000) => {
    const start = Date.now();
    while (Date.now() - start < maxMs) {
        const fromAccount = session.csgo?.accountData?.global_stats?.pricesheet_version;
        if (fromAccount > 0) {
            session.gcPricesheetVersion = fromAccount >>> 0;
            return session.gcPricesheetVersion;
        }
        if (session.gcPricesheetVersion > 0) {
            return session.gcPricesheetVersion;
        }
        await sleep(400);
    }
    return getPriceSheetVersion(session);
};

/** Try several StoreGetUserData variants (SkinLedger/Casemove use live GC price sheet). */
const loadPriceSheetFromGc = async (session) => {
    if (!session?.csgo?.haveGCSession) return session.priceMap || {};

    attachMicroTxnHandler(session);
    await warmupStoreGcSession(session);

    const variants = buildStoreGetUserDataVariants(session);
    const gcPublished = getGcPublishedPriceSheetVersion(session);
    if (gcPublished > 0) {
        console.log(`[STORE] GC published pricesheet_version=${gcPublished} (local cache v${getPriceSheetVersion(session)})`);
    }

    const tried = new Set();
    let lastSheetFingerprint = null;
    for (const body of variants) {
        const key = JSON.stringify(body);
        if (tried.has(key)) continue;
        tried.add(key);

        try {
            const response = await requestStoreUserData(session, body);
            logStoreGetUserDataResponse(response, key);
            const sheetLen = response?.price_sheet?.length ?? 0;
            const fingerprint = `${sheetLen}:${response?.price_sheet_version ?? 0}`;
            if (
                lastSheetFingerprint &&
                fingerprint === lastSheetFingerprint &&
                session.priceMap &&
                Object.keys(session.priceMap).length === 0
            ) {
                console.log('[STORE] Same price_sheet blob — stopping StoreGetUserData retries');
                break;
            }
            lastSheetFingerprint = fingerprint;

            if (await cachePriceSheetResponse(session, response)) {
                return session.priceMap;
            }

            const respVer = response?.price_sheet_version >>> 0;
            const reqVer = body.price_sheet_version >>> 0;
            if (
                response?.result === 1 &&
                respVer > 0 &&
                respVer !== reqVer
            ) {
                const hintKey = JSON.stringify({ price_sheet_version: respVer });
                if (!tried.has(hintKey)) {
                    tried.add(hintKey);
                    const hintRes = await requestStoreUserData(session, {
                        price_sheet_version: respVer,
                    });
                    logStoreGetUserDataResponse(hintRes, hintKey);
                    if (await cachePriceSheetResponse(session, hintRes)) {
                        return session.priceMap;
                    }
                }
            }
        } catch (err) {
            console.warn(`[STORE] StoreGetUserData failed (${key}):`, err.message);
        }
    }

    return session.priceMap || {};
};

const syncStoreAfterAccountData = async (session) => {
    if (!session?.csgo?.haveGCSession || session.storeSyncInFlight) return;
    session.storeSyncInFlight = true;
    try {
        await loadPriceSheetFromGc(session);
    } finally {
        session.storeSyncInFlight = false;
    }
};

const attachClientWelcomeHandler = (session) => {
    if (!session?.csgo || session.clientWelcomeHandlerAttached) return;
    const { csgo } = session;
    const msgType = Language.ClientWelcome;
    const prior = csgo._handlers[msgType];

    csgo._handlers[msgType] = function onClientWelcome(body) {
        try {
            const welcome = decodeProto(Protos.CMsgClientWelcome, body);
            if (welcome.txn_country_code && /^[A-Za-z]{2}$/.test(welcome.txn_country_code)) {
                session.gcTxnCountryCode = welcome.txn_country_code.toUpperCase();
                console.log(`[STORE] GC txn_country_code=${session.gcTxnCountryCode}`);
            }
            if (welcome.currency > 0) {
                session.gcWelcomeCurrency = welcome.currency;
            }
            const cache = welcome.outofdate_subscribed_caches?.[0];
            if (cache?.version != null) {
                const raw = cache.version;
                const v =
                    typeof raw === 'object' && raw.low != null
                        ? Number(raw.low) >>> 0
                        : Number(raw) >>> 0;
                if (v > 0) {
                    session.inventorySocacheVersion = v;
                    console.log(`[STORE] Inventory socache version=${v}`);
                }
            }
        } catch (err) {
            console.warn('[STORE] ClientWelcome parse:', err.message);
        }
        if (prior) prior.call(this, body);
    };

    session.clientWelcomeHandlerAttached = true;
};

/** Capture pricesheet_version from MatchmakingGC2ClientHello (needed for StoreGetUserData). */
const attachStoreGcHandlers = (session) => {
    if (!session?.csgo || session.storeGcHandlersAttached) return;

    ensureStoreGcDispatcher(session);
    attachClientWelcomeHandler(session);

    const applyAccountData = (proto) => {
        const gs = proto?.global_stats;
        if (gs?.pricesheet_version != null) {
            session.gcPricesheetVersion = gs.pricesheet_version >>> 0;
            console.log(`[STORE] GC pricesheet_version=${session.gcPricesheetVersion}`);
        }
        const appVer = gs?.required_appid_version2 || gs?.required_appid_version;
        if (appVer > 0) {
            session.requiredAppIdVersion = appVer >>> 0;
            console.log(`[STORE] GC required_appid_version=${session.requiredAppIdVersion}`);
        }
        syncStoreAfterAccountData(session).catch((err) => {
            console.warn('[STORE] syncStoreAfterAccountData:', err.message);
        });
    };

    session.csgo.on('accountData', applyAccountData);
    if (session.csgo.accountData) {
        applyAccountData(session.csgo.accountData);
    }

    session.storeGcHandlersAttached = true;
};

const attachMicroTxnHandler = (session) => {
    if (session.microTxnHandlerAttached) return;
    const { user } = session;

    session.microTxnHandler = (body) => {
        if (!session.microTxnHandlerAttached) return;
        const pending = session.pendingStorePurchase;
        const initSentAt = pending?.purchaseInitSentAt ?? 0;
        // Microtxn often arrives during PurchaseInit RPC — do not require initResponseReceived.
        if (!initSentAt || !pending) {
            return;
        }
        try {
            const buf = ByteBuffer.isByteBuffer(body)
                ? body
                : ByteBuffer.wrap(body, ByteBuffer.LITTLE_ENDIAN);
            buf.littleEndian = true;
            const appId = buf.readUint32();
            const transId = buf.readUint64().toString();

            if (isStaleMicroTxn(session, transId)) {
                console.warn(`[STORE] Ignoring stale microtxn ${transId}`);
                return;
            }

            if (!isWalletMicroTxnId(transId)) {
                console.warn(`[STORE] Ignoring non-wallet microtxn ${transId}`);
                return;
            }

            const now = Date.now();
            session.lastMicroTxnId = transId;
            session.lastMicroTxnAt = now;
            session.lastMicroTxnAppId = appId;
            pending.pendingMicroTxnAuth = { appId, transId };
            pending.microTxnId = transId;

            storeDiag('microtxn_auth_request', {
                trans_id: transId,
                app_id: appId,
                init_sent_at: initSentAt,
                defer_web: Boolean(pending.deferWebCheckoutFirst),
            });

            if (pending.deferWebCheckoutFirst) {
                console.log(
                    `[STORE] microtxn ${transId} queued — web approvetxn first (app ${appId})`
                );
            } else {
                sendClientMicroTxnAuthorize(session, appId, transId, 1);
            }
        } catch (err) {
            console.error('[STORE] MicroTxn auth handler error:', err);
        }
    };

    user._handlerManager.add(SteamUser.EMsg.ClientMicroTxnAuthRequest, session.microTxnHandler);
    session.microTxnHandlerAttached = true;
};

const detachMicroTxnHandler = (session) => {
    if (!session) return;
    session.microTxnHandlerAttached = false;
};

const buildWalletPayload = (wallet) =>
    wallet
        ? {
              has_wallet: wallet.hasWallet,
              balance: wallet.balance,
              balance_label: formatWalletBalance(wallet),
              currency: wallet.currency,
          }
        : null;

/** Request GC price sheet; CS2 often returns Denied for headless sessions — not fatal. */
const requestStoreUserData = async (session, requestBody) =>
    withStoreGcLock(session, async () => {
        const { csgo } = session;
        const responsePromise = waitForGcMessage(
            session,
            Language.StoreGetUserDataResponse,
            30000
        );
        sendGc(csgo, Language.StoreGetUserData, Protos.CMsgStoreGetUserData, requestBody);
        const raw = await responsePromise;
        return decodeProto(Protos.CMsgStoreGetUserDataResponse, raw);
    });

const fetchGcStoreData = async (session) => {
    const { user } = session;
    attachMicroTxnHandler(session);
    attachStoreGcHandlers(session);

    const walletCurrency = user?.wallet?.currency ?? 0;
    const priceMap = await loadPriceSheetFromGc(session);
    const gcPublished = getGcPublishedPriceSheetVersion(session);
    const priceMapSize = Object.values(priceMap).filter((e) => e?.explicit).length;
    let warning = null;

    if (priceMapSize === 0) {
        const label = GC_RESULT_LABELS[1];
        warning =
            `GC price list could not be parsed (${label}). ` +
            `Published sheet v${gcPublished || session.gcPricesheetVersion || '?'}. ` +
            'Storage Unit uses kr 18.50 fallback until the sheet parses.';
        console.warn(`[STORE] ${warning}`);
    }

    const wallet = user.wallet || null;
    const currency = wallet?.currency || walletCurrency || 1;

    return {
        currency,
        currency_label: getCurrencyLabel(currency),
        price_sheet_version: gcPublished || session.lastPriceSheetVersion || 0,
        price_map_size: priceMapSize,
        price_sheet_denied: priceMapSize === 0,
        wallet: buildWalletPayload(wallet),
        catalog: buildCatalog(session, currency, true),
        price_list: buildPriceList(priceMap, currency),
        storage_unit: resolveStorageUnitStoreLine(session, currency),
        gc_session_mode: isHeadfulStoreMode() ? 'headful' : 'headless',
        gc_connected: true,
        web_session_ready: Boolean(session.webCookies?.length),
        warning,
    };
};

/** Warm GC + download/cache price sheet so PurchaseInit does not return 200. */
const syncGcPriceSheetForPurchase = async (session) => {
    if (!session?.csgo?.haveGCSession) return session.priceMap || {};

    attachStoreGcHandlers(session);
    await warmupStoreGcSession(session);

    const gcPublished = getGcPublishedPriceSheetVersion(session);
    const walletCurrency = session.user?.wallet?.currency ?? 0;
    const targets = [
        {},
        { price_sheet_version: 0 },
        gcPublished > 0 ? { price_sheet_version: gcPublished } : null,
        session.lastPriceSheetVersion > 0
            ? { price_sheet_version: session.lastPriceSheetVersion >>> 0 }
            : null,
        walletCurrency > 0 ? { currency: walletCurrency } : null,
        gcPublished > 0 && walletCurrency > 0
            ? { price_sheet_version: gcPublished, currency: walletCurrency }
            : null,
    ].filter(Boolean);

    const tried = new Set();
    for (const body of targets) {
        const key = JSON.stringify(body);
        if (tried.has(key)) continue;
        tried.add(key);
        try {
            const response = await requestStoreUserData(session, body);
            logStoreGetUserDataResponse(response, key);
            await cachePriceSheetResponse(session, response);
            const respVer = response?.price_sheet_version >>> 0;
            if (response?.result === 1 && respVer > 0 && respVer !== (body.price_sheet_version >>> 0)) {
                const hint = { price_sheet_version: respVer };
                const hintKey = JSON.stringify(hint);
                if (!tried.has(hintKey)) {
                    tried.add(hintKey);
                    const hintRes = await requestStoreUserData(session, hint);
                    logStoreGetUserDataResponse(hintRes, hintKey);
                    await cachePriceSheetResponse(session, hintRes);
                }
            }
        } catch (err) {
            console.warn(`[STORE] syncGcPriceSheet StoreGetUserData (${key}):`, err.message);
        }
        if (session.priceMap?.[STORAGE_UNIT_DEF_ID]?.explicit) {
            break;
        }
    }

    const ver = session.requiredAppIdVersion;
    if (ver) {
        sendGcClientHello(session, ver);
        await sleep(isHeadfulStoreMode() ? 2000 : 1000);
    }

    const storage = session.priceMap?.[STORAGE_UNIT_DEF_ID];
    storeDiag('gc_price_sheet_sync', {
        gc_published: gcPublished,
        cached_version: session.lastPriceSheetVersion ?? null,
        storage_price: storage?.cost_in_local_currency ?? null,
        explicit: storage?.explicit ?? false,
        price_map_size: Object.keys(session.priceMap || {}).length,
    });

    return session.priceMap || {};
};

/** Load GC price sheet into session.priceMap before purchase (best-effort). */
const ensurePriceMapForPurchase = async (session) => {
    attachStoreGcHandlers(session);
    await syncGcPriceSheetForPurchase(session);
    if (session.priceMap?.[STORAGE_UNIT_DEF_ID]?.explicit) {
        return session.priceMap;
    }
    for (let attempt = 0; attempt < 2; attempt += 1) {
        await loadPriceSheetFromGc(session);
        if (session.priceMap?.[STORAGE_UNIT_DEF_ID]?.explicit) {
            return session.priceMap;
        }
        if (attempt < 1) await sleep(3000);
    }
    return session.priceMap || {};
};

/** Always returns a catalog (static items + buyitem URLs). GC data merged when possible. */
const getStoreUserData = async (session) => {
    if (!session?.csgo?.haveGCSession) {
        return {
            gc_connected: false,
            warning: session
                ? 'Connect to Game Coordinator to use the in-game store.'
                : 'No Ratatoskr session — connect from the sidebar.',
            currency: session?.user?.wallet?.currency ?? 1,
            currency_label: getCurrencyLabel(session?.user?.wallet?.currency ?? 1),
            catalog: buildCatalog({}, session?.user?.wallet?.currency ?? 1, false),
            wallet: null,
        };
    }

    attachStoreGcHandlers(session);

    try {
        return await fetchGcStoreData(session);
    } catch (err) {
        console.warn('[STORE] GC store fetch failed:', err.message);
        const currency = session.user?.wallet?.currency ?? 1;
        return {
            gc_connected: true,
            warning: `${err.message} — using fallback prices where available.`,
            currency,
            currency_label: getCurrencyLabel(currency),
            price_map_size: 0,
            catalog: buildCatalog({}, currency, true),
            wallet: buildWalletPayload(session.user?.wallet),
            web_session_ready: Boolean(session.webCookies?.length),
        };
    }
};

const runStorePurchaseInitOnce = async (session, msgType, responseType, { country, currency, lineItem }) =>
    withStoreGcLock(session, async () => {
        const { csgo, user } = session;
        const initPromise = waitForGcMessage(session, responseType, PURCHASE_TIMEOUT_MS);
        const language = getStoreLanguage(user);

        if (msgType === Language.StorePurchaseInit) {
            const body = encodeCs2StorePurchaseInit({ country, language, currency, lineItem });
            sendGcRaw(csgo, msgType, body);
        } else {
            sendGc(csgo, msgType, Protos.CMsgGCStorePurchaseInit, {
                country,
                language,
                currency,
                line_items: [lineItem],
            });
        }

        const initRaw = await initPromise;
        const initResponse = decodeProto(Protos.CMsgGCStorePurchaseInitResponse, initRaw);
        const rawHex = Buffer.isBuffer(initRaw) ? initRaw.toString('hex') : '';
        const urlHint = initResponse.url ? ` url=${String(initResponse.url).slice(0, 80)}` : '';
        console.log(
            `[STORE] PurchaseInit msg=${msgType} result=${initResponse.result} txn=${initResponse.txn_id ?? 'none'}${urlHint} raw=${rawHex.slice(0, 48)}`
        );
        captureInitCheckoutUrl(session, initResponse);
        return initResponse;
    });

/** Warm Steam web checkout (can trigger wallet microtxn before GC init). */
const primeSteamWalletCharge = async (session, itemDefId, quantity) => {
    await ensureWebSession(session);
    const cookieHeader = cookiesToHeader(session.webCookies, session.webSessionId);
    if (!cookieHeader) return;
    const url = buildBuyItemUrl(itemDefId, quantity);
    try {
        const res = await axios.get(url, {
            headers: {
                'User-Agent': BROWSER_UA,
                Accept: 'text/html,*/*',
                Cookie: cookieHeader,
                Referer: 'https://store.steampowered.com/',
            },
            maxRedirects: 5,
            timeout: 20000,
            validateStatus: () => true,
        });
        console.log(`[STORE] buyitem prime HTTP ${res.status} def=${itemDefId}`);
    } catch (err) {
        console.warn('[STORE] buyitem prime failed:', err.message);
    }
};

const buildPurchaseInitLineItems = (defId, priceMap, currency, quantity, session = null) => {
    const resolved =
        defId === STORAGE_UNIT_DEF_ID && session
            ? resolveStorageUnitStoreLine(session, currency)
            : resolveItemCost(defId, priceMap, currency);
    const primaryCost = resolved?.cost ?? resolved?.cost_in_local_currency;
    const purchaseType = sanitizePurchaseType(resolved?.purchase_type ?? 0);
    const explicitSheet =
        defId === STORAGE_UNIT_DEF_ID &&
        (resolved?.price_source === 'gc_sheet_explicit' ||
            priceMap?.[STORAGE_UNIT_DEF_ID]?.explicit);

    if (explicitSheet && primaryCost > 0) {
        return [
            {
                item_def_id: defId,
                quantity,
                cost_in_local_currency: primaryCost,
                purchase_type: purchaseType,
            },
        ];
    }

    const costs = new Set();
    if (primaryCost > 0) costs.add(primaryCost);
    if (defId === STORAGE_UNIT_DEF_ID) {
        for (const c of STORAGE_UNIT_PRICE_CANDIDATES[currency] || []) {
            if (c > 0) costs.add(c);
        }
        if (STORAGE_UNIT_FALLBACK_PRICE[currency]) {
            costs.add(STORAGE_UNIT_FALLBACK_PRICE[currency]);
        }
    }
    const types = new Set([purchaseType]);
    if (defId === STORAGE_UNIT_DEF_ID) {
        types.add(0);
    }
    const attempts = [];
    for (const cost of costs) {
        for (const purchase_type of types) {
            attempts.push({
                item_def_id: defId,
                quantity,
                cost_in_local_currency: cost,
                purchase_type,
            });
        }
    }
    return attempts;
};

const runStorePurchaseInit = async (session, opts) => {
    let res = null;
    try {
        res = await runStorePurchaseInitOnce(
            session,
            Language.StorePurchaseInit,
            Language.StorePurchaseInitResponse,
            opts
        );
    } catch (err) {
        console.warn('[STORE] StorePurchaseInit (2510) error:', err.message);
    }

    if (res?.result === 0) return res;
    if (res?.result === 200) return res;
    if (isGcPurchaseInitUsable(res)) return res;

    const tryDeprecated = async () =>
        runStorePurchaseInitOnce(
            session,
            Language.StorePurchaseInit_DEPRECATED,
            Language.StorePurchaseInitResponse_DEPRECATED,
            opts
        );

    if (res?.result != null) {
        try {
            const dep = await tryDeprecated();
            console.log(
                `[STORE] PurchaseInit DEPRECATED result=${dep.result} txn=${dep.txn_id ?? 'none'}`
            );
            if (dep.result === 0) return dep;
            if (dep.result === 1 && hasValidPurchaseTxn(dep)) return dep;
        } catch (err) {
            console.warn('[STORE] StorePurchaseInit (2502) error:', err.message);
        }
        return res;
    }

    return tryDeprecated();
};

const warmGcForStore = async (session) => {
    const { user, csgo } = session;
    if (!csgo?.haveGCSession) return;
    try {
        user.gamesPlayed([{ game_id: STEAM_APPID, game_extra_info: 'Counter-Strike 2' }]);
    } catch (err) {
        console.warn('[STORE] gamesPlayed:', err.message);
    }
    if (isHeadfulStoreMode()) {
        await waitForGcInventoryReady(session, 20000);
    }
    const ver = session.requiredAppIdVersion;
    if (ver) {
        sendGcClientHello(session, ver);
        await sleep(isHeadfulStoreMode() ? 3000 : 1500);
    }
};

const purchaseInitWithPriceDiscovery = async (
    session,
    { itemDefId, quantity, purchaseType, country, currency }
) => {
    const defId = Number(itemDefId);
    const priceMap = session.priceMap || {};
    const lineItems = buildPurchaseInitLineItems(defId, priceMap, currency, quantity, session);

    if (!lineItems.length) {
        throw new Error('No price candidates for this item');
    }

    let lastResult = null;
    let lastError = null;
    let lastLineItem = null;
    console.log(
        `[STORE] PurchaseInit country=${country} currency=${currency} attempts=${lineItems.length}`
    );

    await primeSteamWalletCharge(session, defId, quantity);
    await sleep(400);

    for (const lineItem of lineItems) {
        let initResponse;
        try {
            if (session.pendingStorePurchase) {
                session.pendingStorePurchase.purchaseInitSentAt = Date.now();
                session.pendingStorePurchase.initResponseReceived = false;
                session.pendingStorePurchase.phase = 'init';
            }
            initResponse = await runStorePurchaseInit(session, { country, currency, lineItem });
            if (session.pendingStorePurchase) {
                session.pendingStorePurchase.initResponseReceived = true;
            }
        } catch (err) {
            lastError = err;
            console.warn(
                `[STORE] PurchaseInit error cost=${lineItem.cost_in_local_currency} type=${lineItem.purchase_type}:`,
                err.message
            );
            continue;
        }

        lastResult = initResponse;
        lastLineItem = lineItem;
        const resultCode = initResponse.result ?? 0;
        const microWaitMs = resultCode === 1 ? 20000 : 12000;
        await waitForFreshMicroTxn(session, getPurchaseMicroTxnSince(session), microWaitMs);
        if (isPurchaseInitAccepted(initResponse, session)) {
            if (resultCode === 0) {
                console.log(
                    `[STORE] PurchaseInit OK cost=${lineItem.cost_in_local_currency} type=${lineItem.purchase_type}`
                );
            } else if (resultCode === 1) {
                console.log(
                    `[STORE] PurchaseInit denied+txn=${initResponse.txn_id} cost=${lineItem.cost_in_local_currency} microtxn=${session.lastMicroTxnId ?? 'pending'}`
                );
            } else {
                console.log(
                    `[STORE] PurchaseInit result=${resultCode} cost=${lineItem.cost_in_local_currency} type=${lineItem.purchase_type} checkout=${session.lastMicroTxnId}`
                );
            }
            storeDiag('purchase_init_accepted', {
                result: resultCode,
                gc_txn_id: initResponse.txn_id?.toString?.() ?? null,
                microtxn: session.lastMicroTxnId ?? null,
                cost: lineItem.cost_in_local_currency,
                purchase_type: lineItem.purchase_type,
            });
            return { initResponse, lineItem };
        }

        const label =
            STORE_PURCHASE_ERRORS[resultCode] || GC_RESULT_LABELS[resultCode] || resultCode;
        console.warn(
            `[STORE] PurchaseInit result=${resultCode} (${label}) cost=${lineItem.cost_in_local_currency} type=${lineItem.purchase_type} txn=${initResponse.txn_id ?? 'none'}`
        );
        if (PURCHASE_FATAL_RESULTS.has(resultCode)) {
            break;
        }
        if (resultCode === 1 && hasValidPurchaseTxn(initResponse)) {
            break;
        }
    }

    const code = lastResult?.result ?? (lastError ? 'unknown' : 'unknown');
    throw new Error(`Purchase init failed: ${describePurchaseInitFailure(code, session)}`);
};

const prepareStorePurchase = async (session, itemDefId, quantity = 1) => {
    storeDiag('purchase_prepare_start', {
        item_def_id: itemDefId,
        quantity,
        session: snapshotStoreSession(session),
    });

    const meta = FEATURED_ITEMS[itemDefId];
    if (!meta) {
        throw new Error(`Item ${itemDefId} is not available in the store`);
    }

    const qty = Math.max(1, Math.min(parseInt(quantity, 10) || 1, meta.max_quantity || 1));

    if (!session?.csgo?.haveGCSession) {
        throw new Error('Not connected to Game Coordinator — use Connect in the sidebar');
    }

    attachStoreGcHandlers(session);
    attachMicroTxnHandler(session);

    const steamPrep = await prepareSteamStoreSession(session);
    if (!steamPrep.ok) {
        throw new Error(steamPrep.error || 'Steam checkout session not ready');
    }

    await warmGcForStore(session);
    await ensurePriceMapForPurchase(session);

    const currency = session.user?.wallet?.currency ?? 1;
    if (Number(itemDefId) !== STORAGE_UNIT_DEF_ID) {
        throw new Error('Only Storage Unit (def 1201) can be purchased from this store');
    }
    const resolved = resolveStorageUnitStoreLine(session, currency);
    if (!resolved?.cost_in_local_currency) {
        throw new Error('Storage Unit price unavailable — reconnect and wait for GC sync');
    }

    storeDiag('purchase_line_resolved', {
        item_def_id: Number(itemDefId),
        cost: resolved.cost_in_local_currency,
        purchase_type: resolved.purchase_type,
        price_source: resolved.price_source,
        currency,
    });

    const country = getSteamCountryCode(session);

    await clearAbandonedStorePurchases(session);

    if (session.failedCheckoutMicroTxnIds?.size) {
        console.log(
            `[STORE] Denying ${session.failedCheckoutMicroTxnIds.size} failed-checkout microtxn(s) before PurchaseInit`
        );
        for (const id of session.failedCheckoutMicroTxnIds) {
            denyStaleMicroTxn(session, id, session.lastMicroTxnAppId);
        }
        await sleep(1500);
        session.lastMicroTxnId = null;
        session.lastMicroTxnAt = 0;
    }

    const purchaseStartedAt = Date.now();
    if (!session.staleMicroTxnIds) session.staleMicroTxnIds = new Set();
    session.microTxnAuthorizedId = null;
    session.pendingStorePurchase = {
        purchaseStartedAt,
        purchaseInitSentAt: 0,
        initResponseReceived: false,
        itemDefId: Number(itemDefId),
        quantity: qty,
        phase: 'prep',
        deferWebCheckoutFirst: true,
        pendingMicroTxnAuth: null,
    };

    const { initResponse, lineItem } = await purchaseInitWithPriceDiscovery(session, {
        itemDefId: Number(itemDefId),
        quantity: qty,
        purchaseType: resolved.purchase_type ?? 0,
        country,
        currency,
    });

    const initResult = initResponse.result ?? 0;
    const initSentAt = session.pendingStorePurchase?.purchaseInitSentAt ?? 0;
    const gcTxnUsable = isGcPurchaseInitUsable(initResponse);

    if (!gcTxnUsable && !hasValidPurchaseTxn(initResponse)) {
        delete session.pendingStorePurchase;
        throw new Error(
            describePurchaseInitFailure(initResult, session) ||
                'Purchase init failed — reconnect Ratatoskr and try again.'
        );
    }

    let freshMicroTxn = await waitForFreshMicroTxn(session, initSentAt, 25000);
    if (!freshMicroTxn && initResult === 1) {
        console.log('[STORE] Denied+gc txn — priming Steam wallet checkout for microtxn');
        await primeSteamWalletCharge(session, Number(itemDefId), qty);
        freshMicroTxn = await waitForFreshMicroTxn(session, initSentAt, 35000);
    }

    const initOk = initResult === 0;
    const hasMicro = Boolean(freshMicroTxn);

    if (!initOk && !hasMicro && !hasValidPurchaseTxn(initResponse)) {
        delete session.pendingStorePurchase;
        throw new Error(
            'GC denied the purchase and Steam did not open a new wallet charge for this buy. ' +
                'Disconnect Ratatoskr, reconnect, wait ~15s, then try again (do not reuse old checkout links).'
        );
    }

    if (!initResponse.txn_id && !freshMicroTxn) {
        delete session.pendingStorePurchase;
        throw new Error('Purchase init returned no transaction id');
    }

    if (!initOk && (hasMicro || hasValidPurchaseTxn(initResponse))) {
        console.log(
            `[STORE] PurchaseInit result=${initResult} gc_txn=${initResponse.txn_id} wallet=${freshMicroTxn ?? 'pending'}`
        );
    }

    let { gcTxnId, webTxnId, authorizeUrl } = resolveCheckoutTxnIds(
        session,
        initResponse,
        purchaseStartedAt
    );

    if (webTxnId && isStaleMicroTxn(session, webTxnId)) {
        webTxnId = '';
    }
    if (!webTxnId && freshMicroTxn && !isStaleMicroTxn(session, freshMicroTxn)) {
        webTxnId = String(freshMicroTxn);
        authorizeUrl = buildApproveTxnUrl(webTxnId);
    }
    if (!webTxnId) {
        const newer = await waitForFreshMicroTxn(session, initSentAt, 45000);
        if (newer && !isStaleMicroTxn(session, newer)) {
            webTxnId = newer;
            authorizeUrl = buildApproveTxnUrl(webTxnId);
        }
    }

    if (!webTxnId || isStaleMicroTxn(session, webTxnId)) {
        delete session.pendingStorePurchase;
        throw new Error(
            'Steam did not open a new wallet checkout for this purchase. ' +
                'Wait 30s, reconnect Ratatoskr, then try again (an old pending charge may need to clear).'
        );
    }

    session.pendingStorePurchase = {
        txnId: initResponse.txn_id,
        gcTxnId,
        webTxnId,
        txnIdStr: gcTxnId,
        initResult,
        lineItem,
        itemDefId: Number(itemDefId),
        quantity: qty,
        meta,
        currency,
        authorizeUrl,
        purchaseStartedAt,
        purchaseInitSentAt: initSentAt,
        initResponseReceived: true,
        microTxnId: freshMicroTxn || session.lastMicroTxnId || null,
        createdAt: Date.now(),
    };

    console.log(
        `[STORE] Checkout ids gc=${gcTxnId} web=${webTxnId} microtxn=${session.lastMicroTxnId ?? 'none'}`
    );

    storeDiag('purchase_prepare_done', {
        init_result: initResult,
        gc_txn_id: gcTxnId,
        web_txn_id: webTxnId,
        authorize_url: authorizeUrl,
        line_item: {
            cost: lineItem.cost_in_local_currency,
            purchase_type: lineItem.purchase_type,
        },
        microtxn: session.lastMicroTxnId ?? null,
        country,
        currency,
    });

    return {
        txn_id: gcTxnId,
        checkout_txn_id: webTxnId,
        authorize_url: authorizeUrl,
        item_def_id: Number(itemDefId),
        quantity: qty,
        cost_in_local_currency: lineItem.cost_in_local_currency,
        price_label: formatPrice(lineItem.cost_in_local_currency, currency),
        item_name: meta.name,
        country,
        currency,
    };
};

/** SkinLedger-style step 1: GC init → open Steam Authorize page in browser. */
const beginStorePurchase = async (session, itemDefId, quantity = 1) => {
    const prepared = await prepareStorePurchase(session, itemDefId, quantity);
    console.log(
        `[STORE] Purchase begun txn=${prepared.txn_id} price=${prepared.price_label} url=${prepared.authorize_url}`
    );
    return {
        success: true,
        step: 'authorize',
        ...prepared,
        message:
            'Completing wallet charge via Ratatoskr (same session as SkinLedger). Browser checkout often fails without that session.',
    };
};

const runGcPurchaseFinalize = async (session, gcTxnId) => {
    const { csgo } = session;
    const finalizeRaw = await withStoreGcLock(session, async () => {
        const finalizePromise = waitForGcMessage(
            session,
            Language.StorePurchaseFinalizeResponse,
            PURCHASE_FINALIZE_TIMEOUT_MS
        );
        sendGc(csgo, Language.StorePurchaseFinalize, Protos.CMsgGCStorePurchaseFinalize, {
            txn_id: gcTxnId,
        });
        return finalizePromise;
    });
    return decodeProto(Protos.CMsgGCStorePurchaseFinalizeResponse, finalizeRaw);
};

/** Step 2: microtxn auth → approvetxn → GC finalize. */
const finishStorePurchase = async (session, txnIdStr) => {
    storeDiag('purchase_finish_start', {
        txn_id: txnIdStr,
        session: snapshotStoreSession(session),
    });

    const pending = session.pendingStorePurchase;
    const gcTxn = String(txnIdStr || pending?.gcTxnId || pending?.txnIdStr || '');
    if (!gcTxn && !pending?.webTxnId) {
        throw new Error('Missing transaction id — start purchase again');
    }
    if (
        pending?.gcTxnId &&
        txnIdStr &&
        pending.gcTxnId !== String(txnIdStr) &&
        pending.webTxnId !== String(txnIdStr)
    ) {
        throw new Error('Transaction id does not match the pending purchase');
    }

    const meta = pending?.meta || FEATURED_ITEMS[pending?.itemDefId];
    const lineItem = pending?.lineItem;
    const qty = pending?.quantity ?? 1;
    const currency = pending?.currency ?? session.user?.wallet?.currency ?? 1;
    const purchaseStartedAt = pending?.purchaseStartedAt ?? pending?.createdAt ?? 0;
    const initResult = pending?.initResult ?? 0;
    const initSentAt = pending?.purchaseInitSentAt ?? 0;

    let webTxn = pending?.webTxnId || '';
    const microSince = initSentAt || purchaseStartedAt;
    if (!webTxn || (session.lastMicroTxnAt || 0) < microSince) {
        const fresh = await waitForFreshMicroTxn(session, microSince, 15000);
        if (fresh) webTxn = fresh;
    }
    if (!webTxn) {
        webTxn = session.lastMicroTxnId || '';
    }

    if (
        pending?.microTxnId &&
        webTxn !== String(pending.microTxnId) &&
        initResult !== 0
    ) {
        throw new Error(
            'Checkout transaction does not match this purchase — start a new buy from the Store page.'
        );
    }

    if (!webTxn || !isWalletMicroTxnId(webTxn)) {
        const itemDefId = pending?.itemDefId ?? STORAGE_UNIT_DEF_ID;
        await primeSteamWalletCharge(session, itemDefId, qty);
        const waited = await waitForFreshMicroTxn(session, microSince, 45000);
        if (waited) {
            webTxn = waited;
        }
    }

    if (!webTxn || !isWalletMicroTxnId(webTxn)) {
        if (isGcPurchaseInitUsable({ result: initResult, txn_id: gcTxn })) {
            throw new Error(
                'GC created purchase txn ' +
                    gcTxn +
                    ' but Steam wallet checkout did not start (no microtxn). Wait a few seconds and click finish again, or reconnect Ratatoskr.'
            );
        }
        throw new Error(
            'No valid wallet transaction for checkout. Wait for Steam to request a new charge, then try again.'
        );
    }

    if (
        initSentAt &&
        (session.lastMicroTxnAt || 0) < initSentAt &&
        webTxn === session.lastMicroTxnId
    ) {
        throw new Error(
            'Wallet charge is from before this purchase — disconnect Ratatoskr, reconnect, and buy again.'
        );
    }

    attachMicroTxnHandler(session);

    storeDiag('purchase_finish_checkout', { web_txn_id: webTxn, gc_txn_id: gcTxn });

    const approval = await approveCheckoutTxn(session, webTxn);
    storeDiag('purchase_finish_approval', {
        web_txn_id: webTxn,
        ok: approval.ok,
        error: approval.error,
    });

    if (!approval.ok) {
        flushPendingMicroTxnAuth(session);
        await sleep(500);
        let finalizeResponse = await runGcPurchaseFinalize(session, pending?.txnId ?? gcTxn);
        if (finalizeResponse.result === 0) {
            console.log('[STORE] PurchaseFinalize OK after approvetxn error (txn already settled)');
            delete session.pendingStorePurchase;
            const itemIds = (finalizeResponse.item_ids || []).map(
                (id) => id?.toString?.() ?? String(id)
            );
            storeDiag('purchase_finish_ok', {
                gc_txn_id: gcTxn,
                web_txn_id: webTxn,
                item_ids: itemIds,
                via: 'finalize_after_checkout_error',
            });
            return {
                success: true,
                txn_id: gcTxn,
                checkout_txn_id: webTxn,
                item_ids: itemIds,
                item_def_id: pending?.itemDefId,
                quantity: qty,
                cost_in_local_currency: lineItem?.cost_in_local_currency,
                price_label: lineItem
                    ? formatPrice(lineItem.cost_in_local_currency, currency)
                    : null,
                message: `Purchased ${qty}× ${meta?.name || 'item'}`,
            };
        }
        rememberFailedCheckoutMicroTxn(session, webTxn);
        throw new Error(
            approval.error ||
                'Steam wallet checkout failed — disconnect, reconnect Ratatoskr, and buy again.'
        );
    }

    flushPendingMicroTxnAuth(session);
    await sleep(800);

    let finalizeResponse = await runGcPurchaseFinalize(session, pending?.txnId ?? gcTxn);

    if (finalizeResponse.result !== 0) {
        console.warn(
            `[STORE] PurchaseFinalize result=${finalizeResponse.result}, retry once`
        );
        await sleep(600);
        finalizeResponse = await runGcPurchaseFinalize(session, pending?.txnId ?? gcTxn);
    }

    if (finalizeResponse.result !== 0) {
        storeDiag('purchase_finalize_failed', {
            result: finalizeResponse.result,
            gc_txn_id: gcTxn,
        });
        throw new Error(
            `Purchase finalize failed: ${GC_RESULT_LABELS[finalizeResponse.result] || finalizeResponse.result}`
        );
    }

    delete session.pendingStorePurchase;

    const itemIds = (finalizeResponse.item_ids || []).map((id) => id?.toString?.() ?? String(id));

    storeDiag('purchase_finish_ok', {
        gc_txn_id: gcTxn,
        web_txn_id: webTxn,
        item_ids: itemIds,
    });

    return {
        success: true,
        txn_id: gcTxn,
        checkout_txn_id: webTxn,
        authorize_url: pending?.authorizeUrl || buildApproveTxnUrl(webTxn),
        item_ids: itemIds,
        item_def_id: pending?.itemDefId,
        quantity: qty,
        cost_in_local_currency: lineItem?.cost_in_local_currency,
        price_label: lineItem
            ? formatPrice(lineItem.cost_in_local_currency, currency)
            : null,
        message: `Purchased ${qty}× ${meta?.name || 'item'}`,
    };
};

const purchaseStoreItem = async (session, itemDefId, quantity = 1) => {
    const begun = await beginStorePurchase(session, itemDefId, quantity);
    return finishStorePurchase(session, begun.txn_id);
};

/** Parse dumped sheet + optional live GC store snapshot (for debugging). */
const runStoreDiagnostics = async (session, options = {}) => {
    const fs = require('fs');
    const report = {
        at: new Date().toISOString(),
        session: snapshotStoreSession(session),
        price_sheet_dump: null,
        live_store: null,
        purchase_test: null,
    };

    const accountId =
        getSteamAccountId(session?.user) || options.accountId || 'unknown';
    const decodedPath = `/tmp/pricesheet-${accountId}-decoded.bin`;
    const rawPath = `/tmp/pricesheet-${accountId}.bin`;

    try {
        if (fs.existsSync(decodedPath)) {
            const buf = fs.readFileSync(decodedPath);
            const currency = session?.user?.wallet?.currency ?? options.currency ?? 9;
            const items = parsePriceSheet(buf, currency);
            report.price_sheet_dump = {
                path: decodedPath,
                bytes: buf.length,
                currency,
                parsed_count: Object.keys(items).length,
                storage_unit: items[STORAGE_UNIT_DEF_ID] ?? null,
            };
        } else if (fs.existsSync(rawPath)) {
            report.price_sheet_dump = { path: rawPath, note: 'compressed only — no decoded dump' };
        } else {
            report.price_sheet_dump = { note: 'no dump file (set STORE_DUMP_PRICESHEET=1 and open Store)' };
        }
    } catch (err) {
        report.price_sheet_dump = { error: err.message };
    }

    if (session?.csgo?.haveGCSession) {
        try {
            const store = await getStoreUserData(session);
            report.live_store = {
                gc_connected: store.gc_connected,
                warning: store.warning ?? null,
                currency: store.currency,
                catalog_item: store.catalog?.find((i) => i.item_def_id === STORAGE_UNIT_DEF_ID),
                wallet: store.wallet,
            };
        } catch (err) {
            report.live_store = { error: err.message };
        }

        if (options.executePurchase) {
            try {
                storeDiag('diag_purchase_execute', { item_def_id: STORAGE_UNIT_DEF_ID });
                const result = await purchaseStoreItem(session, STORAGE_UNIT_DEF_ID, 1);
                report.purchase_test = { ok: true, result };
            } catch (err) {
                report.purchase_test = { ok: false, error: err.message };
            }
        }
    }

    storeDiag('diag_report', report);
    return report;
};

module.exports = {
    FEATURED_ITEMS,
    STEAM_APPID,
    buildBuyItemUrl,
    buildApproveTxnUrl,
    approveCheckoutTxn,
    completeWebCheckout,
    ensureWebSession,
    buildCatalog,
    buildPriceList,
    attachMicroTxnHandler,
    attachStoreGcHandlers,
    loadPriceSheetFromGc,
    syncStoreAfterAccountData,
    detachMicroTxnHandler,
    getStoreUserData,
    beginStorePurchase,
    finishStorePurchase,
    purchaseStoreItem,
    runStoreDiagnostics,
    storeDiag,
    snapshotStoreSession,
    formatPrice,
    formatWalletBalance,
    getCurrencyLabel,
};
