"""Migration functions for legacy UUID-based data to integer IDs."""

from typing import Any

from infrasys.serialization import TYPE_METADATA, SerializedType


def upgrade_legacy_component_ids(system_data: dict[str, Any]) -> None:
    """Upgrade legacy serialized component references from UUIDs to integer IDs in-place.

    .. warning::
        This function mutates *system_data* and all nested component dicts in-place.
        Callers that need to preserve the original serialized data should pass a deep
        copy or save a snapshot before calling this function.
    """
    components = system_data.get("components", [])
    supplemental_attributes = system_data.get("supplemental_attributes", [])
    uuid_to_id: dict[str, int] = {}
    next_id = 1

    for item in [*components, *supplemental_attributes]:
        existing_id = item.get("id")
        if existing_id is None:
            existing_id = next_id
            item["id"] = existing_id
        next_id = max(next_id, int(existing_id) + 1)

        legacy_uuid = item.get("legacy_uuid") or item.get("uuid")
        if legacy_uuid is not None:
            item["legacy_uuid"] = legacy_uuid
            uuid_to_id[str(legacy_uuid)] = int(existing_id)
            item.pop("uuid", None)

    for component in components:
        _upgrade_component_reference_ids(component, uuid_to_id)


def _upgrade_component_reference_ids(data: Any, uuid_to_id: dict[str, int]) -> None:
    """Upgrade legacy UUID component references to integer IDs using an explicit stack.

    Mutates *data* in-place. Uses an iterative stack rather than recursion
    to avoid hitting Python's recursion limit on deeply nested structures.
    """
    stack = [data]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            metadata = current.get(TYPE_METADATA)
            if (
                isinstance(metadata, dict)
                and metadata.get("serialized_type") == SerializedType.COMPOSED_COMPONENT.value
            ):
                if "id" not in metadata:
                    legacy_uuid = metadata.get("legacy_uuid") or metadata.get("uuid")
                    if legacy_uuid is not None and str(legacy_uuid) in uuid_to_id:
                        metadata["id"] = uuid_to_id[str(legacy_uuid)]
                if "uuid" in metadata:
                    metadata["legacy_uuid"] = metadata.pop("uuid")
                continue

            for value in current.values():
                stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
