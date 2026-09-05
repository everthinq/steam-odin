import { useState, useRef } from 'react';

// Lightweight hover tooltip with a short, human delay. The native `title`
// attribute waits ~1s before showing (sticky and annoying); this shows after
// ~250ms and hides at once. Rendered position:fixed so the rotation table's
// scroll container can't clip it.
const InfoTip = ({ tip, children, delay = 250, className = '' }) => {
    const [pos, setPos] = useState(null);
    const timer = useRef(null);

    if (!tip) return children;

    const show = (e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        const anchor = { x: rect.left + rect.width / 2, y: rect.bottom };
        clearTimeout(timer.current);
        timer.current = setTimeout(() => setPos(anchor), delay);
    };
    const hide = () => { clearTimeout(timer.current); setPos(null); };

    return (
        <span
            className={`relative inline-flex ${className}`}
            onMouseEnter={show}
            onMouseLeave={hide}
            onMouseDown={hide}
        >
            {children}
            {pos && (
                <span
                    style={{ position: 'fixed', left: pos.x, top: pos.y + 6, transform: 'translateX(-50%)' }}
                    className="z-[100] w-60 rounded-lg border border-white/15 bg-[#0b1119] px-2.5 py-1.5 text-[11px] font-normal normal-case leading-snug text-left text-slate-300 shadow-xl shadow-black/60 pointer-events-none whitespace-normal"
                >
                    {tip}
                </span>
            )}
        </span>
    );
};

export default InfoTip;
