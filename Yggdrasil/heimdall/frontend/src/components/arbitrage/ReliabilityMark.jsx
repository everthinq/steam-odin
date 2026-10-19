import { AlertTriangle, ShieldCheck } from 'lucide-react';
import InfoTip from '../gjallarhorn/InfoTip';

// Our own check of a market's pulse prices on one side (the registry's `reliability`:
// listings or buy orders): red triangle = unreliable, green shield = believable. A mark
// only; nothing is left out because of it. Hover the icon for why.
const SIDE = { listings: 'lowest listings', buyOrders: 'buy orders' };

const ReliabilityMark = ({ mark, side, size = 12 }) => {
    if (!mark) return null;
    const unreliable = mark.status === 'unreliable';
    const tip = `${unreliable ? 'Unreliable' : 'Believable'} ${SIDE[side] || 'prices'}. ${mark.note}`;
    const Icon = unreliable ? AlertTriangle : ShieldCheck;
    return (
        <InfoTip tip={tip} className={`shrink-0 cursor-help ${unreliable ? 'text-red-400/80 hover:text-red-300' : 'text-emerald-400/70 hover:text-emerald-300'}`}>
            <Icon size={size} aria-label={tip} />
        </InfoTip>
    );
};

export default ReliabilityMark;
