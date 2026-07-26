import re
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from dateutil.relativedelta import relativedelta

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


def as_utc(value: datetime) -> datetime:
    """Return ``value`` as a timezone-aware UTC datetime.

    infrasys models carry naive timestamps that are understood to be UTC; the time series
    store requires them to be aware.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def as_naive_utc(value: datetime) -> datetime:
    """Return ``value`` as a naive UTC datetime, the form infrasys models use."""
    return value.astimezone(timezone.utc).replace(tzinfo=None)
