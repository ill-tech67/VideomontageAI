"""Запуск: python -m app"""

from __future__ import annotations

import asyncio
import sys

from app.bot import run


def main() -> int:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("Остановлено пользователем.")
    except Exception as exc:  # noqa: BLE001
        print(f"Ошибка запуска: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
