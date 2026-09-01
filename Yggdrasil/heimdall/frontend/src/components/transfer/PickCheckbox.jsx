import { Check, Minus } from 'lucide-react';

// Tri-state pick checkbox (checked / partial / empty). Extracted from Transfer.jsx.
const PickCheckbox = ({ checked, partial = false, onChange, label }) => (
    <button
        type="button"
        role="checkbox"
        aria-checked={checked || partial}
        aria-label={label}
        onClick={(e) => {
            e.stopPropagation();
            onChange();
        }}
        className={`p-1 rounded border transition-colors shrink-0 ${checked
            ? 'bg-emerald-600/30 border-emerald-500/50 text-emerald-400'
            : partial
              ? 'bg-amber-600/20 border-amber-500/40 text-amber-400'
              : 'border-white/10 text-slate-500 hover:border-white/20'
            }`}
    >
        {checked ? (
            <Check size={14} />
        ) : partial ? (
            <Minus size={14} />
        ) : (
            <span className="block w-3.5 h-3.5" />
        )}
    </button>
);

export default PickCheckbox;
