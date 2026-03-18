"""Defines a System"""

import shutil
import sqlite3
import tempfile
import zipfile
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Generator, Iterable, Literal, Optional, Type, TypeAlias, TypeVar
from uuid import UUID, uuid4

import orjson
from loguru import logger
from rich import print as _pprint
from rich.table import Table

from .component import (
    Component,
)
from .component_manager import ComponentManager
from .exceptions import (
    ISConflictingArguments,
    ISFileExists,
    ISInvalidParameter,
    ISOperationNotAllowed,
)
from .migrations.db_migrations import (
    metadata_store_needs_migration,
    migrate_legacy_metadata_store,
)
from .migrations.metadata_migration import (
    component_needs_metadata_migration,
    migrate_component_metadata,
)
from .models import make_label
from .serialization import (
    TYPE_METADATA,
    CachedTypeHelper,
    SerializedBaseType,
    SerializedComponentReference,
    SerializedQuantityType,
    SerializedType,
    SerializedTypeMetadata,
)
from .supplemental_attribute import SupplementalAttribute
from .supplemental_attribute_manager import SupplementalAttributeManager
from .time_series_manager import TIME_SERIES_KWARGS, TimeSeriesManager
from .time_series_models import (
    SingleTimeSeries,
    TimeSeriesData,
    TimeSeriesKey,
    TimeSeriesMetadata,
    TimeSeriesStorageContext,
)
from .utils.sqlite import backup, create_in_memory_db, restore
from .utils.time_utils import from_iso_8601

T = TypeVar("T", bound="Component")
U = TypeVar("U", bound="SupplementalAttribute")
FileMode: TypeAlias = Literal["r", "r+", "a"]


class System:
    """Implements behavior for systems"""

    DB_FILENAME = "time_series_metadata.db"

    def __init__(
        self,
        name: Optional[str] = None,
        description: Optional[str] = None,
        auto_add_composed_components: bool = False,
        con: Optional[sqlite3.Connection] = None,
        time_series_manager: Optional[TimeSeriesManager] = None,
        supplemental_attribute_manager: Optional[SupplementalAttributeManager] = None,
        uuid: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Constructs a System.

        Parameters
        ----------
        name : str | None
            Optional system name
        description : str | None
            Optional system description
        auto_add_composed_components : bool
            Set to True to automatically add composed components to the system in add_components.
            The default behavior is to raise an ISOperationNotAllowed when this condition occurs.
            This handles values that are components, such as generator.bus, and lists of
            components, such as subsystem.generators, but not any other form of nested components.
        con : None | sqlite3.Connection
            Users should not pass this. De-serialization (from_json) will pass a Connection.
        time_series_manager : None | TimeSeriesManager
            Users should not pass this. De-serialization (from_json) will pass a constructed
            manager.
        kwargs : Any
            Configures time series behaviors:
              - time_series_storage_type: Defaults to TimeSeriesStorageType.ARROW.
              - time_series_read_only: Disables add/remove of time series, defaults to False.
              - time_series_directory: Location to store time series files, defaults to the system's
                tmp directory. Use an alternate location if the space in that directory is limited,
                such as on a compute node with no local storage.
              - chronify_engine_name: Database engine to use with chronify, defaults to "duckdb".

        Examples
        --------
        >>> system = System(name="my_system")
        >>> system2 = System(name="my_system", time_series_directory="/tmp/scratch")
        """
        self._uuid = uuid or uuid4()
        self._name = name
        self._description = description
        self._component_mgr = ComponentManager(auto_add_composed_components)
        self._con = con or create_in_memory_db()
        time_series_kwargs = {k: v for k, v in kwargs.items() if k in TIME_SERIES_KWARGS}
        self._time_series_mgr = time_series_manager or TimeSeriesManager(
            self._con, **time_series_kwargs
        )
        self._supplemental_attr_mgr = (
            supplemental_attribute_manager or SupplementalAttributeManager(self._con)
        )
        self._closed = False

        self._data_format_version: Optional[str] = None
        # Note to devs: if you add new fields, add support in to_json/from_json as appropriate.

        # TODO: add pretty printing of components and time series

    def close(self) -> None:
        """Close open resources such as SQLite connections."""
        if self._closed:
            return
        self._closed = True
        try:
            self._component_mgr.close()
        except Exception:
            logger.debug("Error closing component manager", exc_info=True)

        try:
            self._time_series_mgr.close()
        except Exception:
            logger.debug("Error closing time series manager", exc_info=True)

        if self._con is not None:
            try:
                self._con.close()
            except Exception:
                logger.debug("Error closing system SQLite connection", exc_info=True)

    def __enter__(self) -> "System":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            logger.debug("Error closing system in destructor", exc_info=True)

    @property
    def auto_add_composed_components(self) -> bool:
        """Return the setting for auto_add_composed_components."""
        return self._component_mgr.auto_add_composed_components

    @auto_add_composed_components.setter
    def auto_add_composed_components(self, val: bool) -> None:
        """Set auto_add_composed_components."""
        self._component_mgr.auto_add_composed_components = val

    def to_json(self, filename: Path | str, overwrite=False, indent=None, data=None) -> None:
        """Write the contents of a system to a JSON file. Time series will be written to a
        directory at the same level as filename.

        Parameters
        ----------
        filename : Path | str
           Filename to write. If the parent directory does not exist, it will be created.
        overwrite : bool
            Set to True to overwrite the file if it already exists.
        indent : int | None
            Indentation level in the JSON file. Defaults to no indentation.
        data : dict | None
            This is an override for packages that compose this System inside a parent System
            class. If set, it will be the outer object in the JSON file. It must not set the
            key 'system'. Packages that derive a custom instance of this class should leave this
            field unset.

        Examples
        --------
        >>> system.to_json("systems/system1.json")
        INFO: Wrote system data to systems/system1.json
        INFO: Copied time series data to systems/system1_time_series
        """
        # TODO: how to get all python package info from environment?
        if isinstance(filename, str):
            filename = Path(filename)
        if filename.exists() and not overwrite:
            msg = f"{filename=} already exists. Choose a different path or set overwrite=True."
            raise ISFileExists(msg)

        filename.parent.mkdir(exist_ok=True)
        time_series_dir = filename.parent / (filename.stem + "_time_series")
        time_series_dir.mkdir(exist_ok=True)
        system_data: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "uuid": str(self.uuid),
            "data_format_version": self.data_format_version,
            "components": [x.model_dump_custom() for x in self._component_mgr.iter_all()],
            "supplemental_attributes": [
                x.model_dump_custom() for x in self._supplemental_attr_mgr.iter_all()
            ],
            "time_series": {
                # Note: parent directory is stripped. De-serialization will find it from the
                # parent of the JSON file.
                "directory": time_series_dir.name,
            },
        }
        extra = self.serialize_system_attributes()
        intersection = set(extra).intersection(system_data)
        if intersection:
            msg = f"Extra attributes from parent class collide with System: {intersection}"
            raise ISConflictingArguments(msg)
        system_data.update(extra)

        if data is None:
            data = system_data
        else:
            if "system" in data:
                msg = "data contains the key 'system'"
                raise ISConflictingArguments(msg)
            data["system"] = system_data

        backup(self._con, time_series_dir / self.DB_FILENAME)
        self._time_series_mgr.serialize(
            system_data["time_series"], time_series_dir, db_name=self.DB_FILENAME
        )

        data_dump = orjson.dumps(data)
        with open(filename, "wb") as f_out:
            f_out.write(data_dump)
        logger.info("Wrote system data to {}", filename)

    @classmethod
    def from_json(
        cls, filename: Path | str, upgrade_handler: Callable | None = None, **kwargs
    ) -> "System":
        """Deserialize a System from a JSON file. Refer to System constructor for kwargs.

        Parameters
        ----------
        filename : Path | str
            JSON file containing the system data.
        upgrade_handler : Callable | None
            Optional function to handle data format upgrades. Should only be set when the parent
            package composes this package. If set, it will be called before de-serialization of
            the components.

        Examples
        --------
        >>> system = System.from_json("systems/system1.json")
        """
        with open(filename, "rb") as f_in:
            data = orjson.loads(f_in.read())
        time_series_parent_dir = Path(filename).parent
        return cls.from_dict(
            data, time_series_parent_dir, upgrade_handler=upgrade_handler, **kwargs
        )

    @classmethod
    def load(
        cls,
        zip_path: Path | str,
        time_series_directory: Path | str | None = None,
        upgrade_handler: Callable | None = None,
        **kwargs: Any,
    ) -> "System":
        """Load a System from a zip archive created by the save() method.

        The zip file will be extracted to a temporary directory, the system will be
        deserialized, and the temporary files will be cleaned up automatically.
        Time series storage files are copied to a permanent location during deserialization.

        Parameters
        ----------
        zip_path : Path | str
            Path to the zip file containing the system.
        time_series_directory: Path | str
            Path to the final time series location
        upgrade_handler : Callable | None
            Optional function to handle data format upgrades. Should only be set when the parent
            package composes this package. If set, it will be called before de-serialization of
            the components.
        **kwargs : Any
            Additional arguments passed to the System constructor. Refer to System constructor
            for available options. Use `time_series_directory` to specify where time series
            files should be stored.

        Returns
        -------
        System
            The deserialized system.

        Raises
        ------
        ISFileExists
            Raised if the zip file does not exist.
        ISInvalidParameter
            Raised if the zip file is not a valid zip archive or doesn't contain a valid system.
        FileNotFoundError
            Raised if there is no JSON file in the zip folder.

        Examples
        --------
        >>> system = System.load("my_system.zip")
        >>> system2 = System.load(Path("archived_systems/system1.zip"))
        >>> # Specify where time series files should be stored
        >>> system3 = System.load("my_system.zip", time_series_directory="/path/to/storage")

        See Also
        --------
        save : Save a system to a directory or zip file
        from_json : Load a system from a JSON file
        """
        if isinstance(zip_path, str):
            zip_path = Path(zip_path)

        if not zip_path.exists():
            msg = f"Zip file does not exist: {zip_path}"
            raise FileNotFoundError(msg)

        if not zipfile.is_zipfile(zip_path):
            msg = f"File is not a valid zip archive: {zip_path}"
            raise ISInvalidParameter(msg)

        # Create a temporary directory for extraction
        with tempfile.TemporaryDirectory(dir=time_series_directory) as temp_dir:
            temp_path = Path(temp_dir)

            try:
                with zipfile.ZipFile(zip_path, "r") as zip_ref:
                    zip_ref.extractall(temp_path)
                logger.debug("Extracted {} to temporary directory {}", zip_path, temp_path)
            except (zipfile.BadZipFile, OSError) as e:
                msg = f"Failed to extract zip file {zip_path}: {e}"
                raise ISInvalidParameter(msg) from e

            # We need to find the JSON files since Zips can have different names
            json_files = list(temp_path.rglob("*.json"))

            if not json_files:
                msg = f"No JSON file found in zip archive: {zip_path}"
                raise ISInvalidParameter(msg)

            if len(json_files) > 1:
                msg = (
                    f"Multiple JSON files found in zip archive: {zip_path}. "
                    f"Expected exactly one system JSON file."
                )
                raise ISOperationNotAllowed(msg)

            json_file = json_files[0]
            logger.debug("Found system JSON file: {}", json_file)

            kwargs["time_series_directory"] = time_series_directory
            try:
                system = cls.from_json(json_file, upgrade_handler=upgrade_handler, **kwargs)
                logger.info("Loaded system from {}", zip_path)
            except (OSError, KeyError, ValueError, TypeError) as e:
                msg = f"Failed to deserialize system from {json_file}: {e}"
                raise ISInvalidParameter(msg) from e
            return system

    def to_records(
        self,
        component_type: Type[Component],
        filter_func: Callable | None = None,
        **kwargs,
    ) -> Iterable[dict]:
        """Return a list of dictionaries of components (records) with the requested type(s) and
        optionally match filter_func.

        Parameters
        ----------
        components:
            Component types to get as dictionaries
        filter_func:
            A function to filter components. Default is None
        kwargs
            Configures Pydantic model_dump behaviour
              - exclude: List or dict of excluded fields.
        Notes
        -----
        If a component type is an abstract type, all matching concrete subtypes will be included in the output.

        It is only recommended to use this function on a single "concrete" types. For example, if
        you have an abstract type called Generator and you create two subtypes called
        ThermalGenerator and RenewableGenerator where some fields are different, if you pass the
        return of System.to_records(Generator) to pandas.DataFrame.from_records, each
        ThermalGenerator row will have NaN values for RenewableGenerator-specific fields.

        Examples
        --------
        To get a tabular representation of a certain type you can use:
        >>> import pandas as pd
        >>> df = pd.DataFrame.from_records(System.to_records(SimpleGen))

        With polars:
        >>> import polars as pl
        >>> df = pl.DataFrame(System.to_records(SimpleGen))

        """
        return self._component_mgr.to_records(component_type, filter_func=filter_func, **kwargs)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        time_series_parent_dir: Path | str,
        upgrade_handler: Callable | None = None,
        **kwargs: Any,
    ) -> "System":
        """Deserialize a System from a dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            System data in serialized form.
        time_series_parent_dir : Path | str
            Directory that contains the system's time series directory.
        upgrade_handler : Callable | None
            Optional function to handle data format upgrades. Should only be set when the parent
            package composes this package. If set, it will be called before de-serialization of
            the components.

        Examples
        --------
        >>> system = System.from_dict(data, "systems")
        """
        system_data = data if "system" not in data else data["system"]
        ts_kwargs = {k: v for k, v in kwargs.items() if k in TIME_SERIES_KWARGS}
        if "time_series_storage_type" in kwargs:
            logger.warning("Ignoring keyword 'time_series_storage_type.' Use existing setting.")
            kwargs.pop("time_series_storage_type")

        ts_path = (
            time_series_parent_dir
            if isinstance(time_series_parent_dir, Path)
            else Path(time_series_parent_dir)
        )
        con = create_in_memory_db()
        restore(con, ts_path / data["time_series"]["directory"] / System.DB_FILENAME)

        if metadata_store_needs_migration(con):
            migrate_legacy_metadata_store(con)

        time_series_manager = TimeSeriesManager.deserialize(
            con, data["time_series"], ts_path, **ts_kwargs
        )
        supplemental_attribute_manager = SupplementalAttributeManager(con, initialize=False)
        system = cls(
            name=system_data.get("name"),
            description=system_data.get("description"),
            con=con,
            supplemental_attribute_manager=supplemental_attribute_manager,
            time_series_manager=time_series_manager,
            uuid=UUID(system_data["uuid"]),
            **kwargs,
        )
        if system_data.get("data_format_version") != system.data_format_version:
            # This handles the case where the parent package inherited from System.
            system.handle_data_format_upgrade(
                system_data,
                system_data.get("data_format_version"),
                system.data_format_version,
            )
            # This handles the case where the parent package composes an instance of System.
            if upgrade_handler is not None:
                upgrade_handler(
                    system_data,
                    system_data.get("data_format_version"),
                    system.data_format_version,
                )
        system.deserialize_system_attributes(system_data)

        if component_needs_metadata_migration(system_data["components"][0]):
            system_data["components"] = migrate_component_metadata(system_data["components"])
        system._deserialize_components(system_data["components"])
        system._deserialize_supplemental_attributes(system_data["supplemental_attributes"])
        logger.info("Deserialized system {}", system.label)
        return system

    def save(
        self,
        fpath: Path | str,
        filename: str = "system.json",
        zip: bool = False,
        overwrite: bool = False,
    ) -> None:
        """Save the contents of a system and the Time series in a single directory.

        By default, this method creates the user specified folder using the
        `to_json` method. If user sets `zip = True`, we create the folder of
        the user (if it does not exists), zip it to the same location specified
        and delete the folder.

        Parameters
        ----------
        fpath : Path | str
           Filepath to write the contents of the system.
        zip : bool
            Set to True if you want to archive to a zip file.
        filename: str
            Name of the sytem to serialize. Default value: "system.json".
        overwrite: bool
            Overwrites the system if it already exist on the fpath.

        Raises
        ------
        FileExistsError
            Raised if the folder provided exists and the overwrite flag was not provided.

        Examples
        --------
        >>> fpath = Path("folder/subfolder/")
        >>> system.save(fpath)
        INFO: Wrote system data to folder/subfolder/system.json
        INFO: Copied time series data to folder/subfolder/system_time_series

        >>> system_fname = "my_system.json"
        >>> fpath = Path("folder/subfolder/")
        >>> system.save(fpath, filename=system_fname, zip=True)
        INFO: Wrote system data to folder/subfolder/my_system.json
        INFO: Copied time series data to folder/subfolder/my_system_time_series
        INFO: System archived at folder/subfolder/my_system.zip

        See Also
        --------
        to_json: System serialization
        """
        if isinstance(fpath, str):
            fpath = Path(fpath)

        if fpath.exists() and not overwrite:
            msg = f"{fpath} exists already. To overwrite the folder pass `overwrite=True`"
            raise FileExistsError(msg)

        fpath.mkdir(parents=True, exist_ok=True)
        self.to_json(fpath / filename, overwrite=overwrite)

        if zip:
            logger.debug("Archiving system and time series into a single zip file at {}", fpath)
            _ = shutil.make_archive(str(fpath), "zip", fpath)
            logger.debug("Removing {}", fpath)
            shutil.rmtree(fpath)
            logger.info("System archived at {}", fpath)

        return

    def add_component(self, component: Component, **kwargs) -> None:
        """Add one component to the system.

        Parameters
        ----------
        component : Component
            Component to add to the system.

        Raises
        ------
        ISAlreadyAttached
            Raised if a component is already attached to a system.

        Examples
        --------
        >>> system.add_component(Bus.example())

        See Also
        --------
        add_components
        """
        return self.add_components(component, **kwargs)

    def add_components(self, *components: Component, **kwargs) -> None:
        """Add one or more components to the system.

        Parameters
        ----------
        components : Component
            Component(s) to add to the system.

        Raises
        ------
        ISAlreadyAttached
            Raised if a component is already attached to a system.

        Examples
        --------
        >>> system.add_components(Bus.example(), Generator.example())

        See Also
        --------
        add_component
        """
        return self._component_mgr.add(*components, **kwargs)

    def add_supplemental_attribute(
        self,
        component: Component,
        attribute: SupplementalAttribute,
    ) -> None:
        """Attach a supplemental attribute to a component. The attribute will get added to the
        system if it is not already stored.

        Parameters
        ----------
        component
            Existing component
        attribute
            Supplemental attribute to attach to the component

        Raises
        ------
        ISAlreadyAttached
            Raised if the component and attribute are already attached.

        Examples
        --------
        >>> bus = Bus.example()
        >>> system.add_component(bus)
        >>> geo_json = GeographicInfo.example()
        >>> system.add_supplemental_attribute(bus, geo_json)
        """
        self._supplemental_attr_mgr.add(component, attribute)

    def change_component_uuid(self, component: Component) -> None:
        """Change the component UUID.

        Parameters
        ----------
        component : Component
        """
        return self._component_mgr.change_uuid(component)

    def copy_component(
        self,
        component: Component,
        name: str | None = None,
        attach: bool = False,
    ) -> Any:
        """Create a copy of the component. Time series data is excluded.

        - The new component will have a different UUID than the original.
        - The copied component will have shared references to any composed components.

        The intention of this method is to provide a way to create variants of a component that
        will be added to the same system. Please refer to :`deepcopy_component`: to create
        copies that are suitable for addition to a different system.

        Parameters
        ----------
        component : Component
            Source component
        name : str
            Optional, if None, keep the original name.
        attach : bool
            Optional, if True, attach the new component to the system.

        Examples
        --------
        >>> gen1 = system.get_component(Generator, "gen1")
        >>> gen2 = system.copy_component(gen, name="gen2")
        >>> gen3 = system.copy_component(gen, name="gen3", attach=True)

        See Also
        --------
        deepcopy_component
        """
        return self._component_mgr.copy(component, name=name, attach=attach)

    def deepcopy_component(self, component: Component) -> Any:
        """Create a deep copy of the component and all composed components. All attributes,
        including names and UUIDs, will be identical to the original. Unlike
        :meth:`copy_component`, there will be no shared references to composed components.

        The intention of this method is to provide a way to create variants of a component that
        will be added to a different system. Please refer to :`copy_component`: to create
        copies that are suitable for addition to the same system.

        Parameters
        ----------
        component : Component
            Source component

        Examples
        --------
        >>> gen1 = system.get_component(Generator, "gen1")
        >>> gen2 = system.deepcopy_component(gen)

        See Also
        --------
        copy_component
        """
        return self._component_mgr.deepcopy(component)

    def get_component(self, component_type: Type[T], name: str) -> T:
        """Return the component with the passed type and name.

        Parameters
        ----------
        component_type : Type[T]
            Generic component type
        name : Type
            Name of component

        Raises
        ------
        ISDuplicateNames
            Raised if more than one component match the inputs.

        Examples
        --------
        >>> system.get_component(Generator, "gen1")

        See Also
        --------
        list_by_name
        """
        return self._component_mgr.get(component_type, name)

    def get_component_by_label(self, label: str) -> Any:
        """Return the component with the label.

        Note that this method is slower than :meth:`get_component` because the
        component type cannot be looked up directly. Code that is looping over components
        repeatedly should not use this method.

        Parameters
        ----------
        label : str

        Raises
        ------
        ISNotStored
            Raised if the UUID is not stored.
        ISOperationNotAllowed
            Raised if there is more than one matching component.

        Examples
        --------
        >>> component = system.get_component_by_label("Bus.bus1")
        """
        return self._component_mgr.get_by_label(label)

    def get_component_by_uuid(self, uuid: UUID) -> Any:
        """Return the component with the input UUID.

        Parameters
        ----------
        uuid : UUID

        Raises
        ------
        ISNotStored
            Raised if the UUID is not stored.

        Examples
        --------
        >>> uuid = UUID("714c8311-8dff-4ae2-aa2e-30779a317d42")
        >>> component = system.get_component_by_uuid(uuid)
        """
        return self._component_mgr.get_by_uuid(uuid)

    def get_components(
        self, *component_types: Type[T], filter_func: Callable | None = None
    ) -> Iterable[T]:
        """Return the components with the passed type(s) and that optionally match filter_func.

        Parameters
        ----------
        component_type : Type[T]
            If component_type is an abstract type, all matching subtypes will be returned.
            The function will return all the matching `component_type` passed.
        filter_func : Callable | None
            Optional function to filter the returned values. The function must accept a component
            as a single argument.

        Examples
        --------
        >>> for component in system.get_components(Component)
            print(component.label)
        >>> names = {"bus1", "bus2", "gen1", "gen2"}
        >>> for component in system.get_components(
            Component,
            filter_func=lambda x: x.name in names,
        ):
            print(component.label)

        To request multiple component types:
        >>> for component in system.get_components(SimpleGenerator, SimpleBus)
        print(component.label)
        """
        return self._component_mgr.iter(*component_types, filter_func=filter_func)

    def get_component_types(self) -> Iterable[Type[Component]]:
        """Return an iterable of all component types stored in the system.

        Examples
        --------
        >>> for component_type in system.get_component_types():
        print(component_type)
        """
        return self._component_mgr.get_types()

    def get_components_with_supplemental_attribute(
        self,
        attribute: SupplementalAttribute,
    ) -> list[Component]:
        """Return all components attached to the given supplemental attribute."""
        return [
            self._component_mgr.get_by_uuid(x)
            for x in self._supplemental_attr_mgr.get_component_uuids_with_attribute(attribute)
        ]

    def get_supplemental_attributes_with_component(
        self,
        component: Component,
        supplemental_attribute_type: Optional[Type[U]] = None,
        filter_func: Optional[Callable[[U], bool]] = None,
    ) -> list[U]:
        """Return all supplemental attributes attached to the given component and optionally,
        with the given attribute type."""
        if (
            supplemental_attribute_type is not None
            and supplemental_attribute_type.__subclasses__()
        ):
            msg = (
                "get_supplemental_attributes_with_component does not support supplemental_attribute_type as "
                "an abstract class"
            )
            raise ISOperationNotAllowed(msg)

        return self._supplemental_attr_mgr.get_attributes_with_component(
            component,
            attribute_type=supplemental_attribute_type,
            filter_func=filter_func,
        )

    def get_supplemental_attribute_by_uuid(self, uuid: UUID) -> SupplementalAttribute:
        """Return the supplemental attribute with the given UUID."""
        return self._supplemental_attr_mgr.get_by_uuid(uuid)

    def get_supplemental_attributes(
        self,
        *supplemental_attribute_types: Type[U],
        filter_func: Optional[Callable[[U], bool]] = None,
    ) -> Generator[Any, None, None]:
        return self._supplemental_attr_mgr.iter(
            *supplemental_attribute_types, filter_func=filter_func
        )

    def get_supplemental_attribute_counts_by_type(self) -> list[dict[str, Any]]:
        """Return a list of dicts of stored supplemental attribute counts by type."""
        return self._supplemental_attr_mgr.get_attribute_counts_by_type()

    def get_num_supplemental_attributes(self) -> int:
        """Return the number of supplemental attributes stored in the system."""
        return self._supplemental_attr_mgr.get_num_attributes()

    def get_num_components_with_supplemental_attributes(self) -> int:
        """Return the number of supplemental attributes stored in the system."""
        return self._supplemental_attr_mgr.get_num_components_with_attributes()

    def has_supplemental_attribute(
        self,
        component: Component,
        supplemental_attribute_type: Optional[Type[SupplementalAttribute]] = None,
    ) -> bool:
        """Return True if the component has a supplemental attribute, optionally of the given
        type.
        """
        return self._supplemental_attr_mgr.has_association_by_type(
            component, attribute_type=supplemental_attribute_type
        )

    def has_supplemental_attribute_association(
        self, component: Component, supplemental_attribute: SupplementalAttribute
    ) -> bool:
        """Return True if the component and supplemental attribute have an association."""
        return self._supplemental_attr_mgr.has_association(component, supplemental_attribute)

    def has_component(self, component) -> bool:
        """Return True if the component is attached."""
        return self._component_mgr.has_component(component)

    def list_child_components(
        self, component: Component, component_type: Optional[Type[Component]] = None
    ) -> list[Component]:
        """Return a list of all components that this component composes.

        Parameters
        ----------
        component: Component
        component_type: Optional[Type[Component]]
            Filter the returned list to components of this type.
            If the type has subclasses, all subclasses will be included.

        See Also
        --------
        list_parent_components
        """
        return self._component_mgr.list_child_components(component, component_type=component_type)

    def list_parent_components(
        self, component: Component, component_type: Optional[Type[Component]] = None
    ) -> list[Component]:
        """Return a list of all components that compose this component.

        An example usage is where you need to find all components connected to a bus and the Bus
        class does not contain that information. The system tracks these connections internally
        and can find those components quickly.

        Parameters
        ----------
        component: Component
        component_type: Optional[Type[Component]]
            Filter the returned list to components of this type.
            If the type has subclasses, all subclasses will be included.

        Examples
        --------
        >>> components = system.list_parent_components(bus)
        >>> print(f"These components are connected to {bus.label}: ", " ".join(components))

        See Also
        --------
        list_child_components
        """
        return self._component_mgr.list_parent_components(component, component_type=component_type)

    def list_components_by_name(self, component_type: Type[Component], name: str) -> list[Any]:
        """Return all components that match component_type and name.

        Parameters
        ----------
        component_type : Type
        name : str

        Examples
        --------
        system.list_components_by_name(Generator, "gen1")
        """
        return self._component_mgr.list_by_name(component_type, name)

    def iter_all_components(self) -> Iterable[Any]:
        """Return an iterator over all components.

        Examples
        --------
        >>> for component in system.iter_all_components()
            print(component.label)

        See Also
        --------
        get_components
        """
        return self._component_mgr.iter_all()

    def rebuild_component_associations(self) -> None:
        """Clear the component associations and rebuild the table. This may be necessary
        if a user reassigns connected components that are part of a system.
        """
        self._component_mgr.rebuild_component_associations()

    def remove_component(
        self, component: Component, cascade_down: bool = True, force: bool = False
    ) -> None:
        """Remove the component from the system.

        Parameters
        ----------
        component : Component
        cascade_down : bool
            If True, remove all child components if they have no other parents. Defaults to True.
            For example, if a generator has a bus, no other component holds a reference to that
            bus, and you call remove_component on that generator, the bus will get removed as well.
        force : bool
            If True, remove the component even if other components hold references to this
            component. Defaults to False.

        Raises
        ------
        ISNotStored
            Raised if the component is not stored in the system.
        ISOperationNotAllowed
            Raised if the other components hold references to this component and force=False.

        Examples
        --------
        >>> gen = system.get_component(Generator, "gen1")
        >>> system.remove_component(gen)
        """
        self._component_mgr.raise_if_not_attached(component)
        if self.has_time_series(component):
            for metadata in self._time_series_mgr.list_time_series_metadata(component):
                self.remove_time_series(
                    component,
                    time_series_type=metadata.get_time_series_data_type(),
                    name=metadata.name,
                    **metadata.features,
                )
        self._component_mgr.remove(component, cascade_down=cascade_down, force=force)

    def remove_component_by_name(
        self,
        component_type: Type[Component],
        name: str,
        cascade_down: bool = True,
        force: bool = False,
    ) -> None:
        """Remove the component with component_type and name from the system.

        Parameters
        ----------
        component_type : Type
        name : str
        cascade_down : bool
            Refer :meth:`remove_component`.
        force : bool
            Refer :meth:`remove_component`.

        Raises
        ------
        ISNotStored
            Raised if the inputs do not match any components in the system.
        ISOperationNotAllowed
            Raised if there is more than one component with component type and name.

        Examples
        --------
        >>> generators = system.remove_by_name(Generator, "gen1")
        """
        component = self.get_component(component_type, name)
        return self.remove_component(component, cascade_down=cascade_down, force=force)

    def remove_component_by_uuid(
        self, uuid: UUID, cascade_down: bool = True, force: bool = False
    ) -> None:
        """Remove the component with uuid from the system.

        Parameters
        ----------
        uuid : UUID
        cascade_down : bool
            Refer :meth:`remove_component`.
        force : bool
            Refer :meth:`remove_component`.

        Raises
        ------
        ISNotStored
            Raised if the UUID is not stored in the system.

        Examples
        --------
        >>> uuid = UUID("714c8311-8dff-4ae2-aa2e-30779a317d42")
        >>> generator = system.remove_component_by_uuid(uuid)
        """
        component = self.get_component_by_uuid(uuid)
        return self.remove_component(component, cascade_down=cascade_down, force=force)

    def remove_supplemental_attribute(self, attribute: SupplementalAttribute) -> None:
        """Remove the supplemental attribute from the system."""
        self._supplemental_attr_mgr.raise_if_not_attached(attribute)
        if self.has_time_series(attribute):
            for metadata in self._time_series_mgr.list_time_series_metadata(attribute):
                self.remove_time_series(
                    attribute,
                    time_series_type=metadata.get_time_series_data_type(),
                    name=metadata.name,
                    **metadata.features,
                )
        return self._supplemental_attr_mgr.remove(attribute)

    def remove_supplemental_attribute_from_component(
        self,
        component: Component,
        attribute: SupplementalAttribute,
    ) -> None:
        """Remove the association between the component and supplemental attribute.
        If the attribute is not attached to any other components, remove it from the system.
        """
        self._supplemental_attr_mgr.remove_attribute_from_component(component, attribute)

    def update_components(
        self,
        component_type: Type[Component],
        update_func: Callable,
        filter_func: Callable | None = None,
    ) -> None:
        """Update multiple components of a given type.

        Parameters
        ----------
        component_type : Type[Component]
            Type of component to update. Can be abstract.
        update_func : Callable
            Function to call on each component. Must take a component as a single argument.
        filter_func : Callable | None
            Optional function to filter the components to update. Must take a component as a
            single argument.

        Examples
        --------
        >>> system.update_components(Generator, lambda x: x.active_power *= 10)
        """
        return self._component_mgr.update(component_type, update_func, filter_func=filter_func)

    def add_time_series(
        self,
        time_series: TimeSeriesData,
        *owners: Component | SupplementalAttribute,
        context: TimeSeriesStorageContext | None = None,
        **features: Any,
    ) -> TimeSeriesKey:
        """Store a time series array for one or more components or supplemental attributes.

        Parameters
        ----------
        time_series : TimeSeriesData
            Time series data to store.
        owners : Component | SupplementalAttribute
            Add the time series to all of these components or supplemental attributes.
        features : Any
            Key/value pairs to store with the time series data. Must be JSON-serializable.

        Returns
        -------
        TimeSeriesKey
            Returns a key that can be used to retrieve the time series.

        Raises
        ------
        ISAlreadyAttached
            Raised if the variable name and user attributes match any time series already
            attached to one of the owners.
        ISOperationNotAllowed
            Raised if the manager was created in read-only mode.

        Examples
        --------
        >>> gen1 = system.get_component(Generator, "gen1")
        >>> gen2 = system.get_component(Generator, "gen2")
        >>> ts = SingleTimeSeries.from_array(
            data=[0.86, 0.78, 0.81, 0.85, 0.79],
            name="active_power",
            start_time=datetime(year=2030, month=1, day=1),
            resolution=timedelta(hours=1),
        )
        >>> system.add_time_series(ts, gen1, gen2)
        """
        return self._time_series_mgr.add(
            time_series,
            *owners,
            context=context,
            **features,
        )

    def copy_time_series(
        self,
        dst: Component | SupplementalAttribute,
        src: Component | SupplementalAttribute,
        name_mapping: dict[str, str] | None = None,
    ) -> None:
        """Copy all time series from src to dst.

        Parameters
        ----------
        dst : Component
            Destination component
        src : Component
            Source component
        name_mapping : dict[str, str]
            Optionally map src names to different dst names.
            If provided and src has a time_series with a name not present in name_mapping, that
            time_series will not copied. If name_mapping is nothing then all time_series will be
            copied with src's names.

        Notes
        -----
        name_mapping is currently not implemented.

        Examples
        --------
        >>> gen1 = system.get_component(Generator, "gen1")
        >>> gen2 = system.get_component(Generator, "gen2")
        >>> system.copy_time_series(gen1, gen2)
        """
        return self._time_series_mgr.copy(dst, src, name_mapping=name_mapping)

    def get_time_series(
        self,
        owner: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] | None = None,
        start_time: datetime | None = None,
        length: int | None = None,
        context: TimeSeriesStorageContext | None = None,
        **features: str,
    ) -> Any:
        """Return a time series array.

        Parameters
        ----------
        component : Component
            Component to which the time series must be attached.
        name : str | None
            Optional, search for time series with this name.
            Required if the other inputs will match more than one time series.
        time_series_type : Type[TimeSeriesData] | None
            Optional, search for time series of this type.
            Required if the other inputs will match more than one time series.
        start_time : datetime | None
            If not None, take a slice of the time series starting at this time.
        length : int | None
            If not None, take a slice of the time series with this length.
        features : str
            Optional, search for time series with these attributes.
        context: TimeSeriesStorageContext
            Optional, connection returned by :meth:`open_time_series_store`

        Raises
        ------
        ISNotStored
            Raised if no time series matches the inputs.
            Raised if the inputs match more than one time series.
        ISOperationNotAllowed
            Raised if the inputs match more than one time series.

        Examples
        --------
        >>> gen1 = system.get_component(Generator, "gen1")
        >>> ts_full = system.get_time_series(gen1, "active_power")
        >>> ts_slice = system.get_time_series(
            gen1,
            "active_power",
            start_time=datetime(year=2030, month=1, day=1, hour=5),
            length=5,
        )

        See Also
        --------
        list_time_series
        """
        return self._time_series_mgr.get(
            owner,
            name=name,
            time_series_type=time_series_type,
            start_time=start_time,
            length=length,
            context=context,
            **features,
        )

    def get_time_series_by_key(
        self, owner: Component | SupplementalAttribute, key: TimeSeriesKey
    ) -> Any:
        """Return a time series array by key."""
        return self._time_series_mgr.get_by_key(owner, key)

    def has_time_series(
        self,
        owner: Component | SupplementalAttribute,
        name: Optional[str] = None,
        time_series_type: Type[TimeSeriesData] = SingleTimeSeries,
        **features: str,
    ) -> bool:
        """Return True if the component has time series matching the inputs.

        Parameters
        ----------
        component : Component
            Component to check for matching time series.
        name : str | None
            Optional, search for time series with this name.
        time_series_type : Type[TimeSeriesData]
            Optional, search for time series with this type.
        features : str
            Optional, search for time series with these attributes.
        """
        return self.time_series.has_time_series(
            owner,
            name=name,
            time_series_type=time_series_type,
            **features,
        )

    def list_time_series(
        self,
        component: Component,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] = SingleTimeSeries,
        start_time: datetime | None = None,
        length: int | None = None,
        **features: Any,
    ) -> list[TimeSeriesData]:
        """Return all time series that match the inputs.

        Parameters
        ----------
        component : Component
            Component to which the time series must be attached.
        name : str | None
            Optional, search for time series with this name.
        time_series_type : Type[TimeSeriesData] | None
            Optional, search for time series with this type.
        start_time : datetime | None
            If not None, take a slice of the time series starting at this time.
        length : int | None
            If not None, take a slice of the time series with this length.
        features : str
            Optional, search for time series with these attributes.

        Examples
        --------
        >>> gen1 = system.get_component(Generator, "gen1")
        >>> for ts in system.list_time_series(gen1):
            print(ts)
        """
        return self._time_series_mgr.list_time_series(
            component,
            name=name,
            time_series_type=time_series_type,
            start_time=start_time,
            length=length,
            **features,
        )

    def list_time_series_keys(
        self,
        owner: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] = SingleTimeSeries,
        **features: Any,
    ) -> list[TimeSeriesKey]:
        """Return all time series keys that match the inputs.

        Parameters
        ----------
        owner : Component | SupplementalAttribute
            Component to which the time series must be attached.
        name : str | None
            Optional, search for time series with this name.
        time_series_type : Type[TimeSeriesData] | None
            Optional, search for time series with this type.
        features : str
            Optional, search for time series with these attributes.

        Examples
        --------
        >>> gen1 = system.get_component(Generator, "gen1")
        >>> for key in system.list_time_series_keys(gen1):
        ...     time_series = system.get_time_series_by_key(gen1, key)
        """
        return self.time_series.list_time_series_keys(
            owner,
            name=name,
            time_series_type=time_series_type,
            **features,
        )

    def list_time_series_metadata(
        self,
        component: Component,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] = SingleTimeSeries,
        **features: Any,
    ) -> list[TimeSeriesMetadata]:
        """Return all time series metadata that match the inputs.

        Parameters
        ----------
        component : Component
            Component to which the time series must be attached.
        name : str | None
            Optional, search for time series with this name.
        time_series_type : Type[TimeSeriesData] | None
            Optional, search for time series with this type.
        features : str
            Optional, search for time series with these attributes.

        Examples
        --------
        >>> gen1 = system.get_component(Generator, "gen1")
        >>> for metadata in system.list_time_series_metadata(gen1):
            print(metadata)
        """
        return self.time_series.list_time_series_metadata(
            component,
            name=name,
            time_series_type=time_series_type,
            **features,
        )

    def remove_time_series(
        self,
        *owners: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] = SingleTimeSeries,
        **features: Any,
    ) -> None:
        """Remove all time series arrays attached to the components or supplemental attributes
        matching the inputs.

        Parameters
        ----------
        owners
            Affected components or supplemental attributes
        name : str | None
            Optional, search for time series with this name.
        time_series_type : Type[TimeSeriesData]
            Optional, search for time series with this type.
        features : str
            Optional, search for time series with these attributes.

        Raises
        ------
        ISNotStored
            Raised if no time series match the inputs.
        ISOperationNotAllowed
            Raised if the manager was created in read-only mode.

        Examples
        --------
        >>> gen1 = system.get_component(Generator, "gen1")
        >>> system.remove_time_series(gen1, "active_power")
        """
        return self._time_series_mgr.remove(
            *owners,
            name=name,
            time_series_type=time_series_type,
            **features,
        )

    @contextmanager
    def open_time_series_store(
        self, mode: FileMode = "r+"
    ) -> Generator[TimeSeriesStorageContext, None, None]:
        """Open a connection to the time series store. This can improve performance when
        reading or writing many time series arrays for specific backends (chronify and HDF5).
        It will also rollback any changes if an exception is raised.

        Returns
        -------
        Any
            An opaque object specific to the type of store being used. The object should be
            passed to all add_time_series/get_time_series methods.

        Examples
        --------
        >>> with system.open_time_series_store() as conn:
        ...     system.add_time_series(ts1, gen1)
        ...     system.add_time_series(ts2, gen1)
        """
        with self._time_series_mgr.open_time_series_store(mode=mode) as conn:
            yield conn

    @contextmanager
    def open_metadata_store(self) -> Generator[sqlite3.Connection, None, None]:
        """Open a connection to the metadata store.

        This transaction applies to supplemental attribute metadata stored in SQLite.
        Any failure rolls back the SQLite transaction and in-memory supplemental
        attribute cache updates.

        Returns
        -------
        sqlite3.Connection
            SQLite connection for metadata operations.

        Examples
        --------
        >>> with system.open_metadata_store():
        ...     system.add_supplemental_attribute(bus, geo1)
        ...     system.add_supplemental_attribute(bus, geo2)
        """
        with self._supplemental_attr_mgr.open_metadata_store() as connection:
            yield connection

    def serialize_system_attributes(self) -> dict[str, Any]:
        """Allows subclasses to serialize attributes at the root level."""
        return {}

    def deserialize_system_attributes(self, data: dict[str, Any]) -> None:
        """Allows subclasses to deserialize attributes stored in the JSON at the root level.

        The method should modify self with its custom attributes in data.
        """

    def handle_data_format_upgrade(
        self, data: dict[str, Any], from_version: str | None, to_version: str | None
    ) -> None:
        """Allows subclasses to upgrade data models.

        The parameter data contains the full contents of the serialized JSON file.
        The method should modify the data models in-place.
        """

    def merge_system(self, other: "System") -> None:
        """Merge the contents of another system into this one."""
        msg = "merge_system"
        raise NotImplementedError(msg)

    # TODO: add delete methods that (1) don't raise if not found and (2) don't return anything?

    def convert_storage(self, **kwargs) -> None:
        """
        Converts the time series storage medium.

        Parameters
        ----------
        **kwargs:
            The same keys as TIME_SERIES_KWARGS in time_series_manager.py
            {
                "time_series_storage_type": TimeSeriesStorageType = TimeSeriesStorageType.ARROW,
                "time_series_read_only": bool = False,
                "time_series_directory": Path | None = None,
            }

            Only arguments that need to be changed from the default TIME_SERIES_KWARGS
            need to be passed

        Examples
        --------

        # Initialize the system (defaults to Arrow storage)
        >>> system = infrasys.System(auto_add_composed_components=True)

        # Add components and time series data
        >>> generator, bus, load_data = create_some_data()
        >>> system.add_components(generator, bus)
        >>> system.add_time_series(load_data, generator)

        # Convert the storage to in_memory
        >>> system.convert_storage(time_series_storage_type=TimeSeriesStorageType.MEMORY)
        """
        self._time_series_mgr.convert_storage(**kwargs)

    @property
    def _components(self) -> ComponentManager:
        """Return the component manager."""
        return self._component_mgr

    @property
    def data_format_version(self) -> str | None:
        """Return the data format version of the component models."""
        return self._data_format_version

    @data_format_version.setter
    def data_format_version(self, data_format_version: str) -> None:
        """Set the data format version for the component models."""
        self._data_format_version = data_format_version

    @property
    def name(self) -> str | None:
        """Return the name of the system."""
        return self._name

    @name.setter
    def name(self, name: Optional[str]) -> None:
        """Set the name of the system."""
        self._name = name

    @property
    def description(self) -> str | None:
        """Return the description of the system."""
        return self._description

    @description.setter
    def description(self, description: str | None) -> None:
        """Set the description of the system."""
        self._description = description

    @property
    def label(self) -> str:
        """Provides a description of the system."""
        name = self.name or str(self.uuid)
        return make_label(self.__class__.__name__, name)

    @property
    def time_series(self) -> TimeSeriesManager:
        """Return the time series manager."""
        return self._time_series_mgr

    @property
    def uuid(self) -> UUID:
        """Return the UUID of the system."""
        return self._uuid

    def get_time_series_directory(self) -> Path | None:
        """Return the directory containing time series files. Will be none for in-memory time
        series.
        """
        return self.time_series.storage.get_time_series_directory()

    def _deserialize_components(self, components: list[dict[str, Any]]) -> None:
        """Deserialize components from dictionaries and add them to the system."""
        cached_types = CachedTypeHelper()
        skipped_types = self._deserialize_components_first_pass(components, cached_types)
        if skipped_types:
            self._deserialize_components_nested(skipped_types, cached_types)

    def _deserialize_components_first_pass(
        self, components: list[dict], cached_types: CachedTypeHelper
    ) -> dict[Type, list[dict[str, Any]]]:
        deserialized_types = set()
        skipped_types: dict[Type, list[dict[str, Any]]] = defaultdict(list)
        for component_dict in components:
            component = self._try_deserialize_component(component_dict, cached_types)
            if component is None:
                metadata = SerializedTypeMetadata.validate_python(component_dict[TYPE_METADATA])
                assert isinstance(metadata, SerializedBaseType)
                component_type = cached_types.get_type(metadata)
                skipped_types[component_type].append(component_dict)
            else:
                deserialized_types.add(type(component))

        cached_types.add_deserialized_types(deserialized_types)
        return skipped_types

    def _deserialize_components_nested(
        self,
        skipped_types: dict[Type, list[dict[str, Any]]],
        cached_types: CachedTypeHelper,
    ) -> None:
        max_iterations = len(skipped_types)
        for _ in range(max_iterations):
            deserialized_types = set()
            for component_type, components in skipped_types.items():
                component = self._try_deserialize_component(components[0], cached_types)
                if component is None:
                    continue
                if len(components) > 1:
                    for component_dict in components[1:]:
                        component = self._try_deserialize_component(component_dict, cached_types)
                        assert component is not None
                deserialized_types.add(component_type)

            for component_type in deserialized_types:
                skipped_types.pop(component_type)
            cached_types.add_deserialized_types(deserialized_types)

        if skipped_types:
            msg = f"Bug: still have types remaining to be deserialized: {skipped_types.keys()}"
            raise Exception(msg)

    def _try_deserialize_component(
        self, component: dict[str, Any], cached_types: CachedTypeHelper
    ) -> Any:
        actual_component = None
        values = self._deserialize_fields(component, cached_types)
        if values is None:
            return None

        metadata = SerializedTypeMetadata.validate_python(component[TYPE_METADATA])
        component_type = cached_types.get_type(metadata)
        actual_component = component_type(**values)
        self._components.add(actual_component, deserialization_in_progress=True)
        return actual_component

    def _deserialize_fields(
        self, component: dict[str, Any], cached_types: CachedTypeHelper
    ) -> dict | None:
        values = {}
        for field, value in component.items():
            if isinstance(value, dict) and TYPE_METADATA in value:
                metadata = SerializedTypeMetadata.validate_python(value[TYPE_METADATA])
                if isinstance(metadata, SerializedComponentReference):
                    composed_value = self._deserialize_composed_value(metadata, cached_types)
                    if composed_value is None:
                        return None
                    values[field] = composed_value
                elif isinstance(metadata, SerializedQuantityType):
                    quantity_type = cached_types.get_type(metadata)
                    values[field] = quantity_type(value=value["value"], units=value["units"])
                else:
                    msg = f"Bug: unhandled type: {field=} {value=}"
                    raise NotImplementedError(msg)
            elif (
                isinstance(value, list)
                and value
                and isinstance(value[0], dict)
                and TYPE_METADATA in value[0]
                and value[0][TYPE_METADATA]["serialized_type"]
                == SerializedType.COMPOSED_COMPONENT.value
            ):
                metadata = SerializedTypeMetadata.validate_python(value[0][TYPE_METADATA])
                assert isinstance(metadata, SerializedComponentReference)
                composed_values = self._deserialize_composed_list(value, cached_types)
                if composed_values is None:
                    return None
                values[field] = composed_values
            elif field != TYPE_METADATA:
                values[field] = value

        return values

    def _deserialize_composed_value(
        self, metadata: SerializedComponentReference, cached_types: CachedTypeHelper
    ) -> Any:
        component_type = cached_types.get_type(metadata)
        if cached_types.allowed_to_deserialize(component_type):
            return self._components.get_by_uuid(metadata.uuid)
        return None

    def _deserialize_composed_list(
        self, components: list[dict[str, Any]], cached_types: CachedTypeHelper
    ) -> list[Any] | None:
        deserialized_components = []
        for component in components:
            metadata = SerializedTypeMetadata.validate_python(component[TYPE_METADATA])
            assert isinstance(metadata, SerializedComponentReference)
            component_type = cached_types.get_type(metadata)
            if cached_types.allowed_to_deserialize(component_type):
                deserialized_components.append(self._components.get_by_uuid(metadata.uuid))
            else:
                return None
        return deserialized_components

    def _deserialize_supplemental_attributes(
        self, supplemental_attributes: list[dict[str, Any]]
    ) -> None:
        """Deserialize supplemental_attributes from dictionaries and add them to the system."""
        cached_types = CachedTypeHelper()
        for sa_dict in supplemental_attributes:
            metadata = SerializedTypeMetadata.validate_python(sa_dict[TYPE_METADATA])
            supplemental_attribute_type = cached_types.get_type(metadata)
            values = self._deserialize_fields(sa_dict, cached_types)
            attr = supplemental_attribute_type(**values)
            self._supplemental_attr_mgr.add(None, attr, deserialization_in_progress=True)
            cached_types.add_deserialized_type(supplemental_attribute_type)

    @staticmethod
    def _make_time_series_directory(filename: Path) -> Path:
        return filename.parent / (filename.stem + "_time_series")

    def show_components(
        self,
        component_type: Type[Component],
        show_uuid: bool = False,
        show_time_series: bool = False,
        show_supplemental: bool = False,
    ) -> None:
        """Display a table of components of the specified type.

        Parameters
        ----------
        component_type : Type[Component]
            The type of components to display. If component_type is an abstract type,
            all matching subtypes will be included.
        show_uuid : bool
            Whether to include the UUID column in the table. Defaults to False.
        show_time_series : bool
            Whether to include the Time Series count column in the table. Defaults to False.
        show_time_series : bool
            Whether to include the Supplemental Attributes count column in the table. Defaults to False.

        Examples
        --------
        >>> system.show_components(Generator)  # Shows only names
        >>> system.show_components(Bus, show_uuid=True)
        >>> system.show_components(Generator, show_time_series=True)
        >>> system.show_components(Generator, show_supplemental=True)
        """
        components = list(self.get_components(component_type))

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

        if show_uuid:
            table.add_column("UUID", min_width=36, justify="left")
        if show_time_series:
            table.add_column("Has Time Series", min_width=12, justify="right")
        if show_supplemental:
            table.add_column("Has Supplemental Attributes", min_width=12, justify="right")

        sorted_components = sorted(components, key=lambda x: getattr(x, "name", x.label))

        for component in sorted_components:
            row_data = [component.name]

            if show_uuid:
                row_data.append(str(component.uuid))
            if show_time_series:
                row_data.append(str(len(self.list_time_series_metadata(component))))
            if show_supplemental:
                row_data.append(
                    str(len(self.get_supplemental_attributes_with_component(component)))
                )

            table.add_row(*row_data)

        _pprint(table)

    def info(self):
        info = SystemInfo(system=self)
        info.render()


class SystemInfo:
    """Class to store system component info"""

    def __init__(self, system) -> None:
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

        # System table
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

        # Component and time series table
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
