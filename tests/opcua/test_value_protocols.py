import pytest

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


@pytest.mark.parametrize(
    ("wrapper", "left", "right", "union", "intersection"),
    [
        (AccessLevelType, 1, 5, 5, 1),
        (AccessLevelExType, 1, 513, 513, 1),
        (AccessRestrictionType, 1, 3, 3, 1),
        (AttributeWriteMask, 1, 33, 33, 1),
        (EventNotifierType, 1, 5, 5, 1),
        (PermissionType, 1, 33, 33, 1),
    ],
)
def test_flag_operators_keep_wrapper_and_literal_bits(wrapper, left, right, union, intersection):
    combined = wrapper(left) | wrapper(right)
    common = wrapper(left) & wrapper(right)
    assert type(combined) is wrapper
    assert int(combined) == union
    assert type(common) is wrapper
    assert int(common) == intersection
