//! Absolute OPC UA browse paths and browse-name segment encoding.

use std::fmt;
use std::str::FromStr;

use anyhow::Error;
use anyhow::Result;
use anyhow::bail;
use serde::Deserialize;
use serde::Serialize;

const fn is_reserved(ch: char) -> bool {
    matches!(ch, '/' | '.' | '<' | '>' | ':' | '#' | '!' | '&')
}

pub(crate) fn split_absolute_segments(path: &str, escape: char) -> Result<Vec<String>> {
    let Some(path) = path.strip_prefix('/') else {
        bail!("browse path must be absolute");
    };

    if path.is_empty() {
        return Ok(Vec::new());
    }

    let mut segments = Vec::new();
    let mut segment = String::new();
    let mut escaped = false;

    for ch in path.chars() {
        if escaped {
            segment.push(escape);
            segment.push(ch);
            escaped = false;
        } else if ch == escape {
            escaped = true;
        } else if ch == '/' {
            if segment.is_empty() {
                bail!("browse path cannot contain an empty segment");
            }

            segments.push(std::mem::take(&mut segment));
        } else {
            segment.push(ch);
        }
    }

    if escaped {
        bail!("browse path cannot end with an escape marker");
    }

    if segment.is_empty() {
        bail!("browse path cannot end with '/'");
    }

    segments.push(segment);

    Ok(segments)
}

pub(crate) fn split_namespace_prefix(segment: &str, escape: char) -> Result<(Option<u16>, &str)> {
    let mut escaped = false;

    for (index, ch) in segment.char_indices() {
        if escaped {
            escaped = false;
        } else if ch == escape {
            escaped = true;
        } else if ch == ':' {
            let namespace = &segment[..index];

            if !namespace.is_empty() && namespace.chars().all(|ch| ch.is_ascii_digit()) {
                return Ok((Some(namespace.parse()?), &segment[index + ch.len_utf8()..]));
            }

            break;
        }
    }

    Ok((None, segment))
}

fn encode_name(name: &str) -> String {
    let mut encoded = String::with_capacity(name.len());
    for ch in name.chars() {
        if is_reserved(ch) {
            encoded.push('&');
        }

        encoded.push(ch);
    }

    encoded
}

fn decode_name(encoded: &str) -> Result<String> {
    let mut decoded = String::with_capacity(encoded.len());
    let mut chars = encoded.chars();

    while let Some(ch) = chars.next() {
        if ch == '&' {
            let escaped = chars
                .next()
                .ok_or_else(|| Error::msg("browse name cannot end with an escape marker"))?;

            if !is_reserved(escaped) {
                bail!("'&' in a browse name must escape a reserved character");
            }

            decoded.push(escaped);
        } else if is_reserved(ch) {
            bail!("reserved character '{ch}' in a browse name must be escaped");
        } else {
            decoded.push(ch);
        }
    }

    Ok(decoded)
}

/// A browse-path segment preserving the namespace that qualifies its name.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct OpcUaBrowseName {
    namespace: u16,
    name: String,
}

impl OpcUaBrowseName {
    /// Creates a browse name from an unescaped name.
    pub const fn new(namespace: u16, name: String) -> Self {
        Self { namespace, name }
    }

    pub const fn namespace(&self) -> u16 {
        self.namespace
    }

    pub const fn name(&self) -> &str {
        self.name.as_str()
    }
}

impl fmt::Display for OpcUaBrowseName {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.namespace != 0 {
            write!(f, "{}:", self.namespace)?;
        }

        f.write_str(&encode_name(&self.name))
    }
}

impl FromStr for OpcUaBrowseName {
    type Err = Error;

    fn from_str(segment: &str) -> Result<Self> {
        if segment.is_empty() {
            bail!("browse name cannot be empty");
        }

        let (namespace, name) = split_namespace_prefix(segment, '&')?;
        let name = decode_name(name)?;

        if name.is_empty() {
            bail!("browse name cannot be empty");
        }

        Ok(Self::new(namespace.unwrap_or(0), name))
    }
}

/// A rooted absolute path through OPC UA browse names.
///
/// `/` is the standard Root node. Every other path begins at Root and contains
/// one or more encoded browse-name segments.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[serde(try_from = "String", into = "String")]
pub struct OpcUaBrowsePath {
    segments: Vec<OpcUaBrowseName>,
}

impl OpcUaBrowsePath {
    /// Returns the standard Root path.
    pub const fn root() -> Self {
        Self {
            segments: Vec::new(),
        }
    }

    /// Creates an absolute path containing one segment below Root.
    pub fn from_segment(segment: OpcUaBrowseName) -> Self {
        Self {
            segments: vec![segment],
        }
    }

    /// Creates an absolute path from browse-name segments below Root.
    pub fn from_segments(segments: impl IntoIterator<Item = OpcUaBrowseName>) -> Self {
        Self {
            segments: segments.into_iter().collect(),
        }
    }

    /// Appends one child segment.
    pub fn append(&mut self, segment: OpcUaBrowseName) -> &mut Self {
        self.segments.push(segment);
        self
    }

    /// Returns a new path with one child segment appended.
    #[must_use]
    pub fn with_child(mut self, segment: OpcUaBrowseName) -> Self {
        self.segments.push(segment);
        self
    }

    /// Returns whether this is `/`.
    pub const fn is_root(&self) -> bool {
        self.segments.is_empty()
    }

    /// Returns the segments below Root.
    pub const fn segments(&self) -> &[OpcUaBrowseName] {
        self.segments.as_slice()
    }
}

impl fmt::Display for OpcUaBrowsePath {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.is_root() {
            return f.write_str("/");
        }

        for segment in &self.segments {
            write!(f, "/{segment}")?;
        }

        Ok(())
    }
}

impl FromStr for OpcUaBrowsePath {
    type Err = Error;

    fn from_str(path: &str) -> Result<Self> {
        split_absolute_segments(path, '&')?
            .into_iter()
            .map(|segment| segment.parse())
            .collect()
    }
}

impl TryFrom<String> for OpcUaBrowsePath {
    type Error = Error;

    fn try_from(path: String) -> Result<Self> {
        path.parse()
    }
}

impl From<OpcUaBrowsePath> for String {
    fn from(path: OpcUaBrowsePath) -> Self {
        path.to_string()
    }
}

impl FromIterator<OpcUaBrowseName> for OpcUaBrowsePath {
    fn from_iter<T: IntoIterator<Item = OpcUaBrowseName>>(segments: T) -> Self {
        Self::from_segments(segments)
    }
}

impl AsRef<[OpcUaBrowseName]> for OpcUaBrowsePath {
    fn as_ref(&self) -> &[OpcUaBrowseName] {
        self.segments()
    }
}

#[cfg(test)]
mod tests {
    use anyhow::Result;

    use super::OpcUaBrowseName;
    use super::OpcUaBrowsePath;

    #[test]
    fn browse_names_and_paths_share_one_roundtrip_codec() -> Result<()> {
        let names = [
            OpcUaBrowseName::new(0, "Root".to_owned()),
            OpcUaBrowseName::new(0, "2:numeric-colon".to_owned()),
            OpcUaBrowseName::new(4, "PLC/MAIN:TEMP&<hot>".to_owned()),
            OpcUaBrowseName::new(7, "温度🌡".to_owned()),
        ];

        for name in &names {
            assert_eq!(name.to_string().parse::<OpcUaBrowseName>()?, *name);
        }

        let path = OpcUaBrowsePath::from_segments(names);
        assert_eq!(path.to_string().parse::<OpcUaBrowsePath>()?, path);
        Ok(())
    }

    #[test]
    fn root_is_slash_and_relative_or_empty_text_is_rejected() {
        assert_eq!(OpcUaBrowsePath::root().to_string(), "/");
        assert_eq!(
            "/".parse::<OpcUaBrowsePath>().expect("root path"),
            OpcUaBrowsePath::root()
        );
        assert!("".parse::<OpcUaBrowsePath>().is_err());
        assert!("Root".parse::<OpcUaBrowsePath>().is_err());
        assert!("/Root/".parse::<OpcUaBrowsePath>().is_err());
        assert!("/Root//Objects".parse::<OpcUaBrowsePath>().is_err());
        assert!("".parse::<OpcUaBrowseName>().is_err());
        assert!("4:".parse::<OpcUaBrowseName>().is_err());
    }
}
