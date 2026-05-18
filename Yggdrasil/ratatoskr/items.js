const fs = require('fs');
const VDF = require('@node-steam/vdf');

// Helper to process items_game.json structure
function updateItemsLoop(jsonData, keyToRun) {
  const returnDict = {};
  if (!jsonData['items_game']) return returnDict;

  for (const [key, value] of Object.entries(jsonData['items_game'])) {
    if (key == keyToRun) {
      for (const [subKey, subValue] of Object.entries(value)) {
        returnDict[subKey] = subValue;
      }
    }
  }
  return returnDict;
}

class items {
  translation = {};
  csgoItems = {};

  constructor() {
    this.loadLocalFiles();
  }

  loadLocalFiles() {
    try {
      console.log('Loading CS2 item definitions from local files...');

      // Load Translations
      const csgoEnglish = require('./csgo_english.json');
      // Casemove's backup file seems to be the raw KV or already processed? 
      // Based on fileGetError it passes it to setTranslations.
      // Let's assume the JSONs in itemsBackupFiles are directly usable or need simple parsing.
      // The original code in fileGetError does: items.setTranslations(csgoEnglish, 'Error');
      // But getTranslations does some regex parsing on the raw file.
      // If csgo_english.json is the raw converted to JSON, we might need to adapt.
      // Let's inspect the files first? No, I copied them. 
      // Let's assume they are the VDF converted to JSON or just key-values.

      // Actually, looking at fileGetError:
      // let csgoEnglish = require('./itemsBackupFiles/csgo_english.json');
      // items.setTranslations(csgoEnglish, 'Error');

      this.setTranslations(csgoEnglish, 'Local Load');

      // Load Items Game
      const itemsGameRaw = require('./items_game.json');
      // The original updateItems function expects VDF parsed data. 
      // check fileGetError again: items.setCSGOItems(itemsGame);
      // It seems itemsGame.json is ALREADY the processed dictionary with 'items', 'paint_kits' etc?
      // OR it is the raw VDF-to-JSON?
      // fileGetError passes it to setCSGOItems. 
      // setCSGOItems sets this.csgoItems.
      // But updateItems builds a specific dictionary structure (dict_to_write).
      // If items_game.json is the raw VDF-converted, we need to process it.

      // Let's try to process it as if it was the raw JSON from VDF.
      // If the file is the RESULT of processing, then we just set it.
      // Given the name 'items_game.json' in 'backupFiles', it's likely the raw VDF->JSON.

      const processedItems = this.processItemsGame(itemsGameRaw);
      this.setCSGOItems(processedItems);

      console.log(`Loaded ${Object.keys(this.csgoItems.items).length} items.`);
      console.log(`Loaded ${Object.keys(this.csgoItems.sticker_kits).length} sticker kits.`);

      // Debug specific items
      console.log(`Check 1355:`, this.csgoItems.items['1355'] ? 'Found' : 'Missing');
      console.log(`Check 1209:`, this.csgoItems.items['1209'] ? 'Found' : 'Missing');

      console.log('CS2 item definitions loaded successfully.');
    } catch (err) {
      console.error('Failed to load local item definitions:', err);
    }
  }


  // ...
  getPrefab(prefab) {
    if (!this.csgoItems['prefabs']) {
      console.error('Prefabs dictionary missing!');
      return undefined;
    }
    const val = this.csgoItems['prefabs'][prefab.toString()];
    if (!val) {
      // console.warn(`Prefab not found: ${prefab}`);
    }
    return val;
  }

  processItemsGame(jsonData) {
    const dict_to_write = {
      items: {},
      paint_kits: {},
      prefabs: {},
      sticker_kits: {},
      casket_icons: {},
      music_kits: {},
      graffiti_tints: {},
      item_sets: {},
      client_loot_lists: {},
    };

    // If the JSON is already structured (keys like 'items', 'paint_kits' at top level), use it directly.
    if (jsonData.items && jsonData.paint_kits) return jsonData;

    // Otherwise process it from items_game root
    dict_to_write['items'] = updateItemsLoop(jsonData, 'items');
    dict_to_write['paint_kits'] = updateItemsLoop(jsonData, 'paint_kits');
    dict_to_write['prefabs'] = updateItemsLoop(jsonData, 'prefabs');
    dict_to_write['sticker_kits'] = updateItemsLoop(jsonData, 'sticker_kits');
    dict_to_write['music_kits'] = updateItemsLoop(jsonData, 'music_definitions');
    dict_to_write['graffiti_tints'] = updateItemsLoop(jsonData, 'graffiti_tints');
    dict_to_write['item_sets'] = updateItemsLoop(jsonData, 'item_sets');
    dict_to_write['client_loot_lists'] = updateItemsLoop(jsonData, 'client_loot_lists');

    if (jsonData['items_game'] && jsonData['items_game']['alternate_icons2']) {
      dict_to_write['casket_icons'] = jsonData['items_game']['alternate_icons2']['casket_icons'];
    }

    return dict_to_write;
  }

  setCSGOItems(value) {
    this.csgoItems = value;
  }

  setTranslations(value, commandFrom) {
    // Casemove's backup csgo_english.json might be in a specific format.
    // If it's the raw VDF->JSON, we need to extract "lang"->"Tokens"
    if (value.lang && value.lang.Tokens) {
      // Lowercase keys for case-insensitive lookup
      const lowerDict = {};
      for (const [k, v] of Object.entries(value.lang.Tokens)) {
        lowerDict[k.toLowerCase()] = v;
      }
      this.translation = lowerDict;
    } else {
      this.translation = value;
    }
  }



  handleError(callback, args) {
    try {
      return callback.apply(this, args);
    } catch (err) {
      console.log(err);
      return '';
    }
  }

  inventoryConverter(inventoryResult, isCasket = false) {
    var returnList = [];
    if (typeof inventoryResult === 'object' && inventoryResult !== null) {
      returnList;
    } else {
      return returnList;
    }

    for (const [key, value] of Object.entries(inventoryResult)) {
      if (value['def_index'] == undefined) {
        continue;
      }
      const freeRewardStatusBytes = getAttributeValueBytes(value, 277);
      if (
        freeRewardStatusBytes &&
        freeRewardStatusBytes.readUInt32LE(0) === 1
      ) {
        continue;
      }
      let musicIndexBytes = getAttributeValueBytes(value, 166);
      if (musicIndexBytes) {
        value.music_index = musicIndexBytes.readUInt32LE(0);
      }
      let graffitiTint = getAttributeValueBytes(value, 233);
      if (graffitiTint) {
        value.graffiti_tint = graffitiTint.readUInt32LE(0);
      }
      if (
        (value['casket_id'] !== undefined && isCasket == false) ||
        ['17293822569110896676', '17293822569102708641'].includes(value['id'])
      ) {
        continue;
      }
      // console.log(value['item_id'])

      const returnDict = {};
      // URL
      let imageURL = this.handleError(this.itemProcessorImageUrl, [value]);

      const iconMatch = getAttributeValueBytes(value, 70)?.readUInt32LE(0);
      if (
        value['def_index'] == 1201 &&
        iconMatch &&
        this.csgoItems['casket_icons']?.[iconMatch]?.icon_path
      ) {
        imageURL = this.csgoItems['casket_icons']?.[iconMatch]?.icon_path;
      }
      // Check names
      returnDict['item_name'] = this.handleError(this.itemProcessorName, [
        value,
        imageURL,
      ]);
      if (returnDict['item_name'] == '') {
        console.log('Error');
        try {
          console.log(value, this.get_def_index(value['def_index']));
        } catch (err) {
          console.log(value);
        }
      }
      returnDict['item_customname'] = value['custom_name'];
      returnDict['item_url'] = imageURL;
      returnDict['item_id'] = value['id'];
      returnDict['position'] = 9999;
      if (value['position'] != null) {
        returnDict['position'] = value['position'];
      }

      // Check tradable after value
      if (value['tradable_after'] !== undefined) {
        const tradable_after_date = new Date(value['tradable_after']);
        const todaysDate = new Date();
        if (
          tradable_after_date >= todaysDate &&
          returnDict['item_name'].includes('Key') == false
        ) {
          returnDict['trade_unlock'] = tradable_after_date;
        }
      }

      if (value['casket_contained_item_count'] !== undefined) {
        returnDict['item_storage_total'] = value['casket_contained_item_count'];
      }

      // Check paint_wear value
      if (value['paint_wear'] !== undefined) {
        returnDict['item_wear_name'] = this.handleError(getSkinWearName, [
          value['paint_wear'],
        ]);
        returnDict['item_paint_wear'] = value['paint_wear'];
      }

      // Trade restrictions (maybe?)
      returnDict['item_origin'] = value['origin'];

      returnDict['item_moveable'] = this.handleError(
        this.itemProcessorCanBeMoved,
        [returnDict, value]
      );

      returnDict['item_has_stickers'] = this.handleError(
        this.itemProcessorHasStickersApplied,
        [returnDict, value]
      );
      let equipped = this.handleError(this.itemProcessorisEquipped, [value]);
      returnDict['equipped_ct'] = equipped[0];
      returnDict['equipped_t'] = equipped[1];
      returnDict['def_index'] = value['def_index'];
      returnDict['item_collection'] = this.handleError(this.getCollectionName, [value]) || '';

      if (returnDict['item_has_stickers']) {
        const stickerList = [];
        for (const [stickersKey, stickersValue] of Object.entries(
          value['stickers']
        )) {
          stickerList.push(
            this.handleError(this.stickersProcessData, [stickersValue])
          );
        }
        returnDict['stickers'] = stickerList;
      } else {
        returnDict['stickers'] = [];
      }

      if (
        value?.rarity == 6 ||
        value?.quality == 3 ||
        returnDict['item_name'].includes('Souvenir') ||
        !returnDict['item_url'].includes('econ/default_generated')
      ) {
        returnDict['tradeUp'] = false;
      } else {
        returnDict['rarity'] = value.rarity;
        returnDict['rarityName'] = this.handleError(
          this.itemProcessorGetRarityName,
          [value.rarity]
        );
        returnDict['rarity_color'] = this.handleError(
          this.itemProcessorGetRarityColor,
          [value.rarity]
        );
        returnDict['tradeUp'] = true;
      }
      returnDict['stattrak'] = false;
      if (this.isStatTrak(value)) {
        returnDict['stattrak'] = true;
        returnDict['item_name'] = 'StatTrak™ ' + returnDict['item_name'];
      }
      // Star
      if (value['quality'] == 3) {
        returnDict['item_name'] = '★ ' + returnDict['item_name'];
        returnDict['item_moveable'] = true;
        returnDict['rarity_color'] = '#eb4b4b'; // Star items are effectively Covert/Special
      }

      // Promotional pin fix
      if (returnDict['item_name']?.includes('Pin') && value['origin'] == 5) {
        returnDict['item_moveable'] = false;
      }

      // Promotional music kit fix
      if (value['music_index'] != undefined && value['origin'] == 0) {
        returnDict['item_moveable'] = false;
      }

      // returnDict['coordinator_data'] = JSON.stringify(value);
      // console.log(value, returnDict)

      returnList.push(returnDict);
    }
    return returnList;
  }

  itemProcessorGetRarityName(rarity) {
    const rarityDict = {
      1: 'Consumer Grade',
      2: 'Industrial Grade',
      3: 'Mil-Spec',
      4: 'Restricted',
      5: 'Classified',
      6: 'Covert',
      7: 'Contraband',
    };
    return rarityDict[rarity] || 'Common';
  }

  itemProcessorGetRarityColor(rarity) {
    const colorDict = {
      1: '#b0c3d9', // Consumer
      2: '#5e98d9', // Industrial
      3: '#4b69ff', // Mil-Spec
      4: '#8847ff', // Restricted
      5: '#d32ce6', // Classified
      6: '#eb4b4b', // Covert
      7: '#e4ae39', // Contraband
    };
    return colorDict[rarity] || '#b0c3d9';
  }

  itemProcessorHasStickersApplied(returnDict, storageRow) {
    if (
      returnDict['item_url'].includes('econ/characters') ||
      returnDict['item_url'].includes('econ/default_generated') ||
      returnDict['item_url'].includes('weapons/base_weapons')
    ) {
      if (storageRow['stickers'] !== undefined) {
        return true;
      }
    }
    return false;
  }

  itemProcessorisEquipped(storageRow) {
    // 2 = CT
    // 3 = T
    let CT = false;
    let T = false;

    for (const [key, value] of Object.entries(storageRow?.equipped_state)) {
      if (value?.new_class == 2) {
        T = true;
      }
      if (value?.new_class == 3) {
        CT = true;
      }
    }
    return [CT, T];
  }

  isStatTrak(storageRow) {
    if (storageRow['attribute'] !== undefined) {
      for (const [, value] of Object.entries(storageRow['attribute'])) {
        if (value['def_index'] == 80) {
          return true;
        }
      }
    }
    return false;
  }

  itemProcessorName(storageRow, imageURL) {
    const defIndexresult = this.get_def_index(storageRow['def_index']);

    if (!defIndexresult) return `Unknown Item (${storageRow['def_index']})`;

    // Check if CSGO Case Key
    if (imageURL == 'econ/tools/weapon_case_key') {
      return this.getTranslation(defIndexresult['item_name']);
    }

    if (imageURL == 'econ/test/test_quest_icon') {
      return `Quest Item (${storageRow['def_index']})`;
    }

    // DEBUG: Log storageRow keys for first few items
    if (Math.random() < 0.05) {
      console.log('Item Keys:', Object.keys(storageRow));
      console.log('Paint Index check:', 'paint_index:', storageRow['paint_index'], 'paintIndex:', storageRow['paintIndex']);
    }

    // Music kit check
    if (storageRow['music_index'] !== undefined) {
      const musicKitIndex = storageRow['music_index'];
      const musicKitResult = this.getMusicKits(musicKitIndex);
      let nameToUse =
        'Music Kit | ' + this.getTranslation(musicKitResult['loc_name']);

      return nameToUse;
    }

    // Main checks
    // Get first string
    if (defIndexresult['item_name'] !== undefined) {
      var baseOne = this.getTranslation(defIndexresult['item_name']);
    } else if (defIndexresult['prefab'] !== undefined) {
      const baseSkinName = this.getPrefab(defIndexresult['prefab'])[
        'item_name'
      ];
      var baseOne = this.getTranslation(baseSkinName);
    }

    // Get second string
    if (
      storageRow['stickers'] !== undefined &&
      storageRow['stickers'].length > 0 &&
      imageURL.includes('econ/characters/') == false
    ) {
      var relevantStickerData = storageRow['stickers'][0];
      if (
        relevantStickerData &&
        relevantStickerData['slot'] == 0 &&
        baseOne.includes('Coin') == false
      ) {
        var stickerDefIndex = this.getStickerDetails(
          relevantStickerData['sticker_id']
        );
        // Added safety check
        if (stickerDefIndex && stickerDefIndex['item_name']) {
          var baseTwo = this.getTranslation(stickerDefIndex['item_name']);
        }
      }
    }
    if (storageRow['paint_index'] !== undefined) {
      var skinPatternName = this.getPaintDetails(storageRow['paint_index']);
      if (skinPatternName) {
        var baseTwo = this.getTranslation(skinPatternName['description_tag']);
      }
    }

    // Get third string (wear name)
    if (storageRow['paint_wear'] !== undefined) {
      var baseThree = getSkinWearName(storageRow['paint_wear']);
    }

    if (baseOne) {
      var finalName = baseOne;
      if (baseTwo) {
        var finalName = `${baseOne} | ${baseTwo}`;
        if (baseThree) {
          var finalName = `${baseOne} | ${baseTwo} (${baseThree})`;
        }
      }
    }

    if (storageRow['attribute'] !== undefined) {
      for (const [, value] of Object.entries(storageRow['attribute'])) {
        if (
          value['def_index'] == 140 &&
          finalName.includes('Souvenir') == false
        ) {
          var finalName = 'Souvenir ' + finalName;
        }
      }
    }

    // Graffiti kit check
    if (storageRow['graffiti_tint'] !== undefined) {
      const graffitiKitIndex = storageRow['graffiti_tint'];
      const graffitiKitResult = capitalizeWords(
        this.getGraffitiKitName(graffitiKitIndex).replaceAll('_', ' ')
      );
      var finalName = finalName + ' (' + graffitiKitResult + ')';
      var finalName = finalName.replace('Swat', 'SWAT');
    }

    // console.log(`Generated name: ${finalName} for def_index: ${storageRow['def_index']}`);
    return finalName;
  }

  itemProcessorImageUrl(storageRow) {
    const defIndexresult = this.get_def_index(storageRow['def_index']);

    if (!defIndexresult) return '';

    // Music kit check
    if (storageRow['music_index'] !== undefined) {
      const musicKitIndex = storageRow['music_index'];
      const localMusicKits = this.getMusicKits(musicKitIndex);
      return localMusicKits['image_inventory'];
    }

    // Rest of check

    // Check if it should use the full image_inventory
    if (defIndexresult['image_inventory'] !== undefined) {
      var imageInventory = defIndexresult['image_inventory'];
    }

    // Get second string
    if (storageRow['stickers'] !== undefined && imageInventory == undefined) {
      var relevantStickerData = storageRow['stickers'][0];
      if (relevantStickerData['slot'] == 0) {
        var stickerDefIndex = this.getStickerDetails(
          relevantStickerData['sticker_id']
        );

        // Added safety check
        if (stickerDefIndex) {
          if (stickerDefIndex['patch_material'] !== undefined) {
            var imageInventory = `econ/patches/${stickerDefIndex['patch_material']}`;
          } else if (stickerDefIndex['sticker_material'] !== undefined) {
            var imageInventory = `econ/stickers/${stickerDefIndex['sticker_material']}`;
          }
        }
      }
    }
    // Weapons and knifes
    if (storageRow['paint_index'] !== undefined) {
      var skinPatternName = this.getPaintDetails(storageRow['paint_index']);
      if (skinPatternName) {
        var imageInventory = `econ/default_generated/${defIndexresult['name']}_${skinPatternName['name']}_light_large`;
      } else {
        // Fallback if paint kit not found
        var imageInventory = `econ/weapons/base_weapons/${defIndexresult['name']}`;
      }
    } else if (defIndexresult['baseitem'] == 1) {
      var imageInventory = `econ/weapons/base_weapons/${defIndexresult['name']}`;
    }

    return imageInventory || '';
  }
  itemProcessorCanBeMoved(returnDict, storageRow) {
    const defIndexresult = this.get_def_index(storageRow['def_index']);

    if (!defIndexresult) return true;

    if (defIndexresult['prefab'] !== undefined) {
      if (defIndexresult['prefab'] == 'collectible_untradable') {
        return false;
      }
    }
    if (defIndexresult['item_name'] !== undefined) {
      if (
        returnDict['item_url'].includes('econ/status_icons/') &&
        returnDict['item_origin'] == 0
      ) {
        return false;
      }
      if (returnDict['item_url'].includes('econ/status_icons/service_medal_')) {
        return false;
      }

      if (storageRow['def_index'] == 987) {
        return false;
      }

      if (returnDict['item_url'].includes('plusstars')) {
        return false;
      }
    }

    // If characters
    if (defIndexresult['attributes'] !== undefined) {
      for (const [key, value] of Object.entries(defIndexresult['attributes'])) {
        if (key == 'cannot trade' && value == 1) {
          return false;
        }
      }
    }
    if (
      returnDict['item_url'].includes('crate_key') &&
      storageRow['flags'] == 10
    ) {
      return false;
    }
    if (returnDict['item_url'].includes('weapons/base_weapons')) {
      return false;
    }
    return true;
  }
  stickersProcessData(relevantStickerData) {
    // Get second string
    var stickerDefIndex = this.getStickerDetails(
      relevantStickerData['sticker_id']
    );
    if (stickerDefIndex['patch_material'] !== undefined) {
      var imageInventory = `econ/patches/${stickerDefIndex['patch_material']}`;
      var stickerType = 'Patch';
    } else if (stickerDefIndex['sticker_material'] !== undefined) {
      var imageInventory = `econ/stickers/${stickerDefIndex['sticker_material']}`;
      var stickerType = 'Sticker';
    }
    const stickerDict = {
      sticker_name: this.getTranslation(stickerDefIndex['item_name']),
      sticker_url: imageInventory,
      sticker_type: stickerType,
    };
    return stickerDict;
  }

  get_def_index(def_index) {
    return this.csgoItems['items'][def_index];
  }

  getTranslation(csgoString) {
    let stringFormatted = csgoString.replace('#', '').toLowerCase();

    if (!this.translation[stringFormatted]) {
      console.warn(`Missing translation for: ${csgoString} -> ${stringFormatted}`);
      return csgoString; // Fallback to key
    }

    return this.translation[stringFormatted].replaceAll('"', '');
  }
  getPrefab(prefab) {
    return this.csgoItems['prefabs'][prefab.toString()];
  }

  getPaintDetails(paintIndex) {
    return this.csgoItems['paint_kits'][paintIndex];
  }

  getMusicKits(musicIndex) {
    return this.csgoItems['music_kits'][musicIndex];
  }

  getGraffitiKitName(graffitiID) {
    for (const [key, value] of Object.entries(
      this.csgoItems['graffiti_tints']
    )) {
      if (value.id == graffitiID) {
        return key;
      }
    }
  }

  getStickerDetails(stickerID) {
    return this.csgoItems['sticker_kits'][stickerID];
  }

  buildPaintKitCollectionMap() {
    if (this._paintKitCollectionMap) return this._paintKitCollectionMap;
    this._paintKitCollectionMap = {};
    const sets = this.csgoItems['item_sets'] || {};
    for (const [, setData] of Object.entries(sets)) {
      if (!setData?.items) continue;
      const setName = this.getTranslation(setData.name || '');
      if (!setName || setName.startsWith('#')) continue;
      for (const itemKey of Object.keys(setData.items)) {
        const match = itemKey.match(/\[([^\]]+)\]/);
        if (match) this._paintKitCollectionMap[match[1]] = setName;
      }
    }
    return this._paintKitCollectionMap;
  }

  getStickerPackDisplayName(lootListKey) {
    const base = lootListKey.replace(
      /_(rare|mythical|legendary|ancient|uncommon|common|lootlist)$/,
      ''
    );
    const packId = base.replace(/^sticker_pack_/, '');
    const candidates = [
      `csgo_crate_sticker_pack_${packId}_short`,
      `csgo_crate_sticker_pack_${packId}_capsule`,
      `csgo_crate_sticker_pack_${packId}`,
    ];
    for (const key of candidates) {
      const label = this.getTranslation(`#${key}`);
      if (label && !label.startsWith('#') && label.toLowerCase() !== key) {
        return label;
      }
    }
    return '';
  }

  buildStickerKitCollectionMap() {
    if (this._stickerKitCollectionMap) return this._stickerKitCollectionMap;
    this._stickerKitCollectionMap = {};
    const lists = this.csgoItems['client_loot_lists'] || {};
    for (const [listKey, entries] of Object.entries(lists)) {
      if (!listKey.startsWith('sticker_pack_') || listKey.endsWith('_lootlist')) {
        continue;
      }
      const packName = this.getStickerPackDisplayName(listKey);
      if (!packName) continue;
      for (const entryKey of Object.keys(entries || {})) {
        const match = entryKey.match(/\[([^\]]+)\]sticker/);
        if (match) this._stickerKitCollectionMap[match[1]] = packName;
      }
    }
    return this._stickerKitCollectionMap;
  }

  getStickerIdFromRow(storageRow) {
    const stickers = storageRow?.stickers;
    if (!stickers) return null;
    const first = Array.isArray(stickers) ? stickers[0] : Object.values(stickers)[0];
    return first?.sticker_id ?? null;
  }

  getStickerCollectionFromMaterial(stickerKit) {
    const material = stickerKit?.sticker_material;
    if (!material) return '';
    const parts = material.split('/');
    const folder = parts[0] === 'community' && parts[1] ? parts[1] : parts[0];
    if (!folder || !folder.includes('capsule')) return '';
    const packSlug = folder.replace(/_capsule$/, '');
    const candidates = [
      `csgo_crate_sticker_pack_${packSlug}_capsule`,
      `csgo_crate_sticker_pack_${packSlug}`,
    ];
    for (const key of candidates) {
      const label = this.getTranslation(`#${key}`);
      if (label && !label.startsWith('#') && label.toLowerCase() !== key) {
        return label;
      }
    }
    return '';
  }

  getStickerCollectionName(storageRow) {
    const stickerId = this.getStickerIdFromRow(storageRow);
    if (stickerId == null) return '';
    const kit = this.getStickerDetails(stickerId);
    if (!kit?.name) return '';
    const map = this.buildStickerKitCollectionMap();
    if (map[kit.name]) return map[kit.name];
    return this.getStickerCollectionFromMaterial(kit) || '';
  }

  getCollectionName(storageRow) {
    if (storageRow['paint_index'] !== undefined) {
      const paintKit = this.getPaintDetails(storageRow['paint_index']);
      if (paintKit?.name) {
        const map = this.buildPaintKitCollectionMap();
        const skinCollection = map[paintKit.name];
        if (skinCollection) return skinCollection;
      }
    }
    return this.getStickerCollectionName(storageRow);
  }

  checkIfAttributeIsThere(item, attribDefIndex) {
    let attrib = (item.attribute || []).find(
      (attrib) => attrib.def_index == attribDefIndex
    );
    return attrib ? true : false;
  }
}

function getSkinWearName(paintWear) {
  const skinWearValues = [0.07, 0.15, 0.38, 0.45, 1];
  const skinWearNames = [
    'Factory New',
    'Minimal Wear',
    'Field-Tested',
    'Well-Worn',
    'Battle-Scarred',
  ];

  for (const [key, value] of Object.entries(skinWearValues)) {
    if (paintWear > value) {
      continue;
    }
    return skinWearNames[key];
  }
}

function getAttributeValueBytes(item, attribDefIndex) {
  let attrib = (item.attribute || []).find(
    (attrib) => attrib.def_index == attribDefIndex
  );
  return attrib ? attrib.value_bytes : null;
}

function capitalizeWords(string) {
  return string.replace(/(?:^|\s)\S/g, function (a) {
    return a.toUpperCase();
  });
}
module.exports = items;
