"""Shared date parsing for provider tools."""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def parse_date(date_str: str, end_of_day: bool = False) -> datetime:
    """Parse a date string, handling multiple formats the LLM might use.

    Args:
        date_str: Date string (e.g., "2026-04-01", "April 1 2026", "2026/04/01").
        end_of_day: If True, set time to 23:59:59.

    Returns:
        A timezone-aware datetime in UTC.

    Raises:
        ValueError: If the date string cannot be parsed in any supported format.
    """
    from dateutil import parser as dateutil_parser

    date_str = date_str.strip()
    if not date_str:
        raise ValueError("Empty date string")

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            dt = dateutil_parser.parse(date_str).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"Could not parse date '{date_str}'. Use YYYY-MM-DD format."
            ) from exc

    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt
