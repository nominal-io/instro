//! Retained Python ordering follows open62541's UA_order, not textual rendering.
//!
//! Native async-opcua structures have equality but no comparison traits. Strings
//! and arrays compare length first; structured values follow schema member order.

use std::cmp::Ordering;

use opcua::types as ua;

fn bytes(left: Option<&[u8]>, right: Option<&[u8]>) -> Ordering {
    left.unwrap_or_default()
        .len()
        .cmp(&right.unwrap_or_default().len())
        .then_with(|| left.cmp(&right))
}

fn string(left: &ua::UAString, right: &ua::UAString) -> Ordering {
    bytes(
        left.value().as_deref().map(str::as_bytes),
        right.value().as_deref().map(str::as_bytes),
    )
}

fn byte_string(left: &ua::ByteString, right: &ua::ByteString) -> Ordering {
    bytes(left.value.as_deref(), right.value.as_deref())
}

fn array<T>(
    left: Option<&[T]>,
    right: Option<&[T]>,
    compare: impl Fn(&T, &T) -> Ordering,
) -> Ordering {
    // UA_order treats a null array and a present empty array as equal.
    let left = left.unwrap_or_default();
    let right = right.unwrap_or_default();

    left.len().cmp(&right.len()).then_with(|| {
        left.iter()
            .zip(right)
            .map(|(left, right)| compare(left, right))
            .find(|order| !order.is_eq())
            .unwrap_or(Ordering::Equal)
    })
}

fn identifier(left: &ua::Identifier, right: &ua::Identifier) -> Ordering {
    fn kind(value: &ua::Identifier) -> u8 {
        match value {
            ua::Identifier::Numeric(_) => 0,
            ua::Identifier::String(_) => 1,
            ua::Identifier::Guid(_) => 2,
            ua::Identifier::ByteString(_) => 3,
        }
    }

    kind(left)
        .cmp(&kind(right))
        .then_with(|| match (left, right) {
            (ua::Identifier::Numeric(left), ua::Identifier::Numeric(right)) => left.cmp(right),
            (ua::Identifier::String(left), ua::Identifier::String(right)) => string(left, right),

            (ua::Identifier::Guid(left), ua::Identifier::Guid(right)) => {
                // UUID's canonical field order matches UA_Guid's numeric fields;
                // Guid::to_bytes() instead returns Microsoft's little-endian layout.
                uuid::Uuid::from(left.clone()).cmp(&uuid::Uuid::from(right.clone()))
            }

            (ua::Identifier::ByteString(left), ua::Identifier::ByteString(right)) => {
                byte_string(left, right)
            }

            _ => Ordering::Equal,
        })
}

pub(super) fn node_id(left: &ua::NodeId, right: &ua::NodeId) -> Ordering {
    left.namespace
        .cmp(&right.namespace)
        .then_with(|| identifier(&left.identifier, &right.identifier))
}

pub(super) fn qualified_name(left: &ua::QualifiedName, right: &ua::QualifiedName) -> Ordering {
    left.namespace_index
        .cmp(&right.namespace_index)
        .then_with(|| string(&left.name, &right.name))
}

pub(super) fn localized_text(left: &ua::LocalizedText, right: &ua::LocalizedText) -> Ordering {
    string(&left.locale, &right.locale).then_with(|| string(&left.text, &right.text))
}

pub(super) fn expanded_node_id(left: &ua::ExpandedNodeId, right: &ua::ExpandedNodeId) -> Ordering {
    left.server_index
        .cmp(&right.server_index)
        .then_with(|| string(&left.namespace_uri, &right.namespace_uri))
        .then_with(|| node_id(&left.node_id, &right.node_id))
}

pub(super) fn status_code(left: &ua::StatusCode, right: &ua::StatusCode) -> Ordering {
    left.bits().cmp(&right.bits())
}

pub(super) fn relative_path_element(
    left: &ua::RelativePathElement,
    right: &ua::RelativePathElement,
) -> Ordering {
    node_id(&left.reference_type_id, &right.reference_type_id)
        .then_with(|| left.is_inverse.cmp(&right.is_inverse))
        .then_with(|| left.include_subtypes.cmp(&right.include_subtypes))
        .then_with(|| qualified_name(&left.target_name, &right.target_name))
}

pub(super) fn relative_path(left: &ua::RelativePath, right: &ua::RelativePath) -> Ordering {
    array(
        left.elements.as_deref(),
        right.elements.as_deref(),
        relative_path_element,
    )
}

pub(super) fn browse_path(left: &ua::BrowsePath, right: &ua::BrowsePath) -> Ordering {
    node_id(&left.starting_node, &right.starting_node)
        .then_with(|| relative_path(&left.relative_path, &right.relative_path))
}

pub(super) fn enum_field(left: &ua::EnumField, right: &ua::EnumField) -> Ordering {
    left.value
        .cmp(&right.value)
        .then_with(|| localized_text(&left.display_name, &right.display_name))
        .then_with(|| localized_text(&left.description, &right.description))
        .then_with(|| string(&left.name, &right.name))
}

pub(super) fn enum_definition(left: &ua::EnumDefinition, right: &ua::EnumDefinition) -> Ordering {
    array(left.fields.as_deref(), right.fields.as_deref(), enum_field)
}

pub(super) fn structure_field(left: &ua::StructureField, right: &ua::StructureField) -> Ordering {
    string(&left.name, &right.name)
        .then_with(|| localized_text(&left.description, &right.description))
        .then_with(|| node_id(&left.data_type, &right.data_type))
        .then_with(|| left.value_rank.cmp(&right.value_rank))
        .then_with(|| {
            array(
                left.array_dimensions.as_deref(),
                right.array_dimensions.as_deref(),
                Ord::cmp,
            )
        })
        .then_with(|| left.max_string_length.cmp(&right.max_string_length))
        .then_with(|| left.is_optional.cmp(&right.is_optional))
}

pub(super) fn structure_definition(
    left: &ua::StructureDefinition,
    right: &ua::StructureDefinition,
) -> Ordering {
    node_id(&left.default_encoding_id, &right.default_encoding_id)
        .then_with(|| node_id(&left.base_data_type, &right.base_data_type))
        .then_with(|| (left.structure_type as u32).cmp(&(right.structure_type as u32)))
        .then_with(|| {
            array(
                left.fields.as_deref(),
                right.fields.as_deref(),
                structure_field,
            )
        })
}

fn application_description(
    left: &ua::ApplicationDescription,
    right: &ua::ApplicationDescription,
) -> Ordering {
    string(&left.application_uri, &right.application_uri)
        .then_with(|| string(&left.product_uri, &right.product_uri))
        .then_with(|| localized_text(&left.application_name, &right.application_name))
        .then_with(|| (left.application_type as u32).cmp(&(right.application_type as u32)))
        .then_with(|| string(&left.gateway_server_uri, &right.gateway_server_uri))
        .then_with(|| string(&left.discovery_profile_uri, &right.discovery_profile_uri))
        .then_with(|| {
            array(
                left.discovery_urls.as_deref(),
                right.discovery_urls.as_deref(),
                string,
            )
        })
}

fn user_token_policy(left: &ua::UserTokenPolicy, right: &ua::UserTokenPolicy) -> Ordering {
    string(&left.policy_id, &right.policy_id)
        .then_with(|| (left.token_type as u32).cmp(&(right.token_type as u32)))
        .then_with(|| string(&left.issued_token_type, &right.issued_token_type))
        .then_with(|| string(&left.issuer_endpoint_url, &right.issuer_endpoint_url))
        .then_with(|| string(&left.security_policy_uri, &right.security_policy_uri))
}

pub(super) fn endpoint_description(
    left: &ua::EndpointDescription,
    right: &ua::EndpointDescription,
) -> Ordering {
    string(&left.endpoint_url, &right.endpoint_url)
        .then_with(|| application_description(&left.server, &right.server))
        .then_with(|| byte_string(&left.server_certificate, &right.server_certificate))
        .then_with(|| (left.security_mode as u32).cmp(&(right.security_mode as u32)))
        .then_with(|| string(&left.security_policy_uri, &right.security_policy_uri))
        .then_with(|| {
            array(
                left.user_identity_tokens.as_deref(),
                right.user_identity_tokens.as_deref(),
                user_token_policy,
            )
        })
        .then_with(|| string(&left.transport_profile_uri, &right.transport_profile_uri))
        .then_with(|| left.security_level.cmp(&right.security_level))
}
