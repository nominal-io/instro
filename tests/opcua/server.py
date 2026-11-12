import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from multiprocessing.connection import Connection
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID

from asyncua import Server, ua
from asyncua.server.address_space import AttributeValue
from asyncua.server.user_managers import User, UserManager, UserRole
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


@dataclass
class OpcUaTestServer:
    endpoint: str
    connection: Connection

    def set_temperature(self, value: float) -> None:
        self.request(value)

    def request(self, value: float | str) -> object:
        self.connection.send(value)
        if not self.connection.poll(5):
            raise TimeoutError("OPC UA test server did not acknowledge the request")
        return self.connection.recv()


def run_server(connection: Connection, secure: bool = False) -> None:
    with TemporaryDirectory() as directory:
        asyncio.run(serve(connection, secure, Path(directory)))


class TestUsers(UserManager):
    def get_user(
        self, iserver: Any, username: str | None = None, password: str | None = None, certificate: Any = None
    ) -> User | None:
        if username == "test-operator" and password == "test-only-password":
            return User(role=UserRole.User, name=username)
        if username is None:
            return User(role=UserRole.User)
        return None


async def serve(connection: Connection, secure: bool, directory: Path) -> None:
    server = Server(user_manager=TestUsers())
    await server.init()
    server.set_endpoint("opc.tcp://127.0.0.1:0/instro-test/")
    server.set_server_name("Instro Python tests")
    server.application_type = ua.ApplicationType.Server
    server.product_uri = "urn:instro:test-product"
    await server.set_application_uri("urn:instro:test-server")
    if secure:
        certificate, key = server_credentials(directory)
        await server.load_certificate(certificate)
        await server.load_private_key(key)
        server.set_security_policy(
            [
                ua.SecurityPolicyType.Basic256Sha256_Sign,
                ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt,
            ]
        )
    else:
        server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    server.set_identity_tokens([ua.AnonymousIdentityToken, ua.UserNameIdentityToken])
    await populate(server)
    observed: dict[str, object] = {}
    service = server.iserver.subscription_service
    create_subscription = service.create_subscription
    create_monitored_items = service.create_monitored_items

    async def record_subscription(
        params: ua.CreateSubscriptionParameters, *args: Any, **kwargs: Any
    ) -> ua.CreateSubscriptionResult:
        observed["subscription"] = (
            params.RequestedPublishingInterval,
            params.RequestedLifetimeCount,
            params.RequestedMaxKeepAliveCount,
            params.MaxNotificationsPerPublish,
            params.Priority,
        )
        return await create_subscription(params, *args, **kwargs)

    async def record_items(params: ua.CreateMonitoredItemsParameters) -> list[ua.MonitoredItemCreateResult]:
        observed["items"] = [
            (
                item.RequestedParameters.SamplingInterval,
                item.RequestedParameters.QueueSize,
                item.RequestedParameters.DiscardOldest,
            )
            for item in params.ItemsToCreate
        ]
        return await create_monitored_items(params)

    service.create_subscription = record_subscription
    service.create_monitored_items = record_items
    async with server:
        connection.send(f"opc.tcp://127.0.0.1:{server.bserver.port}/instro-test/")
        while (value := await asyncio.to_thread(connection.recv)) is not None:
            if value == "subscription_parameters":
                connection.send(observed)
            else:
                await server.get_node("ns=2;s=temperature").write_value(value)
                connection.send(None)


def server_credentials(directory: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Instro OPC UA test server")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.UniformResourceIdentifier("urn:instro:test-server"),
                    x509.DNSName("localhost"),
                    x509.IPAddress(ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    certificate_path = directory / "server.der"
    key_path = directory / "server-key.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.DER))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return certificate_path, key_path


async def populate(server: Server) -> None:
    ns = 2
    await server.nodes.namespace_array.write_value(
        [
            "http://opcfoundation.org/UA/",
            "urn:instro:test-server",
            "urn:instro:python-tests",
            "urn:reserved",
            "urn:remote",
        ]
    )
    rig = await server.nodes.objects.add_folder(ua.NodeId("rig", ns), ua.QualifiedName("Rig", ns))
    other = await server.nodes.objects.add_folder(ua.NodeId("other", ns), ua.QualifiedName("Other", ns))
    await rig.write_attribute(
        ua.AttributeIds.Description,
        ua.DataValue(ua.Variant(ua.LocalizedText("Test rig", ""), ua.VariantType.LocalizedText)),
    )
    temperature = await rig.add_variable(
        ua.NodeId("temperature", ns), ua.QualifiedName("Temperature", ns), ua.Variant(23.5, ua.VariantType.Double)
    )
    await other.add_variable(
        ua.NodeId("other-temperature", ns),
        ua.QualifiedName("Temperature", ns),
        ua.Variant(-10.0, ua.VariantType.Double),
    )
    attributes = {
        ua.AttributeIds.DisplayName: ua.Variant(ua.LocalizedText("Temperatur", "de-DE"), ua.VariantType.LocalizedText),
        ua.AttributeIds.Description: ua.Variant(
            ua.LocalizedText("Probe reading", "en-US"), ua.VariantType.LocalizedText
        ),
        ua.AttributeIds.WriteMask: ua.Variant(64, ua.VariantType.UInt32),
        ua.AttributeIds.UserWriteMask: ua.Variant(32, ua.VariantType.UInt32),
        ua.AttributeIds.AccessLevel: ua.Variant(1, ua.VariantType.Byte),
        ua.AttributeIds.UserAccessLevel: ua.Variant(0, ua.VariantType.Byte),
        ua.AttributeIds.AccessLevelEx: ua.Variant(513, ua.VariantType.UInt32),
        ua.AttributeIds.AccessRestrictions: ua.Variant(5, ua.VariantType.UInt16),
        ua.AttributeIds.MinimumSamplingInterval: ua.Variant(250.0, ua.VariantType.Double),
        ua.AttributeIds.Historizing: ua.Variant(True, ua.VariantType.Boolean),
    }
    for attr, value in attributes.items():
        server.iserver.aspace[temperature.nodeid].attributes[attr] = AttributeValue(ua.DataValue(value))

    server.iserver.aspace[rig.nodeid].attributes[ua.AttributeIds.EventNotifier] = AttributeValue(
        ua.DataValue(ua.Variant(5, ua.VariantType.Byte))
    )

    object_type = await server.nodes.base_object_type.add_object_type(
        ua.NodeId("PumpType", ns), ua.QualifiedName("PumpType", ns)
    )
    await object_type.write_attribute(
        ua.AttributeIds.IsAbstract, ua.DataValue(ua.Variant(True, ua.VariantType.Boolean))
    )
    await rig.add_reference(object_type.nodeid, ua.ObjectIds.Organizes)
    reference_type = await server.get_node(ua.ObjectIds.References).add_reference_type(
        ua.NodeId("Owns", ns), ua.QualifiedName("Owns", ns), symmetric=False, inversename="OwnedBy"
    )
    await rig.add_reference(reference_type.nodeid, ua.ObjectIds.Organizes)
    method = await rig.add_method(ua.NodeId("Reset", ns), ua.QualifiedName("Reset", ns), None, [], [])
    await method.write_attribute(ua.AttributeIds.Executable, ua.DataValue(ua.Variant(True, ua.VariantType.Boolean)))
    await method.write_attribute(
        ua.AttributeIds.UserExecutable, ua.DataValue(ua.Variant(False, ua.VariantType.Boolean))
    )
    results = await server.iserver.isession.add_nodes(
        [
            ua.AddNodesItem(
                ParentNodeId=rig.nodeid,
                ReferenceTypeId=ua.NodeId(ua.ObjectIds.Organizes),
                RequestedNewNodeId=ua.NodeId("LiveView", ns),
                BrowseName=ua.QualifiedName("LiveView", ns),
                NodeClass_=ua.NodeClass.View,
                NodeAttributes=ua.ViewAttributes(DisplayName=ua.LocalizedText("Live view"), ContainsNoLoops=True),
            )
        ]
    )
    results[0].StatusCode.check()

    values = [
        ("Boolean", True, ua.VariantType.Boolean),
        ("SByte", -100, ua.VariantType.SByte),
        ("Byte", 250, ua.VariantType.Byte),
        ("Int16", -32000, ua.VariantType.Int16),
        ("UInt16", 64000, ua.VariantType.UInt16),
        ("Int32", -2_000_000_000, ua.VariantType.Int32),
        ("UInt32", 4_000_000_000, ua.VariantType.UInt32),
        ("Int64", -9_000_000_000_000_000_000, ua.VariantType.Int64),
        ("UInt64", 18_000_000_000_000_000_000, ua.VariantType.UInt64),
        ("Float", 1.25, ua.VariantType.Float),
        ("Double", -9.5, ua.VariantType.Double),
        ("String", "pressure µ", ua.VariantType.String),
        ("Guid", UUID("12345678-1234-5678-9abc-def012345678"), ua.VariantType.Guid),
        ("DateTime", datetime(1970, 1, 1, 0, 0, 5, 125000, tzinfo=timezone.utc), ua.VariantType.DateTime),
        ("NodeId", ua.NodeId("remote-pump", 4), ua.VariantType.NodeId),
        (
            "ExpandedNodeId",
            ua.ExpandedNodeId(42, 4, NamespaceUri="urn:external", ServerIndex=7),
            ua.VariantType.ExpandedNodeId,
        ),
        ("LocalExpandedNodeId", ua.ExpandedNodeId(43, 4), ua.VariantType.ExpandedNodeId),
        ("QualifiedName", ua.QualifiedName("Pressure", 4), ua.VariantType.QualifiedName),
        ("GoodStatus", ua.StatusCode(0), ua.VariantType.StatusCode),
        ("UncertainStatus", ua.StatusCode(0x40000000), ua.VariantType.StatusCode),
        ("BadStatus", ua.StatusCode(0x80000000), ua.VariantType.StatusCode),
        ("Bytes", b"abc", ua.VariantType.ByteString),
        ("Array", [1, 2], ua.VariantType.Int32),
    ]
    for name, value, variant_type in values:
        node = await rig.add_variable(ua.NodeId(name, ns), ua.QualifiedName(name, ns), ua.Variant(value, variant_type))
        server.iserver.aspace[node.nodeid].attributes[ua.AttributeIds.Value] = AttributeValue(
            ua.DataValue(
                ua.Variant(value, variant_type),
                SourceTimestamp=datetime(1970, 1, 1, 0, 0, 4, tzinfo=timezone.utc),
                ServerTimestamp=datetime(1970, 1, 1, 0, 0, 7, tzinfo=timezone.utc),
            )
        )

    timed = await rig.add_variable(ua.NodeId("Timed", ns), ua.QualifiedName("Timed", ns), 12.5)
    server.iserver.aspace[timed.nodeid].attributes[ua.AttributeIds.Value] = AttributeValue(
        ua.DataValue(
            ua.Variant(12.5, ua.VariantType.Double),
            SourceTimestamp=datetime(1970, 1, 1, 0, 0, 1, 250000, tzinfo=timezone.utc),
            ServerTimestamp=datetime(1970, 1, 1, 0, 0, 2, 750000, tzinfo=timezone.utc),
        )
    )
    for name, source in [("Epoch", datetime(1970, 1, 1, tzinfo=timezone.utc)), ("ServerTimed", None)]:
        node = await rig.add_variable(ua.NodeId(name, ns), ua.QualifiedName(name, ns), 8.5)
        server.iserver.aspace[node.nodeid].attributes[ua.AttributeIds.Value] = AttributeValue(
            ua.DataValue(
                ua.Variant(8.5, ua.VariantType.Double),
                SourceTimestamp=source,
                ServerTimestamp=datetime(1970, 1, 1, 0, 0, 7, tzinfo=timezone.utc),
            )
        )
    bad = await rig.add_variable(ua.NodeId("BadData", ns), ua.QualifiedName("BadData", ns), 0)
    await bad.write_value(ua.DataValue(ua.Variant(42, ua.VariantType.Int64), StatusCode_=ua.StatusCode(0x803A0000)))
    missing = await rig.add_variable(ua.NodeId("NoValue", ns), ua.QualifiedName("NoValue", ns), 0)
    missing_value = ua.DataValue()
    # asyncua's constructor turns None into a null Variant instead of an absent Value.
    object.__setattr__(missing_value, "Value", None)
    server.iserver.aspace[missing.nodeid].attributes[ua.AttributeIds.Value] = AttributeValue(missing_value)
    untimed = await rig.add_variable(ua.NodeId("NoTimestamp", ns), ua.QualifiedName("NoTimestamp", ns), 9.5)
    server.iserver.aspace[untimed.nodeid].attributes[ua.AttributeIds.Value] = AttributeValue(
        ua.DataValue(ua.Variant(9.5, ua.VariantType.Double))
    )

    enum = await server.nodes.enum_data_type.add_data_type(ua.NodeId("Mode", ns), ua.QualifiedName("Mode", ns))
    await enum.write_data_type_definition(
        ua.EnumDefinition(
            Fields=[
                ua.EnumField(Value=-7, Name="Off", Description=ua.LocalizedText("Disabled", "en-US")),
                ua.EnumField(Value=42, Name="On", Description=ua.LocalizedText("Enabled", "en-US")),
            ]
        )
    )
    await rig.add_reference(enum.nodeid, ua.ObjectIds.Organizes)

    structure = await server.nodes.base_structure_type.add_data_type(
        ua.NodeId("Reading", ns), ua.QualifiedName("Reading", ns)
    )
    await structure.write_data_type_definition(
        ua.StructureDefinition(
            DefaultEncodingId=ua.NodeId(7001, ns),
            BaseDataType=ua.NodeId(22),
            StructureType_=ua.StructureType.StructureWithOptionalFields,
            Fields=[
                ua.StructureField(
                    Name="Samples",
                    Description=ua.LocalizedText("Voltage samples", "en-US"),
                    DataType=ua.NodeId(11),
                    ValueRank=1,
                    ArrayDimensions=[3],
                    MaxStringLength=0,
                    IsOptional=True,
                ),
                ua.StructureField(
                    Name="Label",
                    Description=ua.LocalizedText("Channel label"),
                    DataType=ua.NodeId(12),
                    ValueRank=-1,
                    ArrayDimensions=None,
                    MaxStringLength=32,
                    IsOptional=False,
                ),
            ],
        )
    )
    await rig.add_reference(structure.nodeid, ua.ObjectIds.Organizes)
