/**
 * CS2 store price sheet binary KeyValues (post-LZMA).
 * Supports SteamKit/VBKV (AlternateEnd), and Valve string-table binary KV.
 */
const KV_TYPE = {
    None: 0,
    String: 1,
    Int32: 2,
    Float32: 3,
    Pointer: 4,
    WideString: 5,
    Color: 6,
    UInt64: 7,
    End: 8,
    ProbablyBinary: 9,
    Int64: 10,
    AlternateEnd: 11,
};

const VBKV_MAGIC = 0x564b4256; // "VKV\x56" LE — VBKV header

const isEndType = (t, endMarker) => t === endMarker || t === KV_TYPE.End || t === KV_TYPE.AlternateEnd;

const isKvTypeByte = (b) =>
    b === KV_TYPE.None ||
    b === KV_TYPE.String ||
    b === KV_TYPE.Int32 ||
    b === KV_TYPE.Float32 ||
    b === KV_TYPE.Pointer ||
    b === KV_TYPE.WideString ||
    b === KV_TYPE.Color ||
    b === KV_TYPE.UInt64 ||
    b === KV_TYPE.ProbablyBinary ||
    b === KV_TYPE.Int64;

const readU64String = (buffer, offset) => {
    const lo = buffer.readUInt32LE(offset);
    const hi = buffer.readUInt32LE(offset + 4);
    return (BigInt(lo) + (BigInt(hi) << 32n)).toString();
};

const assignChild = (parent, name, value) => {
    if (!name) return;
    if (Object.prototype.hasOwnProperty.call(parent, name)) {
        const prev = parent[name];
        parent[name] = Array.isArray(prev) ? [...prev, value] : [prev, value];
    } else {
        parent[name] = value;
    }
};

/**
 * @param {Buffer} buffer
 * @param {number} startOffset
 * @param {{ stringTable?: string[], endMarker?: number }} opts
 */
const parseSteamKitBinaryKv = (buffer, startOffset = 0, opts = {}) => {
    const stringTable = opts.stringTable ?? null;
    const endMarker = opts.endMarker ?? KV_TYPE.End;
    const offset = [startOffset];

    const readU8 = () => {
        if (offset[0] >= buffer.length) {
            throw new Error(`KV read past end at ${offset[0]}`);
        }
        return buffer.readUInt8(offset[0]++);
    };

    const readCString = (maxLen = 65536) => {
        const start = offset[0];
        const end = buffer.indexOf(0, start);
        if (end === -1) {
            if (start + 4 <= buffer.length) {
                const len = buffer.readInt32LE(start);
                if (len > 0 && len <= maxLen && start + 4 + len <= buffer.length) {
                    const str = buffer.toString('utf8', start + 4, start + 4 + len);
                    offset[0] = start + 4 + len;
                    return str;
                }
            }
            offset[0] = buffer.length;
            return buffer.toString('utf8', start);
        }
        const str = buffer.toString('utf8', start, end);
        offset[0] = end + 1;
        return str;
    };

    const readKey = () => {
        if (stringTable) {
            if (offset[0] + 4 > buffer.length) {
                throw new Error('KV key index past end');
            }
            const index = buffer.readInt32LE(offset[0]);
            offset[0] += 4;
            if (index < 0 || index >= stringTable.length) {
                throw new Error(`KV string table index ${index} out of range (${stringTable.length})`);
            }
            return stringTable[index];
        }
        return readCString();
    };

    const readI32 = () => {
        const v = buffer.readInt32LE(offset[0]);
        offset[0] += 4;
        return String(v);
    };

    const readI32Number = () => {
        const v = buffer.readInt32LE(offset[0]);
        offset[0] += 4;
        return v;
    };

    const skipBytes = (n) => {
        if (n > 0) {
            offset[0] = Math.min(buffer.length, offset[0] + n);
        }
    };

    const readI64 = () => {
        const lo = buffer.readInt32LE(offset[0]);
        const hi = buffer.readInt32LE(offset[0] + 4);
        offset[0] += 8;
        return String(lo + hi * 0x1_0000_0000);
    };

    const readF32 = () => {
        const v = buffer.readFloatLE(offset[0]);
        offset[0] += 4;
        return String(v);
    };

    const readWideString = () => {
        const chars = [];
        while (offset[0] + 1 < buffer.length) {
            const code = buffer.readUInt16LE(offset[0]);
            offset[0] += 2;
            if (code === 0) break;
            chars.push(String.fromCharCode(code));
        }
        return chars.join('');
    };

    const readSection = () => {
        const section = {};
        while (offset[0] < buffer.length) {
            const fieldStart = offset[0];
            let type;
            let name;
            let value;
            try {
                type = readU8();
                if (isEndType(type, endMarker)) {
                    break;
                }
                name = readKey();
            } catch {
                offset[0] = fieldStart;
                break;
            }
            try {
            switch (type) {
                case KV_TYPE.None:
                    value = readSection();
                    break;
                case KV_TYPE.String:
                    value = readCString();
                    break;
                case KV_TYPE.Int32:
                case KV_TYPE.Color:
                case KV_TYPE.Pointer:
                    value = readI32();
                    break;
                case KV_TYPE.UInt64:
                    value = readU64String(buffer, offset[0]);
                    offset[0] += 8;
                    break;
                case KV_TYPE.Int64:
                    value = readI64();
                    break;
                case KV_TYPE.Float32:
                    value = readF32();
                    break;
                case KV_TYPE.WideString:
                    value = readWideString();
                    break;
                case KV_TYPE.ProbablyBinary: {
                    // CS2/VBKV: type 9 is often a nested KV block (not a raw length-prefixed blob).
                    const peek = buffer[offset[0]];
                    if (peek != null && (isKvTypeByte(peek) || isEndType(peek, endMarker))) {
                        value = readSection();
                        break;
                    }
                    const len = readI32Number();
                    if (len >= 0 && len <= buffer.length && offset[0] + len <= buffer.length) {
                        value = len > 0 ? buffer.subarray(offset[0], offset[0] + len) : Buffer.alloc(0);
                        skipBytes(len);
                    } else {
                        offset[0] -= 4;
                        value = readSection();
                    }
                    break;
                }
                default:
                    // Stop this section; keep partial siblings (CS2 sheets hit extra types mid-entries).
                    offset[0] -= 1;
                    break;
            }
            } catch {
                offset[0] = fieldStart;
                break;
            }
            assignChild(section, name, value);
        }
        return section;
    };

    const start = offset[0];
    const tree = readSection();
    return { tree, bytesRead: offset[0] - start };
};

/** Build the sheet string table and offset where indexed KV keys begin. */
const buildStringTableAndKvOffset = (buffer, maxScan = 65536) => {
    const table = [];
    let o = 0;
    const limit = Math.min(buffer.length, maxScan);
    let kvOffset = 7;

    while (o < limit) {
        if (buffer[o] === 0) {
            o += 1;
            continue;
        }
        const start = o;
        while (o < limit && buffer[o] !== 0) {
            o += 1;
        }
        const label = buffer.toString('utf8', start, o);
        if (label.length > 0 && label.length < 128) {
            table.push(label);
        }
        o += 1;

        if (o >= limit || !isKvTypeByte(buffer[o])) {
            continue;
        }
        if (o + 4 < limit) {
            const idx = buffer.readInt32LE(o + 1);
            if (idx >= 0 && idx < table.length) {
                kvOffset = o;
                return { table, kvOffset };
            }
        }
    }

    return { table, kvOffset };
};

/**
 * Grow a string table from the prefix; return once typed KV with indexed keys parses.
 * CS2 store sheets often prefix all key names before the binary KV body.
 */
const detectStringTableKvStart = (buffer, maxScan) => {
    const table = [];
    let o = 0;
    const limit = Math.min(buffer.length, maxScan ?? buffer.length);
    let best = null;

    while (o < limit) {
        if (buffer[o] === 0) {
            o += 1;
            continue;
        }
        const start = o;
        while (o < limit && buffer[o] !== 0) {
            o += 1;
        }
        const label = buffer.toString('utf8', start, o);
        if (label.length > 0 && label.length < 128) {
            table.push(label);
        }
        o += 1;

        if (o >= limit || !isKvTypeByte(buffer[o])) {
            continue;
        }

        const tryOffsets = [o];
        if (o + 1 < limit && isKvTypeByte(buffer[o + 1])) {
            tryOffsets.push(o + 1);
        }

        for (const kvOffset of tryOffsets) {
            for (const endMarker of [KV_TYPE.End, KV_TYPE.AlternateEnd]) {
                try {
                    const { tree, bytesRead } = parseSteamKitBinaryKv(buffer, kvOffset, {
                        stringTable: table,
                        endMarker,
                    });
                    const score = scoreKvTree(tree);
                    if (isAcceptableStoreKvTree(tree, bytesRead)) {
                        const candidate = {
                            table: [...table],
                            offset: kvOffset,
                            tree,
                            score,
                            bytesRead,
                            endMarker,
                        };
                        if (!best || score > best.score) {
                            best = candidate;
                        }
                        if (score >= 14) {
                            return best;
                        }
                    }
                } catch {
                    /* string-table parse not aligned yet */
                }

                try {
                    const { tree, bytesRead } = parseSteamKitBinaryKv(buffer, kvOffset, { endMarker });
                    const score = scoreKvTree(tree);
                    if (isAcceptableStoreKvTree(tree, bytesRead)) {
                        const candidate = {
                            table: null,
                            offset: kvOffset,
                            tree,
                            score,
                            bytesRead,
                            endMarker,
                        };
                        if (!best || score > best.score) {
                            best = candidate;
                        }
                        if (score >= 14) {
                            return best;
                        }
                    }
                } catch {
                    /* plain KV parse not aligned yet */
                }
            }
        }
    }
    return best;
};

/** Collect null-terminated UTF-8 strings from a prefix region (labels only). */
const collectStringTableCandidates = (buffer, maxScan = 256) => {
    const detected = detectStringTableKvStart(buffer, maxScan);
    return detected?.table?.slice(0, 32) ?? [];
};

const tryParseAtOffset = (buffer, start, stringTable, endMarker) => {
    const { tree, bytesRead } = parseSteamKitBinaryKv(buffer, start, {
        stringTable,
        endMarker,
    });
    return { tree, bytesRead, score: scoreKvTree(tree) };
};

const treeHasStorePriceData = (node, depth = 0) => {
    if (node == null || depth > 16) return false;
    if (typeof node !== 'object') return false;
    if (Array.isArray(node)) {
        return node.some((entry) => treeHasStorePriceData(entry, depth + 1));
    }
    for (const [key, value] of Object.entries(node)) {
        if (
            key === '1201' ||
            key === 'price' ||
            key === '9' ||
            key === 'store' ||
            key === 'entries' ||
            key === 'currencies'
        ) {
            return true;
        }
        if (Buffer.isBuffer(value)) {
            continue;
        }
        if (typeof value === 'object' && treeHasStorePriceData(value, depth + 1)) {
            return true;
        }
    }
    return false;
};

const scoreKvTree = (node, depth = 0) => {
    if (node == null || depth > 14) return 0;
    if (typeof node !== 'object' || Array.isArray(node)) return 0;
    let score = Object.keys(node).length;
    const keys = Object.keys(node);
    if (keys.includes('store')) score += 8;
    if (keys.includes('entries')) score += 12;
    if (keys.includes('currencies')) score += 6;
    if (keys.some((k) => /batch|item|price|casket/i.test(k))) score += 4;
    if (keys.some((k) => /^\d+$/.test(k) && parseInt(k, 10) === 1201)) score += 10;
    for (const value of Object.values(node)) {
        if (value && typeof value === 'object' && !Array.isArray(value)) {
            score += scoreKvTree(value, depth + 1);
        }
    }
    return score;
};

const isAcceptableStoreKvTree = (tree, bytesRead) => {
    if (!tree || bytesRead < 24 || !treeHasStorePriceData(tree)) {
        return false;
    }
    const keys = Object.keys(tree);
    const store = tree.store && typeof tree.store === 'object' ? tree.store : tree;
    const storeKeys = Object.keys(store);
    if (storeKeys.includes('entries') || keys.includes('entries')) {
        return true;
    }
    return scoreKvTree(tree) >= 6;
};

/**
 * Parse CS2 store price sheet; tries string-table KV, VBKV, and plain binary KV.
 * @param {Buffer} buffer
 */
const parseStorePriceSheetBinary = (buffer) => {
    const stringTableHit = detectStringTableKvStart(buffer);
    if (stringTableHit && isAcceptableStoreKvTree(stringTableHit.tree, stringTableHit.bytesRead)) {
        const root =
            stringTableHit.tree?.store != null
                ? stringTableHit.tree
                : stringTableHit.table?.includes('store')
                  ? { store: stringTableHit.tree }
                  : stringTableHit.tree;
        return {
            tree: root,
            offset: stringTableHit.offset,
            labels: (stringTableHit.table ?? []).slice(0, 8),
            stringTableSize: stringTableHit.table?.length ?? 0,
            endMarker: stringTableHit.endMarker ?? KV_TYPE.End,
        };
    }

    const labels = collectStringTableCandidates(buffer, 256);
    let best = null;
    let lastErr = null;

    const attempts = [];

    // CS2 store sheets: \0store\0… then VBKV-style sections (AlternateEnd)
    if (buffer.length > 8 && buffer[0] === 0 && buffer.toString('utf8', 1, 6) === 'store') {
        for (const kvOffset of [7, 8, 14, 16, 28, 29]) {
            if (kvOffset < buffer.length && isKvTypeByte(buffer[kvOffset])) {
                attempts.unshift({
                    offset: kvOffset,
                    stringTable: null,
                    endMarker: KV_TYPE.AlternateEnd,
                    labels: ['store'],
                });
            }
        }
    }

    // VBKV at offset 0
    if (buffer.length >= 8 && buffer.readUInt32LE(0) === VBKV_MAGIC) {
        attempts.push({ offset: 8, stringTable: null, endMarker: KV_TYPE.AlternateEnd, labels: ['VBKV'] });
    }

    // String-table KV after label preamble (CS2 sheets often start with \0store\0…)
    for (const tableSize of [labels.length, labels.length + 2, labels.length + 4, 20, 40, 80]) {
        const table = labels.slice(0, tableSize);
        if (table.length < 2) continue;
        for (let kvOffset = 0; kvOffset <= 128; kvOffset += 1) {
            if (kvOffset >= buffer.length) break;
            if (!isKvTypeByte(buffer[kvOffset])) continue;
            attempts.push({
                offset: kvOffset,
                stringTable: table,
                endMarker: KV_TYPE.End,
                labels: table.slice(0, 6),
            });
        }
    }

    // Plain binary KV (SteamKit) at many offsets
    for (let offset = 0; offset <= 128; offset += 1) {
        if (offset >= buffer.length || !isKvTypeByte(buffer[offset])) continue;
        attempts.push({ offset, stringTable: null, endMarker: KV_TYPE.End, labels });
        attempts.push({ offset, stringTable: null, endMarker: KV_TYPE.AlternateEnd, labels });
    }

    const seen = new Set();
    for (const attempt of attempts) {
        const key = `${attempt.offset}:${attempt.stringTable?.length ?? 0}:${attempt.endMarker}`;
        if (seen.has(key)) continue;
        seen.add(key);
        try {
            const parsed = tryParseAtOffset(
                buffer,
                attempt.offset,
                attempt.stringTable,
                attempt.endMarker
            );
            if (!isAcceptableStoreKvTree(parsed.tree, parsed.bytesRead)) continue;
            if (!best || parsed.score > best.score) {
                best = {
                    tree: parsed.tree,
                    offset: attempt.offset,
                    labels: attempt.labels,
                    score: parsed.score,
                    stringTableSize: attempt.stringTable?.length ?? 0,
                    endMarker: attempt.endMarker,
                };
            }
            if (parsed.score >= 12) break;
        } catch (err) {
            lastErr = err;
        }
    }

    if (best) {
        const root =
            best.tree?.store != null
                ? best.tree
                : labels.includes('store')
                  ? { store: best.tree }
                  : best.tree;
        return {
            tree: root,
            offset: best.offset,
            labels: best.labels,
            stringTableSize: best.stringTableSize,
            endMarker: best.endMarker,
        };
    }

    throw lastErr || new Error('Failed to parse store price sheet binary KV');
};

/** Best-effort parse for CS2 sheets at the standard post-preamble offset. */
const parseCs2StorePriceSheet = (buffer) => {
    const { table: stringTable, kvOffset: tableKvOffset } = buildStringTableAndKvOffset(buffer);
    const candidates = [];

    if (stringTable.length > 4) {
        for (const endMarker of [KV_TYPE.AlternateEnd, KV_TYPE.End]) {
            candidates.push({
                offset: tableKvOffset,
                endMarker,
                stringTable,
            });
        }
    }

    candidates.push({ offset: 7, endMarker: KV_TYPE.AlternateEnd, stringTable: null });
    if (tableKvOffset !== 7 && tableKvOffset < buffer.length) {
        candidates.push({
            offset: tableKvOffset,
            endMarker: KV_TYPE.AlternateEnd,
            stringTable: stringTable.length > 4 ? stringTable : null,
        });
    }

    let best = null;
    for (const { offset, endMarker, stringTable: strTab } of candidates) {
        if (offset >= buffer.length) continue;
        try {
            const { tree, bytesRead } = parseSteamKitBinaryKv(buffer, offset, {
                endMarker,
                stringTable: strTab,
            });
            const score = scoreKvTree(tree);
            const store = tree?.store ?? tree;
            const entries = store?.entries ?? tree?.entries;
            const hasEntries = entries != null && typeof entries === 'object';
            const entryKeys = hasEntries ? Object.keys(entries).length : 0;
            const hasCasket =
                hasEntries &&
                (Object.prototype.hasOwnProperty.call(entries, 'casket') ||
                    Object.prototype.hasOwnProperty.call(entries, 'tool_casket'));
            if (entryKeys < 8 && !hasCasket) {
                continue;
            }
            const weighted =
                score + entryKeys * 3 + (hasEntries ? 20 : 0) + (hasCasket ? 100 : 0);
            if (!best || weighted > best.weighted || (weighted === best.weighted && bytesRead > best.bytesRead)) {
                best = {
                    tree,
                    bytesRead,
                    score,
                    weighted,
                    offset,
                    endMarker,
                    stringTableSize: strTab?.length ?? 0,
                };
            }
        } catch {
            /* try next */
        }
    }
    if (!best) {
        throw new Error('CS2 store price sheet parse failed');
    }
    const root =
        best.tree?.store != null
            ? best.tree
            : buffer[0] === 0 && buffer.toString('utf8', 1, 6) === 'store'
              ? { store: best.tree }
              : best.tree;
    return {
        tree: root,
        offset: best.offset,
        endMarker: best.endMarker,
        labels: ['store'],
        stringTableSize: best.stringTableSize,
    };
};

module.exports = {
    parseSteamKitBinaryKv,
    parseStorePriceSheetBinary,
    parseCs2StorePriceSheet,
    detectStringTableKvStart,
    KV_TYPE,
};
