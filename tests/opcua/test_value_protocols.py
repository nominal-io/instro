import pytest
from typing_extensions import TypeVar

from instro.unstable.opcua.types import (
    AccessLevelExType,
    AccessLevelType,
    AccessRestrictionType,
    AttributeWriteMask,
    DateTime,
    Duration,
    EventNotifierType,
    LocalizedText,
    NodeId,
    PermissionType,
    QualifiedName,
    RelativePath,
    SecurityPolicy,
    StatusCode,
)


def test_node_id_python_ordering_and_equal_key_lookup():
    first = NodeId("ns=2;i=7")
    last = NodeId("ns=2;i=19")
    assert first < last
    assert {first: "retained"}[NodeId("ns=2;i=7")] == "retained"


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (DateTime(116_444_736_000_000_000), DateTime.parse("1970-01-01T00:00:00Z")),
        (Duration(1, 500_000_000), Duration.from_millis(1500)),
        (SecurityPolicy("basic256sha256"), SecurityPolicy.BASIC256_SHA256),
        (StatusCode(0), StatusCode(0)),
        (QualifiedName(2, "Temperature"), QualifiedName(2, "Temperature")),
        (
            RelativePath("/Objects/2:Temperature"),
            RelativePath.from_qualified_names([QualifiedName(0, "Objects"), QualifiedName(2, "Temperature")]),
        ),
        (LocalizedText("Temperature", "en-US"), LocalizedText("Temperature", "en-US")),
        (AccessLevelExType(0x8000_0201), AccessLevelExType(0x8000_0201)),
    ],
)
def test_equal_native_values_can_retrieve_dictionary_entries(first, second):
    assert first == second
    assert {first: "retained"}[second] == "retained"


def test_localized_text_keys_distinguish_native_null_and_empty_fields():
    absent = LocalizedText()
    values = {absent, LocalizedText(""), LocalizedText(locale="")}

    assert len(values) == 3


T = TypeVar(
    "T",
    AccessLevelType,
    AccessLevelExType,
    AccessRestrictionType,
    AttributeWriteMask,
    EventNotifierType,
    PermissionType,
)


@pytest.mark.parametrize(
    ("wrapper", "left", "right"),
    [
        (AccessLevelType, 1, 5),
        (AccessLevelExType, 1, 0x8000_0201),
        (AccessRestrictionType, 1, 0x8003),
        (AttributeWriteMask, 1, 0x8000_0021),
        (EventNotifierType, 1, 5),
        (PermissionType, 1, 33),
    ],
)
def test_flag_operators_keep_wrapper_and_literal_bits(wrapper: type[T], left: int, right: int):
    combined_value = left | right
    combined = wrapper(left) | wrapper(right)
    assert type(combined) is wrapper
    assert combined == wrapper(combined_value)
    assert int(combined) == combined_value

    common_value = left & right
    common = wrapper(left) & wrapper(right)
    assert type(common) is wrapper
    assert common == wrapper(common_value)
    assert int(common) == common_value
