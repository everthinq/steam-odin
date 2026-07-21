import React, { useState, useRef, useEffect, useMemo } from 'react';
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react';

const pad = (n) => String(n).padStart(2, '0');
const fmt = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parse = (s) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || '');
    if (!m) return null;
    const d = new Date(+m[1], +m[2] - 1, +m[3]);
    return isNaN(d.getTime()) ? null : d;
};
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const DOW = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];
const sameDay = (a, b) => a && b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

// Date text field (YYYY-MM-DD) with a compact, opaque, theme-matched calendar
// popover. Month + year dropdowns let you jump to any year directly.
export const DateField = ({ value, onChange, className = '', placeholder = 'YYYY-MM-DD' }) => {
    const [open, setOpen] = useState(false);
    const [view, setView] = useState(() => parse(value) || new Date());
    const wrapRef = useRef(null);
    const selected = parse(value);
    const today = new Date();
    const selectCls = 'bg-slate-800 border border-white/10 rounded-md px-1.5 py-1 text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-yellow-500/50 cursor-pointer';

    const years = useMemo(() => {
        const top = new Date().getFullYear() + 1;
        return Array.from({ length: top - 2012 + 1 }, (_, i) => top - i);
    }, []);

    useEffect(() => {
        if (!open) return;
        const onDoc = (e) => { if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false); };
        const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
        document.addEventListener('mousedown', onDoc);
        window.addEventListener('keydown', onKey);
        return () => { document.removeEventListener('mousedown', onDoc); window.removeEventListener('keydown', onKey); };
    }, [open]);

    const toggle = () => { setView(parse(value) || new Date()); setOpen(o => !o); };
    const pick = (d) => { onChange(fmt(d)); setOpen(false); };
    const shiftMonth = (n) => setView(v => new Date(v.getFullYear(), v.getMonth() + n, 1));

    const cells = useMemo(() => {
        const y = view.getFullYear(), m = view.getMonth();
        const lead = new Date(y, m, 1).getDay();
        const days = new Date(y, m + 1, 0).getDate();
        const out = [];
        for (let i = 0; i < lead; i++) out.push(null);
        for (let d = 1; d <= days; d++) out.push(new Date(y, m, d));
        while (out.length % 7 !== 0) out.push(null);
        return out;
    }, [view]);

    return (
        <div ref={wrapRef} className="relative">
            <input className={className} placeholder={placeholder} pattern="\d{4}-\d{2}-\d{2}"
                value={value} onChange={(e) => onChange(e.target.value)} />
            <button type="button" onClick={toggle} title="Pick a date"
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-yellow-300 transition-colors">
                <CalendarDays size={15} />
            </button>
            {open && (
                <div className="absolute z-40 mt-1 right-0 w-[264px] bg-slate-900 border border-white/10 rounded-xl shadow-2xl p-3">
                    <div className="flex items-center gap-1.5 mb-2">
                        <button type="button" onClick={() => shiftMonth(-1)}
                            className="p-1 rounded-md text-slate-400 hover:text-white hover:bg-white/10 transition-colors shrink-0"><ChevronLeft size={16} /></button>
                        <select value={view.getMonth()} onChange={e => setView(v => new Date(v.getFullYear(), +e.target.value, 1))} className={`${selectCls} flex-1`}>
                            {MONTHS.map((m, i) => <option key={m} value={i}>{m}</option>)}
                        </select>
                        <select value={view.getFullYear()} onChange={e => setView(v => new Date(+e.target.value, v.getMonth(), 1))} className={selectCls}>
                            {years.map(y => <option key={y} value={y}>{y}</option>)}
                        </select>
                        <button type="button" onClick={() => shiftMonth(1)}
                            className="p-1 rounded-md text-slate-400 hover:text-white hover:bg-white/10 transition-colors shrink-0"><ChevronRight size={16} /></button>
                    </div>
                    <div className="grid grid-cols-7 mb-1">
                        {DOW.map(d => <div key={d} className="h-6 flex items-center justify-center text-[10px] font-semibold text-slate-500">{d}</div>)}
                    </div>
                    <div className="grid grid-cols-7 gap-1">
                        {cells.map((d, i) => d ? (
                            <button key={i} type="button" onClick={() => pick(d)}
                                className={`h-8 w-8 flex items-center justify-center rounded-lg text-[13px] tabular-nums transition-colors ${
                                    sameDay(d, selected) ? 'bg-yellow-600 text-white font-semibold'
                                        : sameDay(d, today) ? 'text-yellow-300 ring-1 ring-inset ring-yellow-500/50 hover:bg-white/10'
                                            : 'text-slate-200 hover:bg-white/10'}`}>
                                {d.getDate()}
                            </button>
                        ) : <div key={i} className="h-8 w-8" />)}
                    </div>
                    <div className="flex justify-between mt-2 pt-2 border-t border-white/10">
                        <button type="button" onClick={() => pick(new Date())} className="text-xs font-medium text-yellow-300/90 hover:text-yellow-200 transition-colors">Today</button>
                        <button type="button" onClick={() => setOpen(false)} className="text-xs text-slate-400 hover:text-white transition-colors">Close</button>
                    </div>
                </div>
            )}
        </div>
    );
};
