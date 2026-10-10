import { Info } from 'lucide-react';
import InfoTip from '../gjallarhorn/InfoTip';

// Marks a market the pulse website does not list (GgSwap, GamerPay, SkinSwap Trade):
// its prices still arrive, but can be old. Hover the icon for the note.
const NOTE = 'Not on the pulse website. Its prices still come in, but they can be old. Check the market before you trade.';

const NotInPulseNote = ({ size = 12 }) => (
    <InfoTip tip={NOTE} className="shrink-0 text-sky-400/70 hover:text-sky-300 cursor-help">
        <Info size={size} aria-label={NOTE} />
    </InfoTip>
);

export default NotInPulseNote;
