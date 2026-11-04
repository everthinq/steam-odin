import React from 'react';
import { RefreshCw } from 'lucide-react';

// The live-price status line. The backend writes the words (pricing_note), says
// whether it is a warning, and whether prices are still loading
// (poll_after_seconds); this only shows them.
const PricingNote = ({ status, className = '' }) => {
    if (!status?.pricing_note) return null;
    if (status.pricing_warning) {
        return <span className={`text-xs text-amber-400/80 ${className}`}>{status.pricing_note}</span>;
    }
    return (
        <span className={`flex items-center gap-1.5 text-xs text-slate-400 ${className}`}>
            {status.poll_after_seconds != null && <RefreshCw size={12} className="animate-spin" />}
            {status.pricing_note}
        </span>
    );
};

export default PricingNote;
