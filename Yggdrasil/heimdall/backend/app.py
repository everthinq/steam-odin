from flask import Flask, jsonify
import logging
import os
from steam_service import SteamService
from settings import SettingsManager
from scheduler import ConfirmationScheduler
from ratatoskr_service import RatatoskrService
from huginn_service import HuginnService
from draupnir_service import DraupnirService
from draupnir_backup_service import BackupService
from mimir_service import MimirService
from steam_market_service import SteamMarketService
from gjallarhorn_service import GjallarhornService
from gjallarhorn_news_service import GjallarhornNewsService
from cross_arbitrage_service import CrossArbitrageService
from harvest_service import HarvestService
from card_deals_service import CardDealsService
from asf_service import AsfService
from team_fortress_service import TeamFortressService
from card_seller_service import CardSellerService
from store_purchase_service import StorePurchaseService
from storage_shop_service import StorageShopService
from store_arbitrage_service import StoreArbitrageService
from store_catalogue_service import StoreCatalogueService
from morning_routine import MorningRoutine
from telegram_caller import TelegramCaller
from logging_setup import setup_logging
import request_guard
from context import ctx
from routes import register_blueprints

# Configure logging before any service is constructed so their module loggers
# emit through the rotating file + console handlers from the first line.
setup_logging()
logger = logging.getLogger(__name__)

app = Flask(__name__)
# CORS only for the Heimdall frontend + refuse foreign Host headers (DNS
# rebinding): no website open in the browser can read this login-less API.
request_guard.install(app)

settings_manager = SettingsManager()
steam_service = SteamService()
ratatoskr_service = RatatoskrService()
huginn_service = HuginnService(steam_service, ratatoskr_service)
# Every fee comes from the one Fees editor (settings huginn_market_fees).
huginn_service.settings_provider = settings_manager.get_settings
draupnir_service = DraupnirService(huginn_service)
# Draupnir point-in-time backups: snapshot portfolios.json on every change +
# once daily, with GFS retention and safe restore.
draupnir_backup = BackupService(draupnir_service.path)
draupnir_service.set_backup(draupnir_backup)
scheduler = ConfirmationScheduler(settings_manager, steam_service, ratatoskr_service)
# Mimir: encrypted credential vault (login/password/email/comment), sharing the
# maFile encryption key. SteamService.get_password falls back to it by login.
mimir_service = MimirService(steam_service.storage)
# Gjallarhorn: event-rotation cockpit. SteamMarketService supplies Steam Market
# liquidity (volume/spread); GjallarhornService joins it with Draupnir holdings,
# Ratatoskr inventory (tradable-now), and the pulse price map.
steam_market_service = SteamMarketService(steam_service)
gjallarhorn_service = GjallarhornService(
    draupnir_service, huginn_service, steam_market_service,
    ratatoskr_service, steam_service)
# Telegram caller: rings a target from a burner user account (a bot can't call)
# for Gjallarhorn event alerts. No-op until telegram_caller.json is set up.
telegram_caller = TelegramCaller()
# Gjallarhorn news watcher (bullet 4): polls the official CS2 update feed and
# rings + texts when Valve adds/removes a case/collection/capsule/souvenir.
gjallarhorn_news_service = GjallarhornNewsService(settings_manager, telegram_caller)
# Cross-profile arbitrage: best buy-min -> autobuy-sell route per held item,
# pooled across all Draupnir accounts (Huginn pulse prices + Draupnir holdings).
cross_arbitrage_service = CrossArbitrageService(huginn_service, draupnir_service)
# Harvest: your holdings at their purchase price vs what autobuy markets pay now.
harvest_service = HarvestService(huginn_service, draupnir_service)
# Andvari card deals: games whose trading-card drops resell for more than the
# game costs, per account (owned games, regional price, remaining drops).
card_deals_service = CardDealsService(steam_service, settings_manager)
# ASF card farming: drives the ArchiSteamFarm container (hardened bot configs,
# password + Steam Guard code only when ASF asks, pause while Ratatoskr plays).
# Off until ASF_IPC_PASSWORD is set (make asf-setup).
asf_service = AsfService(steam_service, ratatoskr_service)
ratatoskr_service.before_login = asf_service.pause_for_ratatoskr
asf_service.card_deals = card_deals_service   # drop counts decide which bots run
# Team Fortress 2 case drops: watch the news for a newly added case, start ASF's
# Team Fortress 2 mode the same minute, and list the drops on the Market as they land.
team_fortress_service = TeamFortressService(settings_manager, steam_service, asf_service, telegram_caller)
# Andvari card auto-sell: dropped cards listed one cent under the lowest listing (off by default).
card_seller_service = CardSellerService(settings_manager, steam_service, asf_service)
# Andvari "Buy games": plan + buy a card-deal game on many accounts (wallet checkout, price-guarded).
store_purchase_service = StorePurchaseService(steam_service, card_deals_service, asf_service,
                                              settings_provider=settings_manager.get_settings)
# Ratatoskr "Storage shop": buy Counter-Strike 2 Storage Units on many accounts through the
# game store (Game Coordinator transaction, approved with the account's web session).
storage_shop_service = StorageShopService(steam_service, ratatoskr_service, card_deals_service)
# Store Catalogue: every item the game store sells, with its price (read-only). It takes
# every price sheet the storage shop reads.
store_catalogue_service = StoreCatalogueService(ratatoskr_service, storage_shop_service)
# Store Catalogue Arbitrage (the catalogue's Arbitrage tab): store items against what markets pay.
store_arbitrage_service = StoreArbitrageService(huginn_service, store_catalogue_service,
                                                storage_shop_service, draupnir_service)

# Expose the singletons to the route blueprints (read from context.ctx at
# request time — see context.py and the routes/ package).
ctx.settings_manager = settings_manager
ctx.steam_service = steam_service
ctx.ratatoskr_service = ratatoskr_service
ctx.huginn_service = huginn_service
ctx.draupnir_service = draupnir_service
ctx.draupnir_backup = draupnir_backup
ctx.scheduler = scheduler
ctx.mimir_service = mimir_service
ctx.steam_market_service = steam_market_service
ctx.gjallarhorn_service = gjallarhorn_service
ctx.gjallarhorn_news_service = gjallarhorn_news_service
ctx.cross_arbitrage_service = cross_arbitrage_service
ctx.harvest_service = harvest_service
ctx.telegram_caller = telegram_caller
ctx.card_deals_service = card_deals_service
ctx.asf_service = asf_service
ctx.team_fortress_service = team_fortress_service
ctx.card_seller_service = card_seller_service
ctx.store_purchase_service = store_purchase_service
ctx.storage_shop_service = storage_shop_service
ctx.store_catalogue_service = store_catalogue_service
ctx.store_arbitrage_service = store_arbitrage_service
register_blueprints(app)

# Morning routine: "Get all items" once a day at 08:00 (or the first minute the Mac
# is awake after it), then the CSFloat buy-order sweep. It uses
# the same guarded scan and sweep as the buttons (routes/huginn.py).
import routes.huginn as huginn_routes  # noqa: E402 (needs the blueprints registered)


def _morning_scan():
    try:
        huginn_routes.run_scan_exclusive()
        return True, 'ok'
    except Exception as e:   # includes ScanAlreadyRunning: retried later
        return False, str(e)


def _last_scan_at():
    from datetime import datetime
    stamp = (huginn_service.get_cache() or {}).get('scan_timestamp')
    try:
        return datetime.fromisoformat(stamp) if stamp else None
    except ValueError:
        return None


morning_routine = MorningRoutine(
    run_scan=_morning_scan,
    start_sweep=lambda: huginn_routes.start_csfloat_sweep()[:2],
    last_scan_at=_last_scan_at,
    sweep_state=huginn_routes.csfloat_sweep_state,
)
ctx.morning_routine = morning_routine


def _should_start_background_scheduler():
    """
    Start the scheduler in the process that actually serves requests.
    With FLASK debug + reloader, only the child has WERKZEUG_RUN_MAIN=true;
    the old guard skipped the child, so auto-confirm never ran in Docker dev.
    """
    if os.environ.get('FLASK_ENV') != 'development':
        return True
    return os.environ.get('WERKZEUG_RUN_MAIN') == 'true'


if _should_start_background_scheduler():
    scheduler.start()
    # Keep Case Arbitrage container prices warm: pull all markets from pulse hourly,
    # then fire alerts when a buy market is newly cheaper than CSFloat.
    huginn_service.start_container_refresh(
        lambda: settings_manager.get_settings())
    # Draupnir: recurring daily portfolio backups (boot snapshot + daily + prune).
    draupnir_backup.start_daily_loop()
    # LOOT.Farm auctions: snapshot the feed every 15 min to build the per-lot history
    # the backtest reads (bids, clear prices, snipe references).
    huginn_service.start_auction_tracker(lambda: settings_manager.get_settings())
    # Gjallarhorn: watch the CS2 update feed for case/collection limiting events.
    gjallarhorn_news_service.start()
    # Andvari: rescan card deals on the configured interval (sale scope first).
    card_deals_service.start_background()
    # ASF: provision bots, assist logins, resume bots after Ratatoskr sessions.
    asf_service.start_background()
    # Team Fortress 2: release watcher + auto-sell of the new case's drops.
    team_fortress_service.start()
    # Andvari: list dropped trading cards on the Market (only while switched on).
    card_seller_service.start()
    # Daily "Get all items" + CSFloat sweep (catches up after the Mac slept).
    morning_routine.start_background()
    # Store Catalogue Arbitrage: warm the default markets hourly so the price history grows.
    store_arbitrage_service.start_background(lambda: settings_manager.get_settings())

# Ensure all errors return JSON, not HTML
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500

@app.errorhandler(Exception)
def handle_exception(e):
    return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@app.route('/health', methods=['GET'])
def health_check():
    settings = settings_manager.get_settings()
    return jsonify({
        "status": "healthy",
        "scheduler": {
            "running": bool(scheduler.thread and scheduler.thread.is_alive()),
            "polling": ConfirmationScheduler._should_poll(settings),
            "interval_sec": settings.get("check_interval"),
        },
    }), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # Disable debug in production!
    debug_mode = os.environ.get('FLASK_ENV') == 'development'
    app.run(debug=debug_mode, host='0.0.0.0', port=port)
