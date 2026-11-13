import time
from unittest.mock import create_autospec

from instro.lib import set_display_options
from instro.lib.publishers import FilePublisher, QueuedPublisher
from instro.lib.publishers.channel_buffer import DequeInMemoryPublisher
from instro.lib.types import BackgroundDaemonConfig, Measurement
from instro.psu import InstroPSU
from instro.psu.psu import PSUDriverBase


def test_instrument_card_never_calls_the_driver():
    driver = create_autospec(PSUDriverBase, instance=True)
    psu = InstroPSU(name="bench", driver=driver, num_channels=2)

    html = psu._repr_html_()

    assert html is not None
    assert "<th>Driver</th>" in html and "<th>Channel count</th>" in html
    assert driver.mock_calls == []


def test_instrument_card_escapes_user_values():
    psu = InstroPSU(
        name="<b>bench</b>",
        driver=create_autospec(PSUDriverBase, instance=True),
        num_channels=1,
        operator="<script>alert(1)</script>",
    )

    html = psu._repr_html_()

    assert html is not None
    assert "<script>alert" not in html and "&lt;script&gt;alert" in html
    assert "<b>bench</b>" not in html


def test_instrument_card_shows_latest_values_while_running():
    psu = InstroPSU(
        name="bench",
        driver=create_autospec(PSUDriverBase, instance=True),
        num_channels=1,
        background_config=BackgroundDaemonConfig(interval=0.01),
    )
    psu.define_background_daemon(
        lambda: psu.publish(Measurement(channel_data={"bench.probe": [1.25]}, timestamps=[time.time_ns()]))
    )
    psu.start()
    try:
        psu.get_channel("probe", wait_for_new_samples=True)
        html = psu._repr_html_()
    finally:
        psu.stop()

    assert html is not None
    assert "<code>probe</code>" in html and "1.25" in html
    assert "running" in html


def test_text_style_keeps_the_plain_repr(tmp_path):
    publisher = FilePublisher(tmp_path, format="jsonl")
    try:
        with set_display_options(style="text"):
            assert publisher._repr_html_() is None
        assert publisher._repr_html_() is not None
    finally:
        publisher.close()


def test_wrapper_summary_names_the_wrapped_publisher(tmp_path):
    publisher = QueuedPublisher(FilePublisher(tmp_path, format="csv"))
    try:
        assert "csv file" in publisher._display_summary()
    finally:
        publisher.close()


def test_channel_buffer_latest_returns_the_newest_sample_per_channel():
    buffer = DequeInMemoryPublisher(maxlen=5)
    buffer.publish(Measurement(channel_data={"a": [1.0, 2.0], "b": ["x", "y"]}, timestamps=[10, 20]))

    assert buffer.latest() == {"a": (20, 2.0), "b": (20, "y")}
