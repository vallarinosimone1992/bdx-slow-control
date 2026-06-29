"""Command-line entry points."""

from __future__ import annotations

import argparse
from pathlib import Path

from caproto.server import run

from .builders import BUILDERS
from .config import ConfigurationError, load_json
from .logging_setup import configure_logging
from .prototype import build_prototype


def _parser(name: str, default_config: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=name)
    parser.add_argument(
        "--config",
        default=default_config,
        help=f"JSON configuration file (default: {default_config})",
    )
    return parser


def _run(builder_name: str, default_config: str, argv=None) -> None:
    configure_logging()
    parser = _parser(f"bdx-{builder_name}-ioc", default_config)
    args = parser.parse_args(argv)
    try:
        pvdb, settings = BUILDERS[builder_name](load_json(args.config))
    except (ConfigurationError, NotImplementedError, ValueError) as exc:
        parser.error(str(exc))
    run(
        pvdb,
        interfaces=list(settings.interfaces),
        log_pv_names=settings.log_pv_names,
    )



def psu_main(argv=None) -> None:
    _run("psu", "config/psu.json", argv)


def chiller_main(argv=None) -> None:
    _run("chiller", "config/chiller.json", argv)


def environment_main(argv=None) -> None:
    _run("environment", "config/environment.json", argv)


def hv_main(argv=None) -> None:
    _run("hv", "config/hv.json", argv)


def daq_main(argv=None) -> None:
    _run("daq", "config/daq.json", argv)


def global_main(argv=None) -> None:
    _run("global", "config/global.json", argv)


def prototype_main(argv=None) -> None:
    """Run every configured subsystem in one Channel Access server process."""
    configure_logging()
    parser = argparse.ArgumentParser(prog="bdx-prototype-ioc")
    parser.add_argument("--config-dir", default="config")
    args = parser.parse_args(argv)

    try:
        pvdb, settings = build_prototype(Path(args.config_dir))
    except (ConfigurationError, NotImplementedError, ValueError) as exc:
        parser.error(str(exc))

    run(
        pvdb,
        interfaces=list(settings.interfaces),
        log_pv_names=settings.log_pv_names,
    )


def pv_list_main(argv=None) -> None:
    configure_logging()
    parser = argparse.ArgumentParser(prog="bdx-pv-list")
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--output", help="Optional output file")
    args = parser.parse_args(argv)

    try:
        pvdb, _ = build_prototype(Path(args.config_dir))
    except (ConfigurationError, NotImplementedError, ValueError) as exc:
        parser.error(str(exc))

    text = "\n".join(sorted(pvdb)) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
