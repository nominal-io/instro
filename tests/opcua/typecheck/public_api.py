from typing import assert_type

from instro.unstable.opcua.types import (
    EnumDefinition,
    EnumField,
    NodeId,
    PermissionType,
    RolePermissionType,
    StructureDefinition,
    StructureField,
)


def structure_fields(
    role: RolePermissionType,
    structure: StructureDefinition,
    field: StructureField,
    enumeration: EnumDefinition,
    enum_field: EnumField,
) -> None:
    assert_type(role.role_id, NodeId)
    assert_type(role.permissions, PermissionType)
    assert_type(structure.fields, list[StructureField])
    assert_type(field.array_dimensions, list[int] | None)
    assert_type(field.is_optional, bool)
    assert_type(enumeration.fields, list[EnumField])
    assert_type(enum_field.value, int)
    assert_type(enum_field.display_name.text, str | None)
    assert_type(enum_field.display_name.locale, str | None)
