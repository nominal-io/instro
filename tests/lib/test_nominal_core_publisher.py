from datetime import timedelta
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from nominal.core import Dataset, NominalClient
from nominal_streaming import NominalDatasetStream

from instro.lib import Instrument, set_display_options
from instro.lib.publishers import NominalCorePublisher

RID = "ri.catalog.main.dataset.abc"


def _dataset(nominal_url: str = "https://app.nominal.test/w/ws/datasets/abc") -> MagicMock:
    dataset = create_autospec(Dataset, instance=True)
    dataset.rid = RID
    dataset.name = "Bench <run 1>"
    stream = create_autospec(NominalDatasetStream, instance=True)
    stream._impl = MagicMock()
    dataset.get_write_stream.return_value = stream
    type(dataset).nominal_url = property(lambda _: nominal_url)
    return dataset


def test_needs_exactly_one_dataset():
    with pytest.raises(ValueError, match="dataset"):
        NominalCorePublisher()
    with pytest.raises(ValueError, match="not both"):
        NominalCorePublisher(RID, dataset_rid=RID)


def test_dataset_rid_keyword_still_looks_up_the_rid():
    client = create_autospec(NominalClient, instance=True)
    client.get_dataset.return_value = _dataset()
    with patch("instro.lib.nominal.NominalClient.from_profile", return_value=client) as from_profile:
        publisher = NominalCorePublisher(dataset_rid=RID)

    from_profile.assert_called_once_with("default")
    client.get_dataset.assert_called_once_with(RID)
    assert publisher.client is client and publisher._credentials() == "profile 'default'"


def test_dataset_is_used_without_resolving_credentials():
    dataset = _dataset()
    with patch("instro.lib.publishers.nominal_core._resolve_nominal_client_and_dataset") as resolve:
        publisher = NominalCorePublisher(dataset)

    resolve.assert_not_called()
    assert publisher.dataset is dataset and publisher.dataset_rid == RID
    assert publisher.client is None and publisher.profile is None
    dataset.get_write_stream.return_value._impl.open.assert_called_once()


def test_close_is_idempotent():
    publisher = NominalCorePublisher(_dataset())

    publisher.close()
    publisher.close()

    publisher.write_stream.close.assert_called_once()
    assert publisher.closed


def test_explicit_falsy_stream_settings_are_kept():
    """``max_wait=timedelta(0)`` means flush immediately, not "use the default"."""
    publisher = NominalCorePublisher(_dataset(), max_wait=timedelta(0))

    assert publisher.max_wait == timedelta(0)
    assert publisher.dataset.get_write_stream.call_args.kwargs["max_wait"] == timedelta(0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"dataset": RID, "profile": "staging", "api_key": "secret"},
        {"dataset": _dataset(), "profile": "staging"},
        {"dataset": _dataset(), "api_key": "secret"},
    ],
)
def test_conflicting_credentials_are_rejected(kwargs):
    with pytest.raises(ValueError):
        NominalCorePublisher(**kwargs)


def test_api_key_is_never_shown():
    client = create_autospec(NominalClient, instance=True)
    client.get_dataset.return_value = _dataset()
    with patch("instro.lib.nominal.NominalClient.from_token", return_value=client) as from_token:
        publisher = NominalCorePublisher(RID, api_key="secret-key")

    from_token.assert_called_once_with("secret-key")
    assert publisher._credentials() == "API key"
    html = publisher._repr_html_()
    assert html is not None
    assert "secret-key" not in repr(publisher) and "secret-key" not in html
    assert "secret-key" not in str(vars(publisher))


def test_card_links_the_dataset_and_escapes_its_name():
    publisher = NominalCorePublisher(_dataset())

    html = publisher._repr_html_()

    assert html is not None
    assert 'href="https://app.nominal.test/w/ws/datasets/abc"' in html
    assert "Bench &lt;run 1&gt;" in html and "<run 1>" not in html


def test_card_drops_links_that_are_off_or_unresolvable():
    def unreachable(_):
        raise ConnectionError("offline")

    dataset = _dataset()
    publisher = NominalCorePublisher(dataset)
    with set_display_options(show_link=False):
        assert "href" not in (publisher._repr_html_() or "")

    type(dataset).nominal_url = property(unreachable)
    html = publisher._repr_html_()
    assert html is not None and "href" not in html


def test_instrument_passes_nominal_options_through_and_keeps_them_out_of_tags():
    with patch("instro.lib.instrument.NominalCorePublisher") as publisher_cls:
        instrument = Instrument(name="ut", dataset_rid=RID, nominal_profile="staging")

    publisher_cls.assert_called_once_with(RID, profile="staging")
    assert not {"dataset_rid", "nominal_profile"} & set(instrument.default_tags)


def test_instrument_fetches_the_dataset_with_nominal_client():
    client = create_autospec(NominalClient, instance=True)
    dataset = _dataset()
    client.get_dataset.return_value = dataset
    with patch("instro.lib.instrument.NominalCorePublisher") as publisher_cls:
        instrument = Instrument(name="ut", dataset_rid=RID, nominal_client=client)

    client.get_dataset.assert_called_once_with(RID)
    publisher_cls.assert_called_once_with(dataset)
    assert "nominal_client" not in instrument.default_tags


def test_instrument_rejects_nominal_options_without_dataset_rid():
    with pytest.raises(ValueError, match="dataset_rid"):
        Instrument(name="ut", nominal_profile="staging")
