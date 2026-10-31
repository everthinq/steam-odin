// The move queue confirms each move from the Game Coordinator's own answer (moves.js).
// Run: npm test (node's built-in test runner, no dependencies).
const test = require('node:test');
const assert = require('node:assert');
const { EventEmitter } = require('node:events');
const { createMoveQueues } = require('../moves');

const STEAM_ID = '76561190000000001';
const UNIT = '30080226004';

// A fake Game Coordinator client that answers like the real one did live (2026-10-31).
class FakeGameCoordinator extends EventEmitter {
    constructor({ refuse = [], unitOpened = false } = {}) {
        super();
        this.haveGCSession = true;
        this.refuse = new Set(refuse);
        this.unitOpened = unitOpened;
        this.sent = [];
    }

    addToCasket(casketId, itemId) {
        this.sent.push(['add', casketId, itemId]);
        if (this.refuse.has(itemId)) return;
        setImmediate(() => {
            this.emit('itemRemoved', { id: itemId });
            if (this.unitOpened) this.emit('itemAcquired', { id: itemId, casket_id: casketId });
        });
    }

    removeFromCasket(casketId, itemId) {
        this.sent.push(['remove', casketId, itemId]);
        if (this.refuse.has(itemId)) return;
        setImmediate(() => this.emit('itemAcquired', { id: itemId }));
    }
}

const setup = (gameCoordinator) => {
    const session = { user: {}, csgo: gameCoordinator };
    return createMoveQueues({ getSession: () => session, getMoveDelayMs: () => 0, confirmTimeoutMs: 50 });
};

const settle = async (queues) => {
    for (let i = 0; i < 100 && queues.hasActiveMoveWork(STEAM_ID); i++) {
        await new Promise((resolve) => setTimeout(resolve, 10));
    }
    return queues.getMoveQueueStatus(STEAM_ID);
};

const deposit = (ids) => ids.map((itemID) => ({ itemID, source: 'inventory', target: 'casket', casketID: UNIT }));

test('a deposit counts once the item leaves the inventory', async () => {
    const queues = setup(new FakeGameCoordinator());
    queues.enqueueMoves(STEAM_ID, deposit(['a', 'b']), true);
    const status = await settle(queues);
    assert.strictEqual(status.done, 2);
    assert.strictEqual(status.failed, 0);
    assert.strictEqual(status.running, false);
});

test('a deposit seen arriving inside the unit counts once, not twice', async () => {
    const queues = setup(new FakeGameCoordinator({ unitOpened: true }));
    queues.enqueueMoves(STEAM_ID, deposit(['a']), true);
    const status = await settle(queues);
    assert.strictEqual(status.done, 1);
});

test('a move with no answer is refused and named', async () => {
    const queues = setup(new FakeGameCoordinator({ refuse: ['traded'] }));
    queues.enqueueMoves(STEAM_ID, deposit(['ok', 'traded']), true);
    const status = await settle(queues);
    assert.strictEqual(status.done, 1);
    assert.strictEqual(status.refused, 1);
    assert.deepStrictEqual(status.failedItemIDs, ['traded']);
    assert.deepStrictEqual(status.refusedItemIDs, ['traded']);
    assert.strictEqual(status.errors[0].code, 'refused');
});

test('a withdrawal counts once the item is back in the inventory', async () => {
    const queues = setup(new FakeGameCoordinator());
    queues.enqueueMoves(STEAM_ID, [{ itemID: 'x', source: 'casket', target: 'inventory', casketID: UNIT }], true);
    const status = await settle(queues);
    assert.strictEqual(status.done, 1);
});

test('the queue stays running while a sent move waits for its answer', async () => {
    const gameCoordinator = new FakeGameCoordinator({ refuse: ['slow'] });
    const queues = setup(gameCoordinator);
    const first = queues.enqueueMoves(STEAM_ID, deposit(['slow']), true);
    assert.strictEqual(first.running, true);
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.strictEqual(queues.getMoveQueueStatus(STEAM_ID).running, true);
    assert.strictEqual(queues.hasActiveMoveWork(STEAM_ID), true);
    await settle(queues);
});

test('without a Game Coordinator session nothing is sent', async () => {
    const gameCoordinator = new FakeGameCoordinator();
    gameCoordinator.haveGCSession = false;
    const queues = setup(gameCoordinator);
    queues.enqueueMoves(STEAM_ID, deposit(['a']), true);
    const status = await settle(queues);
    assert.strictEqual(gameCoordinator.sent.length, 0);
    assert.strictEqual(status.failed, 1);
    assert.strictEqual(status.errors[0].code, 'not_sent');
});

test('each run has its own id and lists the confirmed items', async () => {
    const queues = setup(new FakeGameCoordinator({ refuse: ['b'] }));
    const first = queues.enqueueMoves(STEAM_ID, deposit(['a', 'b']), true);
    const status = await settle(queues);
    assert.strictEqual(status.runId, first.runId);
    assert.deepStrictEqual(status.doneItemIDs, ['a']);
    assert.deepStrictEqual(status.failedItemIDs, ['b']);
    const second = queues.enqueueMoves(STEAM_ID, deposit(['c']), true);
    assert.notStrictEqual(second.runId, first.runId);
    assert.deepStrictEqual(second.doneItemIDs, []);
});

test('a dropped queue answers a new run, so nothing of the old batch looks confirmed', async () => {
    const queues = setup(new FakeGameCoordinator({ refuse: ['slow'] }));
    const started = queues.enqueueMoves(STEAM_ID, deposit(['slow']), true);
    queues.dropMoveQueue(STEAM_ID);
    const after = queues.getMoveQueueStatus(STEAM_ID);
    assert.notStrictEqual(after.runId, started.runId);
    assert.deepStrictEqual(after.doneItemIDs, []);
});

test('an item is never queued twice', async () => {
    const gameCoordinator = new FakeGameCoordinator();
    const queues = setup(gameCoordinator);
    queues.enqueueMoves(STEAM_ID, deposit(['a', 'a']), true);
    const status = await settle(queues);
    assert.strictEqual(gameCoordinator.sent.length, 1);
    assert.strictEqual(status.done, 1);
    assert.strictEqual(status.total, 1);
});

test('no answer after the session was lost is unconfirmed, not refused', async () => {
    const gameCoordinator = new FakeGameCoordinator({ refuse: ['a'] });
    const queues = setup(gameCoordinator);
    queues.enqueueMoves(STEAM_ID, deposit(['a']), true);
    gameCoordinator.haveGCSession = false;   // the Game Coordinator drops before answering
    const status = await settle(queues);
    assert.strictEqual(status.refused, 0);
    assert.deepStrictEqual(status.refusedItemIDs, []);
    assert.deepStrictEqual(status.unconfirmedItemIDs, ['a']);
    assert.strictEqual(status.errors[0].code, 'unconfirmed');
});
