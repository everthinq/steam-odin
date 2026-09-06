import { useState, useEffect, useCallback, useMemo } from 'react';
import { RefreshCw, Filter, Boxes, Settings2, Plus, X, ArrowRight, Link2 } from 'lucide-react';
import InfoTip from './gjallarhorn/InfoTip';

// Cross-profile arbitrage tab (lives inside the Huginn Arbitrage page, right after
// Case Arbitrage). For every item held across ALL Draupnir accounts:
//   * the best BUY-min -> AUTOBUY-sell route right now, net of fees, and
//   * any user-defined CHAIN (e.g. LisSkins -> CSMoney -> CSFloat), where each
//     adjacent hop is its own buy(min)->autobuy leg, shown with a chain total.
// The buy sources, sell (autobuy) targets and chains are all editable here and
// persisted server-side. Backend: GET /api/huginn/arbitrage/cross-profile
// (non-blocking; serves cache + warms in the background, so we poll while warming)
// and GET/POST .../cross-profile/config for the market + chain config.

const money = (v) => `$${Number(v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const pct = (v) => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`);

const Stat = ({ label, value, tone }) => (
    <div className="px-4 py-2.5 rounded-xl bg-odin-blue/40 border border-white/10">
        <p className="text-[10px] uppercase tracking-wider text-slate-500">{label}</p>
        <p className={`text-lg font-bold tabular-nums ${tone || 'text-slate-200'}`}>{value}</p>
    </div>
);

const Holders = ({ holders }) => {
    if (!holders || !holders.length) return <span className="text-slate-600">—</span>;
    const head = holders.slice(0, 2);
    const extra = holders.length - head.length;
    return (
        <span className="text-xs text-slate-300" title={holders.map((h) => `${h.account} ×${h.qty}`).join(', ')}>
            {head.map((h) => `${h.account} ×${h.qty}`).join(', ')}
            {extra > 0 && <span className="text-slate-500"> +{extra}</span>}
        </span>
    );
};

// A single toggle chip for a market in the buy/sell picker.
const MarketChip = ({ market, active, onClick, disabled }) => (
    <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        title={disabled ? 'No instant-sell (autobuy) on this market' : undefined}
        className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium border transition ${
            active
                ? 'bg-amber-500/15 border-amber-500/40 text-amber-200'
                : disabled
                    ? 'bg-black/20 border-white/5 text-slate-600 cursor-not-allowed'
                    : 'bg-odin-blue/60 border-white/10 text-slate-400 hover:text-slate-200 hover:border-white/20'
        }`}
    >
        {market.display}
    </button>
);

const CrossProfileArbitrage = () => {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [ownedOnly, setOwnedOnly] = useState(true);
    const [minPct, setMinPct] = useState('');

    // Market + chain config (server-persisted). `available` is the full market list.
    const [config, setConfig] = useState(null);
    const [configOpen, setConfigOpen] = useState(false);
    const [buyDraft, setBuyDraft] = useState([]);
    const [sellDraft, setSellDraft] = useState([]);
    const [chainsDraft, setChainsDraft] = useState([]);
    const [newChain, setNewChain] = useState([]);   // markets of the chain being built
    const [savingConfig, setSavingConfig] = useState(false);

    const available = useMemo(() => config?.available || [], [config]);
    const dispOf = useCallback((id) => available.find((m) => m.id === id)?.display || id, [available]);

    const initDrafts = (c) => {
        setBuyDraft(c.buy_markets || []);
        setSellDraft(c.sell_markets || []);
        setChainsDraft(c.chains || []);
        setNewChain([]);
    };

    useEffect(() => {
        fetch('/api/huginn/arbitrage/cross-profile/config')
            .then((r) => (r.ok ? r.json() : null))
            .then((c) => { if (c) { setConfig(c); initDrafts(c); } })
            .catch(() => {});
    }, []);

    const fetchData = useCallback(() => {
        const params = new URLSearchParams({ owned: ownedOnly ? '1' : '0' });
        if (minPct !== '') params.set('min_pct', minPct);
        return fetch(`/api/huginn/arbitrage/cross-profile?${params.toString()}`)
            .then((r) => (r.ok ? r.json() : null))
            .then((d) => { setData(d); setLoading(false); })
            .catch(() => { setData(null); setLoading(false); });
    }, [ownedOnly, minPct]);

    const refresh = () => { setLoading(true); fetchData(); };

    useEffect(() => { fetchData(); }, [fetchData]);

    // Poll while the backend warms its price caches.
    useEffect(() => {
        if (data && (data.status === 'warming' || data.status === 'refreshing')) {
            const id = setTimeout(fetchData, 5000);
            return () => clearTimeout(id);
        }
    }, [data, fetchData]);

    const rows = data?.rows || [];
    const chains = data?.chains || [];
    const summary = data?.summary;
    const status = data?.status;
    const warming = status === 'warming' || status === 'refreshing';
    const csfloat = data?.csfloat;

    const dirty = config && (
        JSON.stringify(buyDraft) !== JSON.stringify(config.buy_markets) ||
        JSON.stringify(sellDraft) !== JSON.stringify(config.sell_markets) ||
        JSON.stringify(chainsDraft) !== JSON.stringify(config.chains)
    );

    const toggleBuy = (id) => setBuyDraft((l) => (l.includes(id) ? l.filter((x) => x !== id) : [...l, id]));
    const toggleSell = (id) => setSellDraft((l) => (l.includes(id) ? l.filter((x) => x !== id) : [...l, id]));
    const removeChain = (cid) => setChainsDraft((l) => l.filter((c) => c.id !== cid));
    const addHop = (id) => { if (id) setNewChain((c) => [...c, id]); };
    const undoHop = () => setNewChain((c) => c.slice(0, -1));
    const addChain = () => {
        if (newChain.length < 2) return;
        const name = newChain.map(dispOf).join(' → ');
        setChainsDraft((l) => [...l, { id: `draft-${Date.now()}`, name, markets: newChain }]);
        setNewChain([]);
    };

    const saveConfig = () => {
        setSavingConfig(true);
        fetch('/api/huginn/arbitrage/cross-profile/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ buy_markets: buyDraft, sell_markets: sellDraft, chains: chainsDraft }),
        })
            .then((r) => (r.ok ? r.json() : null))
            .then((c) => { if (c) { setConfig(c); initDrafts(c); } setSavingConfig(false); refresh(); })
            .catch(() => setSavingConfig(false));
    };

    return (
        <>
            {/* Controls */}
            <div className="shrink-0 flex items-center gap-2 flex-wrap">
                <InfoTip tip="For everything you hold across ALL accounts, the best route right now: buy at the cheapest market's listing, sell instantly into another market's buy order (autobuy), net of fees. Ranked by profit %. Chains add multi-hop routes (e.g. LisSkins → CSMoney → CSFloat) where each hop is its own buy→autobuy leg. Prices move fast — always verify on the market before acting." />
                <button
                    type="button"
                    onClick={() => setOwnedOnly((v) => !v)}
                    className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition ${
                        ownedOnly
                            ? 'bg-amber-500/15 border-amber-500/40 text-amber-200'
                            : 'bg-odin-blue/60 border-white/10 text-slate-400 hover:text-slate-200'
                    }`}
                    title={ownedOnly ? 'Showing only items you hold. Click to include the whole market.' : 'Showing the whole priced market (capped). Click to show only what you own.'}
                >
                    <Boxes size={13} /> {ownedOnly ? 'My holdings' : 'All items'}
                </button>
                <div className="inline-flex items-center gap-1 text-xs text-slate-500">
                    <Filter size={12} />
                    <span>min %</span>
                    <input
                        type="number"
                        value={minPct}
                        onChange={(e) => setMinPct(e.target.value)}
                        placeholder="any"
                        className="w-16 bg-black/30 border border-white/10 rounded px-1.5 py-1 text-slate-200 outline-none focus:border-amber-500/40"
                    />
                </div>
                <button
                    type="button"
                    onClick={() => setConfigOpen((v) => !v)}
                    className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition ${
                        configOpen || dirty
                            ? 'bg-sky-500/15 border-sky-500/40 text-sky-200'
                            : 'bg-odin-blue/60 border-white/10 text-slate-400 hover:text-slate-200'
                    }`}
                    title="Choose which markets to buy from / sell into, and build multi-hop chains"
                >
                    <Settings2 size={13} /> Markets &amp; chains{dirty ? ' •' : ''}
                </button>
                <button
                    type="button"
                    onClick={refresh}
                    disabled={loading || warming}
                    className="ml-auto flex items-center gap-2 px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-sm font-medium disabled:opacity-50 transition-colors"
                >
                    <RefreshCw size={14} className={(loading || warming) ? 'animate-spin' : ''} /> Refresh
                </button>
            </div>

            {/* Config panel: buy sources, sell targets, chains */}
            {configOpen && config && (
                <div className="shrink-0 rounded-xl border border-sky-500/20 bg-sky-500/[0.03] p-4 space-y-4">
                    <div>
                        <p className="text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-2">Buy sources <span className="text-slate-600 font-normal normal-case">— priced at each market's cheapest listing</span></p>
                        <div className="flex flex-wrap gap-1.5">
                            {available.map((m) => (
                                <MarketChip key={m.id} market={m} active={buyDraft.includes(m.id)} onClick={() => toggleBuy(m.id)} />
                            ))}
                        </div>
                    </div>
                    <div>
                        <p className="text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-2">Sell targets <span className="text-slate-600 font-normal normal-case">— instant-sell (autobuy) markets only</span></p>
                        <div className="flex flex-wrap gap-1.5">
                            {available.map((m) => (
                                <MarketChip key={m.id} market={m} active={sellDraft.includes(m.id)} disabled={!m.hasAutobuy} onClick={() => m.hasAutobuy && toggleSell(m.id)} />
                            ))}
                        </div>
                    </div>
                    <div>
                        <p className="text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-2">Chains <span className="text-slate-600 font-normal normal-case">— each hop is a buy→autobuy leg; totals concatenate</span></p>
                        <div className="space-y-1.5">
                            {chainsDraft.map((c) => (
                                <div key={c.id} className="flex items-center gap-2 flex-wrap bg-black/20 border border-white/10 rounded-lg px-2.5 py-1.5">
                                    <Link2 size={13} className="text-sky-300 shrink-0" />
                                    <span className="flex items-center gap-1 flex-wrap text-xs text-slate-200">
                                        {c.markets.map((m, i) => (
                                            <span key={i} className="inline-flex items-center gap-1">
                                                {i > 0 && <ArrowRight size={11} className="text-slate-500" />}
                                                <span className="px-1.5 py-0.5 rounded bg-white/5 border border-white/10">{dispOf(m)}</span>
                                            </span>
                                        ))}
                                    </span>
                                    <button type="button" onClick={() => removeChain(c.id)} className="ml-auto text-slate-500 hover:text-red-400 transition-colors" title="Remove chain">
                                        <X size={14} />
                                    </button>
                                </div>
                            ))}
                            {!chainsDraft.length && <p className="text-xs text-slate-600">No chains yet — build one below.</p>}
                        </div>
                        {/* New-chain builder */}
                        <div className="mt-2 flex items-center gap-2 flex-wrap bg-black/10 border border-dashed border-white/10 rounded-lg px-2.5 py-2">
                            {newChain.map((m, i) => (
                                <span key={i} className="inline-flex items-center gap-1 text-xs text-slate-200">
                                    {i > 0 && <ArrowRight size={11} className="text-slate-500" />}
                                    <span className="px-1.5 py-0.5 rounded bg-white/5 border border-white/10">{dispOf(m)}</span>
                                </span>
                            ))}
                            <select
                                value=""
                                onChange={(e) => { addHop(e.target.value); e.target.value = ''; }}
                                className="bg-black/30 border border-white/10 rounded px-2 py-1 text-xs text-slate-200 outline-none focus:border-sky-500/40"
                            >
                                <option value="">{newChain.length ? '+ add market' : 'pick first market'}</option>
                                {available.map((m) => <option key={m.id} value={m.id}>{m.display}</option>)}
                            </select>
                            {newChain.length > 0 && (
                                <button type="button" onClick={undoHop} className="text-slate-500 hover:text-slate-300 text-xs" title="Remove last hop">
                                    undo
                                </button>
                            )}
                            <button
                                type="button"
                                onClick={addChain}
                                disabled={newChain.length < 2}
                                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-sky-600/70 hover:bg-sky-500 text-white text-xs font-medium disabled:opacity-40 transition-colors"
                            >
                                <Plus size={12} /> Add chain
                            </button>
                            <span className="text-[11px] text-slate-600">2+ markets; hop N buys, hop N+1 instant-sells</span>
                        </div>
                    </div>
                    <div className="flex items-center gap-2 pt-1 border-t border-white/5">
                        <button
                            type="button"
                            onClick={saveConfig}
                            disabled={!dirty || savingConfig}
                            className="flex items-center gap-2 px-4 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium disabled:opacity-40 transition-colors"
                        >
                            {savingConfig ? 'Saving…' : 'Save & rescan'}
                        </button>
                        <button
                            type="button"
                            onClick={() => initDrafts(config)}
                            disabled={!dirty || savingConfig}
                            className="px-3 py-1.5 rounded-lg bg-white/5 border border-white/10 text-slate-300 hover:text-white text-sm disabled:opacity-40 transition-colors"
                        >
                            Reset
                        </button>
                        {dirty && <span className="text-xs text-amber-400/80">Unsaved changes — Save to apply and rescan.</span>}
                    </div>
                </div>
            )}

            {/* Summary */}
            <div className="shrink-0 flex flex-wrap items-stretch gap-2">
                <Stat label="Rows" value={summary ? summary.rows.toLocaleString() : '—'} />
                <Stat label="Profitable" value={summary ? summary.profitable.toLocaleString() : '—'} tone="text-emerald-400" />
                <Stat label="Best profit" value={summary && summary.best_profit_pct != null ? pct(summary.best_profit_pct) : '—'} tone="text-emerald-300" />
                <Stat label="Total potential" value={summary ? money(summary.total_potential_profit) : '—'} tone="text-emerald-400" />
                <Stat label="Items held" value={summary ? summary.owned_items_total.toLocaleString() : '—'} />
            </div>

            {/* Status / coverage */}
            <div className="shrink-0 flex flex-wrap items-center gap-3 text-xs">
                {warming && (
                    <span className="inline-flex items-center gap-1.5 text-amber-300/90">
                        <RefreshCw size={12} className="animate-spin" />
                        Warming prices across markets — this takes up to a minute the first time…
                    </span>
                )}
                {status === 'no_token' && (
                    <span className="text-amber-400/80">No tradeon_token set — add it in Settings to price markets.</span>
                )}
                {csfloat && !csfloat.have && (
                    <span className="text-slate-500">CSFloat autobuy not loaded — run the CSFloat buy-orders sweep on the Arbitrage tab to include it as a sell target.</span>
                )}
                {data?.markets?.sell?.length > 0 && (
                    <span className="text-slate-600 ml-auto">
                        Sell venues: {data.markets.sell.filter((m) => m.count > 0).map((m) => m.display).join(' · ') || '—'}
                    </span>
                )}
            </div>

            {/* Best-single-route table + chain sections all scroll together */}
            <div className="flex-1 overflow-auto rounded-xl border border-white/10">
                {/* Best single route */}
                <table className="w-full text-sm">
                    <thead className="sticky top-0 z-10 bg-odin-blue/80 backdrop-blur text-[11px] uppercase tracking-wider text-slate-400">
                        <tr>
                            <th className="text-left font-medium px-3 py-2" colSpan={8}>
                                Best single route <span className="text-slate-600 normal-case tracking-normal">— cheapest buy → best instant-sell</span>
                            </th>
                        </tr>
                        <tr>
                            <th className="text-left font-medium px-3 py-2">Item</th>
                            <th className="text-right font-medium px-3 py-2">Owned</th>
                            <th className="text-left font-medium px-3 py-2">Held by</th>
                            <th className="text-left font-medium px-3 py-2">Buy (min)</th>
                            <th className="text-left font-medium px-3 py-2">Sell (autobuy)</th>
                            <th className="text-right font-medium px-3 py-2">Profit</th>
                            <th className="text-right font-medium px-3 py-2">Profit %</th>
                            <th className="text-right font-medium px-3 py-2">Potential</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-white/5">
                        {rows.map((r, i) => (
                            <tr key={`${r.item_name}-${i}`} className="hover:bg-white/5">
                                <td className="px-3 py-2 text-slate-200">{r.item_name}</td>
                                <td className="px-3 py-2 text-right tabular-nums text-slate-300">{r.owned_qty || '—'}</td>
                                <td className="px-3 py-2"><Holders holders={r.holders} /></td>
                                <td className="px-3 py-2 text-slate-300">
                                    <span className="text-slate-400">{r.buy_market_display}</span> {money(r.buy_price)}
                                </td>
                                <td className="px-3 py-2 text-slate-300">
                                    <span className="text-slate-400">{r.sell_market_display}</span> {money(r.sell_net)}
                                    {r.sell_fee > 0 && <span className="text-slate-600"> (−{(r.sell_fee * 100).toFixed(1)}%)</span>}
                                </td>
                                <td className={`px-3 py-2 text-right tabular-nums ${r.profit > 0 ? 'text-emerald-400' : 'text-red-400'}`}>{money(r.profit)}</td>
                                <td className={`px-3 py-2 text-right tabular-nums font-semibold ${(r.profit_pct || 0) > 0 ? 'text-emerald-400' : 'text-red-400'}`}>{pct(r.profit_pct)}</td>
                                <td className={`px-3 py-2 text-right tabular-nums ${r.potential_profit > 0 ? 'text-emerald-300' : 'text-slate-600'}`}>{r.potential_profit ? money(r.potential_profit) : '—'}</td>
                            </tr>
                        ))}
                        {!rows.length && (
                            <tr>
                                <td colSpan={8} className="px-3 py-10 text-center text-slate-500">
                                    {warming ? 'Warming…' : status === 'no_token' ? 'Set a tradeon_token to price markets.' : 'No arbitrage rows. Try “All items” or lower the min %.'}
                                </td>
                            </tr>
                        )}
                    </tbody>
                </table>

                {/* Chains */}
                {chains.map((chain) => (
                    <table key={chain.id} className="w-full text-sm border-t-4 border-sky-500/20">
                        <thead className="sticky top-0 z-10 bg-odin-blue/80 backdrop-blur text-[11px] uppercase tracking-wider text-slate-400">
                            <tr>
                                <th className="text-left font-medium px-3 py-2" colSpan={6}>
                                    <span className="inline-flex items-center gap-1.5 text-sky-300 normal-case tracking-normal">
                                        <Link2 size={13} /> {chain.name}
                                    </span>
                                    {chain.summary && (
                                        <span className="ml-3 text-slate-500 normal-case tracking-normal">
                                            {chain.summary.profitable}/{chain.summary.rows} profitable · best {chain.summary.best_profit_pct != null ? pct(chain.summary.best_profit_pct) : '—'} · potential {money(chain.summary.total_potential_profit)}
                                        </span>
                                    )}
                                </th>
                            </tr>
                            <tr>
                                <th className="text-left font-medium px-3 py-2">Item</th>
                                <th className="text-right font-medium px-3 py-2">Owned</th>
                                <th className="text-left font-medium px-3 py-2">Held by</th>
                                <th className="text-left font-medium px-3 py-2">Legs (buy → instant-sell)</th>
                                <th className="text-right font-medium px-3 py-2">Total</th>
                                <th className="text-right font-medium px-3 py-2">Total %</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-white/5">
                            {chain.rows.map((r, i) => (
                                <tr key={`${chain.id}-${r.item_name}-${i}`} className="hover:bg-white/5 align-top">
                                    <td className="px-3 py-2 text-slate-200">{r.item_name}</td>
                                    <td className="px-3 py-2 text-right tabular-nums text-slate-300">{r.owned_qty || '—'}</td>
                                    <td className="px-3 py-2"><Holders holders={r.holders} /></td>
                                    <td className="px-3 py-2">
                                        <div className="space-y-0.5">
                                            {r.legs.map((leg, li) => (
                                                <div key={li} className="text-xs">
                                                    {leg ? (
                                                        <span className="text-slate-300">
                                                            <span className="text-slate-500">{li + 1}.</span>{' '}
                                                            <span className="text-slate-400">{leg.buy_market_display}</span> {money(leg.buy_price)}
                                                            {' → '}
                                                            <span className="text-slate-400">{leg.sell_market_display}</span> {money(leg.sell_net)}
                                                            <span className={leg.profit > 0 ? 'text-emerald-400' : 'text-red-400'}> ({money(leg.profit)} / {pct(leg.profit_pct)})</span>
                                                        </span>
                                                    ) : (
                                                        <span className="text-slate-600"><span className="text-slate-500">{li + 1}.</span> not priced on both ends</span>
                                                    )}
                                                </div>
                                            ))}
                                        </div>
                                    </td>
                                    <td className={`px-3 py-2 text-right tabular-nums ${r.total_profit > 0 ? 'text-emerald-400' : 'text-red-400'}`}>{money(r.total_profit)}</td>
                                    <td className={`px-3 py-2 text-right tabular-nums font-semibold ${(r.total_profit_pct || 0) > 0 ? 'text-emerald-400' : 'text-red-400'}`}>{pct(r.total_profit_pct)}</td>
                                </tr>
                            ))}
                            {!chain.rows.length && (
                                <tr>
                                    <td colSpan={6} className="px-3 py-6 text-center text-slate-500 text-xs">
                                        {warming ? 'Warming…' : 'No priced routes for this chain right now.'}
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                ))}
            </div>
        </>
    );
};

export default CrossProfileArbitrage;
