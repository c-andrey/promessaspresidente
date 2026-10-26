"""Avisos no celular: Telegram e/ou ntfy.sh. Sem nenhum configurado, só vai para o log.

  Telegram: TELEGRAM_BOT_TOKEN (do @BotFather) + TELEGRAM_CHAT_ID
  ntfy:     NTFY_TOPICO (nome difícil de adivinhar; assine o mesmo tópico no app ntfy)

    python -m motor.aviso teste      # manda uma mensagem de teste
    python -m motor.aviso chat-id    # mostra o chat id de quem mandou mensagem ao bot
"""
import logging
import os
import sys

from .coletores.http import cliente

log = logging.getLogger("aviso")


def configurado() -> bool:
    return bool((os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))
                or os.environ.get("NTFY_TOPICO"))


def avisar(titulo: str, texto: str, link: str | None = None) -> bool:
    """Manda para todos os canais configurados. Falha de envio nunca derruba o sync."""
    log.info("AVISO %s: %s", titulo, texto)
    enviado = False
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    topico = os.environ.get("NTFY_TOPICO")
    with cliente() as c:
        if token and chat:
            try:
                c.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       json={"chat_id": chat, "text": f"{titulo}\n\n{texto}" + (f"\n\n{link}" if link else ""),
                             "disable_web_page_preview": True}).raise_for_status()
                enviado = True
            except Exception as e:  # noqa: BLE001
                log.warning("Telegram falhou: %s", type(e).__name__)  # a URL tem o token: não logar
        if topico:
            try:
                c.post("https://ntfy.sh/", json={"topic": topico, "title": titulo, "message": texto,
                                                 **({"click": link} if link else {})}).raise_for_status()
                enviado = True
            except Exception as e:  # noqa: BLE001
                log.warning("ntfy falhou: %s", e)
    return enviado


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # a URL do Telegram leva o token
    acao = sys.argv[1] if len(sys.argv) > 1 else "teste"
    if acao == "chat-id":
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            sys.exit("TELEGRAM_BOT_TOKEN não definido")
        with cliente() as c:
            r = c.get(f"https://api.telegram.org/bot{token}/getUpdates").json()
        chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name", "")
                 for u in r.get("result", []) if "message" in u}
        print(chats or "Nenhuma mensagem: mande um 'oi' para o bot e rode de novo.")
    else:
        if not configurado():
            sys.exit("Nenhum canal configurado (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID ou NTFY_TOPICO).")
        print("enviado" if avisar("Cumpriu? · teste", "Os avisos do sync vão chegar aqui.") else "falhou")


if __name__ == "__main__":
    main()
