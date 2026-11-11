"""Entry point for built apps: pyside6-deploy compiles this file (scripts/build_macos.sh).

From a checkout, `uv run dubcrate-gui` or `python -m dubcrate.ui` do the same thing.
"""

import sys
import traceback


def _smoke_report_path(argv):
    for arg in argv[1:]:
        if arg.startswith("--smoke-test="):
            return arg.partition("=")[2] or None
    return None


if __name__ == "__main__":
    try:
        from dubcrate.ui.app import main

        code = main()
    except Exception:
        # A failure before the smoke test's own report (a module that didn't get bundled, a Qt
        # plugin that won't load) would otherwise leave CI with an exit code and nothing else.
        report = _smoke_report_path(sys.argv)
        if report:
            with open(report, "w", encoding="utf-8") as f:
                f.write(
                    "SMOKE TEST CRASHED BEFORE ITS REPORT\n" + traceback.format_exc()
                )
        raise
    raise SystemExit(code)
