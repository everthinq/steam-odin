// Screenshot a Heimdall page in headless Chrome, optionally after running JavaScript in it (for
// example clicking a row open). No dependencies: Chrome's remote debugging protocol over Node's
// built-in WebSocket and fetch (Node 22 or newer). For checking a UI change renders; never use it
// to click a real Pay / Buy / Sell / Approve button.
//
//   node scripts/ui-screenshot.mjs <url> <out.png> ["<JavaScript run in the page>"] [height]
//   node scripts/ui-screenshot.mjs http://localhost:3000/store-catalogue/boosts /tmp/boosts.png \
//     "(() => { document.querySelector('[aria-expanded]').click(); return 'clicked'; })()" 1900
//
// The script's answer is printed; the page gets 6 seconds to load and 4 more after the script.
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const [url, out, script, height] = process.argv.slice(2);
if (!url || !out) {
    console.error('usage: node scripts/ui-screenshot.mjs <url> <out.png> ["<JavaScript>"] [height]');
    process.exit(2);
}
const CHROME = process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9333;
const profile = mkdtempSync(join(tmpdir(), 'ui-screenshot-'));
const chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--hide-scrollbars', `--remote-debugging-port=${PORT}`,
    `--window-size=1600,${height || 1500}`, `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

try {
    let target;
    for (let attempt = 0; attempt < 50 && !target; attempt++) {
        await sleep(200);
        try {
            target = (await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()).find((page) => page.type === 'page');
        } catch { /* Chrome still starting */ }
    }
    if (!target) throw new Error('Chrome did not start');
    const socket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve) => socket.addEventListener('open', resolve));
    let counter = 0;
    const pending = new Map();
    socket.addEventListener('message', (event) => {
        const message = JSON.parse(event.data);
        if (pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id); }
    });
    const send = (method, params = {}) => new Promise((resolve) => {
        const id = ++counter;
        pending.set(id, resolve);
        socket.send(JSON.stringify({ id, method, params }));
    });
    await send('Page.enable');
    await send('Page.navigate', { url });
    await sleep(6000);
    if (script) {
        const answer = await send('Runtime.evaluate', { expression: script, awaitPromise: true });
        console.log(JSON.stringify(answer.result?.result?.value ?? answer.result));
        await sleep(4000);
    }
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(out, Buffer.from(shot.result.data, 'base64'));
    socket.close();
} finally {
    chrome.kill();
    await sleep(300);
    rmSync(profile, { recursive: true, force: true });
}
