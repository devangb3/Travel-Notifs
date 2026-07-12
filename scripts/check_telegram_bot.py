import asyncio

from travel_notifs.config import get_settings
from travel_notifs.notifications import NotificationError, TelegramNotifier


async def main() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not configured")
    try:
        username = await TelegramNotifier(settings.telegram_bot_token).username()
    except NotificationError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"telegram: bot=@{username}")


if __name__ == "__main__":
    asyncio.run(main())
