"""Gjallarhorn — the event-rotation cockpit.

When Valve limits an Active-Duty case/collection (a supply shock that pumps the
item fast), the play is to dump dead/deflated holdings and rotate the capital
into the freshly-limited item before the spike finishes. This service assembles
the one screen that makes that decision fast:

* the **sell list** — each held item joined with its average cost + current
  price (Draupnir), its Steam turnover/spread (SteamMarketService), and a live
  "tradable now vs locked" overlay (Ratatoskr inventory), scored so the most
  liquid dead capital surfaces first;
* the **market whitelist** — which markets do not lock your balance after a
  sale, so proceeds can be redeployed immediately (persisted in settings);
* the **target basket** — the freshly-limited item(s) priced, with how many your
  capital buys;
* **inventory readiness** — free storage-unit slots for bulk-buying cheap cases.

It is read-mostly: it computes and advises, it never executes a trade.
"""
import logging
import math
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Storage Units hold up to 1000 items each (mirrors scheduler.STORAGE_CAPACITY
# and the frontend constant).
STORAGE_CAPACITY = 1000

# Steam Market liquidity is throttled and cached (see SteamMarketService), so we
# only enrich the holdings that actually matter for a rotation — the highest-value
# positions plus every deflated one — instead of every long-tail sticker. Bounds
# the one-time warm to a sane number of Steam calls.
_LIQUIDITY_TOP_BY_VALUE = 150
_LIQUIDITY_HARD_CAP = 250


class GjallarhornService:
    def __init__(self, draupnir_service, huginn_service, steam_market_service,
                 ratatoskr_service, steam_service):
        self.draupnir = draupnir_service
        self.huginn = huginn_service
        self.market = steam_market_service
        self.rat = ratatoskr_service
        self.steam = steam_service

    # ---- rotation sell list ------------------------------------------------

    def rotation(self, token, portfolio_id='combined', market='steam', steamid=None):
        """The sell list: held items scored for how well they fund a rotation.

        portfolio_id 'combined' merges every portfolio; otherwise one portfolio.
        steamid (optional) overlays a connected account's live tradable status."""
        prices, pricing = self.huginn.prices_for_valuation(token, market or 'steam')
        ledger = self._ledger(portfolio_id, prices)
        holdings = [h for h in (ledger.get('holdings') or [])
                    if (h.get('net_qty') or 0) > 0]
        names = [h['item_name'] for h in holdings]
        transactions = ledger.get('transactions') or []
        platforms = self._buy_platforms(transactions)
        # Which account holds each item — only meaningful for the combined book;
        # a single portfolio is itself the holder (its own name).
        holders = self._holders(transactions) if portfolio_id in (None, '', 'combined') else {}
        default_holder = None if portfolio_id in (None, '', 'combined') else ledger.get('name')
        liquidity, liquidity_status = self.market.liquidity(self._liquidity_names(holdings))
        overlay = self._tradable_overlay(steamid, names) if steamid else {}

        rows = [self._row(h, liquidity.get(h['item_name']), overlay.get(h['item_name']),
                          platforms.get(h['item_name']),
                          holders.get(h['item_name'], default_holder))
                for h in holdings]
        rows.sort(key=lambda r: (r['rotation_score'] is None, -(r['rotation_score'] or 0)))
        return {
            'rows': rows,
            'summary': self._summary(rows),
            'pricing': pricing,
            'liquidity': liquidity_status,
            'priced': prices is not None,
            'market': market,
            'portfolio': portfolio_id,
            'overlayAccount': steamid,
        }

    @staticmethod
    def _liquidity_names(holdings):
        """Which held items to pull Steam liquidity for: the highest-value
        positions plus every deflated one, capped. Priced items only (nothing to
        rank on a cold price cache — liquidity fills in once prices warm)."""
        def market_value(holding):
            price = holding.get('current_price')
            return (price * (holding.get('net_qty') or 0)) if price is not None else 0.0

        priced = [h for h in holdings if h.get('current_price') is not None]
        priced.sort(key=market_value, reverse=True)
        selected = {h['item_name'] for h in priced[:_LIQUIDITY_TOP_BY_VALUE]}
        for holding in priced[_LIQUIDITY_TOP_BY_VALUE:]:
            if len(selected) >= _LIQUIDITY_HARD_CAP:
                break
            avg = holding.get('avg_cost')
            if avg and holding['current_price'] < avg:  # deflated matters even if low value
                selected.add(holding['item_name'])
        return list(selected)

    def _ledger(self, portfolio_id, prices):
        """The full Draupnir view (holdings + transactions) for one portfolio or
        the combined book — one call, so we get buy platforms alongside holdings."""
        if portfolio_id in (None, '', 'combined'):
            return self.draupnir.combined_ledger(prices) or {}
        return self.draupnir.get_portfolio(portfolio_id, prices) or {}

    @staticmethod
    def _buy_platforms(transactions):
        """{item_name: 'lisskins, steam'} — where each item was bought, from the
        buy legs, ordered by quantity (the platform most of it came from first)."""
        tally = {}
        for txn in transactions:
            if txn.get('type') != 'buy':
                continue
            name = txn.get('item_name')
            platform = (txn.get('platform') or '').strip()
            if not name or not platform:
                continue
            tally.setdefault(name, {})
            tally[name][platform] = tally[name].get(platform, 0) + (txn.get('qty') or 0)
        return {
            name: ', '.join(p for p, _ in sorted(platforms.items(), key=lambda kv: -kv[1]))
            for name, platforms in tally.items()
        }

    @staticmethod
    def _holders(transactions):
        """{item_name: 'everthinklol, kit_bonilla'} — accounts (portfolio names)
        with a positive net position in the item, most-held first. Uses the
        per-leg 'account' the combined ledger attaches; empty for a single view."""
        net = {}
        for txn in transactions:
            name = txn.get('item_name')
            account = txn.get('account')
            if not name or not account:
                continue
            quantity = txn.get('qty') or 0
            if txn.get('type') == 'buy':
                delta = quantity
            elif txn.get('type') == 'sell':
                delta = -quantity
            else:
                continue
            net.setdefault(name, {})
            net[name][account] = net[name].get(account, 0) + delta
        out = {}
        for name, accounts in net.items():
            holders = sorted(((a, q) for a, q in accounts.items() if q > 0), key=lambda kv: -kv[1])
            if holders:
                out[name] = ', '.join(a for a, _ in holders)
        return out

    def _row(self, holding, liquidity, overlay, buy_platform=None, holder=None):
        avg = holding.get('avg_cost')
        current = holding.get('current_price')
        qty = holding.get('net_qty') or 0

        deflation_pct = None
        realized_if_sold = None
        if avg and current is not None:
            deflation_pct = round((current - avg) / avg * 100, 2)
            realized_if_sold = round((current - avg) * qty, 2)

        liquidity = liquidity or {}
        volume7d = liquidity.get('volume7d')
        volume24h = liquidity.get('volume24h')
        # Prefer real 7-day turnover; fall back to 24h projected across a week.
        if volume7d is not None:
            liquidity_score = volume7d
        elif volume24h is not None:
            liquidity_score = volume24h * 7
        else:
            liquidity_score = None

        return {
            'item_name': holding['item_name'],
            'buy_platform': buy_platform,
            'holder': holder,
            'net_qty': qty,
            'avg_cost': avg,
            'current_price': current,
            'market_value': holding.get('market_value'),
            'unrealized_pl': holding.get('unrealized_pl'),
            'deflation_pct': deflation_pct,
            'deflated': bool(deflation_pct is not None and deflation_pct < 0),
            'realized_if_sold': realized_if_sold,
            'volume24h': volume24h,
            'volume7d': volume7d,
            'volume30d': liquidity.get('volume30d'),
            'trend7d_pct': liquidity.get('trend7dPct'),
            'spread_pct': liquidity.get('spreadPct'),
            'steam_lowest': liquidity.get('lowest'),
            'steam_median': liquidity.get('median'),
            'liquidity_score': liquidity_score,
            'tradable': overlay,
            'rotation_score': self._score(liquidity_score, deflation_pct, overlay),
        }

    @staticmethod
    def _score(liquidity_score, deflation_pct, overlay):
        """0-100 convenience rank: mostly turnover, nudged up by how deflated the
        holding is (dead money is more rotate-worthy), and heavily discounted when
        the item is held but currently trade-locked (can't fund the rotation now)."""
        if liquidity_score is None:
            return None
        # log-scaled turnover: ~1.0 around 1000 sold/week.
        liquidity = min(1.0, math.log10(liquidity_score + 1) / 3.0)
        deflation = 0.0
        if deflation_pct is not None and deflation_pct < 0:
            deflation = min(1.0, abs(deflation_pct) / 50.0)
        score = 0.7 * liquidity + 0.3 * deflation
        if overlay and overlay.get('inInventory') and not overlay.get('tradableNow'):
            score *= 0.25  # locked → can't sell it right now
        return round(score * 100, 1)

    def _summary(self, rows):
        deflated = [r for r in rows if r['deflated']]

        def value(row):
            return row.get('market_value') or 0

        def usable_now(row):
            overlay = row.get('tradable')
            # Unknown (no overlay) counts as usable; only a held-but-locked item is excluded.
            return not (overlay and overlay.get('inInventory') and not overlay.get('tradableNow'))

        return {
            'holdings': len(rows),
            'deflated': len(deflated),
            'totalValue': round(sum(value(r) for r in rows), 2),
            'deflatedValue': round(sum(value(r) for r in deflated), 2),
            'liquidatableNow': round(sum(value(r) for r in rows if usable_now(r)), 2),
        }

    # ---- tradable-now overlay ---------------------------------------------

    def _tradable_overlay(self, steamid, names):
        """Live per-name tradable/locked counts from a connected account's inventory.

        Joins by item name (the ledger has no live-inventory link), so it is an
        overlay, not an exact per-lot match. Empty when the account has no live
        Ratatoskr session."""
        inventory = self.rat.get_inventory(steamid)
        if not isinstance(inventory, dict) or not isinstance(inventory.get('items'), list):
            return {}
        now = datetime.now(timezone.utc)
        by_name = {}
        for item in inventory['items']:
            name = item.get('item_name')
            if not name:
                continue
            slot = by_name.setdefault(name, {
                'inInventory': 0, 'tradableNow': 0, 'locked': 0, 'nextUnlock': None})
            slot['inInventory'] += 1
            unlock = self._parse_unlock(item.get('trade_unlock'))
            if unlock and unlock > now:
                slot['locked'] += 1
                if slot['nextUnlock'] is None or unlock < slot['nextUnlock']:
                    slot['nextUnlock'] = unlock
            else:
                slot['tradableNow'] += 1
        wanted = set(names)
        return {
            name: {**slot, 'nextUnlock': slot['nextUnlock'].isoformat() if slot['nextUnlock'] else None}
            for name, slot in by_name.items() if name in wanted
        }

    @staticmethod
    def _parse_unlock(value):
        if not value:
            return None
        try:
            when = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    # ---- inventory readiness ----------------------------------------------

    def readiness(self, steamid):
        """Free storage-unit slots + loose-inventory count for bulk-buying cheap cases."""
        caskets = self.rat.get_caskets(steamid)
        inventory = self.rat.get_inventory(steamid)
        units = []
        free_total = 0
        casket_list = []
        if isinstance(caskets, dict):
            casket_list = caskets.get('items') or caskets.get('caskets') or []
        for casket in casket_list:
            used = int(casket.get('item_storage_total') or 0)
            free = max(0, STORAGE_CAPACITY - used)
            free_total += free
            units.append({
                'id': str(casket.get('item_id') or ''),
                'name': casket.get('item_customname') or casket.get('item_name') or 'Storage Unit',
                'used': used,
                'free': free,
            })
        loose = len(inventory['items']) if isinstance(inventory, dict) and isinstance(inventory.get('items'), list) else None
        return {
            'storageUnits': units,
            'storageFree': free_total,
            'looseItems': loose,
            'looseCapacity': STORAGE_CAPACITY,
            'error': (caskets.get('error') if isinstance(caskets, dict) else None),
        }

    # ---- target basket -----------------------------------------------------

    def basket(self, token, targets, capital=None, market='steam'):
        """Price each target item and, given a capital amount, how many it buys."""
        prices, pricing = self.huginn.prices_for_valuation(token, market or 'steam')
        prices = prices or {}
        rows = []
        for name in targets:
            price = prices.get(name)
            rows.append({
                'item_name': name,
                'price': price,
                'unitsForCapital': (int(capital // price) if (capital and price) else None),
            })
        return {'rows': rows, 'capital': capital, 'pricing': pricing,
                'priced': bool(prices), 'market': market}

    # ---- settings cleaners (whitelist + targets) ---------------------------

    @staticmethod
    def clean_holds(holds):
        """Sanitise the instant-redeploy market whitelist for persistence."""
        out = []
        seen = set()
        for entry in holds:
            if not isinstance(entry, dict):
                continue
            display = str(entry.get('display') or entry.get('id') or '').strip()
            if not display or display.lower() in seen:
                continue
            seen.add(display.lower())
            try:
                hold_days = max(0, int(entry.get('holdDays')))
            except (TypeError, ValueError):
                hold_days = 0
            out.append({
                'id': str(entry.get('id') or display).strip(),
                'display': display,
                'holdDays': hold_days,
                'instantRedeploy': bool(entry.get('instantRedeploy')),
                'notes': str(entry.get('notes') or '')[:200],
            })
            if len(out) >= 100:
                break
        return out

    @staticmethod
    def clean_targets(targets):
        """Sanitise the target basket for persistence."""
        out = []
        seen = set()
        for entry in targets:
            name = entry.get('name') if isinstance(entry, dict) else entry
            name = str(name or '').strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            out.append({'name': name[:200]})
            if len(out) >= 50:
                break
        return out
