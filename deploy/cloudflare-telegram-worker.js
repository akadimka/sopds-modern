// Ретранслятор Telegram Bot API на Cloudflare Workers — для сервера, с
// которого api.telegram.org недоступен (заблокирован в России).
// Инструкция — DEPLOY.md, раздел 19a, «Telegram с сервера в России».
//
// Принимает запросы бота https://<worker>/bot<токен>/<метод> и пересылает их
// в https://api.telegram.org с тем же путём, телом и заголовками. Бот
// работает через long polling (getUpdates), входящие подключения не нужны.
//
// Переменная ALLOWED_BOT_ID (Settings → Variables): номер бота — часть
// токена до двоеточия. Тогда ретранслятор пропускает только этого бота и не
// годится как бесплатный прокси для чужих. Сам токен в Worker не хранится.

const TELEGRAM = "https://api.telegram.org";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const allowed = (env.ALLOWED_BOT_ID || "").trim();
    const prefix = allowed ? `/bot${allowed}:` : "/bot";
    if (!url.pathname.startsWith(prefix)) {
      return new Response("Not found", { status: 404 });
    }
    return fetch(new Request(TELEGRAM + url.pathname + url.search, request));
  },
};
