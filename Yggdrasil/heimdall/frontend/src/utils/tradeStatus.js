// What the three trade-status colours mean, said once for every place that shows them
// (Transfer lines, stack rows, the transfer queue). `left` is the time label, for example "4d".

export const TRADE_STATUS_HINT = {
    tradable: 'Tradable now: trade, sell or store it.',
    tradeHold: (left) => `Trade hold, usually a Steam Market purchase. You can store it now; trade or sell it in ${left}.`,
    tradeProtected: (left) => `Received in a trade, which can still be reversed. It can't be traded, sold or stored for ${left}.`,
};
