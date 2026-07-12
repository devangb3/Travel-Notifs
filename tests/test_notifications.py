import httpx

from travel_notifs.notifications import DryRunNotifier, TelegramNotifier


async def test_dry_run_notifier_records_without_delivery() -> None:
    messages: list[tuple[str, str, str]] = []
    notifier = DryRunNotifier(messages)
    await notifier.send("recipient", "Route V arrives at 8:10", "event-1")
    assert messages == [("recipient", "Route V arrives at 8:10", "event-1")]


async def test_telegram_bot_identity_and_updates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"username": "arrival_bot"}})
        assert request.url.path.endswith("/getUpdates")
        assert request.url.params["offset"] == "8"
        return httpx.Response(200, json={"ok": True, "result": [{"update_id": 8}]})

    notifier = TelegramNotifier("secret", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await notifier.username() == "arrival_bot"
    assert await notifier.updates(8) == [{"update_id": 8}]
