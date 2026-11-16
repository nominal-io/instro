"""Driver sources: each finds one library's instrument drivers and yields normalized records.

A source is any object with a ``name`` (the ``driver_type`` it emits) and ``discover()``. Add a library
(QCoDeS, ...) by writing a class here and appending it to ``SOURCES``; pages, search, and browsing need no change.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import logging
import pkgutil
import re
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

from .records import InstrumentRecord

logger = logging.getLogger(__name__)


class DriverSource(Protocol):
    name: str

    def discover(self) -> Iterator[InstrumentRecord]: ...


def _first_line(doc: str | None) -> str:
    """The docstring's first sentence on one line, without its period, cut to 160 characters at a word."""
    paragraph: list[str] = []
    for line in (doc or "").strip().splitlines():
        if not re.search(r"\w", line):
            if paragraph:
                break
            continue
        paragraph.append(line.strip())
    text = " ".join(paragraph)
    text = re.split(r"(?<=[a-z0-9\)])\.\s+(?=[A-Z])", text, maxsplit=1)[0].rstrip(".")
    if len(text) > 160:
        text = text[:159].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


_DIRECTIVE = re.compile(r"^(\s*)\.\.\s+(?:code-block|code|sourcecode)::\s*(\w*)\s*$")
MAX_EXAMPLE_LINES = 40
MAX_EXAMPLES = 2


def docstring_examples(doc: str | None) -> list[str]:
    """Python code blocks in a docstring: ``.. code-block:: python`` directives and ``::`` literal blocks.

    Blocks over ``MAX_EXAMPLE_LINES`` lines are skipped (cutting one would leave broken code), and at most
    ``MAX_EXAMPLES`` are kept. Blocks in other languages are ignored.
    """
    lines = (doc or "").expandtabs().splitlines()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        directive = _DIRECTIVE.match(lines[i])
        literal = not directive and lines[i].rstrip().endswith("::") and not lines[i].lstrip().startswith("..")
        if not directive and not literal:
            i += 1
            continue
        base = len(directive.group(1)) if directive else len(lines[i]) - len(lines[i].lstrip())
        i += 1
        if directive and directive.group(2) not in ("", "python", "py", "python3"):
            continue
        block: list[str] = []
        while i < len(lines):
            line = lines[i]
            indent = len(line) - len(line.lstrip())
            if line.strip() and indent <= base:
                break
            if line.strip().startswith(":") and not block:  # directive options, e.g. :linenos:
                i += 1
                continue
            block.append(line)
            i += 1
        code = textwrap.dedent("\n".join(block)).strip("\n")
        if code.strip() and len(code.splitlines()) <= MAX_EXAMPLE_LINES:
            blocks.append(code)
    return blocks[:MAX_EXAMPLES]


def _required_params(params: list[tuple[str, bool]]) -> list[str]:
    return [name for name, required in params if required]


# ---------------------------------------------------------------------------- instro

# Names in an IDN vendor string, or a class-name prefix, to display names.
_INSTRO_VENDORS: dict[str, str] = {
    "KEYSIGHT TECHNOLOGIES": "Keysight",
    "AGILENT TECHNOLOGIES": "Agilent",
    "SIGLENT TECHNOLOGIES": "Siglent",
    "TEKTRONIX": "Tektronix",
    "RIGOL TECHNOLOGIES": "Rigol",
    "B&K PRECISION": "B&K Precision",
    "GW INSTEK": "GW Instek",
    "GWINSTEK": "GW Instek",
    "TDK-LAMBDA": "TDK-Lambda",
    "KEITHLEY INSTRUMENTS": "Keithley",
    "ELEKTRO-AUTOMATIK": "Elektro-Automatik",
    "FLUKE": "Fluke",
}
# Longest first, so "GWInstek" wins over "GW"
_CLASS_PREFIXES: list[tuple[str, str]] = sorted(
    [
        ("Keysight", "Keysight"),
        ("Agilent", "Agilent"),
        ("Siglent", "Siglent"),
        ("Tektronix", "Tektronix"),
        ("Rigol", "Rigol"),
        ("BK", "B&K Precision"),
        ("GWInstek", "GW Instek"),
        ("TDKLambda", "TDK-Lambda"),
        ("Keithley", "Keithley"),
        ("EA", "Elektro-Automatik"),
        ("Fluke", "Fluke"),
        ("Alicat", "Alicat"),
        ("Dewesoft", "Dewesoft"),
        ("Matrix", "Matrix"),
        ("Especs", "ESPEC"),
        ("NanoVNA", "NanoVNA"),
        ("VESC", "VESC"),
    ],
    key=lambda p: -len(p[0]),
)
# categories whose instrument class lives outside ``instro``: category -> (module, class name)
_INSTRUMENT_CLASSES: dict[str, tuple[str, str]] = {
    "psu": ("instro", "InstroPSU"),
    "dmm": ("instro", "InstroDMM"),
    "eload": ("instro", "InstroELoad"),
    "scope": ("instro", "InstroScope"),
    "awg": ("instro", "InstroAWG"),
    "daq": ("instro", "InstroDAQ"),
    "i2c": ("instro", "I2CInterface"),
    "flowcontroller": ("instro.unstable.flowcontroller", "InstroFlowController"),
}
_TIERS = (("instro", "stable"), ("instro.unstable", "unstable"), ("instro.contrib", "contrib"))


def _split_model(class_name: str, manufacturer_hint: str | None) -> tuple[str, str]:
    """Split ``Keysight1200X`` into ``("Keysight", "1200X")`` by class-name prefix."""
    for prefix, display in _CLASS_PREFIXES:
        if class_name.startswith(prefix) and len(class_name) > len(prefix):
            return display, class_name[len(prefix) :]
    return manufacturer_hint or "", class_name


def _ast_classes(path: Path) -> Iterator[tuple[str, str | None, list[tuple[str, bool]]]]:
    """``(class name, docstring, __init__ params)`` for driver classes in a module that can't be imported.

    Used for drivers whose vendor SDK isn't installed: a class counts when a base is named ``*DriverBase``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
            continue
        bases = [b.id if isinstance(b, ast.Name) else getattr(b, "attr", "") for b in node.bases]
        if not any(b.endswith("DriverBase") for b in bases) or node.name.endswith("DriverBase"):
            continue
        params: list[tuple[str, bool]] = []
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                args = item.args
                positional = args.posonlyargs + args.args
                defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
                params = [(a.arg, d is None) for a, d in zip(positional, defaults, strict=True) if a.arg != "self"]
                params += [(a.arg, d is None) for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True)]
        yield node.name, ast.get_docstring(node), params


def _import_from(module: str, name: str, category: str) -> str:
    """The shallowest module users import the driver from: ``instro.drivers``, else its package's flat module."""
    root = ".".join(module.split(".")[: module.split(".").index(category)])
    for candidate in (f"{root}.drivers", f"{root}.{category}.drivers"):
        try:
            if hasattr(importlib.import_module(candidate), name):
                return candidate
        except ImportError:
            continue
    return "instro.drivers" if root == "instro" else f"{root}.{category}.drivers"


class InstroSource:
    """Every driver in ``instro``, ``instro.unstable``, and ``instro.contrib``, found by walking their modules.

    A category is any subpackage with a ``drivers`` package; a driver is a concrete subclass of a ``*DriverBase``
    defined in one of its modules. Modules whose vendor SDK is missing are read from source instead.
    """

    name = "instro"

    def discover(self) -> Iterator[InstrumentRecord]:
        idn = self._idn_patterns()
        for root, tier in _TIERS:
            try:
                package = importlib.import_module(root)
            except ImportError:
                logger.info("instrument_db: %s is not installed, skipping", root)
                continue
            for sub in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
                if sub.ispkg:
                    yield from self._category(root, sub.name, tier, idn)

    @staticmethod
    def _idn_patterns() -> dict[str, Any]:
        """Registry entries by driver class path: the ``*IDN?`` patterns carry vendor and channel counts."""
        try:
            from instro.lib.registry import iter_driver_entries
        except ImportError:
            return {}
        return {entry.path: entry.idn_patterns for _, _, entry in iter_driver_entries()}

    def _category(self, root: str, category: str, tier: str, idn: dict[str, Any]) -> Iterator[InstrumentRecord]:
        drivers_name = f"{root}.{category}.drivers"
        try:
            drivers = importlib.import_module(drivers_name)
        except ModuleNotFoundError as exc:
            if exc.name and drivers_name.startswith(exc.name):
                return
            raise
        for mod_info in sorted(pkgutil.iter_modules(drivers.__path__), key=lambda m: m.name):
            mod_name = f"{drivers_name}.{mod_info.name}"
            try:
                mod = importlib.import_module(mod_name)
            except Exception as exc:  # a vendor SDK missing or Windows-only, which is expected
                logger.info("instrument_db: reading %s from source (%s)", mod_name, type(exc).__name__)
                yield from self._from_source(mod_name, category, tier)
                continue
            for name, cls in sorted(vars(mod).items()):
                if (
                    inspect.isclass(cls)
                    and cls.__module__ == mod_name
                    and not name.startswith("_")
                    and not name.endswith("DriverBase")
                    and any(b.__name__.endswith("DriverBase") for b in cls.__mro__[1:])
                ):
                    params = [
                        (p.name, p.default is p.empty)
                        for p in list(inspect.signature(cls.__init__).parameters.values())[1:]
                        if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
                    ]
                    yield self._record(
                        name, mod_name, category, tier, inspect.getdoc(cls), params, idn.get(f"{mod_name}.{name}", ())
                    )

    def _from_source(self, mod_name: str, category: str, tier: str) -> Iterator[InstrumentRecord]:
        spec = importlib.util.find_spec(mod_name)
        if spec is None or not spec.origin or not spec.origin.endswith(".py"):
            return
        for name, doc, params in _ast_classes(Path(spec.origin)):
            yield self._record(name, mod_name, category, tier, doc, params, ())

    @staticmethod
    def _record(
        name: str,
        module: str,
        category: str,
        tier: str,
        doc: str | None,
        params: list[tuple[str, bool]],
        patterns: tuple[Any, ...],
    ) -> InstrumentRecord:
        simulated = name.startswith(("Virtual", "Simulated"))
        vendor_hint = next((_INSTRO_VENDORS.get(v.upper()) for p in patterns for v in p.vendors), None)
        manufacturer, model = _split_model(name, vendor_hint)
        if simulated:
            manufacturer, model = "instro", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
        channels = next((p.num_channels for p in patterns if p.num_channels), None)
        import_from = _import_from(module, name, category)
        instrument = _INSTRUMENT_CLASSES.get(category)
        return InstrumentRecord(
            driver_type="instro",
            category=category,
            manufacturer=manufacturer or vendor_hint or "Other",
            model=model,
            name=name,
            import_path=f"{module}.{name}",
            summary=_first_line(doc),
            tier=tier,
            simulated=simulated,
            extra={
                "required_params": _required_params(params),
                "param_names": [p for p, _ in params],
                "num_channels": channels,
                "import_from": import_from,
                "instrument_module": instrument[0] if instrument else None,
                "instrument_class": instrument[1] if instrument else None,
            },
        )




# ------------------------------------------------------------------------- pymeasure

_PYMEASURE_VENDORS: dict[str, str] = {
    "activetechnologies": "Active Technologies",
    "aimtti": "Aim-TTi",
    "aja": "AJA International",
    "ami": "American Magnetics",
    "anapico": "AnaPico",
    "andeenhagerling": "Andeen-Hagerling",
    "attocube": "attocube",
    "bkprecision": "B&K Precision",
    "danfysik": "Danfysik",
    "deltaelektronika": "Delta Elektronika",
    "formfactor": "FormFactor",
    "fwbell": "F.W. Bell",
    "hcp": "HCP",
    "hp": "HP",
    "ilxlightwave": "ILX Lightwave",
    "inficon": "INFICON",
    "ipgphotonics": "IPG Photonics",
    "keysight": "Keysight",
    "kuhneelectronic": "Kuhne Electronic",
    "lakeshore": "Lake Shore Cryotronics",
    "lecroy": "LeCroy",
    "mksinst": "MKS Instruments",
    "ni": "National Instruments",
    "oxfordinstruments": "Oxford Instruments",
    "proterial": "Proterial",
    "ptw": "PTW",
    "redpitaya": "Red Pitaya",
    "rohdeschwarz": "Rohde & Schwarz",
    "signalrecovery": "Signal Recovery",
    "siglenttechnologies": "Siglent",
    "spellmanhv": "Spellman",
    "srs": "Stanford Research Systems",
    "tcpowerconversion": "TC Power Conversion",
    "tdk": "TDK",
    "texio": "Texio",
    "ibm": "IBM",
}

# First match wins; matched against "ClassName docstring" lowercased.
_PYMEASURE_CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("scope", ("oscilloscope", "scope", "digitizer")),
    ("vna", ("network analyzer", "vna")),
    ("spectrum", ("spectrum analyzer", "spectrum")),
    ("lockin", ("lock-in", "lockin")),
    ("awg", ("function generator", "waveform generator", "arbitrary", "awg", "pulse generator", "pulse/pattern")),
    ("dmm", ("multimeter", "sourcemeter", "source meter", "source-meter", "electrometer", "picoammeter", "smu", "ohmmeter", "lcr", "voltmeter", "parameter analyzer", "impedance", "capacitance", "universal counter", "gaussmeter")),
    ("eload", ("electronic load", "dc load")),
    ("psu", ("power suppl", "power source", "powersupply", "dc source", "magnet power", "current source", "voltage source", "high voltage", "regulated")),
    ("temperature", ("oven", "temperature", "thermal", "cryo", "chamber", "thermostream", "heater")),
    ("pressure", ("pressure", "vacuum", "gauge", "pirani", "mass flow", "flow")),
    ("laser", ("laser", "optical", "photon", "light", "opo", "amplifier", "spectrometer")),
    ("motion", ("motor", "stage", "positioner", "piezo", "actuator", "translation", "motion", "rotation", "encoder")),
    ("rf", ("rf ", "signal generator", "synthesizer", "microwave", "power meter", "attenuator", "frequency counter")),
    ("daq", ("data acquisition", "daq", "switch", "multiplexer", "matrix", "relay")),
    ("dmm", ("meter",)),
]
# bases and helpers that carry no instrument of their own
_PYMEASURE_SKIP = {"HPLegacyInstrument", "MKSInstrument"}


def pymeasure_category(class_name: str, doc: str, overrides: dict[str, str] | None = None) -> str:
    """The category key for a pymeasure class: an explicit override, else the first keyword rule that matches."""
    if overrides and class_name in overrides:
        return overrides[class_name]
    text = f"{class_name} {doc}".lower()
    for category, words in _PYMEASURE_CATEGORIES:
        if any(w in text for w in words):
            return category
    return "other"


def _pymeasure_manufacturer(package: str) -> str:
    return _PYMEASURE_VENDORS.get(package) or package.title()


def _pymeasure_model(class_name: str, package: str, manufacturer: str) -> str:
    """The class name without a leading manufacturer: ``LeCroyT3DSO1204`` becomes ``T3DSO1204``."""
    for prefix in (package, manufacturer.replace(" ", "").replace("-", "")):
        if class_name.lower().startswith(prefix.lower()) and len(class_name) > len(prefix):
            return class_name[len(prefix) :].lstrip("_")
    return class_name


class PymeasureSource:
    """Every ``pymeasure.instruments.Instrument`` subclass in the installed pymeasure.

    Category has no field in pymeasure, so it comes from keyword rules over the class name and docstring;
    ``category_overrides`` (class name to category) corrects the rest.
    """

    name = "pymeasure"

    def __init__(self, category_overrides: dict[str, str] | None = None) -> None:
        self.category_overrides = category_overrides or {}

    def discover(self) -> Iterator[InstrumentRecord]:
        try:
            import pymeasure.instruments as root
            from pymeasure.instruments import Instrument
        except ImportError:
            logger.warning("instrument_db: pymeasure is not installed, skipping its instruments")
            return
        try:
            from instro.pymeasure import describe
        except ImportError:
            describe = None
        version = getattr(importlib.import_module("pymeasure"), "__version__", "")
        for vendor in sorted(pkgutil.iter_modules(root.__path__), key=lambda m: m.name):
            if not vendor.ispkg:
                continue
            try:
                package = importlib.import_module(f"pymeasure.instruments.{vendor.name}")
            except Exception as exc:
                logger.warning("instrument_db: skipping pymeasure.%s (%s)", vendor.name, type(exc).__name__)
                continue
            for name, cls in sorted(vars(package).items()):
                if not (
                    inspect.isclass(cls)
                    and issubclass(cls, Instrument)
                    and cls.__module__.startswith(package.__name__)
                    and not name.startswith("_")
                    and name not in _PYMEASURE_SKIP
                ):
                    continue
                doc = _first_line(inspect.getdoc(cls))
                manufacturer = _pymeasure_manufacturer(vendor.name)
                params = [
                    (p.name, p.default is p.empty)
                    for p in list(inspect.signature(cls.__init__).parameters.values())[1:]
                    if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
                ]
                read_example = None
                if describe is not None:
                    try:
                        specs = describe(cls)
                        read_example = next((s.path for s in specs if s.readable and s.kind == "measurement"), None)
                    except Exception:  # an odd driver must not stop the listing
                        read_example = None
                yield InstrumentRecord(
                    driver_type="pymeasure",
                    category=pymeasure_category(name, doc, self.category_overrides),
                    manufacturer=manufacturer,
                    model=_pymeasure_model(name, vendor.name, manufacturer),
                    name=name,
                    import_path=f"{cls.__module__}.{name}",
                    summary=doc,
                    extra={
                        "id": f"{vendor.name}-{name}",
                        "vendor_package": vendor.name,
                        "package_import": package.__name__,
                        "required_params": _required_params(params),
                        "read_example": read_example,
                        "examples": docstring_examples(inspect.getdoc(cls)),
                        "library_version": version,
                    },
                )


SOURCES: dict[str, type] = {"instro": InstroSource, "pymeasure": PymeasureSource}


def make_source(name: str, **options: Any) -> DriverSource:
    cls = SOURCES[name]
    return cls(**options) if cls is PymeasureSource else cls()
