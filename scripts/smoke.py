"""
Smoke test — runs the FlibustaProvider directly without Telegram.
Usage: uv run python -m scripts.smoke
"""

import asyncio
import sys
from pathlib import Path

# allow running from repo root without installing package
sys.path.insert(0, str(Path(__file__).parent.parent))

from providers.flibusta import FlibustaProvider


async def main() -> None:
    provider = FlibustaProvider()
    try:
        # 1. Search
        query = "война и мир"
        print(f"\n=== search: {query!r} ===")
        hits, has_next = await provider.search(query)
        if not hits:
            print("  [!] no results — check parsing")
            return
        for h in hits[:5]:
            print(f"  [{h.kind}] id={h.id}  title={h.title!r}  author={h.author!r}")
        print(f"  has_next={has_next}")

        # 2. Get book details
        book_hit = next((h for h in hits if h.kind == "book"), None)
        if not book_hit:
            print("[!] no book hit found")
            return

        print(f"\n=== get_book id={book_hit.id} ===")
        book = await provider.get_book(book_hit.id)
        print(f"  title:  {book.title!r}")
        print(f"  author: {book.author!r}")
        print(f"  genres: {book.genres}")
        print(f"  annotation: {(book.annotation or '')[:200]!r}{'...' if book.annotation and len(book.annotation) > 200 else ''}")
        print(f"  downloads: {[(d.format, d.url) for d in book.downloads]}")

        if not book.downloads:
            print("[!] no download links found — check parsing")
            return

        # 3. Download first format
        dl = book.downloads[0]
        print(f"\n=== download {dl.format} for book {book.id} ===")
        file = await provider.download(book.id, dl.format)
        out = Path(f"/tmp/smoke_{book.id}.{dl.format}")
        out.write_bytes(file.content)
        print(f"  saved {len(file.content):,} bytes → {out}")
        print(f"  filename: {file.filename!r}")
        print(f"  content-type: {file.content_type!r}")

        print("\n[OK] smoke test passed")
    finally:
        await provider.aclose()


if __name__ == "__main__":
    asyncio.run(main())
