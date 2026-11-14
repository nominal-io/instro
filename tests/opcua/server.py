import asyncio
from dataclasses import dataclass
from datetime import UTC
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, cast

from asyncua.crypto.permission_rules import User, UserRole
from asyncua.server.address_space import AttributeValue
from asyncua.server.server import Server
from asyncua.server.user_managers import UserManager
from asyncua.ua import *
from asyncua.ua.object_ids import ObjectIds

CERT_DIR = Path(__file__).with_name("certs")


def timestamp(seconds: int, micros: int = 0) -> DateTime:
    return DateTime.fromtimestamp(seconds + micros / 1_000_000, UTC)


VARIABLES = {
    "Boolean": True,
    "Int64": Int64(-9_000_000_000_000),
    "UInt64": UInt64(18_000_000_000_000),
    "Float": Float(1.25),
    "String": String("pressure µ"),
    "Guid": Guid("12345678-1234-5678-9abc-def012345678"),
    "DateTime": timestamp(5, 125000),
    "Bytes": ByteString(b"\x00\xff\x07"),
    "Array": [Int32(1), Int32(2)],
    "Temperature": Double(23.5),
    "BadData": Int32(0),
    "SourceTimestamped": Double(8.5),
    "ServerTimestamped": Double(8.5),
    "NoTimestamp": Double(8.5),
    "NestedDataValue": DataValue(),
}


@dataclass
class OpcUaTestServer:
    endpoint: str
    connection: Connection

    def request(self, value: str) -> object:
        self.connection.send(value)

        if not self.connection.poll(5):
            raise TimeoutError("OPC UA test server did not acknowledge the request")

        return self.connection.recv()


def run_server(connection: Connection) -> None:
    asyncio.run(serve(connection))


class TestUsers(UserManager):
    def get_user(
        self, iserver: Any, username: str | None = None, password: str | None = None, certificate: Any = None
    ) -> User | None:
        if username == "test-operator" and password == "test-only-password":
            return User(role=UserRole.User, name=username)

        if username is None:
            return User(role=UserRole.User)

        return None


async def serve(connection: Connection) -> None:
    server = Server(user_manager=TestUsers())

    await server.init()

    server.set_endpoint("opc.tcp://127.0.0.1:0/instro-test/")

    await server.set_application_uri("urn:instro:test-server")

    await server.load_certificate(CERT_DIR / "server-cert.pem")
    await server.load_private_key(CERT_DIR / "server-key.pem")

    server.set_security_policy(
        [
            SecurityPolicyType.NoSecurity,
            SecurityPolicyType.Basic256Sha256_Sign,
            SecurityPolicyType.Basic256Sha256_SignAndEncrypt,
        ]
    )

    server.set_identity_tokens([AnonymousIdentityToken, UserNameIdentityToken])
    await populate(server)

    observed: dict[str, object] = {}
    service = server.iserver.subscription_service
    create_subscription = service.create_subscription
    create_monitored_items = service.create_monitored_items

    async def record_subscription(
        params: CreateSubscriptionParameters, *args: Any, **kwargs: Any
    ) -> CreateSubscriptionResult:
        observed["subscription"] = (
            params.RequestedPublishingInterval,
            params.RequestedLifetimeCount,
            params.RequestedMaxKeepAliveCount,
            params.MaxNotificationsPerPublish,
            params.Priority,
        )

        return await create_subscription(params, *args, **kwargs)

    async def record_items(params: CreateMonitoredItemsParameters) -> list[MonitoredItemCreateResult]:
        observed["items"] = [
            (
                param.SamplingInterval,
                param.QueueSize,
                param.DiscardOldest,
            )
            for item in params.ItemsToCreate
            if (param := cast(MonitoringParameters, item.RequestedParameters))
        ]

        return await create_monitored_items(params)

    service.create_subscription = record_subscription  # type: ignore[method-assign]
    service.create_monitored_items = record_items  # type: ignore[method-assign]

    async with server:
        for endpoint in server.iserver.endpoints:
            if endpoint.SecurityMode == MessageSecurityMode.None_:
                for token in endpoint.UserIdentityTokens:
                    if token.TokenType == UserTokenType.UserName:
                        # asyncua otherwise selects encrypted username tokens when secure policies exist.
                        token.SecurityPolicyUri = "http://opcfoundation.org/UA/SecurityPolicy#None"

        connection.send(f"opc.tcp://127.0.0.1:{server.bserver.port}/instro-test/")  # type: ignore

        while await asyncio.to_thread(connection.recv) is not None:
            connection.send(observed)


async def populate(server: Server) -> None:
    await server.register_namespace("urn:instro:python-tests")

    rig = await server.nodes.objects.add_folder(NodeId(String("rig"), Int16(2)), QualifiedName("Rig", 2))

    for name, value in VARIABLES.items():
        node = await rig.add_variable(NodeId(String(name), Int16(2)), QualifiedName(name, 2), Variant(value))
        source = (
            None
            if name in {"ServerTimestamped", "NoTimestamp"}
            else timestamp(0, 0 if name == "SourceTimestamped" else 123456)
        )

        destination = None if name == "NoTimestamp" else timestamp(7)
        quality = StatusCode(UInt32(StatusCodes.BadNotReadable)) if name == "BadData" else StatusCode()
        data = DataValue(Variant(value), StatusCode=quality, SourceTimestamp=source, ServerTimestamp=destination)
        server.iserver.aspace[node.nodeid].attributes[AttributeIds.Value] = AttributeValue(data)

    temperature = server.iserver.aspace[NodeId(String("Temperature"), Int16(2))].attributes
    temperature[AttributeIds.AccessLevelEx] = AttributeValue(DataValue(Variant(UInt32(513))))
    temperature[AttributeIds.MinimumSamplingInterval] = AttributeValue(DataValue(Variant(Double(250.5))))

    enum = await server.nodes.enum_data_type.add_data_type(NodeId(String("Mode"), Int16(2)), QualifiedName("Mode", 2))

    await enum.write_data_type_definition(
        EnumDefinition(
            Fields=[
                EnumField(Value=-7, Name="Off", Description=LocalizedText("Disabled", "en-US")),
                EnumField(Value=42, Name="On", Description=LocalizedText("Enabled", "en-US")),
            ]
        )
    )

    await rig.add_reference(enum.nodeid, ObjectIds.Organizes)
