"""Business-date clock. Routers call this; services receive `today` as a parameter."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .config import get_settings


def today_local(now: datetime | None = None) -> date:
    """The current date in settings.BUSINESS_TIMEZONE. `now` must be timezone-aware."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(ZoneInfo(get_settings().BUSINESS_TIMEZONE)).date()
