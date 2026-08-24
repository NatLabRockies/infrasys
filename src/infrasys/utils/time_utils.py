import re
from collections import OrderedDict
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.relativedelta import relativedelta

from infrasys.exceptions import ISInvalidParameter

REGEX_DURATIONS = OrderedDict(
    {
        "milliseconds": r"^P0DT(\d+\.\d+)S$",
        "seconds": r"^P0DT(\d+)S$",
        "minutes": r"^P0DT(\d+)M$",
        "hours": r"^P0DT(\d+)H$",
        "days": r"^P(\d+)D$",
        "weeks": r"^P(\d+)W$",
        "months": r"^P(\d+)M$",
        "years": r"^P(\d+)Y$",
    }
)

DURATION_TO_TYPE = {
    "milliseconds": timedelta,
    "seconds": timedelta,
    "minutes": timedelta,
    "hours": timedelta,
    "days": timedelta,
    "weeks": timedelta,
    "months": relativedelta,
    "years": relativedelta,
}


def from_iso_8601(duration: str) -> timedelta | relativedelta:
    """Convert a duration string from the ISO 8601 to Python delta.

    Parameters
    ----------
    duration: str
        String representing the time duration following the standard ISO 8601.

    Returns
    -------
    timedelta | relativedelta
        Python object representing the time duration as a delta.

    Raises
    ------
    ValueError
        If fractional milliseconds are provided (e.g, P0DT30.532S)
        If the string does not follow the ISO 8601 format.

    See Also
    --------
    to_iso_8601: Reverse operation of this function

    Examples
    --------
    A simple example for a delta of 1 month.

    >>> delta_str = "P1M"
    >>> result = from_iso_8601(delta_str)
    >>> print(result)
    relativedelta(months=1)

    For a delta of 1 hour

    >>> delta_str = "P0DT1H"
    >>> result = from_iso_8601(delta_str)
    >>> print(result)
    timedelta(hours=1)
    """
    for name, regex in REGEX_DURATIONS.items():
        if match := re.match(regex, duration):
            if name == "milliseconds":
                value_float = float(match.group(1))
                if (value_float * 1_000) % 1 != 0.0:
                    msg = "Fractional milliseconds are not supported. "
                    msg += "Provide seconds with a integer number of milliseconds"
                    raise ValueError(msg)
                value = value_float * 1_000
            else:
                value = int(match.group(1))
            return DURATION_TO_TYPE[name](**{name: value})
    else:
        msg = f"No match found for {duration=}. "
        msg += "Check `REGEX_DURATIONS` to validate that the format is covered."
        raise ValueError(msg)


def to_iso_8601(duration: timedelta | relativedelta) -> str:
    """Convert a timedelta or relativedelta object to ISO 8601 duration string.

    Parameters
    ----------
    duration: timedelta | relativedelta
        Python object representing a timedelta

    Returns
    -------
    str
        String representation of the duration using the ISO 8601.

    Raises
    ------
    TypeError
        If the object provided is not either `timedelta` or `relativedelta`.

    ValueError
        If fractional milliseconds are provided (e.g, P0DT30.532S)

    See Also
    --------
    from_iso_8601: Reverse operation of this function

    Examples
    --------
    A simple example for a delta of 1 hour.

    >>> delta = timedelta(hours=1)
    >>> result = to_iso_8601(delta)
    >>> print(result)
    "P0DT1H"

    For a delta of 1 year

    >>> delta = relativedelta(years=1)
    >>> result = to_iso_8601(delta)
    >>> print(result)
    "P1Y"
    """
    if not isinstance(duration, (timedelta, relativedelta)):
        msg = "Input must be a timedelta or relativedelta object."
        raise TypeError(msg)

    if isinstance(duration, relativedelta):
        years = duration.years or 0
        months = duration.months or 0
        days = duration.days or 0
        seconds = duration.hours * 3600 + duration.minutes * 60 + duration.seconds
        microseconds = duration.microseconds
    else:  # timedelta
        years = months = 0
        days = duration.days
        seconds = duration.seconds
        microseconds = duration.microseconds

    if years and not any([months, days, seconds, microseconds]):
        return f"P{years}Y"

    if months and not any([days, seconds, microseconds]):
        return f"P{months}M"

    if days and not any([seconds, microseconds]):
        if days % 7 == 0:
            return f"P{days // 7}W"
        return f"P{days}D"

    if not days and seconds % 3600 == 0 and not microseconds:
        hours = seconds // 3600
        return f"P0DT{hours}H"

    if not days and seconds % 60 == 0 and seconds % 3600 != 0 and not microseconds:
        minutes = seconds // 60
        return f"P0DT{minutes}M"

    # If not, we return seconds with fraction if milliseconds is provided.
    total_seconds = (
        duration.total_seconds()
        if isinstance(duration, timedelta)
        else seconds + microseconds / 1_000_000
    )
    if round(total_seconds, 3) == 0:
        msg = "The minimum resolution is `1ms`. "
        msg += f"{total_seconds=} must be divisible by 1ms"
        raise ValueError(msg)
    return f"P0DT{total_seconds:.3f}S"


def str_timedelta_to_iso_8601(delta_str: str) -> str:
    """Convert a str(timedelta) to ISO 8601 string."""
    pattern = r"(?:(?P<days>\d+) days?, )?(?P<hours>\d+):(?P<minutes>\d+):(?P<seconds>\d+)"
    match = re.fullmatch(pattern, delta_str)
    if not match:
        msg = f"Invalid timedelta format: {delta_str=}"
        raise ValueError(msg)
    days = int(match.group("days") or 0)
    hours = int(match.group("hours"))
    minutes = int(match.group("minutes"))
    seconds = int(match.group("seconds"))
    delta = timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)

    return to_iso_8601(delta)


def is_zoneless(value: datetime) -> bool:
    """Return True if ``value`` is a wall clock rather than an instant.

    A naive datetime names no instant, and neither does an aware one whose ``tzinfo``
    declines to place it. The store partitions series on exactly this predicate, so
    infrasys asks the same question the same way.
    """
    return value.utcoffset() is None


def tzinfo_from_reference(reference: str | None) -> tzinfo | None:
    """Return the ``tzinfo`` that spells a stored ``time_reference``, or None if zoneless.

    ``reference`` is the store's spelling of how a series' timestamps were written:
    ``"utc"``, ``"zoneless"``, a fixed offset such as ``"-07:00"``, or an IANA zone name
    such as ``"America/Denver"``. ``None`` means the series left the reference unset,
    which is not a claim of a wall clock: it is read as UTC, matching the store.

    Raises
    ------
    ISInvalidParameter
        Raised if ``reference`` is a zone name this interpreter's tz database does not
        have. The instants are intact either way; only the label cannot be resolved here.
    """
    if reference is None or reference == "utc":
        return timezone.utc
    if reference == "zoneless":
        return None
    match = re.match(r"^([+-])(\d{2}):(\d{2})$", reference)
    if match:
        sign = -1 if match.group(1) == "-" else 1
        minutes = sign * (int(match.group(2)) * 60 + int(match.group(3)))
        return timezone(timedelta(minutes=minutes))
    try:
        return ZoneInfo(reference)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        msg = (
            f"time_reference names the IANA zone {reference!r}, which this interpreter's "
            "tz database does not have; the instants are stored either way, but rendering "
            "them in that zone needs a database that knows it (try installing or updating "
            "the tzdata package)"
        )
        raise ISInvalidParameter(msg) from exc


def from_catalog_timestamp(text: str, reference: str | None) -> datetime:
    """Parse one catalog timestamp back into the spelling its series was written in.

    The store renders a catalog timestamp honestly: a zoneless row's timestamps are wall
    clocks with no offset, and everything that names an instant is RFC 3339 UTC. This
    turns that text plus the row's ``time_reference`` back into the datetime the caller
    handed in.
    """
    value = datetime.fromisoformat(text)
    zone = tzinfo_from_reference(reference)
    if zone is None:
        # A wall clock. The text carries no offset, so it parses naive already; strip
        # anything an unexpected spelling added rather than asserting an instant.
        return value.replace(tzinfo=None)
    if value.tzinfo is None:
        # Defensive: an instant-bearing row whose text lost its offset is read as UTC,
        # which is the frame the store writes it in.
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(zone)


def as_instant(value: datetime) -> datetime:
    """Return ``value`` in the frame its arithmetic is well defined in.

    An aware value becomes UTC; a wall clock is returned unchanged, because it has no
    other frame. This exists because Python's ``datetime`` arithmetic is *wall clock*
    whenever both operands share a ``tzinfo`` object: subtracting two
    ``ZoneInfo("America/Denver")`` timestamps that straddle a transition is off by the
    offset change, and ordering them can disagree with the order of the instants they
    name. Converting both sides first makes the comparison and the difference say what
    the store means by them.
    """
    return value if value.utcoffset() is None else value.astimezone(timezone.utc)


def advance(instant: datetime, delta: timedelta) -> datetime:
    """Return ``instant`` moved forward by ``delta``, keeping the spelling it arrived in.

    A time series grid is stepped in *instants*: ``resolution`` is a fixed duration, so
    the store's own arithmetic runs on UTC. Python's ``datetime`` addition is wall-clock
    arithmetic even for an aware value --- it carries the ``tzinfo`` across unchanged and
    lets the offset be recomputed --- so adding a day to a ``ZoneInfo("America/Denver")``
    timestamp across a transition moves the instant by 23 or 25 hours, not 24. Doing the
    addition in UTC and re-spelling the result is what keeps infrasys's bounds on the
    same grid the store slices.
    """
    if instant.utcoffset() is None:
        return instant + delta
    zone = instant.tzinfo
    return (instant.astimezone(timezone.utc) + delta).astimezone(zone)
