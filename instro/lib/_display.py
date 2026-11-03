"""Rich notebook display for instruments and publishers.

Jupyter, VS Code and Colab call ``_repr_html_`` to display an object at the end of a cell. The objects that
implement it build their HTML from the helpers here, and `set_display_options` configures how it looks.

Copied from ``nominal/core/_display.py`` in nominal-io/nominal-client#1260, with the same CSS class names and
`render`/`Section` shapes so the cards look alike and a later switch to a shared module is mechanical. Consolidation
is tracked in nominal-io/instro#710.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from types import TracebackType
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DisplayStyle = Literal["html", "text"]
TimeFormat = Literal["iso", "readable"]
TimePrecision = Literal["s", "ms", "us", "ns"]
_FRACTION_DIGITS: dict[str, int] = {"s": 0, "ms": 3, "us": 6, "ns": 9}


def _resolve_timezone(name: str) -> tzinfo | None:
    """Return the tzinfo for an option value, where `None` means the kernel's local time zone."""
    if name == "local":
        return None
    if name == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"unknown time zone {name!r}; use 'UTC', 'local' or an IANA name") from e


@dataclass(frozen=True)
class DisplayOptions:
    """How instruments and publishers display in notebooks.

    Change these with `set_display_options`, and read the current values with `get_display_options`.
    """

    style: DisplayStyle = "html"
    """``"html"`` shows objects as formatted cards. ``"text"`` shows their plain ``repr``."""
    timezone: str = "UTC"
    """Time zone for timestamps: ``"UTC"``, ``"local"`` for the notebook kernel's time zone, or an IANA name such as
    ``"Europe/Berlin"``."""
    time_format: TimeFormat = "iso"
    """``"iso"`` shows timestamps as ISO 8601, such as ``2026-11-02T16:33:03Z``. ``"readable"`` uses a space and
    the time zone's name, such as ``2026-11-02 16:33:03 UTC``."""
    time_precision: TimePrecision = "s"
    """The finest unit timestamps show: ``"s"``, ``"ms"``, ``"us"`` or ``"ns"``. Finer digits are cut off, not
    rounded, and trailing zeros are left out, so ``"ns"`` shows each timestamp exactly."""
    show_link: bool = True
    """Whether cards link datasets to their pages in the Nominal app.

    Building a link resolves the client's workspace. The first card for a client that hasn't resolved it yet makes
    one request; later cards reuse the result.
    """
    collapse_threshold: int = 10
    """Sections with more items than this, such as channels, start collapsed."""
    max_items: int = 50
    """The most items a section lists. The section says how many more were left out."""

    def __post_init__(self) -> None:
        if self.style not in ("html", "text"):
            raise ValueError(f"style must be 'html' or 'text', not {self.style!r}")
        if self.time_format not in ("iso", "readable"):
            raise ValueError(f"time_format must be 'iso' or 'readable', not {self.time_format!r}")
        if self.time_precision not in _FRACTION_DIGITS:
            raise ValueError(f"time_precision must be 's', 'ms', 'us' or 'ns', not {self.time_precision!r}")
        if self.collapse_threshold < 0:
            raise ValueError(f"collapse_threshold must be at least 0, not {self.collapse_threshold}")
        if self.max_items < 1:
            raise ValueError(f"max_items must be at least 1, not {self.max_items}")
        _resolve_timezone(self.timezone)


_options = DisplayOptions()


def get_display_options() -> DisplayOptions:
    """Return the current notebook display options."""
    return _options


class set_display_options:  # noqa: N801 - named like a function because it's called like one, as in xarray
    """Change how instruments and publishers display in notebooks.

    Options left out keep their current values. The change applies to the whole Python process. Used as a context
    manager, it lasts until the ``with`` block ends.

    Args:
        style: ``"html"`` shows objects as formatted cards. ``"text"`` shows their plain ``repr``.
        timezone: Time zone for timestamps: ``"UTC"``, ``"local"``, or an IANA name such as ``"Europe/Berlin"``.
        time_format: ``"iso"`` for ISO 8601, or ``"readable"`` for a space and the time zone's name.
        time_precision: The finest unit timestamps show: ``"s"``, ``"ms"``, ``"us"`` or ``"ns"``.
        show_link: Whether cards link datasets to their pages in the Nominal app.
        collapse_threshold: Sections with more items than this start collapsed.
        max_items: The most items a section lists.

    Raises:
        ValueError: If an option has an invalid value, such as an unknown time zone.

    Example:
        .. code-block:: python

            from instro.lib import set_display_options

            set_display_options(timezone="America/Los_Angeles", time_precision="ms")

            with set_display_options(show_link=False):
                psu  # shows the card without resolving Nominal links
    """

    def __init__(
        self,
        *,
        style: DisplayStyle | None = None,
        timezone: str | None = None,
        time_format: TimeFormat | None = None,
        time_precision: TimePrecision | None = None,
        show_link: bool | None = None,
        collapse_threshold: int | None = None,
        max_items: int | None = None,
    ) -> None:
        global _options
        self._previous = _options
        _options = DisplayOptions(
            style=_options.style if style is None else style,
            timezone=_options.timezone if timezone is None else timezone,
            time_format=_options.time_format if time_format is None else time_format,
            time_precision=_options.time_precision if time_precision is None else time_precision,
            show_link=_options.show_link if show_link is None else show_link,
            collapse_threshold=_options.collapse_threshold if collapse_threshold is None else collapse_threshold,
            max_items=_options.max_items if max_items is None else max_items,
        )

    def __enter__(self) -> DisplayOptions:
        return _options

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        global _options
        _options = self._previous


@dataclass(frozen=True)
class Section:
    """A collapsible part of a card, such as an instrument's channels or publishers."""

    title: str
    rows: Sequence[Sequence[str]] = ()
    """Cell HTML for each row, already escaped."""
    columns: Sequence[str] = ()
    """Column headings. Without them, the first cell of each row is a row heading."""
    has_more: bool = False
    """Whether more rows exist than were loaded."""
    body: str = ""
    """HTML shown before the rows."""
    note: str = ""
    """Text shown after the rows, such as why there are none."""
    counted: bool = True
    """Whether the title shows how many rows there are."""


def escape(value: object) -> str:
    """Return ``str(value)`` with HTML special characters escaped."""
    return html.escape(str(value))


def code(value: object) -> str:
    """Return ``value`` escaped and set in a ``<code>`` element."""
    return f"<code>{escape(value)}</code>"


def timestamp(value: int) -> str:
    """Format a nanosecond UTC timestamp in the configured time zone, format and precision."""
    seconds, nanos = divmod(value, 1_000_000_000)
    moment = datetime.fromtimestamp(seconds, timezone.utc).astimezone(_resolve_timezone(_options.timezone))
    fraction = f"{nanos:09d}"[: _FRACTION_DIGITS[_options.time_precision]]
    # Leave out trailing zeros in groups of three, so whole milliseconds don't show as nanoseconds.
    while fraction.endswith("000"):
        fraction = fraction[:-3]
    clock = moment.strftime("%H:%M:%S") + (f".{fraction}" if fraction else "")
    if _options.time_format == "readable":
        text = f"{moment:%Y-%m-%d} {clock} {moment.tzname()}"
    else:
        offset = moment.strftime("%z")
        zone = "Z" if offset == "+0000" else f"{offset[:3]}:{offset[3:]}"
        text = f"{moment:%Y-%m-%d}T{clock}{zone}"
    # The full value stays available on hover and to scripts reading the page.
    full = f"{datetime.fromtimestamp(seconds, timezone.utc):%Y-%m-%dT%H:%M:%S}.{nanos:09d}Z"
    return f'<time datetime="{full}" title="{full}">{escape(text)}</time>'


def seconds(value: float) -> str:
    """Format a short duration given in seconds, such as a loop interval."""
    if value == 0:
        return "0 s"
    if abs(value) < 1:
        return escape(f"{value * 1000:.3g} ms")
    return escape(f"{value:.3g} s")


def value(data: float | str | None) -> str:
    """Format a channel value: numbers to six significant digits, strings as they are."""
    if data is None:
        return ""
    if isinstance(data, str):
        return escape(data)
    return escape(f"{data:.6g}")


def link(url: str, text: str) -> str:
    """An external link, or plain text when the URL isn't http(s), so a stored value can't run script."""
    if not _is_web_url(url):
        return escape(text)
    return f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer">{escape(text)}</a>'


def resource_link(text: str, url: Callable[[], str]) -> str:
    """Link text to a page in the Nominal app, when links are on and the workspace resolves."""
    resolved = _resolved_url(url)
    return escape(text) if resolved is None else link(resolved, text)


def _is_web_url(url: str) -> bool:
    return url.startswith(("https://", "http://"))


def _resolved_url(url: Callable[[], str]) -> str | None:
    """A page in the Nominal app, or `None` when links are off or the page can't be linked.

    Resolving the workspace can make a request, so the text style, which builds no card, never resolves one.
    """
    if _options.style == "text" or not _options.show_link:
        return None
    try:
        resolved = url()
    except Exception:  # noqa: BLE001 - offline or unauthorized: show the card without the link
        return None
    return resolved if _is_web_url(resolved) else None


def copy_button(value: str) -> str:
    """A button that copies `value`. Notebooks that strip scripts leave it inert; the value stays selectable."""
    return (
        f'<button type="button" class="nominal-repr-copy" title="Copy" aria-label="Copy {escape(value)}" '
        f'data-copy="{escape(value)}" '
        "onclick=\"navigator.clipboard.writeText(this.dataset.copy).then(() => { this.textContent = '✓'; })\""
        ">⧉</button>"
    )


def identifier(value: str, url: Callable[[], str] | None = None) -> str:
    """An ID such as a RID: selectable in one click, with a copy button, and linked when it has a page."""
    text = escape(value) if url is None else resource_link(value, url)
    return f'<span class="nominal-repr-id-group"><code class="nominal-repr-id">{text}</code>{copy_button(value)}</span>'


def named(name: str, rid: str, url: Callable[[], str] | None = None) -> str:
    """A resource's name, linked to its page, with a button that copies its RID."""
    text = escape(name) if url is None else resource_link(name, url)
    return f'<span class="nominal-repr-id-group">{text}{copy_button(rid)}</span>'


def properties_section(properties: Mapping[str, object], title: str = "Properties") -> Section:
    """A section listing ``properties`` as key and value rows, sorted by key."""
    return Section(title, [[escape(key), escape(value)] for key, value in sorted(properties.items())])


def built(title: str, build: Callable[[], Section]) -> Section:
    """A section built from in-memory state, or a note in its place if building it fails.

    A display shows what it can: a failure, such as a buffer closing mid-read, must not hide the rest of the card.
    """
    try:
        return build()
    except Exception as e:  # noqa: BLE001 - the error is reported in the card
        message = str(e).splitlines()[0] if str(e) else ""
        return Section(title, note=f"Couldn't show: {type(e).__name__} {message}"[:300])


def summary(obj: object) -> str:
    """One line of HTML describing `obj`, for a row in another object's card.

    Objects that define ``_display_summary`` describe themselves; any other object shows its class name.
    """
    describe = getattr(obj, "_display_summary", None)
    if callable(describe):
        try:
            return str(describe())
        except Exception as e:  # noqa: BLE001 - one bad row must not hide the card
            return escape(f"{type(obj).__name__} (couldn't show: {type(e).__name__})")
    return code(type(obj).__name__)


def render(
    *,
    kind: str,
    title: str,
    fallback: str,
    fields: Sequence[tuple[str, str | None]],
    sections: Sequence[Section | None] = (),
    badges: Sequence[str] = (),
    url: Callable[[], str] | None = None,
) -> str | None:
    """Build an object's card, or return `None` so the notebook shows its plain ``repr`` instead.

    Args:
        kind: The object's type, such as ``"InstroPSU"``.
        title: The object's display name, unescaped.
        fallback: Plain text shown where the notebook strips styles, such as on GitHub.
        fields: Label and value HTML for the card's table. Values must already be escaped. Fields whose value is
            `None` are left out.
        sections: Collapsible parts after the table. Empty sections and `None` are left out.
        badges: Short unescaped states shown after the title, such as ``"running"``.
        url: Returns the object's page in the Nominal app.
    """
    if _options.style == "text":
        return None

    header = [f'<span class="nominal-repr-kind">{escape(kind)}</span>', f"<strong>{escape(title)}</strong>"]
    header.extend(f'<span class="nominal-repr-badge">{escape(badge)}</span>' for badge in badges)
    if url is not None and (page := _resolved_url(url)) is not None:
        header.append(f'<span class="nominal-repr-link">{link(page, "Open in Nominal ↗")}</span>')

    rows = "".join(f"<tr><th>{escape(label)}</th><td>{value}</td></tr>" for label, value in fields if value is not None)
    body = [f'<div class="nominal-repr-header">{" ".join(header)}</div>', f"<table>{rows}</table>"]
    body.extend(
        _render_section(section)
        for section in sections
        if section is not None and (section.rows or section.body or section.note)
    )
    return (
        f"<style>{_CSS}</style>"
        f'<pre class="nominal-repr-fallback">{escape(fallback)}</pre>'
        f'<div class="nominal-repr" style="display: none">{"".join(body)}</div>'
    )


def _render_section(section: Section) -> str:
    rows = section.rows[: _options.max_items]
    left_out = len(section.rows) - len(rows)
    content = section.body
    if rows:
        head = "".join(f"<th>{escape(column)}</th>" for column in section.columns)
        lines = [f"<tr>{head}</tr>"] if head else []
        for row in rows:
            cells = [f"<td>{cell}</td>" for cell in row]
            if not section.columns and len(row) > 1:
                cells[0] = f"<th>{row[0]}</th>"
            lines.append(f"<tr>{''.join(cells)}</tr>")
        content += f"<table>{''.join(lines)}</table>"
    if left_out:
        content += f'<p class="nominal-repr-more">… and {left_out} more</p>'
    elif section.has_more:
        content += f'<p class="nominal-repr-more">Showing the first {len(rows)}.</p>'
    if section.note:
        content += f'<p class="nominal-repr-more">{escape(section.note)}</p>'

    count = f"{len(section.rows)}{'+' if section.has_more else ''}"
    summary = escape(section.title) + (f" ({count})" if section.rows and section.counted else "")
    is_open = "" if len(section.rows) > _options.collapse_threshold else " open"
    return f"<details{is_open}><summary>{summary}</summary>{content}</details>"


# Colors come from the notebook's theme where it sets them (JupyterLab, then VS Code), so cards follow light and
# dark themes. Where styles are stripped, the hidden card stays hidden and the plain-text fallback shows instead.
_CSS = """
.nominal-repr-fallback { display: none; }
.nominal-repr { display: block !important; }
.nominal-repr {
  --nominal-repr-border: var(--jp-border-color2, var(--vscode-editorWidget-border, rgba(128, 128, 128, 0.4)));
  --nominal-repr-tint: var(--jp-layout-color2, rgba(128, 128, 128, 0.12));
  color: var(--jp-content-font-color0, var(--vscode-editor-foreground, inherit));
  font-family: var(--jp-ui-font-family, var(--vscode-font-family, sans-serif));
  font-size: 13px;
  line-height: 1.5;
  max-width: 900px;
  border: 1px solid var(--nominal-repr-border);
  border-radius: 6px;
  padding: 8px 12px;
}
.nominal-repr-header { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 6px; }
.nominal-repr-kind, .nominal-repr-badge, .nominal-repr-label {
  background: var(--nominal-repr-tint);
  border: 1px solid var(--nominal-repr-border);
  border-radius: 4px;
  padding: 0 6px;
  font-size: 12px;
}
.nominal-repr-badge { font-weight: 600; }
.nominal-repr-link { margin-left: auto; }
.nominal-repr a { color: var(--jp-content-link-color, var(--vscode-textLink-foreground, inherit)); }
.nominal-repr-label { display: inline-block; margin: 1px 0; }
.nominal-repr table { border-collapse: collapse; margin: 0; width: auto; }
.nominal-repr th, .nominal-repr td {
  text-align: left !important;
  vertical-align: top;
  padding: 2px 16px 2px 0 !important;
  border: none !important;
  background: none !important;
  white-space: normal;
  overflow-wrap: break-word;
}
.nominal-repr th { font-weight: 600; white-space: nowrap; }
.nominal-repr code, .nominal-repr time { font-family: var(--jp-code-font-family, monospace); font-size: 12px; }
.nominal-repr time { white-space: nowrap; }
.nominal-repr-id { user-select: all; word-break: break-all; }
.nominal-repr-id-group { display: inline-flex; align-items: flex-start; max-width: 100%; }
.nominal-repr-copy { flex: none; }
.nominal-repr-copy {
  min-width: 20px;
  min-height: 20px;
  margin-left: 4px;
  padding: 0 4px;
  border: 1px solid var(--nominal-repr-border);
  border-radius: 4px;
  background: none;
  color: inherit;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  vertical-align: middle;
}
.nominal-repr-copy:hover { background: var(--nominal-repr-tint); }
.nominal-repr details { border-top: 1px solid var(--nominal-repr-border); margin-top: 6px; padding-top: 4px; }
.nominal-repr summary { cursor: pointer; font-weight: 600; padding: 2px 0; }
.nominal-repr-more { font-style: italic; margin: 2px 0; }
"""
