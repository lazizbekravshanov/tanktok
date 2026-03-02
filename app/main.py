"""TankTok — Telegram bot entry point."""

from __future__ import annotations

import logging
import sys

from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from app.config import load_config
from app.handlers import BotHandlers
from app.storage.cache import Cache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    config = load_config()

    if not config.telegram_token:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Exiting.")
        sys.exit(1)

    cache = Cache(db_path=config.db_path)
    handlers = BotHandlers(config, cache)

    app = (
        ApplicationBuilder()
        .token(config.telegram_token)
        .build()
    )

    # Lifecycle hooks — start Kalshi WS on boot, clean up on shutdown
    async def post_init(application) -> None:
        logger.info("Initializing async providers…")
        await handlers.startup()

    async def post_shutdown(application) -> None:
        logger.info("Shutting down async providers…")
        await handlers.shutdown()

    app.post_init = post_init
    app.post_shutdown = post_shutdown

    # Register commands
    app.add_handler(CommandHandler("start", handlers.cmd_start))
    app.add_handler(CommandHandler("help", handlers.cmd_help))
    app.add_handler(CommandHandler("sources", handlers.cmd_sources))
    app.add_handler(CommandHandler("setunits", handlers.cmd_setunits))

    # Catch-all text handler for ZIP / city queries
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_message))

    logger.info("TankTok bot starting…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
