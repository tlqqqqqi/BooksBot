"""
Smoke test — гоняет LibgenProvider напрямую, без Telegram.
Usage: uv run python -m scripts.smoke_libgen [запрос]

Зеркала libgen отвечают неравномерно: если всё пусто, сначала попробуйте другое
зеркало в LIBGEN_BASE_URL (bz / vg / li / la / gl), а уже потом чините селекторы.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
from providers.libgen import LibgenProvider


async def main() -> None:
    query = " ".join(sys.argv[1:]) or "the ascent of money"
    provider = LibgenProvider()
    print(f"base_url = {settings.libgen_base_url}")
    try:
        print(f"\n=== search: {query!r} ===")
        hits, has_next = await provider.search(query)
        if not hits:
            print("  [!] пусто — проверьте зеркало, потом селекторы")
            return
        for h in hits[:5]:
            print(f"  id={h.id}  title={h.title!r}  author={h.author!r}")
        print(f"  has_next={has_next}")

        book_id = hits[0].id
        print(f"\n=== get_book: {book_id} ===")
        book = await provider.get_book(book_id)
        print(f"  title:      {book.title!r}")
        print(f"  author:     {book.author!r}")
        print(f"  выходные:   {book.annotation!r}")
        print(f"  форматы:    {[d.format for d in book.downloads]}")
        print(f"  ссылка:     {provider.download_url(book_id, '')}")

        fmt = book.downloads[0].format if book.downloads else "download"
        print(f"\n=== download: {book_id} / {fmt} ===")
        file = await provider.download(book_id, fmt)
        print(f"  filename:   {file.filename!r}")
        print(f"  size:       {len(file.content)} bytes")
        print(f"  magic:      {file.content[:4]!r}")
    finally:
        await provider.aclose()


if __name__ == "__main__":
    asyncio.run(main())
