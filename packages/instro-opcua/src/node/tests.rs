use std::collections::VecDeque;

use instro_opcua_test::TestServer;
use instro_opcua_test::server::FolderSpec;
use instro_opcua_test::server::ParentRef;

use super::*;
use crate::test_support::TestResult;
use crate::test_support::status;
use crate::test_support::with_python;

fn target(server: u32, remaining: u32) -> ua::BrowsePathTarget {
    ua::BrowsePathTarget {
        target_id: ua::ExpandedNodeId {
            node_id: ua::NodeId::new(2, 42u32),
            server_index: server,
            ..Default::default()
        },
        remaining_path_index: remaining,
    }
}

#[test]
fn translation_rejects_malformed_unresolved_remote_and_ambiguous_results() -> PyResult<()> {
    with_python(|_py| {
        let good = |targets| ua::BrowsePathResult {
            status_code: ua::StatusCode::Good,
            targets: Some(targets),
        };

        let resolved = || target(0, u32::MAX);
        let invalid_name = ua::BrowsePathResult {
            status_code: ua::StatusCode::BadBrowseNameInvalid,
            targets: None,
        };

        let cases = [
            (vec![], 0x8001_0000),
            (
                vec![good(vec![resolved()]), good(vec![resolved()])],
                0x8001_0000,
            ),
            (vec![invalid_name], 0x8060_0000),
            (vec![good(vec![])], 0x806F_0000),
            (vec![good(vec![resolved(), resolved()])], 0x806D_0000),
            (vec![good(vec![target(0, 1)])], 0x406C_0000),
            (vec![good(vec![target(1, u32::MAX)])], 0x406C_0000),
        ];

        for (results, expected) in cases {
            assert_eq!(status(translated_node(results).unwrap_err())?, expected);
        }

        Ok(())
    })
}

fn reference(name: &str, server_index: u32) -> ua::ReferenceDescription {
    ua::ReferenceDescription {
        node_id: ua::ExpandedNodeId {
            server_index,
            ..Default::default()
        },
        browse_name: ua::QualifiedName::new(2, name),
        ..Default::default()
    }
}

fn page(point: &[u8], references: Vec<ua::ReferenceDescription>) -> ua::BrowseResult {
    ua::BrowseResult {
        status_code: ua::StatusCode::Good,
        continuation_point: point.to_vec().into(),
        references: Some(references),
    }
}

#[tokio::test]
async fn pagination_collects_every_page_with_one_aggregated_warning() -> PyResult<()> {
    let mut replies = VecDeque::from([
        vec![page(b"second", vec![reference("remote-b", 2)])],
        vec![page(b"", vec![reference("local-b", 0)])],
    ]);

    let first = page(
        b"first",
        vec![reference("local-a", 0), reference("remote-a", 1)],
    );

    let mut requests = Vec::new();
    let output = collect_children(Ok(vec![first]), |release, points| {
        requests.push((release, points));
        let reply = replies
            .pop_front()
            .ok_or_else(|| PyValueError::new_err("missing page"));
        Box::pin(async move { reply })
    })
    .await;

    assert_eq!(
        requests,
        vec![
            (false, vec![b"first".to_vec().into()]),
            (false, vec![b"second".to_vec().into()])
        ]
    );

    with_python(|py| {
        let warnings = py.import("warnings")?;
        let context = py.eval(
            c"__import__('warnings').catch_warnings(record=True)",
            None,
            None,
        )?;

        let recorded = context.call_method0("__enter__")?;
        warnings.call_method1("simplefilter", ("always",))?;
        let result = output.finish(py);
        context.call_method1("__exit__", (py.None(), py.None(), py.None()))?;

        let names = result?
            .into_iter()
            .map(|reference| reference.browse_name.name)
            .collect::<Vec<_>>();

        assert_eq!(
            names,
            [ua::UAString::from("local-a"), ua::UAString::from("local-b")]
        );

        assert_eq!(recorded.len()?, 1);

        let warning = recorded.get_item(0)?.getattr("message")?;
        assert!(warning.is_instance_of::<PyRuntimeWarning>());
        let text = warning.str()?.to_str()?.to_owned();
        assert!(text.contains("remote-a") && text.contains("remote-b"));
        Ok(())
    })
}

#[tokio::test]
async fn failed_next_requests_release_prior_points_and_preserve_primary_errors() -> PyResult<()> {
    Python::initialize();

    for service_failure in [true, false] {
        let first = page(b"prior", vec![reference("remote-before-failure", 3)]);
        let mut requests = Vec::new();
        let output = collect_children(Ok(vec![first]), |release, points| {
            requests.push((release, points));
            Box::pin(async move {
                if release || service_failure {
                    let code = if release {
                        ua::StatusCode::BadInternalError
                    } else {
                        ua::StatusCode::BadTimeout
                    };

                    Err(error::status(code, "fixture failure"))
                } else {
                    Ok(vec![ua::BrowseResult {
                        status_code: ua::StatusCode::BadTimeout,
                        ..page(b"", vec![])
                    }])
                }
            })
        })
        .await;

        assert_eq!(
            requests,
            vec![
                (false, vec![b"prior".to_vec().into()]),
                (true, vec![b"prior".to_vec().into()])
            ]
        );

        with_python(|py| {
            let context = py.import("warnings")?.call_method0("catch_warnings")?;
            context.call_method0("__enter__")?;
            py.import("warnings")?
                .call_method1("simplefilter", ("error",))?;

            let failure = output.finish(py).unwrap_err();
            context.call_method1("__exit__", (py.None(), py.None(), py.None()))?;
            assert_eq!(status(failure.clone_ref(py))?, 0x800A_0000);

            let warning = failure
                .context(py)
                .ok_or_else(|| PyValueError::new_err("missing warning"))?;
            assert!(warning.is_instance_of::<PyRuntimeWarning>(py));
            assert!(
                warning
                    .value(py)
                    .str()?
                    .to_str()?
                    .contains("remote-before-failure")
            );

            Ok(())
        })?;
    }

    Ok(())
}

#[tokio::test]
async fn malformed_page_count_releases_every_received_point() -> PyResult<()> {
    Python::initialize();
    let mut released = Vec::new();
    let pages = vec![page(b"a", vec![]), page(b"b", vec![])];
    let output = collect_children(Ok(pages), |release, points| {
        assert!(release);
        released = points;
        Box::pin(async { Ok(vec![]) })
    })
    .await;

    let expected: Vec<ua::ByteString> = vec![b"a".to_vec().into(), b"b".to_vec().into()];
    assert_eq!(released, expected);
    assert_eq!(status(output.result.unwrap_err())?, 0x8001_0000);
    Ok(())
}

#[test]
fn native_pagination_and_retained_nodes_keep_rooted_identity() -> TestResult {
    let server = TestServer::builder()
        .add_folder(FolderSpec::new(1000, "Plant"))
        .add_folder(FolderSpec::new(1001, "Temperature").parent(ParentRef::Label("Plant".into())))
        .add_folder(FolderSpec::new(1002, "Private").parent(ParentRef::Label("Plant".into())))
        .start()?;

    crate::test_support::with_python(|py| {
        let client = crate::test_support::connected(py, &server)?;
        let plant_path = format!("/Objects/{}:Plant", server.namespace_index());
        let plant = client
            .call_method1("get_node", (plant_path,))?
            .extract::<Node>()?;

        drop(client);

        let children = plant.browse_children(py, 1)?;
        assert_eq!(children.len(), 2);

        for child in children {
            assert_eq!(child.browse_path.starting_node, ua::NodeId::new(0, 84));
            let root = Node::root(child.client.clone());
            let resolved = util::wait(py, async move {
                root.resolve(child.browse_path.relative_path).await
            })?;

            assert_eq!(resolved.node_id, child.node_id);
        }

        let client = plant.client.clone();
        util::wait(py, async move { client.disconnect_async().await })?;
        Ok(())
    })?;
    Ok(())
}
