import React, { useState, useEffect, useRef } from 'react';
import { AlertTriangle, X } from 'lucide-react';

const Backdrop = ({ children, onCancel }) => (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onCancel} />
        <div className="relative w-full max-w-sm bg-slate-900 border border-white/10 rounded-2xl shadow-2xl p-5">
            {children}
        </div>
    </div>
);

// Styled confirmation dialog — replaces window.confirm(). Esc cancels, Enter confirms.
export const ConfirmDialog = ({ open, title, message, confirmLabel = 'Confirm', cancelLabel = 'Cancel', danger = false, onConfirm, onCancel }) => {
    const confirmRef = useRef(null);
    useEffect(() => {
        if (!open) return;
        // Move focus onto the confirm button so the element that opened the dialog
        // (e.g. the row's trash button) can't be re-triggered by the same Enter —
        // which was deleting the item AND reopening the dialog. Enter now activates
        // this button natively (one confirm); we only handle Escape here.
        const t = setTimeout(() => confirmRef.current?.focus(), 30);
        const onKey = (e) => { if (e.key === 'Escape') onCancel?.(); };
        window.addEventListener('keydown', onKey);
        return () => { clearTimeout(t); window.removeEventListener('keydown', onKey); };
    }, [open, onCancel]);
    if (!open) return null;
    return (
        <Backdrop onCancel={onCancel}>
            <div className="flex items-start gap-3">
                {danger && <div className="p-2 rounded-lg bg-red-500/15 text-red-400 shrink-0"><AlertTriangle size={18} /></div>}
                <div className="flex-1">
                    <h3 className="font-serif font-semibold text-white">{title}</h3>
                    {message && <p className="text-sm text-slate-400 mt-1 leading-relaxed">{message}</p>}
                </div>
            </div>
            <div className="flex justify-end gap-2 mt-5">
                <button onClick={onCancel} className="px-3.5 py-1.5 rounded-lg text-sm text-slate-300 hover:bg-white/10 transition-colors">{cancelLabel}</button>
                <button ref={confirmRef} onClick={onConfirm} className={`px-3.5 py-1.5 rounded-lg text-sm font-medium text-white transition-colors ${danger ? 'bg-red-600 hover:bg-red-500' : 'bg-yellow-600 hover:bg-yellow-500'}`}>{confirmLabel}</button>
            </div>
        </Backdrop>
    );
};

// Styled text-input dialog — replaces window.prompt(). Enter submits, Esc cancels.
export const PromptDialog = ({ open, title, label, initial = '', confirmLabel = 'Save', placeholder = '', onConfirm, onCancel }) => {
    const [val, setVal] = useState(initial);
    const inputRef = useRef(null);
    useEffect(() => { if (open) { setVal(initial); setTimeout(() => inputRef.current?.focus(), 30); } }, [open, initial]);
    if (!open) return null;
    const submit = () => { if (val.trim()) onConfirm?.(val.trim()); };
    return (
        <Backdrop onCancel={onCancel}>
            <div className="flex items-center justify-between mb-3">
                <h3 className="font-serif font-semibold text-white">{title}</h3>
                <button onClick={onCancel} className="p-1 rounded text-slate-500 hover:text-white hover:bg-white/10 transition-colors"><X size={16} /></button>
            </div>
            {label && <label className="block text-[10px] font-bold tracking-widest text-slate-500 uppercase mb-1.5">{label}</label>}
            <input
                ref={inputRef} value={val} placeholder={placeholder}
                onChange={(e) => setVal(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') submit(); if (e.key === 'Escape') onCancel?.(); }}
                className="w-full bg-odin-dark/60 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-yellow-500/50 placeholder:text-slate-600"
            />
            <div className="flex justify-end gap-2 mt-5">
                <button onClick={onCancel} className="px-3.5 py-1.5 rounded-lg text-sm text-slate-300 hover:bg-white/10 transition-colors">Cancel</button>
                <button onClick={submit} className="px-3.5 py-1.5 rounded-lg text-sm font-medium text-white bg-yellow-600 hover:bg-yellow-500 transition-colors">{confirmLabel}</button>
            </div>
        </Backdrop>
    );
};
