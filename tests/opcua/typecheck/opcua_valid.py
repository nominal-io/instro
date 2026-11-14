from collections.abc import Iterator
from datetime import timedelta
from typing import assert_type

from instro.unstable.opcua import IdentityToken, Node, OpcUaClient, Subscription
from instro.unstable.opcua.types import (
    AttributePayload,
    AttributeValue,
    DateTime,
    EnumDefinition,
    NodeClass,
    RolePermissionType,
    StructureDefinition,
    Value,
)


def inspect_node(node: Node, subscription: Subscription) -> None:
    _payload: AttributeValue[AttributePayload] = node.role_permissions

    assert_type(node.node_class, AttributeValue[NodeClass])
    assert_type(node.node_class.value, NodeClass | None)
    assert_type(node.value.value, Value | None)
    assert_type(node.array_dimensions.value, list[int] | None)
    assert_type(node.role_permissions.value, list[RolePermissionType] | None)
    assert_type(node.data_type_definition.value, StructureDefinition | EnumDefinition | None)
    assert_type(node.minimum_sampling_interval.value, timedelta | None)
    assert_type(node.value.source_timestamp, DateTime | None)
    assert_type(node.value.source_unix_timestamp, int | None)
    assert_type(next(subscription), tuple[Node, AttributeValue[AttributePayload]])


def connect_and_subscribe(paths: Iterator[str]) -> None:
    with OpcUaClient.connect(
        "opc.tcp://localhost:4840",
        identity=IdentityToken.user_name("operator", "password"),
        request_timeout=timedelta(seconds=60),
        session_timeout=60_000,
    ) as client:
        with client.subscribe(paths, poll_interval=timedelta(seconds=2)) as subscription:
            assert_type(subscription, Subscription)
