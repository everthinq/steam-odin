from flask import Flask, jsonify
from flask_cors import CORS
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
from telegram_caller import TelegramCaller
from logging_setup import setup_logging
from context import ctx
from routes import register_blueprints

# Configure logging before any service is constructed so their module loggers
# emit through the rotating file + console handlers from the first line.
setup_logging()
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

settings_manager = SettingsManager()
steam_service = SteamService()
ratatoskr_service = RatatoskrService()
huginn_service = HuginnService(steam_service, ratatoskr_service)
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
ctx.telegram_caller = telegram_caller
register_blueprints(app)


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
    # then fire LisSkins/Buff-cheaper-than-CSFloat alerts on new crossings.
    huginn_service.start_container_refresh(
        lambda: settings_manager.get_settings())
    # Draupnir: recurring daily portfolio backups (boot snapshot + daily + prune).
    draupnir_backup.start_daily_loop()
    # LOOT.Farm auctions: snapshot the feed every 15 min to build the per-lot history
    # the backtest reads (bids, clear prices, snipe references).
    huginn_service.start_auction_tracker(lambda: settings_manager.get_settings())
    # Gjallarhorn: watch the CS2 update feed for case/collection limiting events.
    gjallarhorn_news_service.start()

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
