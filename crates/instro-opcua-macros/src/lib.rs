use std::collections::BTreeSet;

use proc_macro::TokenStream;
use proc_macro2::TokenStream as Tokens;
use quote::format_ident;
use quote::quote;
use syn::Attribute;
use syn::Error;
use syn::Fields;
use syn::Ident;
use syn::ItemEnum;
use syn::ItemStruct;
use syn::MetaNameValue;
use syn::Path;
use syn::Result;
use syn::Token;
use syn::Visibility;
use syn::meta::ParseNestedMeta;
use syn::parse::Parse;
use syn::parse::Parser;
use syn::punctuated::Punctuated;

#[proc_macro_attribute]
pub fn ua_struct(args: TokenStream, item: TokenStream) -> TokenStream {
    let item = syn::parse_macro_input!(item as ItemStruct);
    expand_struct(args, item)
        .unwrap_or_else(Error::into_compile_error)
        .into()
}

fn expand_struct(args: TokenStream, item: ItemStruct) -> Result<Tokens> {
    let name = &item.ident;
    let Fields::Named(fields) = &item.fields else {
        return Err(Error::new_spanned(name, "expected named getter fields"));
    };

    let wrapper = Wrapper::parse(args, name, &item.vis, &item.attrs, |arg| {
        Err(arg.error("unknown struct option"))
    })?;

    let wire = if let Some(wire) = &wrapper.wire {
        wire_struct(name, wire)?
    } else {
        Tokens::new()
    };

    let declaration = wrapper.declare();

    let mut getters = Tokens::new();
    for field in &fields.named {
        let name = &field.ident;
        let ty = &field.ty;
        let (options, attrs): (Vec<_>, Vec<_>) = field
            .attrs
            .iter()
            .partition(|attr| attr.path().is_ident("ua"));

        let body = match options.as_slice() {
            [] => quote!(self.0.#name.clone().into()),
            [_, duplicate, ..] => return Err(Error::new_spanned(duplicate, "duplicate ua getter")),

            [attr] => {
                let option = attr.parse_args::<MetaNameValue>()?;
                if !option.path.is_ident("get") {
                    return Err(Error::new_spanned(option.path, "expected get"));
                }

                let expr = option.value;
                quote!(#expr)
            }
        };

        getters.extend(quote! {
            #(#attrs)*
            #[getter]
            fn #name(&self) -> #ty {
                #body
            }
        });
    }

    Ok(quote! {
        #declaration

        #[::pyo3::pymethods]
        impl #name {
            #getters
        }

        #wire
    })
}

fn wire_struct(name: &Ident, mode: &Ident) -> Result<Tokens> {
    match mode.to_string().as_str() {
        "scalar" => Ok(quote! {
            impl crate::values::payload::ReadValue for #name {
                const KIND: ::opcua::types::VariantScalarTypeId =
                    ::opcua::types::VariantScalarTypeId::#name;

                fn read(
                    py: ::pyo3::Python<'_>,
                    value: &::opcua::types::Variant,
                ) -> ::pyo3::PyResult<::pyo3::Py<::pyo3::PyAny>> {
                    let ::opcua::types::Variant::#name(inner) = value else {
                        return Err(crate::values::payload::type_mismatch());
                    };

                    Self(ua::#name::clone(inner)).into_py_any(py)
                }
            }
        }),

        "structure" => Ok(quote! {
            impl TryFrom<&ua::ExtensionObject> for #name {
                type Error = PyErr;

                fn try_from(object: &ua::ExtensionObject) -> PyResult<Self> {
                    object.inner_as::<ua::#name>()
                        .cloned()
                        .map(Self)
                        .ok_or_else(|| crate::values::payload::incompatible_structure(object))
                }
            }

            impl crate::values::payload::ReadValue for #name {
                const KIND: ::opcua::types::VariantScalarTypeId =
                    ::opcua::types::VariantScalarTypeId::ExtensionObject;

                fn read(
                    py: ::pyo3::Python<'_>,
                    value: &::opcua::types::Variant,
                ) -> ::pyo3::PyResult<::pyo3::Py<::pyo3::PyAny>> {
                    let ::opcua::types::Variant::ExtensionObject(inner) = value else {
                        return Err(crate::values::payload::type_mismatch())
                    };

                    Self::try_from(inner)?.into_py_any(py)
                }
            }
        }),

        _ => Err(Error::new_spanned(mode, "expected scalar or structure")),
    }
}

#[proc_macro_attribute]
pub fn ua_enum(args: TokenStream, item: TokenStream) -> TokenStream {
    let item = syn::parse_macro_input!(item as ItemEnum);
    expand_enum(args, item)
        .unwrap_or_else(Error::into_compile_error)
        .into()
}

fn expand_enum(args: TokenStream, item: ItemEnum) -> Result<Tokens> {
    let name = &item.ident;
    let mut repr = None;
    let mut flags = false;

    let wrapper = Wrapper::parse(args, name, &item.vis, &item.attrs, |arg| {
        if arg.path.is_ident("repr") {
            repr = Some(arg.value()?.parse::<Ident>()?);
        } else if arg.path.is_ident("flags") {
            flags = true;
        } else {
            return Err(arg.error("unknown enum option"));
        }

        Ok(())
    })?;

    let repr = repr.ok_or_else(|| Error::new_spanned(name, "expected repr = integer"))?;
    let native = quote!(::opcua::types::#name);
    let mut names = Vec::new();
    let mut constants = Tokens::new();

    for variant in &item.variants {
        if !matches!(variant.fields, Fields::Unit) || variant.discriminant.is_some() {
            return Err(Error::new_spanned(
                variant,
                "expected a native variant name",
            ));
        }

        let ident = &variant.ident;
        let attrs = &variant.attrs;
        names.push(ident);

        constants.extend(quote! {
            #(#attrs)*
            #[classattr]
            pub const [<#ident:snake:upper>]: Self = Self(#native::#ident);
        });
    }

    let bit_ops = flags.then(|| {
        quote! {
            #[classattr]
            const NONE: Self = Self(#native::empty());

            #(
                #[getter]
                fn [<is_ #names:snake _set>](&self) -> bool {
                    self.0.contains(#native::#names)
                }
            )*

            fn __bool__(&self) -> bool {
                !self.0.is_empty()
            }

            fn __and__(&self, other: &Self) -> Self {
                Self(self.0 & other.0)
            }

            fn __or__(&self, other: &Self) -> Self {
                Self(self.0 | other.0)
            }
        }
    });

    let numeric = numeric_methods(name, &repr, flags);
    let wire = enum_wire(name, &repr, wrapper.wire.as_ref())?;

    let copy = (!wrapper.has_derive("marker", "Copy")).then(|| quote!(#[derive(Copy)]));
    let hash = (!wrapper.has_derive("hash", "Hash")).then(|| {
        quote! {
            impl ::std::hash::Hash for #name {
                fn hash<H: ::std::hash::Hasher>(&self, state: &mut H) {
                    ::std::hash::Hash::hash(&self.value(), state);
                }
            }
        }
    });

    let declaration = wrapper.declare();

    Ok(quote! {
        /// A semantic OPC UA integer value with typed constants and equality.
        ///
        /// Construct a semantic value from its native integer representation.
        ///
        /// Args:
        ///     value (int): Native numeric value or flag bits.
        ///
        /// Returns:
        ///     object: The corresponding typed wrapper.
        ///
        /// Examples:
        ///     >>> from instro.unstable.opcua.types import AttributeId
        ///     >>> AttributeId(13) == AttributeId.VALUE
        ///     True
        #copy
        #declaration

        ::paste::paste! {
            #[::pyo3::pymethods]
            impl #name {
                #constants

                #bit_ops

                fn __str__(&self) -> String {
                    #(
                        if self.0 == #native::#names {
                            return stringify!(#names).to_ascii_lowercase();
                        }
                    )*

                    self.value().to_string()
                }

                #numeric
            }
        }

        #hash
        #wire
    })
}

fn numeric_methods(name: &Ident, repr: &Ident, flags: bool) -> Tokens {
    let native = quote!(::opcua::types::#name);
    let construct = if flags {
        quote!(Ok(Self(#native::from_bits_retain(value as _))))
    } else if name == "AttributeId" {
        quote! {
            #native::from_u32(value).map(Self).map_err(|error| {
                ::pyo3::exceptions::PyValueError::new_err(error.to_string())
            })
        }
    } else {
        quote!(#native::try_from(value).map(Self).map_err(crate::error::native))
    };

    let value = if flags {
        quote!(self.0.bits() as #repr)
    } else {
        quote!(self.0 as #repr)
    };

    quote! {
        #[new]
        fn new(value: #repr) -> ::pyo3::PyResult<Self> {
            #construct
        }

        #[getter]
        fn value(&self) -> #repr {
            #value
        }

        fn __int__(&self) -> #repr {
            self.value()
        }

        fn __repr__(&self) -> String {
            format!("{}({})", stringify!(#name), self.value())
        }
    }
}

fn enum_wire(name: &Ident, repr: &Ident, wire: Option<&Ident>) -> Result<Tokens> {
    let Some(mode) = wire else {
        return Ok(Tokens::new());
    };

    if mode != "scalar" {
        return Err(Error::new_spanned(mode, "expected scalar"));
    }

    Ok(quote! {
        impl crate::values::payload::ReadValue for #name {
            const KIND: ua::VariantScalarTypeId =
                <#repr as crate::values::payload::ReadValue>::KIND;

            fn read(py: Python<'_>, value: &ua::Variant) -> PyResult<Py<PyAny>> {
                let value = crate::values::payload::read_repr::<#repr>(value)?;
                let wrapper = Self::new(value)
                    .map_err(|_| crate::values::payload::type_mismatch())?;

                wrapper.into_py_any(py)
            }
        }
    })
}

struct Wrapper<'a> {
    name: &'a Ident,
    vis: &'a Visibility,
    attrs: &'a [Attribute],
    derives: Vec<Path>,
    py_traits: BTreeSet<Ident>,
    wire: Option<Ident>,
}

impl<'a> Wrapper<'a> {
    fn parse(
        args: TokenStream,
        name: &'a Ident,
        vis: &'a Visibility,
        attrs: &'a [Attribute],
        mut parse_option: impl FnMut(ParseNestedMeta<'_>) -> Result<()>,
    ) -> Result<Self> {
        let mut py_traits = BTreeSet::new();
        let mut wire = None;
        let mut options = BTreeSet::new();

        syn::meta::parser(|arg| {
            if !options.insert(arg.path.require_ident()?.clone()) {
                return Err(arg.error("duplicate wrapper option"));
            }

            if arg.path.is_ident("py_traits") {
                let tuple;
                let value = arg.value()?;
                syn::parenthesized!(tuple in value);
                py_traits.extend(tuple.parse_terminated(Ident::parse, Token![,])?);
            } else if arg.path.is_ident("wire") {
                wire = Some(arg.value()?.parse()?);
            } else {
                return parse_option(arg);
            }

            Ok(())
        })
        .parse(args)?;

        let mut derives = Vec::new();
        for attr in attrs.iter().filter(|attr| attr.path().is_ident("derive")) {
            derives.extend(attr.parse_args_with(Punctuated::<Path, Token![,]>::parse_terminated)?);
        }

        Ok(Self {
            name,
            vis,
            attrs,
            derives,
            py_traits,
            wire,
        })
    }

    fn has_derive(&self, module: &str, name: &str) -> bool {
        self.derives.iter().any(|path| {
            let mut segments = path.segments.iter().map(|segment| &segment.ident);
            let standard_root = segments
                .next()
                .is_some_and(|root| root == "std" || root == "core");

            path.is_ident(name) || (standard_root && segments.eq([module, name]))
        })
    }

    fn declare(mut self) -> Tokens {
        let inferred_traits = [
            ("cmp", "PartialEq", "eq"),
            ("cmp", "Eq", "eq"),
            ("cmp", "PartialOrd", "ord"),
            ("cmp", "Ord", "ord"),
            ("hash", "Hash", "hash"),
        ];

        for (module, rust, python) in inferred_traits {
            if self.has_derive(module, rust) {
                self.py_traits.insert(format_ident!("{python}"));
            }
        }

        if self.py_traits.contains(&format_ident!("ord")) {
            self.py_traits.insert(format_ident!("eq"));
        }

        self.py_traits.insert(format_ident!("frozen"));
        self.py_traits.insert(format_ident!("from_py_object"));

        let clone = (!self.has_derive("clone", "Clone")).then(|| quote!(#[derive(Clone)]));
        let name = self.name;
        let vis = self.vis;
        let attrs = self.attrs;
        let py_traits = self.py_traits;

        quote! {
            #[::pyo3::pyclass(#(#py_traits,)* module = "instro.unstable.opcua.types")]
            #(#attrs)*
            #clone
            #vis struct #name(::opcua::types::#name);

            impl From<::opcua::types::#name> for #name {
                fn from(value: ::opcua::types::#name) -> Self {
                    Self(value)
                }
            }

            impl AsRef<::opcua::types::#name> for #name {
                fn as_ref(&self) -> &::opcua::types::#name {
                    &self.0
                }
            }
        }
    }
}
