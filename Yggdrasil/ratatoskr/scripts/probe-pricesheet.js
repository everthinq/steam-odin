#!/usr/bin/env node
/**
 * Probe a dumped price sheet: node scripts/probe-pricesheet.js /tmp/pricesheet-*-decoded.bin
 */
const fs = require('fs');
const { parseStorePriceSheetBinary, parseSteamKitBinaryKv } = require('../storeBinaryKv');

const path = process.argv[2];
if (!path) {
    console.error('Usage: node scripts/probe-pricesheet.js <decoded.bin>');
    process.exit(1);
}

const buf = fs.readFileSync(path);
console.log('size', buf.length, 'head', buf.slice(0, 32).toString('hex'));

for (let off = 0; off <= 32; off += 1) {
    try {
        const { tree, bytesRead } = parseSteamKitBinaryKv(buf, off);
        const keys = Object.keys(tree).slice(0, 8);
        console.log(`off=${off} ok bytes=${bytesRead} keys=${keys.join(',')}`);
    } catch (e) {
        console.log(`off=${off} fail: ${e.message}`);
    }
}

try {
    const { tree, offset, labels } = parseStorePriceSheetBinary(buf);
    console.log('best offset', offset, 'labels', labels);
    console.log('root keys', Object.keys(tree));
    if (tree.store) {
        console.log('store keys', Object.keys(tree.store).slice(0, 20));
    }
} catch (e) {
    console.error('parseStorePriceSheetBinary:', e.message);
}

const def = 1201;
const needle = Buffer.alloc(4);
needle.writeUInt32LE(def, 0);
let pos = 0;
let hits = 0;
while (hits < 8 && pos < buf.length) {
    const idx = buf.indexOf(needle, pos);
    if (idx === -1) break;
    const slice = buf.slice(Math.max(0, idx - 8), idx + 24);
    console.log(`1201@${idx}`, slice.toString('hex'));
    pos = idx + 4;
    hits += 1;
}
