import pytest
from typing_extensions import TypeVar

from instro.unstable.opcua.types import (
    AccessLevelExType,
    AccessLevelType,
    AccessRestrictionType,
    AttributeWriteMask,
    EventNotifierType,
    NodeId,
    PermissionType,
)


def test_node_id_python_ordering_and_equal_key_lookup():
    first = NodeId("ns=2;i=7")
    last = NodeId("ns=2;i=19")
    assert first < last
    assert {first: "retained"}[NodeId("ns=2;i=7")] == "retained"


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
