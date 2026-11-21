from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Protocol, cast
from unittest.mock import AsyncMock

import pytest
from asyncua import ua
from asyncua.common.utils import ServiceError
from asyncua.server.subscription_service import SubscriptionService
from asyncua.ua import CreateMonitoredItemsParameters, MonitoredItemCreateResult, UInt32
from asyncua.ua.status_codes import StatusCodes

from instro.unstable.opcua import Node, OpcUaClient, OpcUaError
from instro.unstable.opcua.types import Duration, LocalizedText, NodeId, RelativePath, StatusCode
from tests.opcua.conftest import DefaultConnectionOptions


class MonitoredItemRejection(Protocol):
    status_code: StatusCode
    node: Node


@pytest.fixture
def allocations(service: SubscriptionService, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    create = AsyncMock(wraps=service.create_subscription)
    monkeypatch.setattr(service, "create_subscription", create)
    return create


@pytest.fixture
def item_requests(service: SubscriptionService, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    create = AsyncMock(wraps=service.create_monitored_items)
    monkeypatch.setattr(service, "create_monitored_items", create)
    return create


def test_mixed_inputs_preserve_request_order_and_node_association(
    client: OpcUaClient,
    client_node: Node,
    item_requests: AsyncMock,
) -> None:
    inputs: list[Node | RelativePath | str] = [
        RelativePath("/Objects/2:Rig/2:Boolean"),
        client_node,
        "/Objects/2:Rig/2:Float",
    ]

    with client.subscribe(inputs, attribute="DisplayName", publishing_interval=0.001) as samples:
        received = [next(samples) for _ in inputs]

    assert item_requests.call_args is not None

    parameters = item_requests.call_args.args[0]
    assert isinstance(parameters, ua.CreateMonitoredItemsParameters)

    requests = parameters.ItemsToCreate
    assert [item.ItemToMonitor.NodeId.to_string() for item in requests] == [
        "ns=2;s=Boolean",
        "ns=2;s=Temperature",
        "ns=2;s=Float",
    ]
    assert [item.ItemToMonitor.AttributeId for item in requests] == [4, 4, 4]
    assert [item.RequestedParameters.ClientHandle for item in requests] == [1, 2, 3]

    display_names: dict[str, str] = {}

    display_names = {
        "ns=2;s=Boolean": "Boolean",
        "ns=2;s=Temperature": "Temperature",
        "ns=2;s=Float": "Float",
    }

    for received_node, value in received:
        text = value.value
        assert isinstance(text, LocalizedText)
        assert display_names[str(received_node.node_id)] == text.text

    assert all(received_node.browse_path.starting_node == NodeId("i=84") for received_node, _ in received)


def test_invalid_groups_never_allocate(
    client: OpcUaClient,
    client_node: Node,
    endpoint: str,
    connection_options: DefaultConnectionOptions,
    allocations: AsyncMock,
) -> None:
    with OpcUaClient.connect(endpoint, **connection_options) as foreign:
        groups: list[list[object]] = [
            [],
            [object()],
            [client_node, client_node],
            [client_node, "/Objects/2:Rig/2:Temperature"],
            [foreign.objects],
        ]

        for group in groups:
            with pytest.raises((TypeError, ValueError)):
                client.subscribe(group)  # type: ignore[arg-type]

    allocations.assert_not_called()


def test_input_iterator_exception_is_preserved_before_allocation(
    client: OpcUaClient,
    client_node: Node,
    allocations: AsyncMock,
) -> None:
    failure = LookupError("input failed")

    def inputs() -> Iterator[Node]:
        yield client_node
        raise failure

    with pytest.raises(LookupError) as raised:
        client.subscribe(inputs())

    assert raised.value is failure
    allocations.assert_not_called()


def test_item_rejections_are_ordered_and_rolled_back(
    client: OpcUaClient,
    service: SubscriptionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject(
        _parameters: CreateMonitoredItemsParameters,
    ) -> list[MonitoredItemCreateResult]:
        codes = [StatusCodes.BadNodeIdUnknown, StatusCodes.BadAttributeIdInvalid]
        return [MonitoredItemCreateResult(StatusCode=ua.StatusCode(ua.UInt32(code))) for code in codes]

    monkeypatch.setattr(service, "create_monitored_items", reject)

    with pytest.raises(ExceptionGroup) as raised:
        client.subscribe(["/Objects/2:Rig/2:Boolean", "/Objects/2:Rig/2:Temperature"], publishing_interval=0.001)

    rejections = cast(tuple[MonitoredItemRejection, ...], raised.value.exceptions)

    assert [error.status_code.code for error in rejections] == [
        StatusCodes.BadNodeIdUnknown,
        StatusCodes.BadAttributeIdInvalid,
    ]
    assert [str(error.node.node_id) for error in rejections] == ["ns=2;s=Boolean", "ns=2;s=Temperature"]
    assert service.subscriptions == {}


def test_setup_error_survives_failed_rollback(
    client: OpcUaClient,
    client_node: Node,
    service: SubscriptionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempted: list[int] = []

    async def fail_cleanup(ids: list[int]) -> list[ua.StatusCode]:
        attempted.extend(ids)
        return [ua.StatusCode(UInt32(StatusCodes.BadInternalError))]

    monkeypatch.setattr(service, "create_monitored_items", AsyncMock(side_effect=ServiceError(StatusCodes.BadTimeout)))
    monkeypatch.setattr(service, "delete_subscriptions", fail_cleanup)

    with pytest.raises(OpcUaError) as raised:
        client.subscribe([client_node], publishing_interval=0.001, lifetime_count=60_000)

    assert raised.value.status_code.code == 0x800A0000
    assert attempted == list(service.subscriptions)


def test_wrong_item_result_count_rolls_back(
    client: OpcUaClient,
    client_node: Node,
    service: SubscriptionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "create_monitored_items", AsyncMock(return_value=[]))

    with pytest.raises(OpcUaError) as raised:
        client.subscribe([client_node], publishing_interval=0.001)

    assert raised.value.status_code.code == 0x80010000
    assert service.subscriptions == {}


@pytest.mark.parametrize("interval", [0.125, timedelta(milliseconds=125), Duration(0, 125_000_000)])
def test_nondefault_settings_reach_native_requests(
    client: OpcUaClient,
    client_node: Node,
    allocations: AsyncMock,
    item_requests: AsyncMock,
    interval: float | timedelta | Duration,
) -> None:
    with client.subscribe(
        [client_node],
        attribute="DisplayName",
        publishing_interval=interval,
        lifetime_count=30,
        max_keep_alive_count=5,
        max_notifications_per_publish=7,
        priority=3,
        publishing_enabled=False,
        sampling_interval=-0.25,
        queue_size=4,
        discard_oldest=True,
    ):
        pass

    assert allocations.call_args is not None

    group = allocations.call_args.args[0]
    assert isinstance(group, ua.CreateSubscriptionParameters)
    assert group.RequestedPublishingInterval == 125.0
    assert item_requests.call_args is not None
    assert (
        group.RequestedLifetimeCount,
        group.RequestedMaxKeepAliveCount,
        group.MaxNotificationsPerPublish,
        group.Priority,
        group.PublishingEnabled,
    ) == (30, 5, 7, 3, False)

    parameters = item_requests.call_args.args[0]
    assert isinstance(parameters, ua.CreateMonitoredItemsParameters)
    assert isinstance(item := parameters.ItemsToCreate[0], ua.MonitoredItemCreateRequest)
    assert (
        item.ItemToMonitor.AttributeId,
        item.RequestedParameters.SamplingInterval,
        item.RequestedParameters.QueueSize,
        item.RequestedParameters.DiscardOldest,
    ) == (4, -0.25, 4, True)


@pytest.mark.parametrize(
    "options,exception",
    [
        ({"publishing_interval": True}, TypeError),
        ({"publishing_interval": -1}, ValueError),
        ({"publishing_interval": float("nan")}, ValueError),
        ({"poll_interval": 0}, ValueError),
        ({"poll_interval": float("inf")}, ValueError),
        ({"sampling_interval": float("nan")}, ValueError),
        ({"priority": 256}, ValueError),
        ({"priority": True}, TypeError),
        ({"lifetime_count": -1}, ValueError),
        ({"queue_size": 2**32}, ValueError),
        ({"attribute": "Invalid"}, ValueError),
        ({"unknown": 1}, TypeError),
    ],
)
def test_invalid_subscription_options_never_allocate(
    client: OpcUaClient,
    client_node: Node,
    allocations: AsyncMock,
    options: dict[str, object],
    exception: type[TypeError] | type[ValueError],
) -> None:
    with pytest.raises(exception):
        client.subscribe([client_node], **options)  # type: ignore[arg-type]

    allocations.assert_not_called()
