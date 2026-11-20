"""Import Discord channel exports (DiscordChatExporter JSON) into PickProof, and stage analysts for the demo.

    python -m receipts.import_export exports/*.json               # read, OCR, match to Gemini, grade
    python -m receipts.import_export exports/*.json --stage Bronco  # same, then hide Jesse until someone presses Load
    python -m receipts.import_export --stage Bronco                 # hide an already imported analyst again
    python -m receipts.import_export --list                        # what's in the database

Post times come from the message IDs, exactly as for messages read in the browser. Discord image links in an
export expire after about a day, so import soon after exporting (OCR results are cached in the database, so a
second import of the same file is fast and needs no images).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from . import db, observe, verify
from .gemini import GeminiClient

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")


def messages_from_export(data: dict) -> list[dict]:
    """Same mapping as the side panel's Import tab (extension/src/api.ts messagesFromExport)."""
    ch, guild = data.get("channel") or {}, data.get("guild") or {}
    out = []
    for m in data.get("messages") or []:
        images = [a.get("url", "") for a in m.get("attachments") or []]
        images = [u for u in images if u.startswith("https://") and u.split("?")[0].lower().endswith(IMAGE_EXT)][:4]
        if not m.get("id") or not (m.get("content") or images):
            continue
        a = m.get("author") or {}
        out.append({"message_id": str(m["id"]), "channel_id": ch.get("id"), "guild_id": guild.get("id"),
                    "channel_name": ch.get("name"), "author_id": a.get("id"),
                    "author_name": a.get("nickname") or a.get("name") or "unknown", "avatar_url": a.get("avatarUrl"),
                    "text": m.get("content") or "", "edited": bool(m.get("timestampEdited")), "images": images})
    return out


def import_file(path: Path, gemini: GeminiClient, pool: observe.GamePool, conn) -> Counter:
    data = json.loads(path.read_text(encoding="utf-8"))
    msgs = messages_from_export(data)
    counts: Counter = Counter()
    for i in range(0, len(msgs), 25):
        for r in observe.ingest(msgs[i:i + 25], gemini, pool, conn=conn):
            counts[r["outcome"]] += 1
        print(f"  {path.name}: {min(i + 25, len(msgs))}/{len(msgs)} messages  {dict(counts)}", flush=True)
    return counts


def stage(name: str, conn, hidden: bool = True) -> list[str]:
    rows = conn.execute("SELECT id, display_name FROM cappers WHERE lower(display_name)=lower(?) OR lower(handle)=lower(?)",
                        (name, name)).fetchall()
    with db.tx(conn):
        for r in rows:
            conn.execute("UPDATE cappers SET demo_staged=? WHERE id=?", (int(hidden), r["id"]))
    return [r["display_name"] for r in rows]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path, help="DiscordChatExporter-style JSON files")
    ap.add_argument("--stage", action="append", default=[], help="hide this analyst until Load is pressed (repeatable)")
    ap.add_argument("--unstage", action="append", default=[], help="show this analyst again")
    ap.add_argument("--list", action="store_true", help="list analysts and their records")
    args = ap.parse_args(argv)
    conn = db.connect()

    if args.files:
        gemini = GeminiClient()
        pool = observe.GamePool(gemini)
        total: Counter = Counter()
        for f in args.files:
            print(f"Importing {f}")
            total += import_file(f, gemini, pool, conn)
        print("Done:", dict(total))
    for n in args.stage:
        print("Staged (hidden until Load):", stage(n, conn) or f"no analyst named {n}")
    for n in args.unstage:
        print("Unstaged:", stage(n, conn, hidden=False) or f"no analyst named {n}")
    if args.list or not (args.files or args.stage or args.unstage):
        for c in conn.execute("SELECT * FROM cappers ORDER BY display_name"):
            r = verify.compute(c["id"], conn)
            print(f"  {c['display_name']:<20} {r.wins}-{r.losses}  graded={r.graded}  "
                  f"{'STAGED ' if c['demo_staged'] else ''}{'VERIFIED' if c['verified_at'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
