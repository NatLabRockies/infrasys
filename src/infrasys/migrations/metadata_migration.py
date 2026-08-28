from infrasys.serialization import TYPE_METADATA


def component_needs_metadata_migration(component) -> bool:
    """Check if we need to migrate to new metadata format."""
    metadata = component.get(TYPE_METADATA)
    return "fields" in metadata


def migrate_component_metadata(component_list: list) -> list:
    """Migrate legacy metadata for components.

    Checks each component dict for a nested '__metadata__["fields"]' structure
    and flattens it by replacing '__metadata__' value with the 'fields' value.
    """
    if not component_list:
        return []
    for component in component_list:
        metadata = component[TYPE_METADATA]
        if isinstance(metadata, dict) and "fields" in metadata:
            component[TYPE_METADATA] = metadata["fields"]

        for key, value in component.items():
            if isinstance(value, dict):
                nested_metadata = value.get(TYPE_METADATA)
                if isinstance(nested_metadata, dict) and "fields" in nested_metadata:
                    value[TYPE_METADATA] = nested_metadata["fields"]
            elif isinstance(value, list) and value:
                if isinstance(value[0], dict):
                    nested_metadata = value[0].get(TYPE_METADATA)
                    if isinstance(nested_metadata, dict) and "fields" in nested_metadata:
                        component[key] = migrate_component_metadata(value)

    return component_list
