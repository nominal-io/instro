mod client;
mod discover;
mod types;
mod util;

use pyo3::prelude::*;

pub(crate) use self::util::require_both;

#[pymodule(name = "_opcua")]
mod instro_opcua_py {
    #[pymodule_export]
    use super::client::Node;
    #[pymodule_export]
    use super::client::OpcUaClient;
    #[pymodule_export]
    use super::client::Session;
    #[pymodule_export]
    use super::discover::get_endpoints;
    #[pymodule_export]
    use super::instro_opcua_py_types;
}

#[pymodule(name = "types")]
mod instro_opcua_py_types {
    #[pymodule_export]
    use super::types::AccessLevelExType;
    #[pymodule_export]
    use super::types::AccessLevelType;
    #[pymodule_export]
    use super::types::AccessRestrictionType;
    #[pymodule_export]
    use super::types::ApplicationType;
    #[pymodule_export]
    use super::types::AttributeId;
    #[pymodule_export]
    use super::types::AttributeWriteMask;
    #[pymodule_export]
    use super::types::BrowsePath;
    #[pymodule_export]
    use super::types::DataValue;
    #[pymodule_export]
    use super::types::EndpointDescription;
    #[pymodule_export]
    use super::types::EnumDefinition;
    #[pymodule_export]
    use super::types::EnumField;
    #[pymodule_export]
    use super::types::EventNotifierType;
    #[pymodule_export]
    use super::types::ExpandedNodeId;
    #[pymodule_export]
    use super::types::LocalizedText;
    #[pymodule_export]
    use super::types::MessageSecurityMode;
    #[pymodule_export]
    use super::types::NodeClass;
    #[pymodule_export]
    use super::types::NodeId;
    #[pymodule_export]
    use super::types::QualifiedName;
    #[pymodule_export]
    use super::types::RelativePath;
    #[pymodule_export]
    use super::types::RelativePathElement;
    #[pymodule_export]
    use super::types::SecurityLevel;
    #[pymodule_export]
    use super::types::StatusCode;
    #[pymodule_export]
    use super::types::StructureDefinition;
    #[pymodule_export]
    use super::types::StructureField;
}
