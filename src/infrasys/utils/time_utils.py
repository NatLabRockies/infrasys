import re
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta

ISO_8601_DURATION_PATTERN = re.compile(
    r"P(?:(?P<years>\d+)Y)?(?:(?P<months>\d+)M)?(?:(?P<weeks>\d+)W)?"
    r"(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?"
)
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3_600
MILLISECONDS_PER_SECOND = 1_000
MICROSECONDS_PER_MILLISECOND = 1_000
MICROSECONDS_PER_SECOND = MILLISECONDS_PER_SECOND * MICROSECONDS_PER_MILLISECOND
MICROSECONDS_PER_MINUTE = SECONDS_PER_MINUTE * MICROSECONDS_PER_SECOND
MICROSECONDS_PER_HOUR = SECONDS_PER_HOUR * MICROSECONDS_PER_SECOND


def from_iso_8601(duration: str) -> timedelta | relativedelta:
    """Convert an ISO 8601 duration string to a Python delta.

    Calendar units produce a ``relativedelta``. Durations with only fixed units
    produce a ``timedelta``. Compound durations may combine date and time units.
    ``M`` means months before ``T`` and minutes after ``T``. Week durations must
    stand alone. Fractional seconds are supported to millisecond precision.

    Parameters
    ----------
    duration
        ISO 8601 duration string, such as ``P1Y2M3DT4H5M6.007S``.

    Returns
    -------
    timedelta | relativedelta
        Python object representing the duration.

    Raises
    ------
    TypeError
        If ``duration`` is not a string.
    ValueError
        If the string is not a supported ISO 8601 duration, or fractional
        milliseconds are provided.

    See Also
    --------
    to_iso_8601: Reverse operation of this function

    Examples
    --------
    >>> from_iso_8601("P1M")
    relativedelta(months=+1)

    >>> from_iso_8601("P0DT1H")
    datetime.timedelta(seconds=3600)

    >>> from_iso_8601("P1Y2M3DT4H5M6.007S")
    relativedelta(years=+1, months=+2, days=+3, hours=+4, minutes=+5, seconds=+6, microseconds=+7000)
    """
    if not isinstance(duration, str):
        msg = "Input must be an ISO 8601 duration string."
        raise TypeError(msg)

    match = ISO_8601_DURATION_PATTERN.fullmatch(duration)
    if match is None:
        msg = f"Invalid ISO 8601 duration: {duration!r}."
        raise ValueError(msg)

    parts = match.groupdict()
    if not any(value is not None for value in parts.values()):
        msg = f"Invalid ISO 8601 duration: {duration!r}."
        raise ValueError(msg)

    time_units = ("hours", "minutes", "seconds")
    if "T" in duration and not any(parts[unit] is not None for unit in time_units):
        msg = f"Invalid ISO 8601 duration: {duration!r}."
        raise ValueError(msg)

    if parts["weeks"] is not None and any(
        parts[unit] is not None for unit in parts if unit != "weeks"
    ):
        msg = "ISO 8601 week durations cannot be combined with other units."
        raise ValueError(msg)

    seconds_value = Decimal(parts["seconds"] or "0")
    milliseconds_value = seconds_value * MILLISECONDS_PER_SECOND
    if milliseconds_value != milliseconds_value.to_integral_value():
        msg = "Fractional milliseconds are not supported. Provide seconds with a whole number of milliseconds."
        raise ValueError(msg)
    whole_seconds, milliseconds = divmod(int(milliseconds_value), MILLISECONDS_PER_SECOND)

    years = int(parts["years"] or 0)
    months = int(parts["months"] or 0)
    weeks = int(parts["weeks"] or 0)
    days = int(parts["days"] or 0)
    hours = int(parts["hours"] or 0)
    minutes = int(parts["minutes"] or 0)
    microseconds = milliseconds * MICROSECONDS_PER_MILLISECOND

    if parts["years"] is not None or parts["months"] is not None:
        return relativedelta(
            years=years,
            months=months,
            days=days,
            hours=hours,
            minutes=minutes,
            seconds=whole_seconds,
            microseconds=microseconds,
        )
    return timedelta(
        weeks=weeks,
        days=days,
        hours=hours,
        minutes=minutes,
        seconds=whole_seconds,
        microseconds=microseconds,
    )


@dataclass(frozen=True, slots=True)
class ISODuration:
    """Store normalized units for serializing an ISO 8601 duration.

    Attributes
    ----------
    calendar_months : int
        Total calendar months. Twelve months serialize as one year.
    days : int
        Whole calendar or fixed days, depending on the source duration.
    time_microseconds : int
        Sub-day time units stored as microseconds.
    """

    calendar_months: int
    days: int
    time_microseconds: int


def duration_components_from_timedelta(duration: timedelta) -> ISODuration:
    """Normalize a fixed duration to ISO serialization components.

    ``timedelta.seconds`` stores whole seconds, while ``timedelta.microseconds``
    stores the remaining fraction. Convert seconds to microseconds before adding
    the remainder so the time component uses one unit.

    Parameters
    ----------
    duration : timedelta
        Non-negative fixed duration to normalize.

    Returns
    -------
    ISODuration
        Components with whole days and sub-day time in microseconds.

    Raises
    ------
    TypeError
        If ``duration`` is not a ``timedelta``.
    ValueError
        If ``duration`` is negative.
    """
    if not isinstance(duration, timedelta):
        msg = "Input must be a timedelta object."
        raise TypeError(msg)
    if duration < timedelta(0):
        msg = "Negative durations are not supported."
        raise ValueError(msg)
    return ISODuration(
        calendar_months=0,
        days=duration.days,
        time_microseconds=duration.seconds * MICROSECONDS_PER_SECOND + duration.microseconds,
    )


def duration_components_from_relativedelta(duration: relativedelta) -> ISODuration:
    """Normalize a relative calendar duration to ISO serialization components.

    Years and months become a total month count. Days remain separate, and
    sub-day fields are combined into microseconds.

    Parameters
    ----------
    duration : relativedelta
        Non-negative relative duration. Absolute fields are not supported.

    Returns
    -------
    ISODuration
        Normalized calendar months, days, and sub-day microseconds.

    Raises
    ------
    TypeError
        If ``duration`` is not a ``relativedelta``.
    ValueError
        If it contains absolute fields, negative values, or non-integral
        normalized relative fields.
    """
    if not isinstance(duration, relativedelta):
        msg = "Input must be a relativedelta object."
        raise TypeError(msg)
    if (
        duration.weekday is not None
        or duration.leapdays
        or any(
            value is not None
            for value in (
                duration.year,
                duration.month,
                duration.day,
                duration.hour,
                duration.minute,
                duration.second,
                duration.microsecond,
            )
        )
    ):
        msg = "ISO 8601 durations cannot represent absolute relativedelta fields."
        raise ValueError(msg)

    duration = duration.normalized()
    fields = [
        duration.years,
        duration.months,
        duration.days,
        duration.hours,
        duration.minutes,
        duration.seconds,
        duration.microseconds,
    ]
    try:
        whole_fields = [int(value) for value in fields]
    except (OverflowError, TypeError, ValueError) as error:
        msg = "ISO 8601 durations require finite whole relative fields."
        raise ValueError(msg) from error
    if any(value != whole for value, whole in zip(fields, whole_fields, strict=True)):
        msg = "ISO 8601 calendar and date fields must be whole numbers."
        raise ValueError(msg)
    if any(value < 0 for value in whole_fields):
        msg = "Negative durations are not supported."
        raise ValueError(msg)

    years, months, days, hours, minutes, seconds, microseconds = whole_fields
    return ISODuration(
        calendar_months=12 * years + months,
        days=days,
        time_microseconds=(
            hours * MICROSECONDS_PER_HOUR
            + minutes * MICROSECONDS_PER_MINUTE
            + seconds * MICROSECONDS_PER_SECOND
            + microseconds
        ),
    )


def duration_components(duration: timedelta | relativedelta) -> ISODuration:
    """Convert a supported delta to normalized ISO duration components.

    Parameters
    ----------
    duration : timedelta | relativedelta
        Non-negative fixed or relative duration.

    Returns
    -------
    ISODuration
        Calendar months, days, and sub-day time in microseconds.

    Raises
    ------
    TypeError
        If ``duration`` is not a ``timedelta`` or ``relativedelta``.
    ValueError
        If the duration is negative or uses unsupported absolute fields.
    """
    if isinstance(duration, relativedelta):
        return duration_components_from_relativedelta(duration)
    if isinstance(duration, timedelta):
        return duration_components_from_timedelta(duration)
    msg = "Input must be a timedelta or relativedelta object."
    raise TypeError(msg)


def format_iso_8601_duration(components: ISODuration) -> str:
    """Format normalized duration components as an ISO 8601 string.

    Calendar months serialize as years when divisible by twelve. Whole weeks
    use the week form only when no other units are present. Sub-day time is
    rounded to the nearest millisecond.

    Parameters
    ----------
    components : ISODuration
        Non-negative integer duration components.

    Returns
    -------
    str
        ISO 8601 duration string.

    Raises
    ------
    TypeError
        If ``components`` is not an ``ISODuration`` or contains non-integer
        fields.
    ValueError
        If components are negative, represent zero duration, or contain only
        sub-millisecond time.

    Examples
    --------
    >>> format_iso_8601_duration(ISODuration(14, 3, 14_400_000_000))
    'P14M3DT4H'
    """
    if not isinstance(components, ISODuration):
        msg = "Input must be ISO duration components."
        raise TypeError(msg)
    values = (components.calendar_months, components.days, components.time_microseconds)
    if any(type(value) is not int for value in values):
        msg = "ISO duration components must be integers."
        raise TypeError(msg)
    if any(value < 0 for value in values):
        msg = "Negative durations are not supported."
        raise ValueError(msg)

    time_milliseconds = round(components.time_microseconds / MICROSECONDS_PER_MILLISECOND)
    if components.time_microseconds and not time_milliseconds:
        msg = "The minimum resolution is `1ms`."
        raise ValueError(msg)
    whole_seconds, milliseconds = divmod(time_milliseconds, MILLISECONDS_PER_SECOND)
    hours, remainder = divmod(whole_seconds, SECONDS_PER_HOUR)
    minutes, seconds = divmod(remainder, SECONDS_PER_MINUTE)
    time_parts = [
        f"{hours}H" if hours else "",
        f"{minutes}M" if minutes else "",
        f"{seconds}.{milliseconds:03d}S" if seconds or milliseconds else "",
    ]
    time_component = "".join(time_parts)

    if (
        not components.calendar_months
        and components.days
        and components.days % 7 == 0
        and not time_component
    ):
        date_component = f"{components.days // 7}W"
    else:
        calendar_component = (
            f"{components.calendar_months // 12}Y"
            if components.calendar_months % 12 == 0
            else f"{components.calendar_months}M"
        )
        date_component = calendar_component if components.calendar_months else ""
        date_component += f"{components.days}D" if components.days else ""

    if not date_component and not time_component:
        msg = "The minimum resolution is `1ms`."
        raise ValueError(msg)
    date_component = date_component or "0D"
    time_component = f"T{time_component}" if time_component else ""
    return f"P{date_component}{time_component}"


def to_iso_8601(duration: timedelta | relativedelta) -> str:
    """Convert a timedelta or relativedelta object to an ISO 8601 duration.

    Parameters
    ----------
    duration: timedelta | relativedelta
        Python object representing a duration.

    Returns
    -------
    str
        String representation of the duration using the ISO 8601.

    Raises
    ------
    TypeError
        If the object provided is not either `timedelta` or `relativedelta`.

    ValueError
        If the duration is negative or zero, contains absolute fields, or has
        a nonzero time component below one millisecond.

    See Also
    --------
    from_iso_8601: Reverse operation of this function

    Examples
    --------
    A simple example for a delta of 1 hour.

    >>> delta = timedelta(hours=1)
    >>> result = to_iso_8601(delta)
    >>> print(result)
    P0DT1H

    For a delta of 1 year

    >>> delta = relativedelta(years=1)
    >>> result = to_iso_8601(delta)
    >>> print(result)
    P1Y

    For a compound duration

    >>> delta = relativedelta(years=1, months=2, days=3, hours=4)
    >>> result = to_iso_8601(delta)
    >>> print(result)
    P14M3DT4H
    """
    return format_iso_8601_duration(duration_components(duration))


def str_timedelta_to_iso_8601(delta_str: str) -> str:
    """Convert a positive ``str(timedelta)`` value to ISO 8601.

    The accepted form is ``[N day[s], ]HH:MM:SS`` with whole seconds. Zero and
    negative durations are not supported by :func:`to_iso_8601`.

    Parameters
    ----------
    delta_str : str
        String representation of a positive ``timedelta``.

    Returns
    -------
    str
        ISO 8601 duration string.

    Raises
    ------
    ValueError
        If ``delta_str`` does not match the supported form or represents zero.
    TypeError
        If ``delta_str`` is not a string.
    OverflowError
        If the parsed duration exceeds ``timedelta``'s supported range.

    Examples
    --------
    >>> str_timedelta_to_iso_8601("1:00:00")
    'P0DT1H'
    """
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
