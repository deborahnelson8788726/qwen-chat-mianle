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

### Автозапуск через launchd (macOS)
1. Заполните локальный конфиг:
```bash
cp telegram-bot/relay.env.example ~/.codex-relay/relay.env
```
Минимум:
- `CODEX_RELAY_TOKEN=ML-XXXXXXXX`
- `BOT_TOKEN=<ваш Telegram bot token>` (опционально, но нужен для push-уведомлений о статусах)
- `SENTRY_DSN=` (опционально, но рекомендуется для мониторинга ошибок relay)
- `ALERT_BOT_TOKEN` + `ALERT_CHAT_ID` (опционально, если нужны отдельные аварийные уведомления)

2. Сервис запускается скриптом:
```bash
telegram-bot/start_codex_relay.sh
```

3. Полезные команды launchctl:
```bash
launchctl print gui/$(id -u)/com.ssdm4.codex-relay
launchctl kickstart -k gui/$(id -u)/com.ssdm4.codex-relay
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.ssdm4.codex-relay.plist
```

Логи:
- `~/.codex-relay/logs/launchd.out.log`
- `~/.codex-relay/logs/launchd.err.log`
- `~/.codex-relay/logs/worker.log`

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
