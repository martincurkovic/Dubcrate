"""Read-only: print the loop rows of rekordbox's `djmdCue` (every column), to see how rekordbox stores a memory loop.

    uv run python spikes/rekordbox_loop_rows.py [--title TEXT] [--db PATH]

Reads a SNAPSHOT copy of `master.db` (never the live file, never writes). Make a few memory loops in rekordbox first (close
rekordbox afterwards so its database is flushed), then run this with `--title` set to the track: see
`dev_docs/rekordbox-cue-writes.md`, "Memory loops".
"""

import argparse
import tempfile
from pathlib import Path

from pyrekordbox.masterdb import MasterDatabase

from dubcrate.library import rekordbox as rb


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--title", help="only tracks whose title contains this")
    parser.add_argument("--db", type=Path, help="a master.db (default: rekordbox's)")
    args = parser.parse_args()
    source = args.db or rb.default_master_db()
    if source is None or not source.is_file():
        raise SystemExit("no rekordbox master.db found")
    with tempfile.TemporaryDirectory(prefix="dubcrate-loops-") as tmp:
        db = MasterDatabase(path=rb._snapshot(source, Path(tmp)))
        try:
            titles = {c.ID: c.Title for c in db.get_content()}
            rows = [r for r in db.get_cue() if (r.OutMsec or -1) > (r.InMsec or 0)]
            if args.title:
                rows = [r for r in rows if args.title.lower() in str(titles.get(r.ContentID, "")).lower()]  # fmt: skip
            print(f"{len(rows)} loop row(s)")
            for row in rows:
                print(f"\n{titles.get(row.ContentID)!r}")
                for column in row.__table__.columns:
                    print(f"  {column.name:22} {getattr(row, column.name)!r}")
        finally:
            db.close()


if __name__ == "__main__":
    main()
