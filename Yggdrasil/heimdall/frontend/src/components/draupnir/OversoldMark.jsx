// Shown next to a holding's quantity when the backend flags it oversold:
// more units sold than bought, usually a missing buy or a wrong quantity.
export default function OversoldMark({ holding }) {
    if (!holding.oversold) return null;
    return (
        <span
            className="ml-1.5 rounded bg-red-500/15 px-1 text-[10px] font-semibold text-red-300"
            title={`${holding.oversold_qty} more sold than bought: a buy is missing or a quantity is wrong`}>
            oversold {holding.oversold_qty}
        </span>
    );
}
