import sqlite3

from infrasys import SUPPLEMENTAL_ATTRIBUTE_ASSOCIATIONS_TABLE
from infrasys.migrations.metadata_migration import (
    component_needs_metadata_migration,
    migrate_component_metadata,
)

from .models.simple_system import SimpleSystem


def _legacy_metadata(component_type):
    return {
        "fields": {
            "module": f"package.module.{component_type.lower()}",
            "type": component_type,
            "serialized_type": "base",
        }
    }


def test_migrate_flattens_nested_fields():
    components = [
        {
            "name": "comp1",
            "__metadata__": _legacy_metadata("CompA"),
            "nested": {"value": 1, "__metadata__": _legacy_metadata("CompB")},
            "children": [{"name": "child1", "__metadata__": _legacy_metadata("CompC")}],
        }
    ]

    result = migrate_component_metadata(components)

    assert result[0]["__metadata__"]["type"] == "CompA"
    assert "fields" not in result[0]["__metadata__"]
    assert result[0]["nested"]["__metadata__"]["type"] == "CompB"
    assert result[0]["children"][0]["__metadata__"]["type"] == "CompC"


def test_migrate_empty_list_field():
    components = [
        {
            "name": "bus1",
            "__metadata__": _legacy_metadata("Bus"),
            "voltagelimits": [],
        }
    ]

    result = migrate_component_metadata(components)

    assert result[0]["voltagelimits"] == []
    assert result[0]["__metadata__"]["type"] == "Bus"


def test_migrate_legacy_metadata_with_empty_lists():
    components = [
        {
            "name": "bus1",
            "__metadata__": _legacy_metadata("Bus"),
            "voltagelimits": [],
            "children": [{"name": "child1", "__metadata__": _legacy_metadata("CompC")}],
        }
    ]

    result = migrate_component_metadata(components)

    assert result[0]["voltagelimits"] == []
    assert result[0]["__metadata__"]["type"] == "Bus"
    assert result[0]["children"][0]["__metadata__"]["type"] == "CompC"


def test_migrate_empty_component_list():
    assert migrate_component_metadata([]) == []


def test_component_needs_metadata_migration():
    legacy = {"name": "comp1", "__metadata__": _legacy_metadata("CompA")}
    flat = {
        "name": "comp2",
        "__metadata__": {
            "module": "package.module.comp_b",
            "type": "CompB",
            "serialized_type": "base",
        },
    }

    assert component_needs_metadata_migration(legacy) is True
    assert component_needs_metadata_migration(flat) is False


def test_from_json_db_without_supplemental_attribute_table(tmp_path, simple_system):
    """Loading a system whose DB predates supplemental attributes should work."""
    fpath = tmp_path / "legacy"
    fname = "system.json"
    simple_system.save(fpath, filename=fname)

    db_file = next(fpath.rglob("time_series_metadata.db"))
    con = sqlite3.connect(db_file)
    con.execute(f"DROP TABLE {SUPPLEMENTAL_ATTRIBUTE_ASSOCIATIONS_TABLE}")
    con.commit()
    con.close()

    loaded = SimpleSystem.from_json(fpath / fname)
    assert loaded.get_num_supplemental_attributes() == 0
