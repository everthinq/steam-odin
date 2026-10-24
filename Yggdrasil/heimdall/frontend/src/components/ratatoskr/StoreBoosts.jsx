import { Fragment, useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Link } from 'react-router-dom';
import {
    Search, Loader2, ShoppingCart, ChevronRight, ChevronDown, RefreshCw, Rocket, Eye, TrendingDown, Repeat,
    Activity, PhoneCall, Send, AlertTriangle, Package, Flag, Radio, BellRing, Hourglass, CircleHelp, ShieldCheck, Pin, PinOff,
} from 'lucide-react';
import SteamMarketLink from '../SteamMarketLink';

// Store Catalogue → Boosts tab: a Steam price boost of an in-game store item, or of a sticker inside
// a store capsule, caught on its first day. A price boost (Ivan's definition): someone buys an item on
// the Steam Community Market in large portions at rising prices, to resell it on third-party markets.
// The store sells any number of copies at one price, but they cannot be traded for 7 days, so only a
// day-one purchase unlocks while the price is high.
// Read-only: an alarm and a recommendation; Buy opens the item's guarded buy page (dry run first).
// Backend (store_boost_service.py): GET /api/ratatoskr/store-boosts (board), GET …/item?name= (the
// 14-day hourly chart), POST …/read {name} (read Steam now), POST …/check (pulse check now),
// POST …/test-alert {ring}. Switches are settings keys (store_boost_*), posted on their own.

const money = (value, digits = 2) => (value == null ? '—' : `$${Number(value).toFixed(digits)}`);
const times = (value) => (value == null ? '—' : `${value >= 10 ? Math.round(value) : Number(value).toFixed(1)}×`);
const imageOf = (name) => `https://api.steamapis.com/image/item/730/${encodeURIComponent(name)}`;
const ago = (epochSeconds, now) => {
    if (!epochSeconds || !now) return null;
    const minutes = Math.round((now - epochSeconds) / 60);
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes} ${minutes === 1 ? 'minute' : 'minutes'} ago`;
    const hours = Math.round(minutes / 60);
    return hours < 48 ? `${hours} ${hours === 1 ? 'hour' : 'hours'} ago` : `${Math.round(hours / 24)} days ago`;
};
const inTime = (epochSeconds, now) => {
    if (!epochSeconds || !now) return null;
    const minutes = Math.max(0, Math.round((epochSeconds - now) / 60));
    if (minutes < 60) return `in ${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`;
    const hours = Math.round(minutes / 60);
    return `in ${hours} ${hours === 1 ? 'hour' : 'hours'}`;
};
const when = (epochSeconds, withTime = true) => (epochSeconds
    ? new Date(epochSeconds * 1000).toLocaleString(undefined, withTime
        ? { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }
        : { day: '2-digit', month: 'short' })
    : '—');

// The levels, most urgent first. Emerald: worth buying in the store now; red: time to sell.
const LEVELS = {
    boost: { label: 'Boost', Icon: Rocket, chip: 'border-emerald-400/60 bg-emerald-500/15 text-emerald-200', line: '#34d399',
        hint: 'Steam price at least 2× its usual with at least 3× the usual sales, or still near the peak of a boost. Bought in the store today, copies unlock in 7 days.' },
    second_wave: { label: 'Second wave', Icon: Repeat, chip: 'border-sky-400/60 bg-sky-500/15 text-sky-200', line: '#38bdf8',
        hint: 'After a boost peaked, the price climbed 1.3× off its low again.' },
    watch: { label: 'Watch', Icon: Eye, chip: 'border-amber-400/60 bg-amber-500/15 text-amber-200', line: '#fbbf24',
        hint: 'Early sign: price at least 1.2× its usual with at least 3× the usual sales.' },
    fading: { label: 'Fading', Icon: TrendingDown, chip: 'border-red-400/50 bg-red-500/10 text-red-300', line: '#f87171',
        hint: 'The boost turned: under 70% of its peak but still above the usual price. Sell what you hold before third-party markets follow Steam down.' },
    calm: { label: 'Calm', Icon: null, chip: 'border-white/10 text-slate-500', line: '#94a3b8', hint: 'Nothing unusual on Steam.' },
    unknown: { label: 'Too few sales', Icon: null, chip: 'border-white/10 text-slate-600', line: '#94a3b8',
        hint: 'Not enough sales in the last 30 days to know the usual price.' },
};
const ACTIVE = new Set(['boost', 'second_wave', 'watch', 'fading']);
const levelOf = (level) => LEVELS[level] || null;

const LevelChip = ({ level, small = false }) => {
    const meta = levelOf(level);
    if (!meta) return <span className="text-[11px] text-slate-600" title="Steam's price history has not been read yet">not read yet</span>;
    const { Icon } = meta;
    return (
        <span title={meta.hint}
            className={`inline-flex items-center gap-1 rounded-md border whitespace-nowrap ${small ? 'px-1 text-[10px]' : 'px-1.5 py-0.5 text-[11px] font-semibold'} ${meta.chip}`}>
            {Icon && <Icon size={small ? 10 : 12} />}{meta.label}
        </span>
    );
};

// The row's 7-day chart: price per 6 hours (line), sales (bars), the store price (amber dashes)
// and the usual price (dotted).
const Spark = ({ spark, cost, usual, level, width = 150, height = 36 }) => {
    const priced = (spark || []).filter((bucket) => bucket.price);
    if (priced.length < 2) return <span className="text-[11px] text-slate-600">no sales chart yet</span>;
    const prices = priced.map((bucket) => bucket.price);
    const low = Math.min(...prices, cost || Infinity, usual || Infinity) * 0.95;
    const high = Math.max(...prices, cost || 0, usual || 0) * 1.05;
    const span = high - low || 1;
    const maxSales = Math.max(...spark.map((bucket) => bucket.sales), 1);
    const step = width / (spark.length - 1);
    const y = (value) => height - 2 - ((value - low) / span) * (height - 4);
    const line = spark.map((bucket, index) => (bucket.price ? `${(index * step).toFixed(1)},${y(bucket.price).toFixed(1)}` : null)).filter(Boolean).join(' ');
    return (
        <svg width={width} height={height} className="block" aria-hidden="true">
            {spark.map((bucket, index) => (bucket.sales ? (
                <rect key={bucket.time} x={index * step - step * 0.35} width={step * 0.7} y={height - (bucket.sales / maxSales) * height * 0.45}
                    height={(bucket.sales / maxSales) * height * 0.45} fill="#475569" opacity="0.55" />
            ) : null))}
            {cost ? <line x1="0" x2={width} y1={y(cost)} y2={y(cost)} stroke="#f59e0b" strokeDasharray="3 3" opacity="0.7" /> : null}
            {usual ? <line x1="0" x2={width} y1={y(usual)} y2={y(usual)} stroke="#94a3b8" strokeDasharray="1 3" opacity="0.8" /> : null}
            <polyline fill="none" strokeWidth="1.6" stroke={(levelOf(level) || LEVELS.calm).line} points={line} strokeLinejoin="round" />
        </svg>
    );
};

// The detail chart: every hourly Steam point of the last 14 days, sales as bars, the usual price,
// the store price and the peak; hover for the hour.
const BoostChart = ({ points, analysis, cost, level }) => {
    const [hover, setHover] = useState(null);
    const width = 760;
    const height = 240;
    const pad = { left: 44, right: 10, top: 12, bottom: 22 };
    if (!points || points.length < 2) {
        return <div className="h-40 flex items-center justify-center text-slate-500 text-xs">No Steam sales in the last 14 days.</div>;
    }
    const first = points[0][0];
    const last = points[points.length - 1][0];
    const spanTime = last - first || 1;
    const prices = points.map((point) => point[1]);
    const usual = analysis?.usual_price;
    const low = Math.min(...prices, cost || Infinity, usual || Infinity) * 0.92;
    const high = Math.max(...prices, cost || 0, usual || 0) * 1.06;
    const span = high - low || 1;
    const maxSales = Math.max(...points.map((point) => point[2]), 1);
    const plotWidth = width - pad.left - pad.right;
    const plotHeight = height - pad.top - pad.bottom;
    const x = (time) => pad.left + ((time - first) / spanTime) * plotWidth;
    const y = (value) => pad.top + plotHeight - ((value - low) / span) * plotHeight;
    const barWidth = Math.max(1, (plotWidth / (spanTime / 3600)) * 0.7);
    const ticks = [low + span * 0.1, low + span * 0.5, low + span * 0.9];
    const days = [];
    for (let day = Math.ceil(first / 86400) * 86400; day <= last; day += 86400 * Math.max(1, Math.round(spanTime / 86400 / 7))) days.push(day);
    const color = (levelOf(level) || LEVELS.calm).line;
    const onMove = (event) => {
        const box = event.currentTarget.getBoundingClientRect();
        const time = first + ((event.clientX - box.left) / box.width * width - pad.left) / plotWidth * spanTime;
        let best = points[0];
        for (const point of points) if (Math.abs(point[0] - time) < Math.abs(best[0] - time)) best = point;
        setHover(best);
    };
    return (
        <div className="relative">
            <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-auto" onMouseMove={onMove} onMouseLeave={() => setHover(null)}
                role="img" aria-label="Steam price and sales, hourly, last 14 days">
                {ticks.map((value) => (
                    <g key={value}>
                        <line x1={pad.left} x2={width - pad.right} y1={y(value)} y2={y(value)} stroke="#1e293b" />
                        <text x={pad.left - 6} y={y(value) + 3} textAnchor="end" fontSize="9" fill="#64748b">{money(value, value < 1 ? 3 : 2)}</text>
                    </g>
                ))}
                {days.map((day) => (
                    <text key={day} x={x(day)} y={height - 6} textAnchor="middle" fontSize="9" fill="#64748b">{when(day, false)}</text>
                ))}
                {points.map((point) => (
                    <rect key={point[0]} x={x(point[0]) - barWidth / 2} width={barWidth} y={pad.top + plotHeight - (point[2] / maxSales) * plotHeight * 0.35}
                        height={(point[2] / maxSales) * plotHeight * 0.35} fill="#475569" opacity="0.6" />
                ))}
                {cost ? (
                    <g>
                        <line x1={pad.left} x2={width - pad.right} y1={y(cost)} y2={y(cost)} stroke="#f59e0b" strokeDasharray="5 4" />
                        <text x={width - pad.right} y={y(cost) - 4} textAnchor="end" fontSize="9" fill="#f59e0b">store {money(cost)}</text>
                    </g>
                ) : null}
                {usual ? (
                    <g>
                        <line x1={pad.left} x2={width - pad.right} y1={y(usual)} y2={y(usual)} stroke="#94a3b8" strokeDasharray="2 4" />
                        <text x={pad.left + 4} y={y(usual) - 4} fontSize="9" fill="#94a3b8">usual {money(usual, usual < 1 ? 3 : 2)}</text>
                    </g>
                ) : null}
                <polyline fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round"
                    points={points.map((point) => `${x(point[0]).toFixed(1)},${y(point[1]).toFixed(1)}`).join(' ')} />
                {hover && (
                    <g>
                        <line x1={x(hover[0])} x2={x(hover[0])} y1={pad.top} y2={pad.top + plotHeight} stroke="#e2e8f0" opacity="0.3" />
                        <circle cx={x(hover[0])} cy={y(hover[1])} r="3.5" fill={color} />
                    </g>
                )}
            </svg>
            {hover && (
                <div className="absolute top-1 right-2 rounded-md border border-white/10 bg-slate-900/95 px-2 py-1 text-[11px] tabular-nums text-slate-200 pointer-events-none">
                    {when(hover[0])} · {money(hover[1], hover[1] < 1 ? 3 : 2)} · {hover[2]} sold
                </div>
            )}
        </div>
    );
};

const Stat = ({ label, value, sub, tone = 'text-slate-100' }) => (
    <div className="min-w-0">
        <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
        <div className={`tabular-nums font-semibold ${tone}`}>{value}</div>
        {sub && <div className="text-[11px] text-slate-500 tabular-nums">{sub}</div>}
    </div>
);

const ratioTone = (ratio, threshold) => (ratio == null ? 'text-slate-500'
    : ratio >= threshold * 1.6 ? 'text-emerald-300' : ratio >= threshold ? 'text-amber-300' : 'text-slate-300');

// Everything known about one item, shown under its row.
// Pin an item: shown on top, read on the same schedule as every other item (settings store_boost_tracked).
const TrackButton = ({ name, tracking, onTrack, size = 12 }) => (
    <button type="button" onClick={(event) => { event.stopPropagation(); onTrack(name, !tracking); }}
        title={tracking ? 'Stop tracking it' : 'Track it: shown on top, read like every other item'}
        aria-label={tracking ? `Stop tracking ${name}` : `Track ${name}`} aria-pressed={tracking}
        className={`shrink-0 rounded p-0.5 ${tracking ? 'text-sky-300 hover:text-sky-100' : 'text-slate-600 hover:text-slate-200'}`}>
        <Pin size={size} className={tracking ? 'fill-sky-400/40' : ''} />
    </button>
);

const Detail = ({ row, now, tradeHoldDays, onRead, owned, tracking, onTrack }) => {
    const [detail, setDetail] = useState(null);
    const [failed, setFailed] = useState(false);
    useEffect(() => {
        let alive = true;
        fetch(`/api/ratatoskr/store-boosts/item?name=${encodeURIComponent(row.name)}`)
            .then((row) => (row.ok ? row.json() : null))
            .then((answer) => { if (alive) { setDetail(answer); setFailed(!answer); } })
            .catch(() => { if (alive) setFailed(true); });
        return () => { alive = false; };
    }, [row.name, row.read_at]);
    const analysis = row.analysis || {};
    const pulse = row.pulse || {};
    const contents = row.contents;
    const unlock = now ? now + tradeHoldDays * 86400 : null;
    return (
        <div className="px-3 pb-4 pt-1 space-y-3 bg-black/20 border-t border-white/5">
            <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_300px]">
                <div className="rounded-lg border border-white/10 bg-slate-950/60 p-2">
                    <div className="flex items-center gap-2 text-[11px] text-slate-400 mb-1">
                        <Activity size={12} /> Steam, hourly, last 14 days
                        <span className="ml-auto flex items-center gap-3">
                            <span className="flex items-center gap-1"><span className="inline-block w-3 h-0.5" style={{ background: (levelOf(row.level) || LEVELS.calm).line }} /> price</span>
                            <span className="flex items-center gap-1"><span className="inline-block w-2 h-2 bg-slate-600" /> sales</span>
                            <span className="text-amber-400">- - store</span>
                            <span>··· usual</span>
                        </span>
                    </div>
                    {detail ? <BoostChart points={detail.points} analysis={detail.analysis} cost={row.cost} level={row.level} />
                        : failed ? <div className="h-40 flex items-center justify-center text-slate-500 text-xs">Steam&apos;s price history has not been read yet.</div>
                            : <div className="h-40 flex items-center justify-center text-slate-500"><Loader2 size={16} className="animate-spin" /></div>}
                </div>
                <div className="rounded-lg border border-white/10 bg-slate-950/60 p-3 grid grid-cols-2 gap-x-4 gap-y-3 content-start text-sm">
                    <Stat label="Usual price" value={money(analysis.usual_price, analysis.usual_price < 1 ? 3 : 2)} sub="median day, 30 to 3 days ago" />
                    <Stat label="Now (6 hours)" value={money(analysis.price_6h, analysis.price_6h < 1 ? 3 : 2)} sub={analysis.price_ratio ? `${times(analysis.price_ratio)} usual` : null}
                        tone={ratioTone(analysis.price_ratio, 1.2)} />
                    <Stat label="Sold in 24 hours" value={analysis.sales_24h ?? '—'} sub={analysis.usual_sales != null ? `usual ${analysis.usual_sales} a day` : null}
                        tone={ratioTone(analysis.sales_ratio, 3)} />
                    <Stat label="Sales vs usual" value={times(analysis.sales_ratio)} sub={analysis.sales_6h != null ? `${analysis.sales_6h} in the last 6 hours` : null}
                        tone={ratioTone(analysis.sales_ratio, 3)} />
                    <Stat label="Peak" value={analysis.peak ? money(analysis.peak) : '—'} sub={analysis.peak_at ? when(analysis.peak_at, false) : 'no boost in 14 days'} />
                    <Stat label="Low since peak" value={analysis.low_after_peak ? money(analysis.low_after_peak) : '—'} />
                    <Stat label="Rise started" value={analysis.started_at ? when(analysis.started_at) : '—'} />
                    <Stat label="Bought now, tradable" value={unlock ? when(unlock) : '—'} sub={`after the ${tradeHoldDays}-day hold`} tone="text-amber-200" />
                    <div className="col-span-2 border-t border-white/5 pt-2 grid grid-cols-2 gap-x-4 gap-y-2">
                        <Stat label="Steam lowest listing" value={money(pulse.listing)} sub={pulse.listing_count != null ? `${pulse.listing_count} listed` : null} />
                        <Stat label="Steam buy order" value={money(pulse.buy)} sub={pulse.buy_count != null ? `${pulse.buy_count} orders` : null} />
                        <Stat label="Steam 30-day median" value={money(pulse.median30d)} sub="pulse" />
                        <Stat label="Buff163 listing" value={money(pulse.buff)} />
                    </div>
                    {row.confirm && (
                        <div className="col-span-2 text-xs text-slate-300">
                            {row.confirmed ? <ShieldCheck size={12} className="inline text-emerald-400 mr-1" /> : <Hourglass size={12} className="inline text-slate-500 mr-1" />}
                            {row.confirm.display} pays {money(row.confirm.net)} at once after its fee
                            {row.cost ? (row.confirmed ? ', at least the store price.' : `, under the store's ${money(row.cost)}.`) : '.'}
                        </div>
                    )}
                    {owned && <div className="col-span-2 text-xs text-slate-400">You hold {owned.count} (Huginn → Get all items).</div>}
                    <div className="col-span-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                        <span>Steam read {ago(row.read_at, now) || 'never'}</span>
                        {row.error && <span className="text-red-300">({row.error})</span>}
                        <button type="button" onClick={() => onRead(row.name)} disabled={row.queued || row.reading}
                            className="ml-auto inline-flex items-center gap-1 px-2 py-0.5 rounded border border-white/15 text-slate-300 hover:bg-white/5 disabled:opacity-50">
                            <RefreshCw size={11} className={row.reading ? 'animate-spin' : ''} /> {row.reading ? 'Reading…' : row.queued ? 'Queued' : 'Read Steam now'}
                        </button>
                    </div>
                </div>
            </div>
            {contents && <Contents row={row} contents={contents} now={now} onRead={onRead} tracking={tracking} onTrack={onTrack} />}
        </div>
    );
};

// A capsule's stickers (or a patch pack's patches): each one's level, odds and price, and what one
// capsule is worth opened.
const Contents = ({ row, contents, now, onRead, tracking, onTrack }) => {
    const buff = contents.opening?.buff;
    const steam = contents.opening?.steam_buy;
    const active = contents.items.filter((item) => ACTIVE.has(item.level));
    return (
        <div className="rounded-lg border border-white/10 bg-slate-950/60">
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 px-3 py-2 border-b border-white/10">
                <span className="flex items-center gap-1.5 text-[10px] font-bold tracking-widest uppercase text-slate-400"><Package size={12} /> Inside: {contents.items.length}</span>
                <span className="text-xs text-slate-400" title="The chance of each sticker times its price, summed: what one capsule is worth opened">
                    Opening value: <b className={`tabular-nums ${buff && buff.value > row.cost ? 'text-emerald-300' : 'text-slate-200'}`}>{buff ? money(buff.value) : '—'}</b> on Buff163 listings
                    {buff && buff.priced_share < 0.99 && <span className="text-slate-500"> ({Math.round(buff.priced_share * 100)}% of the odds priced)</span>}
                    {' · '}<b className="tabular-nums text-slate-200">{steam ? money(steam.value) : '—'}</b> on Steam buy orders
                    {' · store '}{money(row.cost)}
                </span>
                {contents.opening_beats_store && <span className="text-xs text-emerald-300 inline-flex items-center gap-1"><ShieldCheck size={12} /> opening beats the store price</span>}
                {active.length > 0 && <span className="text-xs text-amber-200">{active.length} moving: {active.slice(0, 3).map((item) => item.name.replace(/^(Sticker|Patch) \| /, '')).join(', ')}</span>}
            </div>
            <div className="max-h-80 overflow-auto">
                <table className="w-full text-xs">
                    <thead className="sticky top-0 bg-slate-950 text-[10px] uppercase tracking-wider text-slate-500">
                        <tr>
                            <th className="text-left font-medium py-1.5 pl-3">Item</th>
                            <th className="text-left font-medium">Rarity</th>
                            <th className="text-right font-medium" title="The chance one capsule gives it">Chance</th>
                            <th className="text-left font-medium pl-3">Level</th>
                            <th className="text-right font-medium">Steam now</th>
                            <th className="text-right font-medium">Sales</th>
                            <th className="text-right font-medium">Buff163</th>
                            <th className="text-right font-medium pr-3">Steam read</th>
                        </tr>
                    </thead>
                    <tbody>
                        {contents.items.map((item) => {
                            const analysis = item.analysis || {};
                            return (
                                <tr key={item.name} className={`border-t border-white/5 ${ACTIVE.has(item.level) ? 'bg-amber-500/[0.04]' : ''}`}>
                                    <td className="py-1 pl-3">
                                        <span className="flex items-center gap-2 min-w-0">
                                            <img src={imageOf(item.name)} alt="" loading="lazy" className="w-7 h-5 object-contain shrink-0" />
                                            <span className="truncate text-slate-200" title={item.name}>{item.name.replace(/^(Sticker|Patch) \| /, '')}</span>
                                            <SteamMarketLink itemName={item.name} viaPulse />
                                            <TrackButton name={item.name} tracking={tracking.has(item.name)} onTrack={onTrack} size={11} />
                                            {item.pulse?.preflag && <Flag size={11} className="text-amber-400 shrink-0" title="pulse: Steam's price is running ahead of its 30-day median" />}
                                        </span>
                                    </td>
                                    <td className="text-slate-400">{item.tier_label}</td>
                                    <td className="text-right tabular-nums text-slate-400">{(item.chance * 100).toFixed(item.chance < 0.01 ? 2 : 1)}%</td>
                                    <td className="pl-3"><LevelChip level={item.level} small /></td>
                                    <td className="text-right tabular-nums">
                                        <span className="text-slate-200">{money(analysis.price_6h, analysis.price_6h < 1 ? 3 : 2)}</span>
                                        {analysis.price_ratio != null && <span className={`ml-1 ${ratioTone(analysis.price_ratio, 1.2)}`}>{times(analysis.price_ratio)}</span>}
                                    </td>
                                    <td className={`text-right tabular-nums ${ratioTone(analysis.sales_ratio, 3)}`}>{analysis.sales_ratio != null ? times(analysis.sales_ratio) : '—'}</td>
                                    <td className="text-right tabular-nums text-slate-300">{money(item.pulse?.buff)}</td>
                                    <td className="text-right pr-3 whitespace-nowrap text-slate-500">
                                        {item.reading ? <Loader2 size={11} className="inline animate-spin" /> : ago(item.read_at, now) || 'never'}
                                        <button type="button" onClick={() => onRead(item.name)} disabled={item.queued || item.reading}
                                            title={item.queued ? 'Queued for a Steam read' : 'Read its Steam price history next'}
                                            className="ml-1.5 align-middle text-slate-400 hover:text-white disabled:opacity-40">
                                            <RefreshCw size={11} />
                                        </button>
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
};

const Toggle = ({ checked, onChange, label, title, disabled }) => (
    <label className={`flex items-center gap-2 text-xs ${disabled ? 'opacity-50' : 'cursor-pointer'} text-slate-300`} title={title}>
        <button type="button" role="switch" aria-checked={checked} disabled={disabled} onClick={() => onChange(!checked)}
            className={`relative w-8 h-4 rounded-full transition-colors ${checked ? 'bg-amber-500/80' : 'bg-slate-700'}`}>
            <span className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-all ${checked ? 'left-[18px]' : 'left-0.5'}`} />
        </button>
        {label}
    </label>
);

const Coverage = ({ label, part, every }) => (
    <div title={`Read in the last ${every} hours`}>
        <div className="flex justify-between text-[11px] text-slate-400"><span>{label}</span><span className="tabular-nums">{part.fresh} / {part.total}</span></div>
        <div className="h-1.5 rounded bg-slate-800 overflow-hidden mt-0.5">
            <div className="h-full bg-amber-500/70" style={{ width: `${part.total ? (part.fresh / part.total) * 100 : 0}%` }} />
        </div>
    </div>
);

const READER_STATUS = {
    starting: ['Starting (the first read a minute after the backend starts)', 'text-slate-300'],
    reading: ['Reading', 'text-emerald-300'],
    waiting: ['Between reads', 'text-emerald-300/80'],
    idle: ['Up to date', 'text-slate-300'],
    backing_off: ['Steam said slow down', 'text-amber-300'],
    no_session: ['No Steam web session', 'text-red-300'],
    session_failing: ['Steam reads keep failing: paused', 'text-red-300'],
    off: ['Switched off', 'text-slate-500'],
};

const VIEWS = [
    { id: 'moving', label: 'Moving now', test: (row) => ACTIVE.has(row.level) || ACTIVE.has(row.contents?.level) || row.pulse?.preflag },
    { id: 'capsules', label: 'Capsules', test: (row) => Boolean(row.contents) },
    { id: 'all', label: 'All', test: () => true },
];
const VIEW_KEY = 'ratatoskr.storeBoosts';
const loadView = () => {
    try { return localStorage.getItem(VIEW_KEY) || 'moving'; } catch { return 'moving'; }
};
const GRID = 'grid grid-cols-[18px_minmax(0,2.3fr)_118px_60px_112px_96px_166px_112px_70px] gap-2 items-center';

const StoreBoosts = () => {
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [view, setView] = useState(loadView);
    const [search, setSearch] = useState('');
    const [category, setCategory] = useState('all');
    const [open, setOpen] = useState(() => new Set());
    const [byHash, setByHash] = useState(null);
    const [note, setNote] = useState(null);
    const [busy, setBusy] = useState(null);
    const [showHow, setShowHow] = useState(false);
    const [gapDraft, setGapDraft] = useState(null);
    const loads = useRef(0);
    const sending = useRef(false);

    const load = useCallback(() => {
        const number = ++loads.current;
        return fetch('/api/ratatoskr/store-boosts')
            .then((row) => row.json())
            .then((answer) => {
                if (number !== loads.current) return;
                if (answer.error) setError(answer.error); else { setData(answer); setError(null); }
            })
            .catch(() => { if (number === loads.current) setError('Could not reach the backend.'); });
    }, []);
    // Poll fast only while the page is waiting on something: an item being read, the pulse check, or
    // items you queued (the reader works all day, so its status alone would mean always fast).
    const reading = Boolean(data?.reader?.reading) || Boolean(data?.check?.running) || (data?.reader?.priority?.length || 0) > 0;
    useEffect(() => {
        load();
        const timer = setInterval(load, reading ? 10000 : 45000);
        return () => clearInterval(timer);
    }, [load, reading]);
    useEffect(() => {
        fetch('/api/huginn/scan/cache')
            .then((row) => (row.ok ? row.json() : null))
            .then((scan) => { if (scan?.by_hash) setByHash(scan.by_hash); })
            .catch(() => {});
    }, []);

    const chooseView = (id) => {
        setView(id);
        try { localStorage.setItem(VIEW_KEY, id); } catch { /* private window: not remembered */ }
    };
    const post = (url, body) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
        .then((response) => response.json().then((answer) => ({ ok: response.ok, ...answer })));
    const track = (name, tracked) => post('/api/ratatoskr/store-boosts/track', { name, tracked })
        .then((answer) => setNote(answer.error ? answer.error
            : tracked ? `Tracking ${name}: shown on top.` : `Stopped tracking ${name}.`))
        .catch(() => setNote('Could not reach the backend.'))
        .finally(load);
    const readNow = (name) => post('/api/ratatoskr/store-boosts/read', { name })
        .then((answer) => setNote(answer.queued ? `${name}: queued for a Steam read (position ${answer.position}).` : answer.error))
        .catch(() => setNote('Could not reach the backend.'))
        .finally(load);
    const checkNow = () => {
        setBusy('check');
        post('/api/ratatoskr/store-boosts/check')
            .then((answer) => setNote(answer.started ? 'Pulse check started: Steam and Buff163 prices of every watched item.' : answer.error))
            .catch(() => setNote('Could not reach the backend.'))
            .finally(() => { setBusy(null); load(); });
    };
    const testAlert = (ring) => {
        if (sending.current) return;
        sending.current = true;
        setBusy(ring ? 'ring' : 'test');
        post('/api/ratatoskr/store-boosts/test-alert', { ring })
            .then((answer) => setNote(answer.sent ? `Test sent to Telegram${answer.rang ? ', and the phone is ringing' : ''}.`
                : answer.rang ? 'The phone is ringing; the Telegram message failed: ' + (answer.error || '') : `Test failed: ${answer.error}`))
            .catch(() => setNote('Could not reach the backend.'))
            .finally(() => { sending.current = false; setBusy(null); load(); });
    };
    // Only this tab's own keys (posting the whole settings object overwrites other screens).
    const saveSetting = (changes) => post('/api/settings', changes)
        .then((answer) => { if (answer.status !== 'success') setNote('The setting was not saved.'); })
        .catch(() => setNote('Could not reach the backend.'))
        .finally(load);

    const rows = useMemo(() => data?.rows || [], [data]);
    const trackedItems = useMemo(() => data?.tracked || [], [data]);
    const tracking = useMemo(() => new Set(trackedItems.map((item) => item.name)), [trackedItems]);
    const categories = useMemo(() => {
        const counts = {};
        for (const row of rows) counts[row.category] = (counts[row.category] || 0) + 1;
        return Object.entries(counts).sort((a, b) => b[1] - a[1]);
    }, [rows]);
    const visible = useMemo(() => {
        const needle = search.trim().toLowerCase();
        const test = (VIEWS.find((candidate) => candidate.id === view) || VIEWS[0]).test;
        return rows.filter((row) => test(row) && (category === 'all' || row.category === category)
            && (!needle || row.name.toLowerCase().includes(needle)
                || (row.contents?.items || []).some((item) => item.name.toLowerCase().includes(needle))));
    }, [rows, view, category, search]);
    const counts = useMemo(() => Object.fromEntries(VIEWS.map((candidate) => [candidate.id, rows.filter(candidate.test).length])), [rows]);

    if (!data) {
        return (
            <div className="p-8 flex items-center justify-center text-slate-400">
                {error ? <span className="text-red-300 flex items-center gap-2"><AlertTriangle size={15} /> {error}</span>
                    : <><Loader2 className="animate-spin mr-2" size={18} /> Loading Store Boosts…</>}
            </div>
        );
    }
    const now = data.now;
    const summary = data.summary;
    const reader = data.reader;
    const alerts = data.alerts;
    const thresholds = data.thresholds;
    const [readerLabel, readerTone] = READER_STATUS[reader.status] || [reader.status, 'text-slate-300'];
    const toggle = (name) => setOpen((previous) => {
        const next = new Set(previous);
        if (next.has(name)) next.delete(name); else next.add(name);
        return next;
    });

    return (
        <div>
            {/* The alarm, the Steam reader, the pulse check and the alerts. */}
            <div className="grid gap-3 p-3 md:grid-cols-2 xl:grid-cols-4 border-b border-white/10">
                <div className="rounded-lg border border-white/10 bg-black/30 p-3">
                    <div className="text-[10px] font-bold tracking-widest uppercase text-slate-400 mb-2 flex items-center gap-1.5"><Rocket size={12} /> Boost alarm</div>
                    <div className="grid grid-cols-2 gap-2">
                        {['boost', 'second_wave', 'watch', 'fading'].map((level) => {
                            const meta = LEVELS[level];
                            const { Icon } = meta;
                            const count = summary[level] || 0;
                            return (
                                <div key={level} title={meta.hint}
                                    className={`rounded-md border px-2 py-1.5 ${count ? meta.chip : 'border-white/5 text-slate-500'}`}>
                                    <div className="flex items-center gap-1 text-[11px]"><Icon size={12} /> {meta.label}</div>
                                    <div className="text-xl font-bold tabular-nums">{count}</div>
                                </div>
                            );
                        })}
                    </div>
                    <div className="mt-2 flex flex-wrap gap-x-3 text-[11px] text-slate-400">
                        <span title="Store capsules with a sticker in watch, boost or second wave">
                            <Package size={11} className="inline mr-0.5" />{summary.capsule_signals} capsule{summary.capsule_signals === 1 ? '' : 's'} with a moving sticker</span>
                        <span title="Store items whose Steam price on pulse runs ahead of its 30-day median"><Flag size={11} className="inline mr-0.5" />{summary.preflagged} flagged by pulse</span>
                        <span title="Boosting store items whose cash buy order or opening value beats the store price"><ShieldCheck size={11} className="inline mr-0.5" />{summary.confirmed} confirmed</span>
                    </div>
                </div>

                <div className="rounded-lg border border-white/10 bg-black/30 p-3 space-y-2">
                    <div className="text-[10px] font-bold tracking-widest uppercase text-slate-400 flex items-center gap-1.5"><Activity size={12} /> Steam price history</div>
                    <div className="text-sm">
                        <span className={`font-semibold ${readerTone}`}>{readerLabel}</span>
                        {reader.reading && <span className="text-slate-400"> · {reader.reading}</span>}
                        {(reader.status === 'backing_off' || reader.status === 'session_failing') && reader.blocked_until && <span className="text-slate-400"> · again {inTime(reader.blocked_until, now)}</span>}
                    </div>
                    <Coverage label="Store items" part={reader.store} every={thresholds.store_item_every_hours} />
                    <Coverage label="Capsule stickers and patches" part={reader.contents} every={thresholds.content_every_hours} />
                    <div className="text-[11px] text-slate-500 tabular-nums flex flex-wrap items-center gap-1">
                        <span>{reader.reads_last_hour} read in the last hour · one every</span>
                        <input type="number" min="10" max="3600" step="5" aria-label="Seconds between two Steam reads"
                            title="Seconds between two Steam price history reads (at least 10). Longer is gentler on Steam's rate limit."
                            value={gapDraft ?? reader.gap_seconds} onChange={(event) => setGapDraft(event.target.value)}
                            onBlur={() => {
                                const seconds = Number(gapDraft);
                                if (gapDraft != null && seconds >= 10 && seconds <= 3600 && seconds !== reader.gap_seconds) saveSetting({ store_boost_steam_gap_seconds: seconds });
                                setGapDraft(null);
                            }}
                            onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}
                            className="w-12 bg-black/30 border border-white/10 rounded px-1 py-0 text-slate-200" />
                        <span>seconds{reader.priority?.length ? ` · ${reader.priority.length} queued first` : ''}</span>
                    </div>
                    {reader.error && reader.status !== 'reading' && <div className="text-[11px] text-red-300/90 break-words">{reader.error}</div>}
                </div>

                <div className="rounded-lg border border-white/10 bg-black/30 p-3 space-y-2">
                    <div className="text-[10px] font-bold tracking-widest uppercase text-slate-400 flex items-center gap-1.5"><Radio size={12} /> Hourly pulse check</div>
                    <div className="text-sm text-slate-200">
                        {data.check.running ? <span className="text-emerald-300 inline-flex items-center gap-1"><Loader2 size={12} className="animate-spin" /> Checking…</span>
                            : data.check.checked_at ? <>Checked {ago(data.check.checked_at, now)}<span className="text-slate-500"> · next {inTime(data.check.next_at, now)}</span></>
                                : 'Not checked yet'}
                    </div>
                    <p className="text-[11px] text-slate-500 leading-snug">
                        Steam&apos;s lowest listing, buy orders and 30-day median, and Buff163&apos;s listing, for every watched item. A price running ahead of its median moves the item to the front of the Steam queue.
                    </p>
                    {data.check.error && <div className="text-[11px] text-red-300/90">{data.check.error}</div>}
                    {!data.has_token && <div className="text-[11px] text-amber-300">No Tradeon token (tradeon_token in backend/settings.json).</div>}
                    <button type="button" onClick={checkNow} disabled={busy === 'check' || data.check.running || !data.has_token}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-white/15 text-slate-300 hover:bg-white/5 disabled:opacity-50 text-xs">
                        <RefreshCw size={12} className={data.check.running ? 'animate-spin' : ''} /> Check now
                    </button>
                </div>

                <div className="rounded-lg border border-white/10 bg-black/30 p-3 space-y-2">
                    <div className="text-[10px] font-bold tracking-widest uppercase text-slate-400 flex items-center gap-1.5"><BellRing size={12} /> Alerts</div>
                    <Toggle checked={alerts.watching} onChange={(value) => saveSetting({ store_boost_enabled: value })} label="Watch for boosts"
                        title="The hourly pulse check and the Steam reader" />
                    <Toggle checked={alerts.enabled} onChange={(value) => saveSetting({ store_boost_alerts_enabled: value })} label="Telegram message (Huginn bot)"
                        title="Every new level of a boost: watch, boost, second wave, fading" />
                    <Toggle checked={alerts.ring} onChange={(value) => saveSetting({ store_boost_ring_enabled: value })} disabled={!alerts.can_ring}
                        label="Ring the phone on a boost" title={alerts.can_ring ? 'Like Gjallarhorn’s news alert' : 'telegram_caller.json is not set up'} />
                    {!alerts.channel && <div className="text-[11px] text-amber-300">No Telegram bot (telegram_bot_token + telegram_chat_id).</div>}
                    <div className="flex flex-wrap gap-1.5">
                        <button type="button" onClick={() => testAlert(false)} disabled={Boolean(busy)}
                            className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-white/15 text-slate-300 hover:bg-white/5 disabled:opacity-50 text-xs">
                            <Send size={12} /> Test message
                        </button>
                        <button type="button" onClick={() => testAlert(true)} disabled={Boolean(busy) || !alerts.can_ring}
                            title="Sends the test message and rings the phone (about 2–3 minutes of calls)"
                            className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-white/15 text-slate-300 hover:bg-white/5 disabled:opacity-50 text-xs">
                            <PhoneCall size={12} /> Test ring
                        </button>
                    </div>
                    {alerts.log?.length > 0 && (
                        <details className="text-[11px] text-slate-400">
                            <summary className="cursor-pointer">Last alerts</summary>
                            <ul className="mt-1 space-y-0.5 max-h-36 overflow-auto">
                                {alerts.log.map((entry) => (
                                    <li key={`${entry.at}-${entry.name}`} className="flex items-center gap-1.5">
                                        <span className="tabular-nums text-slate-500">{when(entry.at)}</span>
                                        <LevelChip level={entry.level} small />
                                        <span className="truncate">{entry.test ? 'test' : entry.name}</span>
                                        <span className={entry.sent ? 'text-emerald-400' : 'text-red-300'} title={entry.error || ''}>{entry.sent ? 'sent' : 'not sent'}</span>
                                        {entry.rang && <PhoneCall size={10} className="text-amber-300" />}
                                    </li>
                                ))}
                            </ul>
                        </details>
                    )}
                </div>
            </div>

            {trackedItems.length > 0 && (
                <div className="p-3 border-b border-white/10">
                    <div className="text-[10px] font-bold tracking-widest uppercase text-sky-300/90 mb-2 flex items-center gap-1.5">
                        <Pin size={12} /> Tracked · read on the same schedule as every other item
                    </div>
                    <div className="space-y-2">
                        {trackedItems.map((item) => {
                            const analysis = item.analysis || {};
                            const key = `tracked:${item.name}`;
                            const isOpen = open.has(key);
                            return (
                                <div key={item.name} className="rounded-lg border border-sky-500/20 bg-sky-950/20 overflow-x-auto">
                                    <div role="button" tabIndex={0} onClick={() => toggle(key)} aria-expanded={isOpen}
                                        onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggle(key); } }}
                                        className="grid grid-cols-[18px_minmax(0,2fr)_118px_120px_110px_80px_210px_120px_28px] gap-3 items-center px-3 py-2 cursor-pointer hover:bg-white/[0.03] min-w-[960px]">
                                        <span className="text-slate-500">{isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
                                        <span className="flex items-center gap-2 min-w-0">
                                            <img src={imageOf(item.name)} alt="" loading="lazy" className="w-12 h-9 object-contain shrink-0" />
                                            <span className="min-w-0">
                                                <span className="flex items-center gap-1.5 min-w-0">
                                                    <span className="truncate text-slate-100 font-semibold" title={item.name}>{item.name}</span>
                                                    <SteamMarketLink itemName={item.name} viaPulse />
                                                    {item.pulse?.preflag && <Flag size={11} className="text-amber-400 shrink-0" title="pulse: Steam's price is running ahead of its 30-day median" />}
                                                </span>
                                                <span className="block text-[11px] text-slate-500 truncate">
                                                    {item.cost != null ? `in-game store ${money(item.cost)}` : null}
                                                    {item.capsules.length > 0 && `inside ${item.capsules.map((capsule) => `${capsule.name} (${money(capsule.cost)})`).join(', ')}`}
                                                </span>
                                            </span>
                                        </span>
                                        <span className="flex flex-col items-start gap-0.5">
                                            <LevelChip level={item.level} />
                                            {analysis.started_at && ACTIVE.has(item.level) && <span className="text-[10px] text-slate-500">since {when(analysis.started_at)}</span>}
                                        </span>
                                        <span className="text-right tabular-nums leading-tight" title="Steam's sales-weighted price of the last 6 hours, against its usual">
                                            <span className="block text-slate-100 text-base">{money(analysis.price_6h, analysis.price_6h < 1 ? 3 : 2)}</span>
                                            <span className={`block text-[11px] ${ratioTone(analysis.price_ratio, 1.2)}`}>
                                                {analysis.price_ratio != null ? `${times(analysis.price_ratio)} usual ${money(analysis.usual_price, analysis.usual_price < 1 ? 3 : 2)}` : ''}
                                            </span>
                                        </span>
                                        <span className="text-right tabular-nums leading-tight" title="Sales in the last 24 hours, against the usual a day">
                                            <span className="block text-slate-200 text-base">{analysis.sales_24h ?? '—'} sold</span>
                                            <span className={`block text-[11px] ${ratioTone(analysis.sales_ratio, 3)}`}>{analysis.sales_ratio != null ? `${times(analysis.sales_ratio)} usual` : ''}</span>
                                        </span>
                                        <span className="text-right tabular-nums leading-tight text-[11px] text-slate-400" title="Buff163's lowest listing and Steam's highest buy order (pulse)">
                                            <span className="block">Buff {money(item.pulse?.buff)}</span>
                                            <span className="block">order {money(item.pulse?.buy)}</span>
                                        </span>
                                        <Spark spark={item.spark} cost={item.cost} usual={analysis.usual_price} level={item.level} width={200} height={40} />
                                        <span className="text-[11px] text-slate-500 leading-tight">
                                            {item.reading ? <span className="text-emerald-300"><Loader2 size={10} className="inline animate-spin" /> reading</span>
                                                : item.queued ? 'queued' : <>read {ago(item.read_at, now) || 'never'}<span className="block">next {inTime(item.next_read_at, now)}</span></>}
                                        </span>
                                        <button type="button" onClick={(event) => { event.stopPropagation(); track(item.name, false); }}
                                            title="Stop tracking it" aria-label={`Stop tracking ${item.name}`} className="text-slate-500 hover:text-red-300">
                                            <PinOff size={14} />
                                        </button>
                                    </div>
                                    {isOpen && <Detail row={item} now={now} tradeHoldDays={data.trade_hold_days} onRead={readNow}
                                        owned={byHash?.[item.name] || null} tracking={tracking} onTrack={track} />}
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}

            {(note || error) && (
                <div className="mx-3 mt-3 flex items-start gap-2 rounded-lg border border-white/10 bg-slate-900/80 px-3 py-1.5 text-xs text-slate-300">
                    <span className="flex-1">{error || note}</span>
                    <button type="button" className="text-slate-500 hover:text-white" onClick={() => { setNote(null); setError(null); }} aria-label="Dismiss">×</button>
                </div>
            )}

            <div className="flex flex-wrap items-center gap-2 p-3 border-b border-white/10">
                <div className="flex gap-1" role="tablist" aria-label="Which items">
                    {VIEWS.map((choice) => (
                        <button key={choice.id} type="button" role="tab" aria-selected={view === choice.id} onClick={() => chooseView(choice.id)}
                            className={`px-2.5 py-1 rounded-lg border text-xs ${view === choice.id ? 'border-amber-500/40 bg-amber-600/20 text-amber-200' : 'border-white/10 text-slate-400 hover:text-white hover:bg-white/5'}`}>
                            {choice.label} <span className="opacity-60">{counts[choice.id]}</span>
                        </button>
                    ))}
                </div>
                <select value={category} onChange={(event) => setCategory(event.target.value)} aria-label="Category"
                    className="bg-black/30 border border-white/10 rounded-lg px-2 py-1 text-xs text-slate-300">
                    <option value="all">Every category</option>
                    {categories.map(([name, count]) => <option key={name} value={name}>{name} ({count})</option>)}
                </select>
                <label className="relative flex-1 min-w-[12rem]">
                    <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
                    <span className="sr-only">Search items</span>
                    <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search items and capsule stickers"
                        className="w-full bg-black/30 border border-white/10 rounded-lg pl-8 pr-2 py-1.5 text-sm focus:border-amber-500/60 focus:outline-none" />
                </label>
                <button type="button" onClick={() => setShowHow((shown) => !shown)}
                    className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-white/10 text-slate-400 hover:text-white text-xs">
                    <CircleHelp size={12} /> How it decides
                </button>
            </div>

            {showHow && (
                <div className="mx-3 mt-3 rounded-lg border border-sky-500/20 bg-sky-950/30 p-3 text-xs text-slate-300 leading-relaxed space-y-1.5">
                    <p><b>A price boost</b>: someone buys an item on the Steam Market in large portions at rising prices, to resell it on third-party markets that follow Steam. The store sells any number of copies at one price, but they cannot be traded for {data.trade_hold_days} days, so only a first-day purchase unlocks while the price is high.</p>
                    <p><b>Usual</b> = the median day of the last 30 days, leaving out the last 3; sales alone never alarm (a capsule had about 30 sales spikes with no price move).</p>
                    <p><LevelChip level="watch" small /> price ≥ {thresholds.watch_price}× usual and sales ≥ {thresholds.watch_sales}× usual. <LevelChip level="boost" small /> price ≥ {thresholds.boost_price}× usual and sales ≥ {thresholds.boost_sales}× usual, or within {Math.round(thresholds.hold_share * 100)}% of a recent peak. <LevelChip level="second_wave" small /> {thresholds.second_wave_rise}× off the low since the peak. <LevelChip level="fading" small /> under {Math.round(thresholds.hold_share * 100)}% of the peak: sell.</p>
                    <p><b>Confirmed</b>: a cash buy order on another market pays at least the store price, or a capsule&apos;s opening value (each sticker&apos;s chance × its Buff163 price) beats it.</p>
                    <p><b>Reads</b>: pulse every hour; Steam&apos;s own history one item at a time — flagged items first, then items in a boost every {thresholds.active_every_hours} hour{thresholds.active_every_hours === 1 ? '' : 's'}, store items every {thresholds.store_item_every_hours} hours, capsule contents every {thresholds.content_every_hours} hours. Tracked items are read like any other (one the store does not hold, like a store item). Tested on Flickshot (January 2026: flagged on day one) and World Eaters 1 with its capsule (October 2026: the sticker warned about a day before the capsule).</p>
                </div>
            )}

            <div className="overflow-x-auto">
                <div className="min-w-[960px]">
                    <div className={`${GRID} px-3 py-2 text-[10px] uppercase tracking-wider text-slate-500 border-b border-white/10 sticky top-0 bg-slate-950 z-10`}>
                        <span />
                        <span>Item</span>
                        <span>Level</span>
                        <span className="text-right" title="The game store's US dollar price">Store</span>
                        <span className="text-right" title="Steam's sales-weighted price of the last 6 hours, and how many times its usual price">Steam now</span>
                        <span className="text-right" title="Sales in the last 24 hours, as a multiple of the usual sales a day">Sales, 24 hours</span>
                        <span className="pl-4" title="Last 7 days on Steam: price (line), sales (bars), store price (amber dashes), usual price (dots)">7 days</span>
                        <span title="A cash buy order at or above the store price, or a capsule's opening value above it">Confirmed</span>
                        <span className="text-right">Buy</span>
                    </div>
                    {visible.length === 0 && (
                        <div className="py-10 text-center text-slate-500 text-sm">
                            {view === 'moving' ? (
                                <>Nothing is moving on Steam right now. <button type="button" className="text-amber-300 hover:underline" onClick={() => chooseView('all')}>Show every store item</button></>
                            ) : 'No item matches.'}
                        </div>
                    )}
                    {visible.map((row) => {
                        const analysis = row.analysis || {};
                        const isOpen = open.has(row.name);
                        const insideLevel = row.contents?.level;
                        const movingInside = (row.contents?.items || []).filter((item) => ACTIVE.has(item.level));
                        const owned = byHash?.[row.name] || null;
                        return (
                            <Fragment key={row.entry}>
                                <div role="button" tabIndex={0} onClick={() => toggle(row.name)}
                                    onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggle(row.name); } }}
                                    aria-expanded={isOpen}
                                    className={`${GRID} px-3 py-1.5 border-b border-white/5 cursor-pointer hover:bg-white/[0.03] ${row.level === 'boost' ? 'bg-emerald-500/[0.05]' : row.level === 'fading' ? 'bg-red-500/[0.04]' : ''}`}>
                                    <span className="text-slate-500">{isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
                                    <span className="flex items-center gap-2 min-w-0">
                                        <img src={imageOf(row.name)} alt="" loading="lazy" className="w-10 h-7 object-contain shrink-0" />
                                        <span className="min-w-0">
                                            <span className="flex items-center gap-1.5 min-w-0">
                                                <span className="truncate text-slate-100" title={row.name}>{row.name}</span>
                                                <SteamMarketLink itemName={row.name} viaPulse />
                                                <TrackButton name={row.name} tracking={tracking.has(row.name)} onTrack={track} />
                                                {row.pulse?.preflag && <Flag size={11} className="text-amber-400 shrink-0" title="pulse: Steam's price is running ahead of its 30-day median" />}
                                            </span>
                                            <span className="block text-[11px] text-slate-500 truncate">
                                                {row.category}
                                                {row.store_name && row.store_name !== row.name ? ` · the store calls it ${row.store_name}` : ''}
                                                {owned ? ` · you hold ${owned.count}` : ''}
                                                {movingInside.length > 0 && (
                                                    <span className="text-amber-300"> · {movingInside.length} inside moving: {movingInside.slice(0, 2).map((item) => item.name.replace(/^(Sticker|Patch) \| /, '')).join(', ')}</span>
                                                )}
                                            </span>
                                        </span>
                                    </span>
                                    <span className="flex flex-col items-start gap-0.5">
                                        <LevelChip level={row.level} />
                                        {insideLevel && ACTIVE.has(insideLevel) && insideLevel !== row.level && (
                                            <span className="text-[10px] text-slate-400 inline-flex items-center gap-1">inside: <LevelChip level={insideLevel} small /></span>
                                        )}
                                        {analysis.started_at && ACTIVE.has(row.level) && <span className="text-[10px] text-slate-500">since {when(analysis.started_at)}</span>}
                                    </span>
                                    <span className="text-right tabular-nums text-slate-200">{money(row.cost)}</span>
                                    <span className="text-right tabular-nums leading-tight">
                                        <span className="block text-slate-100">{money(analysis.price_6h, analysis.price_6h < 1 ? 3 : 2)}</span>
                                        <span className={`block text-[11px] ${ratioTone(analysis.price_ratio, 1.2)}`}>
                                            {analysis.price_ratio != null ? `${times(analysis.price_ratio)} usual` : row.pulse?.listing ? `listed ${money(row.pulse.listing)}` : ''}
                                        </span>
                                    </span>
                                    <span className="text-right tabular-nums leading-tight">
                                        <span className="block text-slate-200">{analysis.sales_24h ?? '—'}</span>
                                        <span className={`block text-[11px] ${ratioTone(analysis.sales_ratio, 3)}`}>{analysis.sales_ratio != null ? `${times(analysis.sales_ratio)} usual` : ''}</span>
                                    </span>
                                    <span className="pl-4"><Spark spark={row.spark} cost={row.cost} usual={analysis.usual_price} level={row.level} /></span>
                                    <span className="text-[11px] leading-tight">
                                        {row.contents?.opening?.buff ? (
                                            <span className={row.contents.opening_beats_store ? 'text-emerald-300' : 'text-slate-400'}
                                                title="What one capsule is worth opened, on Buff163 listings">
                                                {row.contents.opening_beats_store && <ShieldCheck size={11} className="inline mr-0.5" />}
                                                opened {money(row.contents.opening.buff.value)}
                                            </span>
                                        ) : row.confirm ? (
                                            <span className={row.confirmed ? 'text-emerald-300' : 'text-slate-400'} title={`${row.confirm.display} pays this at once, after its fee`}>
                                                {row.confirmed && <ShieldCheck size={11} className="inline mr-0.5" />}
                                                {money(row.confirm.net)} <span className="text-slate-500">{row.confirm.display}</span>
                                            </span>
                                        ) : <span className="text-slate-600">—</span>}
                                        <span className="block text-slate-500">
                                            {row.reading ? <><Loader2 size={10} className="inline animate-spin" /> reading</> : row.queued ? 'queued' : `read ${ago(row.read_at, now) || 'never'}`}
                                        </span>
                                    </span>
                                    <span className="text-right" onClick={(event) => event.stopPropagation()}>
                                        {row.definition_index != null && (
                                            <Link to={`/store-catalogue/buy/${encodeURIComponent(row.entry)}`} title={`Buy ${row.name} on any of your accounts (guarded, dry run first)`}
                                                className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border text-xs ${ACTIVE.has(row.level) && row.level !== 'fading' ? 'border-emerald-500/50 text-emerald-200 hover:bg-emerald-500/10' : 'border-amber-500/30 text-amber-200 hover:bg-amber-500/10'}`}>
                                                <ShoppingCart size={11} /> Buy
                                            </Link>
                                        )}
                                    </span>
                                </div>
                                {isOpen && <Detail row={row} now={now} tradeHoldDays={data.trade_hold_days} onRead={readNow} owned={owned} tracking={tracking} onTrack={track} />}
                            </Fragment>
                        );
                    })}
                </div>
            </div>
            <p className="px-3 py-2 border-t border-white/10 text-[11px] text-slate-500">
                {visible.length} of {rows.length} store items · {reader.contents.total} stickers and patches inside {rows.filter((row) => row.contents).length} capsules watched.
                {' '}A recommendation and an alarm only: Buy opens the guarded buy page. Store purchases cannot be traded for {data.trade_hold_days} days.
            </p>
        </div>
    );
};

export default StoreBoosts;
