"""Bitta jarayonda: Telegram bot + veb-server + rejalashtiruvchi."""
import asyncio
import logging

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from . import bot as botmod
from . import config, db, web


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.BOT_TOKEN or not config.ACCESS_KEY:
        raise SystemExit("BOT_TOKEN va ACCESS_KEY .env da bo'lishi shart")
    db.conn()   # eski bitta foydalanuvchili baza bo'lsa, shu yerda OWNER_ID ga ko'chiriladi

    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(botmod.router)
    # Eski loyiha webhook ishlatgan bo'lsa, uni o'chiramiz (polling bilan to'qnashmasin)
    await bot.delete_webhook(drop_pending_updates=False)
    await bot.set_my_commands(botmod.COMMANDS)

    server = uvicorn.Server(uvicorn.Config(web.app, host=config.WEB_HOST, port=config.WEB_PORT,
                                           log_level="info", proxy_headers=True))
    tasks = [asyncio.create_task(dp.start_polling(bot, handle_signals=False)),
             asyncio.create_task(server.serve()),
             asyncio.create_task(botmod.scheduler(bot))]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    await bot.session.close()
    for t in done:
        if t.exception():
            raise t.exception()


if __name__ == "__main__":
    asyncio.run(main())
