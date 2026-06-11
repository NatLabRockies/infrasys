"""Rendering helpers for system information tables."""

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Generic, Type, TypeAlias, TypeVar

from loguru import logger
from rich import print as _pprint
from rich.table import Table

from .component import Component
from .exceptions import ISInvalidParameter
from .utils.time_utils import from_iso_8601

if TYPE_CHECKING:
    from .system import System

T = TypeVar("T", bound="Component")


@dataclass(frozen=True)
class ComponentColumn(Generic[T]):
    """Computed column specification for :meth:`System.show_components`.

    Parameters
    ----------
    name : str
        Column header to display.
    extractor : Callable[[T], Any]
        Callable that returns the value to render for a component.
    """

    name: str
    extractor: Callable[[T], Any]


ComponentColumnInput: TypeAlias = (
    str
    | ComponentColumn[Any]
    | tuple[str | ComponentColumn[Any], ...]
    | list[str | ComponentColumn[Any]]
)


def render_components_table(
    system: "System",
    component_type: Type[T],
    columns: ComponentColumnInput | None,
    *,
    show_uuid: bool,
    show_time_series: bool,
    show_supplemental: bool,
    filter_func: Callable[[T], bool] | None,
) -> None:
    """Render components of ``component_type`` as a Rich table."""
    component_columns = normalize_component_columns(component_type, columns)
    components = list(system.get_components(component_type, filter_func=filter_func))

    if not components:
        logger.warning(f"No components of type {component_type.__name__} found in the system.")
        return

    table = Table(
        title=f"{component_type.__name__}: {len(components)}",
        show_header=True,
        title_justify="left",
        title_style="bold",
    )
    table.add_column("Name", min_width=20, justify="left")

    for column in component_columns:
        table.add_column(column.name, justify="left")
    add_component_metadata_columns(
        table,
        show_uuid=show_uuid,
        show_time_series=show_time_series,
        show_supplemental=show_supplemental,
    )

    sorted_components = sorted(components, key=lambda x: getattr(x, "name", x.label))

    for component in sorted_components:
        row_data = [component.name]
        row_data.extend(render_component_column(component, column) for column in component_columns)
        extend_component_metadata_row(
            system,
            row_data,
            component,
            show_uuid=show_uuid,
            show_time_series=show_time_series,
            show_supplemental=show_supplemental,
        )
        table.add_row(*row_data)

    _pprint(table)


def add_component_metadata_columns(
    table: Table,
    *,
    show_uuid: bool,
    show_time_series: bool,
    show_supplemental: bool,
) -> None:
    if show_uuid:
        table.add_column("UUID", min_width=36, justify="left")
    if show_time_series:
        table.add_column("Has Time Series", min_width=12, justify="right")
    if show_supplemental:
        table.add_column("Has Supplemental Attributes", min_width=12, justify="right")


def extend_component_metadata_row(
    system: "System",
    row_data: list[str],
    component: Component,
    *,
    show_uuid: bool,
    show_time_series: bool,
    show_supplemental: bool,
) -> None:
    if show_uuid:
        row_data.append(str(component.uuid))
    if show_time_series:
        row_data.append(str(len(system.list_time_series_metadata(component))))
    if show_supplemental:
        row_data.append(str(len(system.get_supplemental_attributes_with_component(component))))


def normalize_component_columns(
    component_type: Type[T], columns: ComponentColumnInput | None
) -> list[ComponentColumn[T]]:
    if columns is None:
        return []
    if isinstance(columns, str | ComponentColumn):
        column_specs = [columns]
    elif isinstance(columns, tuple | list):
        column_specs = list(columns)
    else:
        msg = (
            f"Invalid columns specification for {component_type.__name__}: "
            f"expected a field name, ComponentColumn, tuple, or list; got {type(columns).__name__}."
        )
        raise ISInvalidParameter(msg)

    normalized: list[ComponentColumn[T]] = []
    for column in column_specs:
        if isinstance(column, str):
            normalized.append(make_component_field_column(component_type, column))
        elif isinstance(column, ComponentColumn):
            normalized.append(column)
        else:
            msg = (
                f"Invalid column {column!r} for {component_type.__name__}: "
                "expected a field name or ComponentColumn."
            )
            raise ISInvalidParameter(msg)
    return normalized


def make_component_field_column(component_type: Type[T], field_name: str) -> ComponentColumn[T]:
    if field_name not in component_type.model_fields:
        msg = f"Column {field_name!r} does not exist on component type {component_type.__name__}."
        raise ISInvalidParameter(msg)
    return ComponentColumn(
        name=field_name, extractor=lambda component: getattr(component, field_name)
    )


def render_component_column(component: T, column: ComponentColumn[T]) -> str:
    try:
        value = column.extractor(component)
    except Exception as exc:
        msg = (
            f"Failed to compute column {column.name!r} for component {component.label} "
            f"({type(component).__name__})."
        )
        raise ISInvalidParameter(msg) from exc
    if value is None:
        return ""
    return str(value)


class SystemInfo:
    """Class to store system component info."""

    def __init__(self, system: "System") -> None:
        self.system = system

    def extract_system_counts(self) -> tuple[int, int, dict, dict]:
        component_count = self.system._components.get_num_components()
        component_type_count = {
            k.__name__: v for k, v in self.system._components.get_num_components_by_type().items()
        }
        ts_counts = self.system.time_series.metadata_store.get_time_series_counts()
        return (
            component_count,
            ts_counts.time_series_count,
            component_type_count,
            ts_counts.time_series_type_count,
        )

    def render(self) -> None:
        """Render Summary information from the system."""
        (
            component_count,
            time_series_count,
            component_type_count,
            time_series_type_count,
        ) = self.extract_system_counts()
        owner_type_count = self._get_owner_type_counts(component_type_count)

        system_table = Table(
            title="System",
            show_header=True,
            title_justify="left",
            title_style="bold",
        )
        system_table.add_column("Property")
        system_table.add_column("Value", justify="right")
        system_table.add_row("System name", self.system.name)
        system_table.add_row("Data format version", self.system._data_format_version)
        system_table.add_row("Components attached", f"{component_count}")
        system_table.add_row("Time Series attached", f"{time_series_count}")
        total_suppl_attrs = self.system.get_num_supplemental_attributes()
        system_table.add_row("Supplemental Attributes attached", f"{total_suppl_attrs}")
        system_table.add_row("Description", self.system.description)
        _pprint(system_table)

        component_table = Table(
            title="Component Information",
            show_header=True,
            title_justify="left",
            title_style="bold",
        )
        component_table.add_column("Type", min_width=20)
        component_table.add_column("Count", justify="right")

        for component_type, component_count in sorted(component_type_count.items()):
            component_table.add_row(
                f"{component_type}",
                f"{component_count}",
            )

        if component_table.rows:
            _pprint(component_table)

        time_series_table = Table(
            title="Time Series Summary",
            show_header=True,
            title_justify="left",
            title_style="bold",
        )
        time_series_table.add_column("Owner Type", min_width=20)
        time_series_table.add_column("Time Series Type", justify="right")
        time_series_table.add_column("Initial time", justify="right")
        time_series_table.add_column("Resolution", justify="right")
        time_series_table.add_column("No. Components", justify="right")
        time_series_table.add_column("No. Components with Time Series", justify="right")

        for (
            component_type,
            time_series_type,
            time_series_start_time,
            time_series_resolution,
        ), time_series_count in sorted(
            time_series_type_count.items(),
            key=lambda item: tuple(v if v is not None else "" for v in item[0]),
        ):
            owner_count = owner_type_count.get(component_type, 0)
            time_series_table.add_row(
                f"{component_type}",
                f"{time_series_type}",
                f"{time_series_start_time}" if time_series_start_time is not None else "N/A",
                f"{from_iso_8601(time_series_resolution)}"
                if time_series_resolution is not None
                else "N/A",
                f"{owner_count}",
                f"{time_series_count}",
            )

        if time_series_table.rows:
            _pprint(time_series_table)

    def _get_owner_type_counts(self, component_type_count: dict[str, int]) -> dict[str, int]:
        """Combine component and supplemental attribute counts by type for summary tables."""
        owner_type_count = dict(component_type_count)
        supplemental_attribute_counts: dict[str, int] = defaultdict(int)

        for attribute in self.system._supplemental_attr_mgr.iter_all():
            supplemental_attribute_counts[type(attribute).__name__] += 1

        owner_type_count.update(supplemental_attribute_counts)
        return owner_type_count
