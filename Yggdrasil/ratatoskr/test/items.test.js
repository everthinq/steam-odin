// items.js turns raw Game Coordinator items into the fields Heimdall's storage rules read
// (`item_moveable`, `trade_unlock`, `trade_locked`, `trade_protected_until`). The raw items are
// shaped like ones recorded live on 2026-10-31 (ids replaced): a case received in a trade
// carries attributes 75 and 312, a Steam Market purchase only 75, a service medal neither.
const test = require('node:test');
const assert = require('node:assert');
const Items = require('../items');

const items = new Items();
const HOUR = 60 * 60 * 1000;

const seconds = (date) => {
    const bytes = Buffer.alloc(4);
    bytes.writeUInt32LE(Math.floor(date.getTime() / 1000));
    return bytes;
};

const dreamsAndNightmaresCase = ({ hold, protection }) => ({
    id: '1000000001', account_id: 0, inventory: 3221225475, def_index: 4818, quality: 4,
    flags: 0, origin: hold ? 24 : 0, rarity: 1, position: 0, equipped_state: [],
    ...(hold ? { tradable_after: hold } : {}),
    attribute: [
        ...(hold ? [{ def_index: 75, value_bytes: seconds(hold) }] : []),
        ...(protection ? [{ def_index: 312, value_bytes: seconds(protection) }] : []),
    ],
});

const serviceMedal = {
    id: '1000000002', account_id: 0, inventory: 56, def_index: 1339, quality: 4, flags: 0,
    origin: 9, rarity: 6, position: 56, equipped_state: [],
    attribute: [{ def_index: 222, value_bytes: Buffer.from('gCnCVw==', 'base64') }],
};

const convert = (raw) => items.inventoryConverter([raw])[0];

test('a case received in a trade is trade protected until its date', () => {
    const until = new Date(Date.now() + 5 * 24 * HOUR);
    const item = convert(dreamsAndNightmaresCase({ hold: until, protection: until }));
    assert.strictEqual(item.item_name, 'Dreams & Nightmares Case');
    assert.strictEqual(item.item_moveable, true);
    assert.strictEqual(item.trade_locked, true);
    assert.strictEqual(new Date(item.trade_protected_until).getTime(), Math.floor(until.getTime() / 1000) * 1000);
});

test('a Steam Market purchase has a trade hold but no protection', () => {
    const until = new Date(Date.now() + 5 * 24 * HOUR);
    const item = convert(dreamsAndNightmaresCase({ hold: until }));
    assert.ok(item.trade_unlock);
    assert.strictEqual(item.trade_locked, false);
    assert.strictEqual(item.trade_protected_until, undefined);
});

test('trade protection that has ended no longer counts', () => {
    const past = new Date(Date.now() - HOUR);
    const item = convert(dreamsAndNightmaresCase({ hold: past, protection: past }));
    assert.strictEqual(item.trade_locked, false);
    assert.strictEqual(item.trade_unlock, undefined);
});

test('a service medal can never go into a Storage Unit', () => {
    const item = convert(serviceMedal);
    assert.match(item.item_name, /Service Medal/);
    assert.strictEqual(item.item_moveable, false);
});
