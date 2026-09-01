import { ChevronDown } from 'lucide-react';

// Collapsible titled section with optional icon/summary/right-slot. From Transfer.jsx.
const CollapsibleSection = ({ title, icon: Icon, open, onToggle, summary, headerRight, children }) => (
    <div className="mb-3 rounded-xl border border-white/10 bg-black/20 overflow-hidden">
        <button
            type="button"
            onClick={onToggle}
            className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-white/[0.03] transition-colors"
            aria-expanded={open}
        >
            <ChevronDown
                size={16}
                className={`shrink-0 text-slate-500 transition-transform ${open ? '' : '-rotate-90'}`}
            />
            {Icon && <Icon size={14} className="text-amber-400/80 shrink-0" />}
            <span className="text-xs font-bold tracking-widest text-slate-500 shrink-0">{title}</span>
            {!open && summary && (
                <span className="text-xs text-slate-400 truncate min-w-0">{summary}</span>
            )}
            {headerRight && <div className="ml-auto flex items-center gap-2 shrink-0">{headerRight}</div>}
        </button>
        {open && <div className="px-3 pb-3 border-t border-white/5">{children}</div>}
    </div>
);

export default CollapsibleSection;
