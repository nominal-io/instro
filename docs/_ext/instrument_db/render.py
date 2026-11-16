"""Turn records into MyST pages: driver stubs, the type and manufacturer indexes, and the searchable home.

Code examples come from one template per ``driver_type`` (``EXAMPLES``). A new library adds a template here;
records of a type with no template still get a stub, with the import line and a pointer to the library.
"""

from __future__ import annotations

import html
import json
import posixpath
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from .records import InstrumentRecord, category_title, slug

ROOT = "instruments"  # docs folder of the whole section
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".svg")

# driver_type -> (badge label, one-line description of where the driver comes from)
DRIVER_TYPES: dict[str, tuple[str, str]] = {
    "instro": ("instro", "Native instro driver, used through its instrument class."),
    "pymeasure": (
        "pymeasure",
        "Driver from [pymeasure](https://pymeasure.readthedocs.io), a third-party library. "
        "instro publishes what it sends and reads.",
    ),
}

CATEGORY_NOUN = {
    "psu": "power supply",
    "dmm": "multimeter",
    "eload": "electronic load",
    "scope": "oscilloscope",
    "awg": "function generator",
    "daq": "data acquisition system",
    "i2c": "I2C adapter",
    "flowcontroller": "flow controller",
    "sdr": "software defined radio",
    "vna": "network analyzer",
    "spectrum": "spectrum analyzer",
    "temperature": "temperature controller",
    "lockin": "lock-in amplifier",
    "motion": "motion controller",
    "laser": "laser instrument",
    "pressure": "pressure or vacuum instrument",
    "rf": "RF instrument",
}
_CHANNEL_CATEGORIES = {"psu", "scope", "awg"}  # instrument classes that take num_channels


def doc_for(record: InstrumentRecord) -> str:
    return f"{ROOT}/{record.driver_type}/{record.id}"


def url_for(doc: str) -> str:
    """Folder-style (dirhtml) URL of a docname, relative to the site root."""
    return posixpath.dirname(doc) + "/" if doc.endswith("/index") else doc + "/"


def rel_url(from_doc: str, to_doc: str) -> str:
    """Relative link between two pages of a dirhtml build."""
    start = posixpath.dirname(url_for(from_doc).rstrip("/") + "/x")
    return posixpath.relpath(url_for(to_doc), start or ".") + "/"


def noun(record: InstrumentRecord) -> str:
    return CATEGORY_NOUN.get(record.category, "instrument")


def summary_of(record: InstrumentRecord) -> str:
    s = record.summary
    if not s or s.lower().startswith(("the base class", "mixin", "abstract")):
        return f"{record.manufacturer} {record.model} {noun(record)}"
    return s


# -- code examples ---------------------------------------------------------------------------------------


def _placeholder(param: str) -> str:
    return f'"<{param}>"'


def _instro_example(r: InstrumentRecord) -> list[tuple[str, str]]:
    x = r.extra
    channels = x.get("num_channels") or 1
    args = [_placeholder(p) for p in x.get("required_params", [])]
    if "num_channels" in x.get("param_names", []) and "num_channels" not in x.get("required_params", []):
        args.append(f"num_channels={channels}")
    driver = f"{r.name}({', '.join(args)})"
    module = x.get("import_from") or "instro.drivers"
    cls, inst_module = x.get("instrument_class"), x.get("instrument_module")
    if not cls:
        return [("python", f"from {module} import {r.name}\n\ndriver = {driver}\n")]
    var = cls.removeprefix("Instro").lower() if cls.startswith("Instro") else "device"
    label = cls.removeprefix("Instro") if cls.startswith("Instro") else "Device"
    lines = [f"from {inst_module} import {cls}", f"from {module} import {r.name}", ""]
    call = [f'    name="my{label}",', f"    driver={driver},"]
    if r.category in _CHANNEL_CATEGORIES:
        call.append(f"    num_channels={channels},")
    lines += [f"{var} = {cls}(", *call, ")"]
    return [("python", "\n".join(lines) + "\n")]


def _pymeasure_example(r: InstrumentRecord) -> list[tuple[str, str]]:
    x = r.extra
    package = x["package_import"]
    first, *rest = x.get("required_params") or ["adapter"]
    args = ", ".join(['"<visa_resource>"', *[_placeholder(p) for p in rest]])
    var = slug(r.name).replace("-", "_")
    direct = [f"from {package} import {r.name}", "", f"{var} = {r.name}({args})"]
    if x.get("read_example"):
        direct.append(f"print({var}.{x['read_example']})")
    publish = [
        f"from {package} import {r.name}",
        "",
        "from instro.pymeasure import PymeasureInstrument",
        "",
        "with PymeasureInstrument(",
        f'    name="{var}",',
        f"    instrument={r.name}({args}),",
        '    dataset_rid="<dataset rid>",',
        ") as instrument:",
        "    instrument.start()",
    ]
    return [("python", "\n".join(direct) + "\n"), ("python", "\n".join(publish) + "\n")]


EXAMPLES: dict[str, Callable[[InstrumentRecord], list[tuple[str, str]]]] = {
    "instro": _instro_example,
    "pymeasure": _pymeasure_example,
}


def _fence(code: str) -> str:
    return f"```python\n{code}```\n"


def _body(r: InstrumentRecord, srcdir: Path) -> str:
    if r.driver_type == "instro":
        out = ["## Creating the instrument\n", _fence(_instro_example(r)[0][1])]
        guide = r.extra.get("instrument_class")
        if guide and (srcdir / f"{r.category}.md").is_file():
            out.append(f"{r.name} drives an [`{guide}`](/{r.category}.md).\n")
        out.append(
            f"Parameters and methods specific to {{py:obj}}`{r.name} <{r.import_path}>` are in the "
            "[Reference](/ref/index.md).\n"
        )
        return "\n".join(out)
    if r.driver_type == "pymeasure":
        direct, publish = _pymeasure_example(r)
        module = r.import_path.rsplit(".", 1)[0]
        api = f"https://pymeasure.readthedocs.io/en/latest/api/instruments/{r.extra['vendor_package']}"
        api += f"/{module.rsplit('.', 1)[1]}.html#{r.import_path}"
        return "\n".join(
            [
                "## Using the pymeasure driver\n",
                _fence(direct[1]),
                "## Publishing through instro\n",
                "Wrap the driver to publish every command and reading through an instro "
                "[publisher](/library/publishers.md), as [described here](/library/pymeasure.md).\n",
                _fence(publish[1]),
                "Install with `uv add \"instro[pymeasure]\"`.\n",
                f"For the full list of properties and methods, see the [pymeasure documentation for "
                f"`{r.name}`]({api}).\n",
            ]
        )
    return "\n".join(["## Usage\n", f"```python\nfrom {r.import_path.rsplit('.', 1)[0]} import {r.name}\n```\n"])


def _image(r: InstrumentRecord, srcdir: Path) -> str | None:
    """Path of the device's picture relative to the docs root, if there is one.

    Looked up by convention, so adding ``images/<driver_type>/<id>.png`` is all it takes. Instro drivers also
    reuse the ``docs/<category>/<ClassName>.png`` that their driver page already has.
    """
    candidates = [f"{ROOT}/images/{r.driver_type}/{r.id}{s}" for s in IMAGE_SUFFIXES]
    if r.driver_type == "instro":
        candidates += [f"{r.category}/{r.name}{s}" for s in IMAGE_SUFFIXES]
    return next((c for c in candidates if (srcdir / c).is_file()), None)


def _yaml_str(text: str) -> str:
    return json.dumps(text)


def stub_page(r: InstrumentRecord, srcdir: Path) -> str:
    badge, blurb = DRIVER_TYPES.get(r.driver_type, (r.driver_type, ""))
    title = r.display_name
    description = (
        f"Control the {title} {noun(r)} from Python with the {badge} {r.name} driver: "
        "a short code example, and links to its reference."
    )
    image = _image(r, srcdir)
    meta = [
        f"**Driver:** {badge}",
        f"**Type:** [{category_title(r.category)}](/{ROOT}/type/{slug(r.category)}.md)",
        f"**Manufacturer:** [{r.manufacturer}](/{ROOT}/manufacturer/{slug(r.manufacturer)}.md)",
    ]
    parts = [
        "---\norphan: true\n"
        f"seo_title: {_yaml_str(f'{title} Python Driver ({badge}) - instro')}\n"
        f"myst:\n  html_meta:\n    description: {_yaml_str(description)}\n---\n",
        f"# {title}\n",
        f"{{.lead}}\nControl the {title} {noun(r)} from Python with the `{r.name}` driver.\n",
        "  \n".join(meta) + "\n",
    ]
    if image:
        parts.append(f"![{title}](/{image}){{.driver-image}}\n")
    if r.tier == "unstable":
        parts.append(":::{warning}\nThis driver ships in `instro-unstable`. Its API may change without notice.\n:::\n")
    elif r.tier == "contrib":
        parts.append(
            ":::{note}\nCommunity-contributed driver in `instro-contrib`, verified on hardware by its contributor. "
            "See [Contrib drivers](/library/contrib.md).\n:::\n"
        )
    if r.simulated:
        parts.append("This driver simulates the instrument: no hardware is needed.\n")
    if blurb and r.driver_type != "instro":
        parts.append(f"{blurb}\n")
    parts.append(f"{summary_of(r).rstrip('…')}{'' if summary_of(r).endswith('…') else '.'}\n")
    parts.append(_body(r, srcdir))
    return "\n".join(parts)


# -- index pages -----------------------------------------------------------------------------------------


def _group(records: list[InstrumentRecord], key: Callable[[InstrumentRecord], str]) -> dict[str, list[InstrumentRecord]]:
    groups: dict[str, list[InstrumentRecord]] = defaultdict(list)
    for r in records:
        groups[key(r)].append(r)
    return dict(groups)


def _sorted(records: list[InstrumentRecord]) -> list[InstrumentRecord]:
    return sorted(records, key=lambda r: (r.simulated, r.manufacturer.lower(), r.model.lower(), r.driver_type))


def _link_list(records: list[InstrumentRecord]) -> str:
    lines = []
    for r in _sorted(records):
        badge = DRIVER_TYPES.get(r.driver_type, (r.driver_type,))[0]
        lines.append(f"- [{r.display_name}](/{doc_for(r)}.md) ({badge}): {summary_of(r)}")
    return "\n".join(lines) + "\n"


def _front(description: str) -> str:
    return f"---\nmyst:\n  html_meta:\n    description: {_yaml_str(description)}\n---\n\n"


def category_pages(records: list[InstrumentRecord]) -> dict[str, str]:
    """``type/<category>`` pages and the ``type/index`` that lists and toctrees them."""
    groups = _group(records, lambda r: r.category)
    order = sorted(groups, key=lambda c: (c == "other", category_title(c)))
    pages = {}
    for cat in order:
        title = category_title(cat)
        pages[f"{ROOT}/type/{slug(cat)}"] = (
            _front(f"{title} supported by instro: {len(groups[cat])} drivers from instro and third-party libraries.")
            + f"# {title}\n\n{{.lead}}\n{len(groups[cat])} supported instruments.\n\n"
            + _link_list(groups[cat])
        )
    items = "\n".join(f"- [{category_title(c)}](/{ROOT}/type/{slug(c)}.md) ({len(groups[c])})" for c in order)
    toc = "\n".join(f"{category_title(c)} <{slug(c)}>" for c in order)
    pages[f"{ROOT}/type/index"] = (
        _front("Browse the instruments instro supports by type.")
        + f"# By type\n\n{{.lead}}\nBrowse supported instruments by what they measure or source.\n\n{items}\n\n"
        + f"```{{toctree}}\n:hidden:\n\n{toc}\n```\n"
    )
    return pages


def manufacturer_pages(records: list[InstrumentRecord]) -> dict[str, str]:
    groups = _group(records, lambda r: slug(r.manufacturer))
    names = {s: rs[0].manufacturer for s, rs in groups.items()}
    order = sorted(groups, key=lambda s: names[s].lower())
    pages = {}
    for s in order:
        name = names[s]
        pages[f"{ROOT}/manufacturer/{s}"] = (
            _front(f"{name} instruments supported by instro: {len(groups[s])} drivers.")
            + f"# {name}\n\n{{.lead}}\n{len(groups[s])} supported instruments.\n\n"
            + _link_list(groups[s])
        )
    items = "\n".join(f"- [{names[s]}](/{ROOT}/manufacturer/{s}.md) ({len(groups[s])})" for s in order)
    toc = "\n".join(f"{names[s]} <{s}>" for s in order)
    pages[f"{ROOT}/manufacturer/index"] = (
        _front("Browse the instruments instro supports by manufacturer.")
        + f"# By manufacturer\n\n{{.lead}}\nBrowse supported instruments by manufacturer.\n\n{items}\n\n"
        + f"```{{toctree}}\n:hidden:\n\n{toc}\n```\n"
    )
    return pages


def _search_entry(i: int, r: InstrumentRecord, home: str) -> dict[str, object]:
    return {
        "n": r.display_name,
        "m": r.manufacturer,
        "mo": r.model,
        "cl": r.name,
        "c": r.category,
        "ct": category_title(r.category),
        "t": r.driver_type,
        "u": rel_url(home, doc_for(r)),
        "s": summary_of(r),
        "k": r.tier,
        "v": r.simulated,
    }


def _item(i: int, r: InstrumentRecord, home: str) -> str:
    return f'<li class="idb-item" data-i="{i}"><a href="{html.escape(rel_url(home, doc_for(r)))}">{html.escape(r.display_name)}</a></li>'


def home_page(records: list[InstrumentRecord]) -> str:
    """The Instruments tab's home: search box, filters, and the full list in both groupings.

    The lists are plain HTML so the page works without JavaScript; ``instrument-db.js`` adds ranked search,
    filtering, and the type/manufacturer toggle, over the JSON embedded at the end.
    """
    home = f"{ROOT}/index"
    ordered = _sorted(records)
    index = {id(r): i for i, r in enumerate(ordered)}
    by_type = _group(ordered, lambda r: r.category)
    by_maker = _group(ordered, lambda r: slug(r.manufacturer))
    e = html.escape

    def section(title: str, href: str, rs: list[InstrumentRecord]) -> str:
        items = "".join(_item(index[id(r)], r, home) for r in rs)
        return (
            f'<section class="idb-group"><h2 class="idb-group-title"><a href="{e(href)}">{e(title)}</a>'
            f'<span class="idb-count-chip">{len(rs)}</span></h2><ul class="idb-list">{items}</ul></section>'
        )

    types_html = "".join(
        section(category_title(c), rel_url(home, f"{ROOT}/type/{slug(c)}"), by_type[c])
        for c in sorted(by_type, key=lambda c: (c == "other", category_title(c)))
    )
    makers_html = "".join(
        section(by_maker[s][0].manufacturer, rel_url(home, f"{ROOT}/manufacturer/{s}"), by_maker[s])
        for s in sorted(by_maker, key=lambda s: by_maker[s][0].manufacturer.lower())
    )
    driver_types = sorted({r.driver_type for r in ordered}, key=lambda t: (t != "instro", t))
    chips = "".join(
        f'<button type="button" class="idb-chip" data-driver="{e(t)}" aria-pressed="false">'
        f"{e(DRIVER_TYPES.get(t, (t,))[0])}</button>"
        for t in driver_types
    )
    cats = sorted({r.category for r in ordered}, key=lambda c: (c == "other", category_title(c)))
    options = "".join(f'<option value="{e(c)}">{e(category_title(c))}</option>' for c in cats)
    data = json.dumps([_search_entry(i, r, home) for i, r in enumerate(ordered)], separators=(",", ":"))
    data = data.replace("</", "<\\/")
    widget = f"""
<div class="idb" data-idb>
  <div class="idb-bar">
    <input class="idb-search" type="search" placeholder="Search instruments, manufacturers, models" aria-label="Search instruments" autocomplete="off" spellcheck="false">
    <select class="idb-category" aria-label="Filter by type"><option value="">All types</option>{options}</select>
  </div>
  <div class="idb-controls">
    <div class="idb-chips" role="group" aria-label="Driver">{chips}</div>
    <div class="idb-view" role="group" aria-label="Group by">
      <button type="button" class="idb-chip" data-view="type" aria-pressed="true">By type</button>
      <button type="button" class="idb-chip" data-view="manufacturer" aria-pressed="false">By manufacturer</button>
    </div>
  </div>
  <p class="idb-status" aria-live="polite">{len(ordered)} instruments</p>
  <table class="idb-results" hidden><thead><tr><th>Instrument</th><th>Type</th><th>Driver</th></tr></thead><tbody></tbody></table>
  <div class="idb-browse" data-group="type">{types_html}</div>
  <div class="idb-browse" data-group="manufacturer" hidden>{makers_html}</div>
  <script type="application/json" id="idb-data">{data}</script>
</div>
"""
    counts = {t: sum(1 for r in ordered if r.driver_type == t) for t in driver_types}
    summary = ", ".join(f"{n} from {DRIVER_TYPES.get(t, (t,))[0]}" for t, n in counts.items())
    return (
        _front(
            f"Search and browse the {len(ordered)} instruments supported through instro, by type or manufacturer, "
            "with a Python example for each."
        )
        + "# Supported instruments\n\n"
        + "{.lead}\nSearchable database of instro instruments\n\n"
        + f"```{{raw}} html\n{widget}```\n\n"
        + "If your instrument is not listed, see [Custom Driver Development](/library/custom-instruments.md), "
        + "or open a [Driver Request](https://github.com/nominal-io/instro/issues) issue on GitHub.\n"
    )
