import React, { useState } from 'react';
import { X } from 'lucide-react';
import { readJson } from '../../utils/readJson';

export const CASKET_NAME_MAX_LENGTH = 20;

/**
 * Renames one Storage Unit (free in the game, up to 20 characters). `onRenamed(updatedCasket, previousItemId)` after
 * the backend confirmed the new name; `onClose` on Cancel, Escape or a click outside. Mount it with
 * `key={casket.item_id}` so the draft starts from that unit's name.
 */
const RenameCasketDialog = ({ steamid, casket, onClose, onRenamed }) => {
    const [draft, setDraft] = useState(casket.item_customname || '');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);

    const close = () => {
        if (!saving) onClose();
    };

    const submit = async () => {
        if (saving) return;
        const trimmed = draft.trim();
        if (trimmed.length > CASKET_NAME_MAX_LENGTH) {
            setError(`Name must be ${CASKET_NAME_MAX_LENGTH} characters or less`);
            return;
        }

        setSaving(true);
        setError(null);
        try {
            const res = await fetch('/api/ratatoskr/casket/rename', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ steamID: steamid, casketID: casket.item_id, name: trimmed }),
            });
            const data = await readJson(res);
            if (!res.ok || data.error) {
                throw new Error(data.error || 'Failed to rename storage unit');
            }
            setSaving(false);
            // The Game Coordinator may give the renamed unit a new item id: pass the old one along.
            onRenamed(
                data.casket || { ...casket, item_id: data.item_id || casket.item_id, item_customname: data.custom_name || trimmed || undefined },
                data.previous_item_id || casket.item_id
            );
        } catch (err) {
            console.error(err);
            setError(err.message || 'Rename failed');
            setSaving(false);
        }
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <button
                type="button"
                className="absolute inset-0 bg-black/70 backdrop-blur-sm"
                onClick={close}
                aria-label="Close rename dialog"
            />
            <div
                className="relative w-full max-w-sm bg-[#1a1d24] border border-white/10 rounded-xl shadow-2xl p-5"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-center justify-between mb-4">
                    <h3 className="text-sm font-medium text-white">Rename storage unit</h3>
                    <button
                        type="button"
                        onClick={close}
                        disabled={saving}
                        className="p-1 text-slate-500 hover:text-white disabled:opacity-40"
                        aria-label="Close"
                    >
                        <X size={16} />
                    </button>
                </div>
                <p className="text-xs text-slate-500 mb-3">
                    Custom names are free for storage units (max {CASKET_NAME_MAX_LENGTH} characters).
                </p>
                <input
                    type="text"
                    value={draft}
                    maxLength={CASKET_NAME_MAX_LENGTH}
                    disabled={saving}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                        if (e.key === 'Enter') submit();
                        if (e.key === 'Escape') close();
                    }}
                    placeholder="Storage unit name"
                    className="w-full bg-black/40 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500/40 mb-2"
                    autoFocus
                />
                <p className="text-[10px] text-slate-600 mb-3 text-right tabular-nums">
                    {draft.length}/{CASKET_NAME_MAX_LENGTH}
                </p>
                {error && <p className="text-xs text-red-400 mb-3">{error}</p>}
                <div className="flex gap-2 justify-end">
                    <button
                        type="button"
                        onClick={close}
                        disabled={saving}
                        className="px-3 py-2 text-xs text-slate-400 hover:text-white disabled:opacity-40"
                    >
                        Cancel
                    </button>
                    <button
                        type="button"
                        onClick={submit}
                        disabled={saving}
                        className="px-4 py-2 text-xs font-medium rounded-lg bg-amber-600 hover:bg-amber-500 text-white disabled:opacity-40"
                    >
                        {saving ? 'Saving…' : 'Save'}
                    </button>
                </div>
            </div>
        </div>
    );
};

export default RenameCasketDialog;
