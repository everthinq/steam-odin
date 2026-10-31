import { useState } from 'react';

// One setting for the Inventory and Transfer pages: hidden by default, remembered per browser.
const SHOW_UNSTORABLE_KEY = 'ratatoskr.showUnstorableItems';

const readShowUnstorable = () => {
    try { return localStorage.getItem(SHOW_UNSTORABLE_KEY) === '1'; } catch { return false; }
};

/** `[showUnstorable, setShowUnstorable]`, remembered in this browser. */
export const useShowUnstorable = () => {
    const [show, setShow] = useState(readShowUnstorable);
    const update = (next) => {
        setShow(next);
        try { localStorage.setItem(SHOW_UNSTORABLE_KEY, next ? '1' : '0'); } catch { /* private window: not remembered */ }
    };
    return [show, update];
};

export const UNSTORABLE_EXPLANATION =
    'Untradable items — service medals, veteran and operation coins, badges, the default music kit — cannot go into a Storage Unit.';
