"""Cross-profile arbitrage aggregation.

One board that answers: "across ALL my accounts, what is the best instant-sell
arbitrage on the things I hold right now, and which account holds each?"

For every held item (pooled from Draupnir's combined ledger) it finds the best
BUY-min market and the best AUTOBUY (buy-order / instant-sell) market, nets the
sell fee, and ranks by profit % — exactly the shape of the deals Ivan flagged,
e.g. LisSkins (min) => CSMoney (autobuy) or CSMoney (min) => CSFloat (autobuy).

Autobuy is the priority: the sell leg is only ever a market that can be sold
INTO instantly (pulse 'Buy' price, or the CSFloat swept buy-orders), so a row is
always actionable, not a paper spread against a slow listing.

Two things are user-configurable (persisted in settings.json, edited from the UI):
  * which markets take part as BUY sources and as SELL (autobuy) targets, and
  * multi-hop CHAINS like LisSkins -> CSMoney -> CSFloat. A chain is an ordered
    list of markets; every adjacent pair is one buy(min)->autobuy leg, and the
    legs are shown together with a chain total (concatenated legs — each leg is
    its own trade, joined at the shared middle market).

Scanning is heavy (a pulse pull per non-cached market), so it is NON-BLOCKING: a
request serves the last cached result instantly and warms a fresh one in a
background thread (single-flight per config), fetching every market's price index
in PARALLEL through a small bounded pool. This mirrors
HuginnService.prices_for_valuation's "serve cache, warm in background". The buy
side reuses Draupnir's 1h-cached valuation maps for the common five markets, so it
adds ~no fresh pulls there; only extra markets and autobuy indexes pull live.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


class CrossArbitrageService:
    # Default markets if settings has none. BUY = min listing (what you'd pay);
    # SELL = autobuy / instant-sell (what you'd get). Registry ids.
    _DEFAULT_BUY = ['LisSkins', 'Buff', 'CsFloat', 'Dmarket', 'Steam']
    _DEFAULT_SELL = ['Buff', 'CsMoneyTrade', 'CsMoneyMarket', 'CsFloat']
    # Markets whose MIN listing is already cached for an hour by Draupnir's portfolio
    # valuation (HuginnService._PRICE_MARKETS). Reusing those maps keeps the buy side
    # almost free; any other buy market falls back to a fresh pulse pull.
    _PRICE_MAP_SLUG = {'LisSkins': 'lisskins', 'Buff': 'buff', 'CsFloat': 'csfloat',
                       'Dmarket': 'dmarket', 'Steam': 'steam'}
    _RESULT_TTL = 10 * 60      # seconds a warmed scan is served before re-warming
    _ALL_MODE_CAP = 300        # max rows when not restricted to owned items
    _CACHE_CAP = 8             # distinct (mode, config) results kept warm at once
    # Pulse throttles hard under many concurrent big pulls, so cap concurrency low
    # and let the warm run in the background rather than bursting.
    _MAX_WORKERS = 3

    def __init__(self, huginn_service, draupnir_service):
        self.huginn = huginn_service
        self.draupnir = draupnir_service
        self._cache = {}        # signature -> (ts, base_result)
        self._warming = {}      # signature -> bool (single-flight guard)
        self._lock = threading.Lock()
        self._autobuy_cache = None   # cached set of autobuy-capable market ids

    # ---- config -----------------------------------------------------------

    def _valid_ids(self):
        return self.huginn.market_ids()

    def _autobuy_ids(self):
        if self._autobuy_cache is None:
            self._autobuy_cache = {m['id'] for m in self.huginn.market_registry()
                                   if m.get('hasAutobuy')}
        return self._autobuy_cache

    def _clean_ids(self, ids, default, autobuy_only=False):
        """Keep only known market ids (optionally autobuy-capable), de-duplicated and
        in order. A missing / non-list value falls back to `default`."""
        valid = self._valid_ids()
        auto = self._autobuy_ids()
        if not isinstance(ids, list):
            ids = list(default)
        out = []
        for x in ids:
            if not isinstance(x, str) or x not in valid or x in out:
                continue
            if autobuy_only and x not in auto:
                continue
            out.append(x)
        return out

    def _clean_chains(self, chains):
        """Validate user chains: each keeps only known market ids, needs >= 2 markets,
        gets a stable id and a display name. Returns a clean [{id, name, markets}]."""
        if not isinstance(chains, list):
            return []
        valid = self._valid_ids()
        out, seen = [], set()
        for c in chains:
            if not isinstance(c, dict):
                continue
            markets = [m for m in (c.get('markets') or []) if isinstance(m, str) and m in valid]
            if len(markets) < 2:
                continue
            cid = str(c.get('id') or '').strip() or 'chain-%d' % (len(out) + 1)
            while cid in seen:
                cid = '%s-%d' % (cid, len(out))
            seen.add(cid)
            name = str(c.get('name') or '').strip() or \
                ' → '.join(self.huginn.market_display(m) for m in markets)
            out.append({'id': cid, 'name': name, 'markets': markets})
        return out

    def _config_from_settings(self, settings):
        s = settings or {}
        buy = self._clean_ids(s.get('cross_arb_buy_markets'), self._DEFAULT_BUY)
        sell = self._clean_ids(s.get('cross_arb_sell_markets'), self._DEFAULT_SELL, autobuy_only=True)
        chains = self._clean_chains(s.get('cross_arb_chains'))
        return buy, sell, chains

    def _available_markets(self):
        return [{'id': m['id'], 'display': m['display'], 'hasAutobuy': bool(m.get('hasAutobuy'))}
                for m in self.huginn.market_registry()]

    def config(self, settings):
        """Current market/chain config + the full market list the UI toggles from."""
        buy, sell, chains = self._config_from_settings(settings)
        return {'buy_markets': buy, 'sell_markets': sell, 'chains': chains,
                'available': self._available_markets()}

    def save_config(self, body, settings_manager):
        """Validate + persist the buy/sell markets and chains, then bust the warmed
        caches so the next scan recomputes with the new config."""
        buy = self._clean_ids((body or {}).get('buy_markets'), self._DEFAULT_BUY)
        sell = self._clean_ids((body or {}).get('sell_markets'), self._DEFAULT_SELL, autobuy_only=True)
        chains = self._clean_chains((body or {}).get('chains'))
        settings_manager.save_settings({
            'cross_arb_buy_markets': buy,
            'cross_arb_sell_markets': sell,
            'cross_arb_chains': chains,
        })
        with self._lock:
            self._cache.clear()
            self._warming.clear()
        return {'buy_markets': buy, 'sell_markets': sell, 'chains': chains,
                'available': self._available_markets()}

    # ---- public (non-blocking) --------------------------------------------

    def scan(self, token, owned_only=True, min_profit_pct=None, settings=None):
        """Serve the cached scan for this (mode, config) instantly and warm a fresh one
        in the background if cold/stale. `min_profit_pct` is applied to the cached rows
        (and chain totals) at request time, so changing the filter never re-fetches."""
        buy, sell, chains = self._config_from_settings(settings)
        sig = (bool(owned_only), tuple(buy), tuple(sell),
               tuple((c['id'], tuple(c['markets'])) for c in chains))
        base, status = self._base(token, sig, (buy, sell, chains), settings)

        rows = list((base or {}).get('rows') or [])
        chains_out = list((base or {}).get('chains') or [])
        if min_profit_pct is not None:
            rows = [r for r in rows
                    if r['profit_pct'] is not None and r['profit_pct'] >= min_profit_pct]
            chains_out = [self._filter_chain(ch, min_profit_pct) for ch in chains_out]

        owned = (base or {}).get('owned') or {}
        return {
            'rows': rows,
            'chains': chains_out,
            'status': status,                       # no_token | warming | refreshing | fresh
            'summary': self._summary(rows, owned),
            'markets': (base or {}).get('markets') or {'buy': [], 'sell': []},
            'csfloat': self._csfloat_status(),
            'owned_only': bool(owned_only),
            'min_profit_pct': min_profit_pct,
            'generated_at': (base or {}).get('generated_at'),
        }

    def _filter_chain(self, chain, min_profit_pct):
        crows = [r for r in chain['rows']
                 if r['total_profit_pct'] is not None and r['total_profit_pct'] >= min_profit_pct]
        return {**chain, 'rows': crows, 'summary': self._chain_summary(crows)}

    def _base(self, token, sig, config, settings):
        with self._lock:
            cached = self._cache.get(sig)
        if not token:
            return (cached[1] if cached else None), 'no_token'
        if cached and (time.time() - cached[0]) < self._RESULT_TTL:
            return cached[1], 'fresh'
        # cold or stale → warm in the background (single-flight per config)
        with self._lock:
            if not self._warming.get(sig):
                self._warming[sig] = True
                threading.Thread(target=self._warm, args=(token, sig, config, settings), daemon=True).start()
        return (cached[1] if cached else None), ('refreshing' if cached else 'warming')

    def _warm(self, token, sig, config, settings):
        started = time.time()
        logger.info('[CROSS-ARB] warm start (owned_only=%s, buy=%d, sell=%d, chains=%d)',
                    sig[0], len(config[0]), len(config[1]), len(config[2]))
        try:
            base = self._compute(token, owned_only=sig[0], config=config, settings=settings)
            with self._lock:
                self._cache[sig] = (time.time(), base)
                # keep only the most recent few configs warm
                if len(self._cache) > self._CACHE_CAP:
                    oldest = sorted(self._cache.items(), key=lambda kv: kv[1][0])[:-self._CACHE_CAP]
                    for k, _ in oldest:
                        self._cache.pop(k, None)
            logger.info('[CROSS-ARB] warmed %d rows + %d chains in %.0fs',
                        len(base['rows']), len(base['chains']), time.time() - started)
        except Exception as e:
            logger.error('[CROSS-ARB] warm failed after %.0fs: %s', time.time() - started, e)
        finally:
            with self._lock:
                self._warming[sig] = False

    # ---- holdings ----------------------------------------------------------

    def _owned(self):
        """(owned_qty, holders): owned_qty={item_name: net units held across all
        accounts} (arb legs excluded); holders={item_name:[{account,qty},...]}."""
        ledger = self.draupnir.combined_ledger()
        owned = {h['item_name']: h['net_qty']
                 for h in (ledger.get('holdings') or []) if h.get('net_qty', 0) > 0}
        per = {}   # item_name -> {account: net_qty}
        for t in (ledger.get('transactions') or []):
            if t.get('is_arbitrage'):
                continue
            name = t.get('item_name')
            if not name:
                continue
            qty = t.get('qty') or 0
            if t.get('type') == 'sell':
                qty = -qty
            per.setdefault(name, {})
            acct = t.get('account') or '?'
            per[name][acct] = per[name].get(acct, 0) + qty
        holders = {}
        for name, accts in per.items():
            rows = [{'account': a, 'qty': q} for a, q in accts.items() if q > 0]
            rows.sort(key=lambda r: r['qty'], reverse=True)
            if rows:
                holders[name] = rows
        return owned, holders

    # ---- heavy compute (background) ---------------------------------------

    def _compute(self, token, owned_only, config=None, settings=None):
        if config is None:
            config = self._config_from_settings(settings)
        buy_markets, sell_markets, chains = config
        owned, holders = self._owned()

        # Every market that is used as a buy source (config buys + every non-last hop
        # of a chain) and as an autobuy target (config sells + every non-first hop).
        buy_needed = set(buy_markets)
        sell_needed = set(sell_markets)
        for ch in chains:
            buy_needed |= set(ch['markets'][:-1])
            sell_needed |= set(ch['markets'][1:])
        buy_maps, sell_idx = self._fetch_indexes(token, buy_needed, sell_needed)
        fees = {mid: self.huginn.market_fee(mid, settings) for mid in sell_needed}

        if owned_only:
            names = set(owned)
        else:
            names = set()
            for prices in buy_maps.values():
                names |= set(prices)

        rows = [self._best_row(name, buy_markets, sell_markets, buy_maps, sell_idx, fees, owned, holders)
                for name in names]
        rows = [r for r in rows if r]
        rows.sort(key=lambda r: (r['profit_pct'] if r['profit_pct'] is not None else -1e9), reverse=True)
        if not owned_only:
            rows = rows[:self._ALL_MODE_CAP]

        chains_out = []
        for ch in chains:
            crows = [self._chain_row(ch, name, buy_maps, sell_idx, fees, owned, holders)
                     for name in names]
            crows = [r for r in crows if r]
            crows.sort(key=lambda r: (r['total_profit_pct'] if r['total_profit_pct'] is not None else -1e9),
                       reverse=True)
            if not owned_only:
                crows = crows[:self._ALL_MODE_CAP]
            chains_out.append({
                'id': ch['id'], 'name': ch['name'],
                'markets': [{'id': m, 'display': self.huginn.market_display(m)} for m in ch['markets']],
                'rows': crows,
                'summary': self._chain_summary(crows),
            })

        return {
            'rows': rows,
            'chains': chains_out,
            'owned': owned,
            'markets': self._market_status(buy_maps, sell_idx, buy_markets, sell_markets),
            'generated_at': int(time.time()),
        }

    def _best_row(self, name, buy_markets, sell_markets, buy_maps, sell_idx, fees, owned, holders):
        """Best single buy-min -> autobuy-sell route for one item, or None if it can't
        be both bought and instantly sold within the configured markets."""
        best_buy = None       # (market_id, price)
        for mid in buy_markets:
            price = (buy_maps.get(mid) or {}).get(name)
            if price and (best_buy is None or price < best_buy[1]):
                best_buy = (mid, price)
        best_sell = None      # (market_id, gross, net, fee, count)
        for mid in sell_markets:
            entry = (sell_idx.get(mid) or {}).get(name)
            if not entry:
                continue
            net = entry['price'] * (1 - fees.get(mid, 0.0))
            if best_sell is None or net > best_sell[2]:
                best_sell = (mid, entry['price'], net, fees.get(mid, 0.0), entry.get('count'))
        if not best_buy or not best_sell:
            return None
        buy_price, net = best_buy[1], best_sell[2]
        profit = net - buy_price
        owned_qty = owned.get(name, 0)
        return {
            'item_name': name,
            'buy_market_display': self.huginn.market_display(best_buy[0]), 'buy_price': round(buy_price, 2),
            'sell_market_display': self.huginn.market_display(best_sell[0]), 'sell_gross': round(best_sell[1], 2),
            'sell_fee': best_sell[3], 'sell_net': round(net, 2), 'sell_count': best_sell[4],
            'profit': round(profit, 3),
            'profit_pct': round(profit / buy_price * 100, 2) if buy_price else None,
            'owned_qty': owned_qty,
            'holders': holders.get(name, []),
            'potential_profit': round(profit * owned_qty, 2) if owned_qty else 0.0,
        }

    def _chain_row(self, chain, name, buy_maps, sell_idx, fees, owned, holders):
        """One item's legs down a chain: each adjacent (buy market, sell market) pair is
        a buy(min)->autobuy(net) leg. Legs that aren't priced on both ends are None.
        Returns None if not a single leg is priced for this item."""
        markets = chain['markets']
        legs, total_profit, total_cost, priced = [], 0.0, 0.0, 0
        for i in range(len(markets) - 1):
            buy_mid, sell_mid = markets[i], markets[i + 1]
            buy_price = (buy_maps.get(buy_mid) or {}).get(name)
            entry = (sell_idx.get(sell_mid) or {}).get(name)
            if not buy_price or not entry:
                legs.append(None)
                continue
            fee = fees.get(sell_mid, 0.0)
            net = entry['price'] * (1 - fee)
            profit = net - buy_price
            legs.append({
                'buy_market_display': self.huginn.market_display(buy_mid), 'buy_price': round(buy_price, 2),
                'sell_market_display': self.huginn.market_display(sell_mid), 'sell_gross': round(entry['price'], 2),
                'sell_fee': fee, 'sell_net': round(net, 2), 'sell_count': entry.get('count'),
                'profit': round(profit, 3),
                'profit_pct': round(profit / buy_price * 100, 2) if buy_price else None,
            })
            total_profit += profit
            total_cost += buy_price
            priced += 1
        if not priced:
            return None
        owned_qty = owned.get(name, 0)
        return {
            'item_name': name, 'owned_qty': owned_qty, 'holders': holders.get(name, []),
            'legs': legs,
            'total_profit': round(total_profit, 3),
            'total_profit_pct': round(total_profit / total_cost * 100, 2) if total_cost else None,
            'potential_profit': round(total_profit * owned_qty, 2) if owned_qty else 0.0,
        }

    def _buy_index(self, token, market_id):
        """{name: price} of a market's MIN listing. Reuses Draupnir's 1h-cached
        valuation map for the common five markets; otherwise a fresh pulse pull."""
        slug = self._PRICE_MAP_SLUG.get(market_id)
        if slug:
            return self.huginn.price_map(token, slug)
        idx = self.huginn.market_buy_index(token, market_id)
        return {n: v['price'] for n, v in idx.items() if v.get('price')}

    def _fetch_indexes(self, token, buy_needed, sell_needed):
        """Build the buy price maps ({name: price}) and autobuy indexes
        ({name: {price,...}}) for exactly the markets needed, through ONE small pool
        so we never burst many huge pulls at once."""
        tasks = [('buy', m) for m in sorted(buy_needed)] + [('sell', m) for m in sorted(sell_needed)]

        def fetch(kind, mid):
            try:
                if kind == 'buy':
                    return self._buy_index(token, mid)
                return self.huginn.market_autobuy_index(token, mid)
            except Exception as e:
                logger.warning('[CROSS-ARB] %s index failed for %s: %s', kind, mid, e)
                return {}

        results = {}
        with ThreadPoolExecutor(max_workers=self._MAX_WORKERS) as pool:
            futures = {pool.submit(fetch, kind, mid): (kind, mid) for kind, mid in tasks}
            for fut in as_completed(futures):
                results[futures[fut]] = fut.result()

        buy_maps = {m: results.get(('buy', m)) or {} for m in buy_needed}
        sell_idx = {m: results.get(('sell', m)) or {} for m in sell_needed}
        return buy_maps, sell_idx

    # ---- helpers -----------------------------------------------------------

    def _summary(self, rows, owned):
        profitable = [r for r in rows if (r['profit_pct'] or 0) > 0]
        return {
            'rows': len(rows),
            'profitable': len(profitable),
            'owned_items_total': len(owned),
            'best_profit_pct': max((r['profit_pct'] for r in rows if r['profit_pct'] is not None), default=None),
            'total_potential_profit': round(sum(r['potential_profit'] for r in rows if r['potential_profit'] > 0), 2),
        }

    def _chain_summary(self, rows):
        profitable = [r for r in rows if (r['total_profit_pct'] or 0) > 0]
        return {
            'rows': len(rows),
            'profitable': len(profitable),
            'best_profit_pct': max((r['total_profit_pct'] for r in rows if r['total_profit_pct'] is not None), default=None),
            'total_potential_profit': round(sum(r['potential_profit'] for r in rows if r['potential_profit'] > 0), 2),
        }

    def _market_status(self, buy_maps, sell_idx, buy_markets, sell_markets):
        return {
            'buy': [{'id': m, 'display': self.huginn.market_display(m), 'count': len(buy_maps.get(m) or {})}
                    for m in buy_markets],
            'sell': [{'id': m, 'display': self.huginn.market_display(m), 'count': len(sell_idx.get(m) or {})}
                     for m in sell_markets],
        }

    def _csfloat_status(self):
        cache = self.huginn.get_csfloat_buy_orders_cache() or {}
        return {
            'have': bool(cache.get('by_name')),
            'count': cache.get('count'),
            'updated_at': cache.get('updated_at'),
            'complete': cache.get('complete'),
        }
