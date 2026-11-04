from flask import Flask, jsonify
import atexit
import logging
import os
import signal
import sys
from steam_service import SteamService
from settings import SettingsManager
from scheduler import ConfirmationScheduler
from ratatoskr_service import RatatoskrService
from jobs import JobRegistry
from storage_deposit import DepositService
from storage_rename import RenameService
from huginn_service import CASE_ALERTS_JOB, CASE_FULL_JOB, HuginnService
from notifications import notification_channel
from pulse_views import apply_active_settings
from draupnir_service import DraupnirService
from draupnir_backup_service import BackupService
from mimir_service import MimirService
from steam_market_service import SteamMarketService
from gjallarhorn_service import GjallarhornService
from gjallarhorn_news_service import GjallarhornNewsService
from gjallarhorn_reminders import ResellReminderService
from cross_arbitrage_service import CrossArbitrageService
from harvest_service import HarvestService
from card_deals_service import CardDealsService
from asf_service import AsfService
from team_fortress_service import TeamFortressService
from card_seller_service import CardSellerService
from store_purchase_service import StorePurchaseService
from market_access_service import MarketAccessService
from storage_shop_service import StorageShopService
from store_arbitrage_service import STORE_JOB, StoreArbitrageService
from store_boost_service import BOOST_JOB, StoreBoostService
from store_catalogue_service import StoreCatalogueService
from morning_routine import MorningRoutine
from telegram_caller import TelegramCaller
from logging_setup import setup_logging
import error_handlers
import request_guard
from context import ctx
from routes import register_blueprints

# Configure logging before any service is constructed so their module loggers
# emit through the rotating file + console handlers from the first line.
setup_logging()
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Compact JSON, kept explicit (Flask indents it in debug mode): a Huginn profile answer is
# ~23,000 rows, and indentation makes it about a third bigger and slower to send.
app.json.compact = True
# CORS only for the Heimdall frontend + refuse foreign Host headers (DNS
# rebinding): no website open in the browser can read this login-less API.
request_guard.install(app)

settings_manager = SettingsManager()
steam_service = SteamService()
ratatoskr_service = RatatoskrService()
jobs = JobRegistry()
# Every Storage Unit move (Transfer, Auto-Store, the command line) goes through it.
deposit_service = DepositService(ratatoskr_service, jobs)
# Nothing logs an account out of Ratatoskr between two batches of a move run.
ratatoskr_service.moves_in_progress = deposit_service.busy
# Rename Storage Unit tab; never renames while a move runs (the unit's id changes).
rename_service = RenameService(ratatoskr_service, deposit_service.busy)
huginn_service = HuginnService(steam_service, ratatoskr_service)
# Every fee comes from the one Fees editor (settings huginn_market_fees).
huginn_service.settings_provider = settings_manager.get_settings
# The active Huginn profiles: the only tables the keeper thread below downloads.
apply_active_settings(huginn_service, settings_manager.get_settings())
# Today's price history samples are saved at shutdown too: a docker stop / `make reload`
# exits through stop_on_sigterm, so atexit runs. A hard kill (crash, Docker's timeout) skips
# it, so the history also saves every minute (price_history.FLUSH_SECONDS).
atexit.register(huginn_service.history.flush)
draupnir_service = DraupnirService(huginn_service)
# Draupnir point-in-time backups: snapshot portfolios.json on every change +
# once daily, with GFS retention and safe restore.
draupnir_backup = BackupService(draupnir_service.path)
draupnir_service.set_backup(draupnir_backup)
scheduler = ConfirmationScheduler(settings_manager, steam_service, ratatoskr_service, deposit_service)
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
gjallarhorn_reminders = ResellReminderService(settings_manager)
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
# Andvari card auto-sell: dropped cards listed at the highest price that still sells (off by default).
card_seller_service = CardSellerService(settings_manager, steam_service, asf_service)
# Andvari "Buy games": plan + buy a card-deal game on many accounts (wallet checkout, price-guarded).
store_purchase_service = StorePurchaseService(steam_service, card_deals_service, asf_service,
                                              settings_provider=settings_manager.get_settings)
# Market access (Mímir's button; the Dashboard shows the result): when each account loses the Steam Community Market
# (a purchase older than 7 days and under a year keeps it open; read-only), and the Market unlocker
# (plan = a dry run -> execute once -> a job, followed at GET /api/jobs/<id>).
market_access_service = MarketAccessService(steam_service, card_deals_service, jobs=jobs)
# Ratatoskr "Storage shop": buy Counter-Strike 2 Storage Units on many accounts through the
# game store (Game Coordinator transaction, approved with the account's web session).
storage_shop_service = StorageShopService(steam_service, ratatoskr_service, card_deals_service)
# Store Catalogue: every item the game store sells, with its price (read-only). It takes
# every price sheet the storage shop reads.
store_catalogue_service = StoreCatalogueService(ratatoskr_service, storage_shop_service)
# Store Catalogue Arbitrage (the catalogue's Arbitrage tab): store items against what markets pay.
store_arbitrage_service = StoreArbitrageService(huginn_service, store_catalogue_service,
                                                storage_shop_service, draupnir_service)
# Store Boosts (the catalogue's Boosts tab): a Steam price boost of a store item or of a sticker
# inside a store capsule, caught on its first day; Telegram + a phone ring (read-only).
store_boost_service = StoreBoostService(huginn_service, store_catalogue_service, ratatoskr_service,
                                        steam_service, store_arbitrage_service, telegram_caller)
store_boost_service.set_settings_provider(lambda: settings_manager.get_settings())
# The Arbitrage tab prices the store items the store names differently under their market names.
store_arbitrage_service.market_names = store_boost_service.market_names

# Expose the singletons to the route blueprints (read from context.ctx at
# request time — see context.py and the routes/ package).
# The recurring jobs (slaves) that read pulse tables from memory, so the Pulse feed monitor can show them.
def _case_alerts_on():
    settings = settings_manager.get_settings()
    return bool(settings.get('tradeon_token') and settings.get('case_alerts_enabled')
                and notification_channel(settings) is not None)


huginn_service.pulse.declare_job(
    CASE_FULL_JOB, 'Case Arbitrage', 'every hour',
    lambda: huginn_service.case_job_tables(alerts=False),
    active=lambda: bool(settings_manager.get_settings().get('tradeon_token')))
huginn_service.pulse.declare_job(
    CASE_ALERTS_JOB, 'Case Arbitrage alerts',
    'after every keeper pass (from memory)',
    lambda: huginn_service.case_job_tables(alerts=True), active=_case_alerts_on,
    period=lambda: max(60, int(settings_manager.get_settings().get('case_poll_interval_sec') or 600)))
huginn_service.pulse.declare_job(
    STORE_JOB, 'Store Catalogue Arbitrage', 'every hour, and after the 08:00 Get all items',
    lambda: store_arbitrage_service.job_tables(settings_manager.get_settings()),
    active=lambda: bool(settings_manager.get_settings().get('tradeon_token')))
huginn_service.pulse.declare_job(
    BOOST_JOB, 'Store Boosts', 'every hour',
    store_boost_service.job_tables,
    active=lambda: bool(settings_manager.get_settings().get('tradeon_token')
                        and settings_manager.get_settings().get('store_boost_enabled', True)))

ctx.settings_manager = settings_manager
ctx.steam_service = steam_service
ctx.ratatoskr_service = ratatoskr_service
ctx.jobs = jobs
ctx.deposit_service = deposit_service
ctx.rename_service = rename_service
ctx.huginn_service = huginn_service
ctx.draupnir_service = draupnir_service
ctx.draupnir_backup = draupnir_backup
ctx.scheduler = scheduler
ctx.mimir_service = mimir_service
ctx.steam_market_service = steam_market_service
ctx.gjallarhorn_service = gjallarhorn_service
ctx.gjallarhorn_news_service = gjallarhorn_news_service
ctx.gjallarhorn_reminders = gjallarhorn_reminders
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
ctx.store_boost_service = store_boost_service
ctx.market_access_service = market_access_service
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
    # Ratatoskr anyway: read the store price sheet, re-price the Store Catalogue Arbitrage
    # markets and post the profitable store items to Telegram.
    after_scan=lambda: store_arbitrage_service.start_daily_refresh(settings_manager.get_settings),
)
ctx.morning_routine = morning_routine


def start_background():
    """Start every background loop, once. Called only by ``python app.py`` below:
    importing this module (a test, a script, ``python -c 'import app'``) builds
    the services but never starts a second set of loops next to the live server."""
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
    # Gjallarhorn: one Telegram text when a held item's trade lock ends (never rings).
    gjallarhorn_reminders.start()
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
    # Store Boosts: the hourly pulse check and the Steam price history reader (one item at a time).
    store_boost_service.start_background(lambda: settings_manager.get_settings())
    # Master pulse feed: keep the active profiles' tables (and the Buff163 reference) fresh.
    huginn_service.pulse.start_keeper(lambda: settings_manager.get_settings().get('tradeon_token', ''))


# Every error answers JSON with its real status (error_handlers.py).
error_handlers.install(app)

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

def stop_on_sigterm(signal_number, frame):
    """``docker stop`` / ``make reload`` send SIGTERM. As the container's first process,
    Python would ignore it (Docker then kills it 10 seconds later, skipping atexit).
    Exiting here ends at once and runs atexit (today's price history is saved)."""
    sys.exit(0)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    signal.signal(signal.SIGTERM, stop_on_sigterm)
    start_background()
    # One process, no debug mode and no auto-reloader: a saved file never restarts
    # the backend (it used to restart every background loop); `make reload` does.
    app.run(host='0.0.0.0', port=port, threaded=True, debug=False, use_reloader=False)
