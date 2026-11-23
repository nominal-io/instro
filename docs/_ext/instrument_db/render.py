"""Turn records into MyST pages: driver stubs, the type and manufacturer indexes, and the searchable home.

Code examples come from one template per ``driver_type`` (``EXAMPLES``). A new library adds a template here;
records of a type with no template still get a stub, with the import line and a pointer to the library.
"""

from __future__ import annotations

import ast
import html
import json
import posixpath
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from .records import InstrumentRecord, category_title, slug

ROOT = "instruments"  # docs folder of the whole section
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".svg")

# driver_type -> (badge label, docs URL of the library, "" for instro itself)
DRIVER_TYPES: dict[str, tuple[str, str]] = {
    "instro": ("instro", ""),
    "pymeasure": ("pymeasure", "https://pymeasure.readthedocs.io"),
    "qcodes": ("QCoDeS", "https://microsoft.github.io/Qcodes/"),
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
    rest = (x.get("required_params") or ["adapter"])[1:]
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
        *(
            [f"    print(instrument.device.{x['read_example']})"]
            if x.get("read_example")
            else ["    instrument.start()"]
        ),
    ]
    return [("python", "\n".join(direct) + "\n"), ("python", "\n".join(publish) + "\n")]


def _qcodes_example(r: InstrumentRecord) -> list[tuple[str, str]]:
    x = r.extra
    package = x["package_import"]
    var = slug(r.name).replace("-", "_")
    args = ", ".join([f'"{var}_driver"', *[_placeholder(p) for p in (x.get("required_params") or ["name"])[1:]]])
    direct = [f"from {package} import {r.name}", "", f"{var} = {r.name}({args})"]
    publish = [
        f"from {package} import {r.name}",
        "",
        "from instro.qcodes import QcodesInstrument",
        "",
        "with QcodesInstrument(",
        f'    name="{var}",',
        f"    instrument={r.name}({args}),",
        '    dataset_rid="<dataset rid>",',
        ") as instrument:",
        "    instrument.start()  # publishes every get and set on instrument.device",
    ]
    return [("python", "\n".join(direct) + "\n"), ("python", "\n".join(publish) + "\n")]


EXAMPLES: dict[str, Callable[[InstrumentRecord], list[tuple[str, str]]]] = {
    "instro": _instro_example,
    "pymeasure": _pymeasure_example,
    "qcodes": _qcodes_example,
}


def _constructs(node: ast.stmt, class_name: str) -> str | None:
    """The variable name when ``node`` is ``var = ClassName(...)``, else None."""
    if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
        return None
    call = node.value
    if not isinstance(call, ast.Call):
        return None
    func = call.func
    named = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
    return node.targets[0].id if named == class_name else None


def with_instro(r: InstrumentRecord, code: str) -> str | None:
    """A pymeasure docstring example rewritten to publish through instro.

    ``attach()`` goes right after the line that builds the instrument and ``recorder.close()`` at the end, so
    everything else is the pymeasure example as written. None when the example does not construct the
    driver at its top level (or is not valid Python), since there is nowhere to attach.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    built = next(((n, v) for n in tree.body if (v := _constructs(n, r.name))), None)
    if built is None:
        return None
    node, var = built
    lines = code.splitlines()
    imports = [n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom)]
    new_imports = ["from instro.lib import NominalCorePublisher", "from instro.pymeasure import attach"]
    if not any(isinstance(n, ast.ImportFrom) and any(a.name == r.name for a in n.names) for n in imports):
        new_imports.insert(0, f"from {r.extra['package_import']} import {r.name}")
    at = max((n.end_lineno or 0 for n in imports), default=0)
    attach = f'recorder = attach({var}, "{var}", [NominalCorePublisher(dataset_rid="<dataset rid>")])'
    out: list[tuple[str, bool]] = []
    for i, line in enumerate(lines, start=1):
        out.append((line, False))
        if i == at:
            out += [(imp, True) for imp in new_imports]
        if i == node.end_lineno:
            out.append((attach, True))
    if at == 0:
        out = [(imp, True) for imp in new_imports] + [("", False)] + out
    out += [("", False), ("recorder.close()  # flush and close the publishers", True)]
    return "\n".join(line for line, _ in out) + "\n"


def with_wrapper(r: InstrumentRecord, code: str) -> str | None:
    """A pymeasure docstring example rewritten as new instro code.

    The line that builds the instrument becomes ``with PymeasureInstrument(...) as var:``, the rest of the
    example moves inside it, and each use of ``var`` becomes ``var.device``, pymeasure's own instrument. None
    when ``with_instro`` would also return None.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    built = next(((n, v) for n in tree.body if (v := _constructs(n, r.name))), None)
    if built is None:
        return None
    node, var = built
    lines = code.splitlines()
    imports = [n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom) and (n.end_lineno or 0) < node.lineno]
    new_imports = ["from instro.pymeasure import PymeasureInstrument"]
    if not any(isinstance(n, ast.ImportFrom) and any(a.name == r.name for a in n.names) for n in tree.body):
        new_imports.insert(0, f"from {r.extra['package_import']} import {r.name}")
    at = max((n.end_lineno or 0 for n in imports), default=0)
    segment = ast.get_source_segment(code, node.value) or ""
    instrument = ast.unparse(node.value) if "\n" in segment else segment
    head = [
        "with PymeasureInstrument(",
        f'    name="{var}",',
        f"    instrument={instrument},",
        '    dataset_rid="<dataset rid>",',
        f") as {var}:",
    ]
    if not any(line.strip() for line in lines[node.end_lineno or 0 :]):
        return None  # nothing to run inside the with block
    uses: dict[int, list[int]] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and n.id == var and n.lineno > (node.end_lineno or 0):
            uses.setdefault(n.lineno, []).append(n.col_offset)  # col_offset counts UTF-8 bytes

    out: list[tuple[str, bool]] = []
    for i, line in enumerate(lines, start=1):
        if i < node.lineno:
            out.append((line, False))
            if i == at:
                out += [(imp, True) for imp in new_imports]
        elif i == node.lineno:
            out += [(h, True) for h in head]
        elif i > (node.end_lineno or 0):
            raw = line.encode()
            for col in sorted(uses.get(i, []), reverse=True):
                raw = raw[: col + len(var)] + b".device" + raw[col + len(var) :]
            text = raw.decode()
            out.append(("    " + text if text.strip() else "", i in uses))
    if at == 0:
        out = [(imp, True) for imp in new_imports] + [("", False)] + out
    return "\n".join(line for line, _ in out) + "\n"


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
        examples = r.extra.get("examples", [])
        attach_form = next((t for code in examples if (t := with_instro(r, code))), None)
        wrapper_form = next((t for code in examples if (t := with_wrapper(r, code))), None)
        adapted = bool(attach_form or wrapper_form)
        wrapper_form = wrapper_form or publish[1]
        if not attach_form:
            var = slug(r.name).replace("-", "_")
            lines = direct[1].splitlines()
            at = next(i for i, ln in enumerate(lines) if ln.startswith(f"{var} = "))
            lines.insert(
                at + 1, f'recorder = attach({var}, "{var}", [NominalCorePublisher(dataset_rid="<dataset rid>")])'
            )
            lines = [
                "from instro.lib import NominalCorePublisher",
                "from instro.pymeasure import attach",
                *lines,
                "recorder.close()  # flush and close the publishers",
            ]
            attach_form = "\n".join(lines) + "\n"

        def block(code: str) -> str:
            return f"```python\n{code}```\n"

        closing = (
            f"The examples are adapted from the [pymeasure documentation for `{r.name}`]({api}), "
            "which lists every property and method.\n"
            if adapted
            else f"For the full list of properties and methods, see the [pymeasure documentation for "
            f"`{r.name}`]({api}).\n"
        )
        return "\n".join(
            [
                "## Publishing through instro\n",
                f"instro publishes the commands you send to a {r.name} and the values you read from it, through "
                "any [publisher](/library/publishers.md). It takes two forms, both described in the "
                "[pymeasure guide](/library/pymeasure.md). Install with `uv add \"instro[pymeasure]\"`.\n",
                "### With a PymeasureInstrument\n",
                "[`PymeasureInstrument`](/library/pymeasure.md#write-new-instro-code) wraps the driver in an "
                "instro instrument, with tags and background polling. The instrument is created inside the "
                "`with` block, and `.device` is the pymeasure instrument.\n",
                block(wrapper_form),
                "### With existing pymeasure code (`attach()`)\n",
                "[`attach()`](/library/pymeasure.md#publish-from-existing-code) adds publishing to pymeasure "
                "code you already have. The imports, the `attach()` call after the instrument is created, "
                "and `recorder.close()` are all that instro adds.\n",
                block(attach_form),
                closing,
            ]
        )
    if r.driver_type == "qcodes":
        direct, publish = _qcodes_example(r)
        var = slug(r.name).replace("-", "_")
        lines = direct[1].splitlines()
        at = next(i for i, ln in enumerate(lines) if ln.startswith(f"{var} = "))
        lines.insert(at + 1, f'recorder = attach({var}, "{var}", [NominalCorePublisher(dataset_rid="<dataset rid>")])')
        attach_form = "\n".join(
            [
                "from instro.lib import NominalCorePublisher",
                "from instro.qcodes import attach",
                *lines,
                "recorder.close()  # flush and close the publishers",
            ]
        )
        return "\n".join(
            [
                "## Publishing through instro\n",
                f"instro publishes the commands you send to a {r.name} and the values you read from it, through "
                "any [publisher](/library/publishers.md). It takes two forms, both described in the "
                "[QCoDeS guide](/library/qcodes.md). Install with `uv add \"instro[qcodes]\"`.\n",
                "### With a QcodesInstrument\n",
                "[`QcodesInstrument`](/library/qcodes.md#write-new-instro-code) wraps the driver in an "
                "instro instrument, with tags and background polling. The instrument is created inside the "
                "`with` block, and `.device` is the QCoDeS instrument.\n",
                _fence(publish[1]),
                "### With existing QCoDeS code (`attach()`)\n",
                "[`attach()`](/library/qcodes.md#publish-from-existing-code) adds publishing to QCoDeS "
                "code you already have. The imports, the `attach()` call after the instrument is created, "
                "and `recorder.close()` are all that instro adds.\n",
                _fence(attach_form + "\n"),
                f"QCoDeS drivers register their parameters when they connect, so this page can't list them. "
                f"See the [QCoDeS documentation](https://microsoft.github.io/Qcodes/) for `{r.name}`, or run "
                f"`instro qcodes describe` against the instrument ([CLI](/cli.md#instro-qcodes)).\n",
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
    badge, library_url = DRIVER_TYPES.get(r.driver_type, (r.driver_type, ""))
    title = r.display_name
    description = (
        f"Control the {title} {noun(r)} from Python with the {badge} {r.name} driver: "
        "a short code example, and links to its reference."
    )
    image = _image(r, srcdir)
    meta = [
        f"**Driver:** [{badge}]({library_url})" if library_url else f"**Driver:** {badge}",
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
