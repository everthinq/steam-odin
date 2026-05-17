const fs = require('fs');
const axios = require('axios');
const VDF = require('@node-steam/vdf');

const itemsLink = 'https://raw.githubusercontent.com/SteamDatabase/GameTracking-CS2/master/game/csgo/pak01_dir/scripts/items/items_game.txt';
const translationsLink = 'https://raw.githubusercontent.com/SteamDatabase/GameTracking-CS2/master/game/csgo/pak01_dir/resource/csgo_english.txt';

async function run() {
    console.log('Downloading items_game.txt...');
    try {
        const response = await axios.get(itemsLink, { responseType: 'text' });
        console.log('Parsing items_game.txt...');
        const itemsJson = VDF.parse(response.data);
        fs.writeFileSync('items_game.json', JSON.stringify(itemsJson, null, 2));
        console.log('Saved items_game.json');
    } catch (e) {
        console.error('Failed to process items_game:', e);
    }

    console.log('Downloading csgo_english.txt...');
    try {
        // We use responseType: 'text' to ensure axios returns a string
        // Axios usually handles encoding automatically if server sends charset
        const response = await axios.get(translationsLink, { responseType: 'text' });
        console.log('Parsing csgo_english.txt...');

        const data = response.data;
        const finalDict = {};
        // Split by newline. Handle both \r\n and \n
        const lines = data.split(/\r?\n/);
        let count = 0;

        lines.forEach(function (value) {
            // Casemove logic
            var test = value.match(/"(.*?)"/g);
            if (test && test[1]) {
                const keyLines = test[0].replaceAll('"', '').toLowerCase();
                const valLines = test[1].replaceAll('"', '');
                // We store without quotes to be clean, as items.js expects.
                // But wait, items.js getTranslation does .replaceAll('"', '') on result.
                // So if we store without quotes here, items.js will just remove nothing and return clean string.
                // If we store WITH quotes, items.js removes them.
                // Casemove stored: finalDict[...] = test[1] (which HAS quotes).
                // Let's mimic Casemove and store WITH quotes just in case items.js relies on it?
                // Actually, let's store comments too if needed? No.

                // Let's match Casemove EXACTLY:
                // finalDict[test[0].replaceAll('"', '').toLowerCase()] = test[1];
                finalDict[keyLines] = test[1];
                count++;
            }
        });
        console.log(`Processed ${count} translation tokens.`);

        fs.writeFileSync('csgo_english.json', JSON.stringify(finalDict, null, 2));
        console.log('Saved csgo_english.json');
    } catch (e) {
        console.error('Failed to process csgo_english:', e);
    }
}

run();
