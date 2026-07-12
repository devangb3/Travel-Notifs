from travel_notifs.config import Settings


def test_default_poll_interval_is_45_seconds() -> None:
    settings = Settings(_env_file=None)
    assert settings.poll_interval_seconds == 45
