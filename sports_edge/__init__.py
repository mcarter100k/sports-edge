"""sports-edge: read-only research on Kalshi sports markets (AGENTS.md).

A `python -m sports_edge.<module>` command loads .env before the module runs,
as the wrappers do (docs/specs/status_spec.md). Python sets sys.argv[0] to
"-m" only while it imports the package for such a command, so imports from
tests or other programs never load it.
"""
import sys

if sys.argv[:1] == ["-m"]:
    from . import config

    config.load_env_file()
