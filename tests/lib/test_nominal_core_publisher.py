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


def test_needs_exactly_one_of_dataset_rid_and_dataset():
    with pytest.raises(ValueError, match="exactly one"):
        NominalCorePublisher()
    with pytest.raises(ValueError, match="exactly one"):
        NominalCorePublisher(RID, dataset=_dataset())


def test_dataset_is_used_without_resolving_credentials():
    dataset = _dataset()
    with patch("instro.lib.publishers.nominal_core._resolve_nominal_client_and_dataset") as resolve:
        publisher = NominalCorePublisher(dataset=dataset)

    resolve.assert_not_called()
    assert publisher.dataset is dataset and publisher.dataset_rid == RID
    assert publisher.client is None and publisher.profile is None
    dataset.get_write_stream.return_value._impl.open.assert_called_once()


def test_client_fetches_the_dataset():
    client = create_autospec(NominalClient, instance=True)
    client.get_dataset.return_value = _dataset()

    publisher = NominalCorePublisher(RID, client=client)

    client.get_dataset.assert_called_once_with(RID)
    assert publisher.client is client and publisher.profile is None
    assert publisher._credentials() == "provided client"


def test_close_is_idempotent():
    publisher = NominalCorePublisher(dataset=_dataset())

    publisher.close()
    publisher.close()

    publisher.write_stream.close.assert_called_once()
    assert publisher.closed


def test_explicit_falsy_stream_settings_are_kept():
    """``max_wait=timedelta(0)`` means flush immediately, not "use the default"."""
    publisher = NominalCorePublisher(dataset=_dataset(), max_wait=timedelta(0))

    assert publisher.max_wait == timedelta(0)
    assert publisher.dataset.get_write_stream.call_args.kwargs["max_wait"] == timedelta(0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"profile": "staging", "api_key": "secret"},
        {"base_url": "https://api.nominal.test/api"},
        {"client": create_autospec(NominalClient, instance=True), "profile": "staging"},
    ],
)
def test_conflicting_credentials_are_rejected(kwargs):
    with pytest.raises(ValueError):
        NominalCorePublisher(RID, **kwargs)


def test_api_key_is_never_shown():
    client = create_autospec(NominalClient, instance=True)
    client.get_dataset.return_value = _dataset()
    with patch("instro.lib.nominal.NominalClient.from_token", return_value=client) as from_token:
        publisher = NominalCorePublisher(RID, api_key="secret-key", base_url="https://api.nominal.test/api")

    from_token.assert_called_once_with("secret-key", "https://api.nominal.test/api")
    html = publisher._repr_html_()
    assert html is not None
    assert "secret-key" not in repr(publisher) and "secret-key" not in html
    assert "secret-key" not in str(vars(publisher))


def test_card_links_the_dataset_and_escapes_its_name():
    publisher = NominalCorePublisher(dataset=_dataset())

    html = publisher._repr_html_()

    assert html is not None
    assert 'href="https://app.nominal.test/w/ws/datasets/abc"' in html
    assert "Bench &lt;run 1&gt;" in html and "<run 1>" not in html


def test_card_drops_links_that_are_off_or_unresolvable():
    def unreachable(_):
        raise ConnectionError("offline")

    dataset = _dataset()
    publisher = NominalCorePublisher(dataset=dataset)
    with set_display_options(show_link=False):
        assert "href" not in (publisher._repr_html_() or "")

    type(dataset).nominal_url = property(unreachable)
    html = publisher._repr_html_()
    assert html is not None and "href" not in html


def test_instrument_passes_nominal_options_through_and_keeps_them_out_of_tags():
    client = create_autospec(NominalClient, instance=True)
    with patch("instro.lib.instrument.NominalCorePublisher") as publisher_cls:
        instrument = Instrument(name="ut", dataset_rid=RID, nominal_profile="staging", nominal_client=client)

    publisher_cls.assert_called_once_with(RID, profile="staging", client=client)
    assert not {"dataset_rid", "nominal_profile", "nominal_client"} & set(instrument.default_tags)


def test_instrument_rejects_nominal_options_without_dataset_rid():
    with pytest.raises(ValueError, match="dataset_rid"):
        Instrument(name="ut", nominal_profile="staging")
