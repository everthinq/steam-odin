import { useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, Loader2 } from 'lucide-react';

// A destructive-action dialog that only enables its confirm button once the
// exact phrase has been typed, so a stray click (or Enter) cannot delete
// accounts. Render it only while open so the typed text starts empty each time.
// It is portalled to <body> so a transformed or draggable ancestor (the
// dashboard grid) cannot clip it or capture its drag events.
const TypedConfirmDialog = ({
    title,
    message,
    phrase,
    confirmLabel = 'Delete',
    busy = false,
    error = null,
    onConfirm,
    onCancel,
}) => {
    const [typed, setTyped] = useState('');
    const matches = typed.trim() === phrase;

    const handleSubmit = (event) => {
        event.preventDefault();
        if (!matches || busy) return;
        onConfirm();
    };

    return createPortal(
        <div
            draggable={false}
            onDragStart={(event) => { event.preventDefault(); event.stopPropagation(); }}
            className="fixed inset-0 bg-odin-dark/90 flex items-center justify-center z-[60] backdrop-blur-sm p-4"
            role="dialog"
            aria-modal="true"
            onKeyDown={(event) => { if (event.key === 'Escape' && !busy) onCancel(); }}
        >
            <form
                onSubmit={handleSubmit}
                className="bg-odin-blue/95 border border-red-500/40 rounded-2xl p-5 md:p-6 w-full max-w-md shadow-2xl"
            >
                <div className="flex items-center gap-3 mb-3 text-red-300">
                    <AlertTriangle size={22} className="shrink-0" />
                    <h3 className="text-lg font-bold">{title}</h3>
                </div>
                <p className="text-sm text-slate-300 mb-4">{message}</p>
                <label className="block text-xs text-slate-400 mb-1.5">
                    Type <code className="px-1.5 py-0.5 rounded bg-black/40 text-red-200 font-mono select-all">{phrase}</code> to confirm
                </label>
                <input
                    autoFocus
                    type="text"
                    value={typed}
                    onChange={(event) => setTyped(event.target.value)}
                    disabled={busy}
                    spellCheck={false}
                    autoComplete="off"
                    className="w-full bg-odin-dark/60 border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-red-400 focus:outline-none"
                />
                {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
                <div className="flex gap-3 justify-end mt-5">
                    <button
                        type="button"
                        onClick={onCancel}
                        disabled={busy}
                        className="px-4 py-2 bg-odin-blue hover:bg-odin-blue/80 rounded-lg text-sm text-frost-white border border-white/10 disabled:opacity-50"
                    >
                        Cancel
                    </button>
                    <button
                        type="submit"
                        disabled={!matches || busy}
                        className="px-4 py-2 bg-[#4a040b] hover:bg-[#630611] disabled:bg-odin-dark disabled:text-white/30 text-red-100 border border-[#2b0206] rounded-lg text-sm font-bold flex items-center gap-2 transition-colors"
                    >
                        {busy && <Loader2 size={16} className="animate-spin" />}
                        {confirmLabel}
                    </button>
                </div>
            </form>
        </div>,
        document.body
    );
};

export default TypedConfirmDialog;
