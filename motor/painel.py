"""Painel de revisão: aprova ou rejeita, pelo navegador, o que o sync deixou para uma pessoa decidir.

  - mudanças de status sugeridas (fila de revisão), já com a apuração da IA;
  - declarações de campanha candidatas a virar promessa.

Serviço "painel" do compose, atrás do nginx em /revisar/. Senha: REVISAO_SENHA (sem ela, o painel
fica desligado). Cada decisão grava sob a trava do sync e regera o site na hora.

    python -m motor.painel          # escuta em 0.0.0.0:8000
"""
import hashlib
import hmac
import base64
import logging
import os
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

from web import build

from . import armazem, campanha, revisar, saude
from .trava import Ocupado, trava

log = logging.getLogger("painel")

BASE = "/revisar/"
MAX_CORPO = 64 * 1024
CABECALHOS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Robots-Tag": "noindex, nofollow",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'",
}


def senha() -> str:
    return os.environ.get("REVISAO_SENHA", "")


def token_formulario() -> str:
    """Protege os formulários contra envio a partir de outro site (o navegador reenvia o Basic Auth sozinho)."""
    return hmac.new(senha().encode(), b"cumpriu-revisar", hashlib.sha256).hexdigest()


def autorizado(cabecalho: str | None) -> bool:
    if not senha() or not cabecalho or not cabecalho.startswith("Basic "):
        return False
    try:
        _, _, dada = base64.b64decode(cabecalho[6:]).decode().partition(":")
    except Exception:  # noqa: BLE001
        return False
    return hmac.compare_digest(dada.encode(), senha().encode())


def contexto() -> dict:
    estado = armazem.ler_json(saude.caminho(), None)
    intervalo = max(1, int(os.environ.get("INTERVALO_SYNC_DIAS", "1")))
    graves, avisos = saude.problemas(estado, datetime.now(timezone.utc), intervalo)
    return {"fila": sorted(armazem.ler_json(armazem.FILA, []), key=lambda i: i["criado_em"]),
            "candidatas": armazem.ler_json(campanha.CANDIDATAS, []),
            "saude": estado, "graves": graves, "avisos": avisos,
            "status_opcoes": armazem.STATUS, "token": token_formulario(),
            "veiculos": campanha.veiculos_distintos}


def decidir(campos: dict) -> str:
    tipo, item, acao = campos.get("tipo"), campos.get("id", ""), campos.get("acao")
    if acao not in ("aprovar", "rejeitar"):
        return "Ação desconhecida."
    try:
        with trava(esperar=False):
            if tipo == "fila":
                status = campos.get("status") if campos.get("status") in armazem.STATUS else None
                msg = revisar.decidir(item, acao == "aprovar", status, campos.get("nota", "").strip()[:500])
            elif tipo == "candidata":
                if item not in {c["id"] for c in armazem.ler_json(campanha.CANDIDATAS, [])}:
                    return "Essa declaração já foi decidida."
                (campanha.aprovar if acao == "aprovar" else campanha.rejeitar)([item])
                msg = "Declaração publicada como promessa." if acao == "aprovar" else "Declaração rejeitada."
            else:
                return "Tipo desconhecido."
            build.main()
    except Ocupado:
        return "O sync diário está gravando agora. Tente de novo em alguns minutos."
    except KeyError as e:
        return e.args[0]
    log.info("Revisão: %s %s %s → %s", acao, tipo, item, msg)
    return msg + " O site já foi atualizado."


class Painel(BaseHTTPRequestHandler):
    server_version = "painel"
    sys_version = ""

    def log_message(self, fmt, *args):  # sem IP/credenciais no log, só a linha da requisição
        log.info("%s %s", self.command, self.path.split("?")[0])

    def _responder(self, codigo: int, corpo: bytes = b"", tipo="text/html; charset=utf-8", extras=None):
        self.send_response(codigo)
        for k, v in (CABECALHOS | (extras or {})).items():
            self.send_header(k, v)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(corpo)

    def _checar(self) -> bool:
        if not senha():
            self._responder(503, "Painel desligado: defina REVISAO_SENHA.".encode(), "text/plain; charset=utf-8")
            return False
        if autorizado(self.headers.get("Authorization")):
            return True
        time.sleep(1.5)  # freia tentativa de adivinhar a senha
        self._responder(401, b"", extras={"WWW-Authenticate": 'Basic realm="Cumpriu? revisao", charset="UTF-8"'})
        return False

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/revisar":
            return self._responder(301, extras={"Location": BASE})
        if url.path != BASE:
            return self._responder(404, b"nao encontrado", "text/plain")
        if not self._checar():
            return
        msg = parse_qs(url.query).get("msg", [""])[0]
        html = build.ambiente().get_template("revisar.html").render(raiz="../", pagina="revisar/", msg=msg, **contexto())
        self._responder(200, html.encode())

    do_HEAD = do_GET

    def do_POST(self):
        if urlsplit(self.path).path != BASE + "decidir":
            return self._responder(404, b"nao encontrado", "text/plain")
        if not self._checar():
            return
        tamanho = int(self.headers.get("Content-Length") or 0)
        if tamanho > MAX_CORPO:
            return self._responder(413, b"grande demais", "text/plain")
        campos = {k: v[0] for k, v in parse_qs(self.rfile.read(tamanho).decode()).items()}
        if not hmac.compare_digest(campos.get("token", ""), token_formulario()):
            return self._responder(403, b"formulario invalido: recarregue a pagina", "text/plain")
        msg = decidir(campos)
        self._responder(303, extras={"Location": f"{BASE}?msg={quote(msg)}#{campos.get('tipo', '')}"})


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    porta = int(os.environ.get("PAINEL_PORTA", "8000"))
    if not senha():
        log.warning("REVISAO_SENHA não definida: o painel responde 503 até ela ser configurada")
    log.info("Painel de revisão em :%d%s", porta, BASE)
    ThreadingHTTPServer(("0.0.0.0", porta), Painel).serve_forever()


if __name__ == "__main__":
    main()
