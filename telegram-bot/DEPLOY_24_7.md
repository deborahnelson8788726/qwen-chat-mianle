# MILEAN Telegram Bot: 24/7 Deploy

Причина, почему бот молчит после выключения ноутбука: `bot.py` работает в режиме polling и должен постоянно крутиться на сервере.

## Что уже подготовлено

- Docker-образ: `telegram-bot/Dockerfile`
- Render Blueprint: `render.yaml`
- Railway config: `railway.json`
- Пример ENV: `telegram-bot/.env.example`

## Обязательные переменные окружения

- `BOT_TOKENS` (или `BOT_TOKEN`)  
  Формат для нескольких ботов: `token1,token2,token3`
- `NVIDIA_API_KEY`
- `PPLX_API_KEY` (опционально; без него будет fallback на DuckDuckGo)
- `CODEX_DEFAULT_TOKEN` (опционально; токен проекта для `/codex` и `/codexstatus` без ручного `/connect`)
- `REDIS_URL` (рекомендуется; персистентное состояние пользователей и очередей)
- `SENTRY_DSN` (рекомендуется; мониторинг ошибок)
- `ALERT_BOT_TOKEN` + `ALERT_CHAT_ID` (опционально; уведомления о падениях/ошибках в Telegram)

## Вариант 1: Render (рекомендуется)

1. Откройте Render и создайте сервис из GitHub-репозитория.
2. Render автоматически подхватит `render.yaml` и создаст `worker`.
3. В `Environment` задайте переменные из блока выше.
4. Нажмите Deploy.
5. После старта проверьте логи: должно быть `MILEAN Bot started! Active bots: ...`.
6. Проверка API: `https://milean.vercel.app/api/stats`

## Вариант 2: Railway

1. Создайте проект из GitHub-репозитория.
2. Railway использует `railway.json` + `telegram-bot/Dockerfile`.
3. Добавьте переменные окружения.
4. Дождитесь деплоя и проверьте логи.

## Вариант 3: VPS с Docker

```bash
docker build -f telegram-bot/Dockerfile -t milean-bot .
docker run -d --name milean-bot \
  --restart unless-stopped \
  -e BOT_TOKENS="token1,token2" \
  -e NVIDIA_API_KEY="nvapi-..." \
  -e PPLX_API_KEY="pplx-..." \
  milean-bot
```

Проверка:

```bash
docker logs -f milean-bot
```
