# FlibustaBot

Telegram-бот для поиска и скачивания книг.

## Требования

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — `curl -LsSf https://astral.sh/uv/install.sh | sh`

## Локальный запуск

```bash
git clone <repo>
cd FlibustaBot
uv sync
cp .env.example .env   # вписать BOT_TOKEN
uv run python -m main
```

## Деплой на VPS (systemd)

```bash
# 1. Клонируем и устанавливаем зависимости
git clone <repo> ~/FlibustaBot
cd ~/FlibustaBot
uv sync
cp .env.example .env
nano .env              # вписать BOT_TOKEN

# 2. Устанавливаем systemd-юнит
sudo cp flibusta-bot.service /etc/systemd/system/
# При необходимости поправить User= и пути в файле юнита
sudo systemctl daemon-reload
sudo systemctl enable flibusta-bot
sudo systemctl start flibusta-bot

# Логи
sudo journalctl -u flibusta-bot -f
```

## Обновление на сервере

```bash
cd ~/FlibustaBot
git pull
uv sync
sudo systemctl restart flibusta-bot
```

## Smoke-тест (без Telegram)

```bash
BOT_TOKEN=smoke uv run python -m scripts.smoke
```

Скачает тестовую книгу в `/tmp/` и выведет результаты парсинга.
