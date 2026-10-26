"""Apuração: confere se uma afirmação está mesmo na reportagem, com recursos gratuitos.

1. Resolve o link do Google Notícias para a URL real da matéria e baixa o texto completo
   (não só manchete e resumo).
2. Uma IA verificadora, de preferência de OUTRO provedor (segunda opinião), lê o texto e
   devolve o veredito e o trecho LITERAL que o sustenta.
3. Código, não IA, confere se esse trecho existe palavra por palavra no texto baixado:
   citação inventada é descartada.

Resultado (vai junto com a declaração, referência ou sugestão de status):
    {"status": "apurada" | "nao_confirmada" | "contradita" | "sem_texto",
     "confirmacoes": n, "lidas": k, "trechos": [{url, veiculo, trecho}], "provedores": [...], "em": data}
"""
import html
import json
import logging
import re
import unicodedata

from . import armazem
from .llm.erros import RespostaInvalida

log = logging.getLogger(__name__)

GNEWS_DECODE = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
MAX_CARACTERES = 6000      # texto enviado à IA (cabe no limite de tokens/min do Groq)
MIN_TRECHO = 20            # trecho curto demais não prova nada


def resolver_url(c, url: str) -> str:
    """news.google.com/rss/articles/… → URL real da matéria (mesmo método de decodificadores abertos)."""
    if "news.google.com/rss/articles/" not in url:
        return url
    try:
        gid = url.split("/articles/")[1].split("?")[0]
        pagina = c.get(url).text
        sg = re.search(r'data-n-a-sg="([^"]+)"', pagina).group(1)
        ts = re.search(r'data-n-a-ts="([^"]+)"', pagina).group(1)
        pedido = [[["Fbv4je", json.dumps(
            ["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None,
                             None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0], gid, int(ts), sg]),
            None, "generic"]]]
        r = c.post(GNEWS_DECODE, data={"f.req": json.dumps(pedido)},
                   headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
        return json.loads(json.loads(r.text.split("\n\n", 1)[1])[0][2])[1]
    except Exception as e:  # noqa: BLE001 — sem a URL real, a apuração só fica sem esse texto
        log.info("não resolvi o link do Google Notícias: %s", e)
        return url


def extrair_texto(pagina_html: str) -> str:
    """Parágrafos da matéria (<p>), sem scripts, menus e rodapés. Sem dependências extras."""
    limpo = re.sub(r"(?is)<(script|style|nav|footer|header|aside|form)[^>]*>.*?</\1>", " ", pagina_html)
    paragrafos = []
    for p in re.findall(r"(?is)<p[^>]*>(.*?)</p>", limpo):
        texto = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", p)).split())
        if len(texto) >= 40:
            paragrafos.append(texto)
    return "\n".join(paragrafos)


def baixar_texto(c, url: str) -> tuple[str, str]:
    """(url_real, texto). Texto vazio se a página bloquear ou não tiver parágrafos."""
    real = resolver_url(c, url)
    try:
        r = c.get(real)
        if r.status_code != 200:
            return real, ""
        return real, extrair_texto(r.text)
    except Exception:  # noqa: BLE001
        return real, ""


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"[\"'“”‘’«»]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def trecho_confere(trecho: str, texto: str) -> bool:
    """O trecho citado pela IA existe no texto? (ignora acento, caixa, aspas e espaços)"""
    t = normalizar(trecho).strip(" .…")
    return len(t) >= MIN_TRECHO and t in normalizar(texto)


def recorte(texto: str, termos: list[str]) -> str:
    """Parágrafos que citam os termos (e vizinhos), até MAX_CARACTERES."""
    paragrafos = texto.split("\n")
    termos_n = [normalizar(t) for t in termos if t]
    marcados = {i for i, p in enumerate(paragrafos) if any(t in normalizar(p) for t in termos_n)}
    escolhidos = sorted({j for i in marcados for j in (i - 1, i, i + 1) if 0 <= j < len(paragrafos)}) or range(len(paragrafos))
    saida = "\n".join(paragrafos[j] for j in escolhidos)
    return saida[:MAX_CARACTERES]


SISTEMA = """Você é um checador de fatos apartidário. Recebe uma AFIRMAÇÃO e o TEXTO de uma reportagem.
Decida, usando SOMENTE o texto:
- confirma: o texto mostra claramente que a afirmação é verdadeira (ex.: a pessoa citada disse/prometeu aquilo; o governo fez aquilo);
- nao_confirma: o texto não trata disso ou não é suficiente;
- contradiz: o texto mostra o oposto, ou atribui a fala/ação a outra pessoa.
"trecho_literal": copie EXATAMENTE do texto, sem mudar nenhuma palavra, a frase que sustenta o veredito (vazio se nao_confirma).
"explicacao": uma frase neutra."""

SCHEMA = {
    "type": "object",
    "properties": {
        "veredito": {"type": "string", "enum": ["confirma", "nao_confirma", "contradiz"]},
        "trecho_literal": {"type": "string"},
        "explicacao": {"type": "string"},
    },
    "required": ["veredito", "trecho_literal", "explicacao"],
    "additionalProperties": False,
}


def apurar(cadeia, c, afirmacao: str, fontes: list[dict], termos: list[str],
           evitar: tuple[str, ...] = (), max_leituras: int = 3) -> dict:
    """Lê até max_leituras matérias e pede veredito a uma IA (outro provedor, se der)."""
    resultado = {"status": "sem_texto", "confirmacoes": 0, "contradicoes": 0, "lidas": 0,
                 "trechos": [], "provedores": [], "em": armazem.hoje()}
    for f in fontes:
        if resultado["lidas"] >= max_leituras:
            break
        url_real, texto = baixar_texto(c, f["url"])
        if not texto:
            continue
        resultado["lidas"] += 1
        usuario = f"AFIRMAÇÃO: {afirmacao}\n\nTEXTO ({f.get('veiculo') or f.get('fonte', '')}):\n{recorte(texto, termos)}"
        try:
            r = cadeia.completar_json(SISTEMA, usuario, SCHEMA, evitar=evitar)
        except RespostaInvalida:
            continue
        v = r.dados
        resultado["provedores"].append(r.provedor)
        if v["veredito"] != "nao_confirma" and not trecho_confere(v["trecho_literal"], texto):
            log.info("apuração: trecho citado pela IA não existe no texto (%s) → descartado", r.provedor)
            continue
        if v["veredito"] == "confirma":
            resultado["confirmacoes"] += 1
            resultado["trechos"].append({"url": url_real, "veiculo": f.get("veiculo") or f.get("fonte", ""),
                                         "trecho": v["trecho_literal"].strip()})
        elif v["veredito"] == "contradiz":
            resultado["contradicoes"] += 1
    if resultado["contradicoes"] > resultado["confirmacoes"]:
        resultado["status"] = "contradita"
    elif resultado["confirmacoes"]:
        resultado["status"] = "apurada"
    elif resultado["lidas"]:
        resultado["status"] = "nao_confirmada"
    return resultado
