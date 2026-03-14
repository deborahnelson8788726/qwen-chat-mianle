# Codex Relay (Telegram -> Codex)

## Что это
- Telegram команда `/codex` ставит задачу в очередь проекта.
- Локальный воркер `codex_relay_worker.py` забирает задачу, запускает `codex exec`, пишет результат обратно в очередь и в Telegram.

## 1) Подключите проект в Telegram
1. На сайте синхронизируйте проект (получите `ML-XXXXXXXX`).
2. В Telegram: `/connect ML-XXXXXXXX`

## 2) Запустите relay воркер на Mac
```bash
cd /Users/ssdm4/qwen-chat-vercel
python3 telegram-bot/codex_relay_worker.py \
  --token ML-XXXXXXXX \
  --workspace /Users/ssdm4/qwen-chat-vercel \
  --bot-token "<TELEGRAM_BOT_TOKEN>"
```

Опционально:
- `--once` — обработать одну задачу и выйти
- `--dry-run` — не запускать codex, только проверить очередь
- `--extra-args "--model gpt-5 --sandbox workspace-write"` — дополнительные флаги `codex exec`

## 3) Отправляйте задачи из Telegram
- `/codex исправь 500 в /api/fetch и проверь`
- `/codexstatus` — посмотреть очередь и статусы

## Важно
- Воркер должен быть запущен на машине, где доступна команда `codex`.
- Если воркер остановлен, задачи остаются в очереди со статусом `queued`.
- Статус `claimed` старше ~30 минут автоматически возвращается в `queued`.

