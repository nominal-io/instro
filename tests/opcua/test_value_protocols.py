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
    XmlElement,
)


def test_node_id_python_ordering_and_equal_key_lookup():
    first = NodeId("ns=2;i=7")
    last = NodeId("ns=2;i=19")
    assert first < last
    assert {first: "retained"}[NodeId("ns=2;i=7")] == "retained"


def test_status_codes_have_no_implicit_order():
    with pytest.raises(TypeError):
        _ = StatusCode(0) < StatusCode(0x8000_0000)  # type: ignore[operator]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (DateTime(116_444_736_000_000_000), DateTime.parse("1970-01-01T00:00:00Z")),
        (Duration(1, 500_000_000), Duration.from_millis(1500)),
        (SecurityPolicy("basic256sha256"), SecurityPolicy.BASIC256_SHA256),
        (StatusCode(0), StatusCode(0)),
        (XmlElement("<mode/>"), XmlElement("<mode/>")),
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


def test_localized_text_preserves_absent_and_empty_fields():
    absent = LocalizedText()
    empty_text = LocalizedText("")
    empty_locale = LocalizedText(locale="")

    assert (absent.text, absent.locale) == (None, None)
    assert (empty_text.text, empty_text.locale) == ("", None)
    assert (empty_locale.text, empty_locale.locale) == (None, "")
    assert len({absent, empty_text, empty_locale}) == 3
    assert absent < empty_text < empty_locale
    assert LocalizedText("aa", "en") < LocalizedText("z", "en")


def test_relative_path_orders_elements_before_length():
    longer = RelativePath("/a/b")
    shorter = RelativePath("/z")
    assert longer < shorter


T = TypeVar(
    "T",
    AccessLevelType,
    AccessLevelExType,
    AccessRestrictionType,
    AttributeWriteMask,
    EventNotifierType,
    PermissionType,
)


def test_access_level_flag_truthiness_reflects_the_selected_bit():
    level = AccessLevelType.CURRENT_READ
    assert not (level & AccessLevelType.CURRENT_WRITE)
    assert level & AccessLevelType.CURRENT_READ


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
