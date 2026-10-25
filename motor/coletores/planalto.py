"""Notícias do Planalto (RSS 1.0/RDF do gov.br).

Notícia não prova cumprimento, por isso a fonte é marcada como não oficial na
configuração: serve para achar pistas, nunca aplica status sozinha.
"""
import html
import re
import xml.etree.ElementTree as ET

from .http import cliente

NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rss": "http://purl.org/rss/1.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}
LIMITE_RESUMO = 1500


def _texto(no, caminho):
    el = no.find(caminho, NS)
    return (el.text or "").strip() if el is not None else ""


def coletar(desde: str, ate: str, cfg: dict) -> list[dict]:
    with cliente() as c:
        r = c.get(cfg["url"])
        r.raise_for_status()
    raiz = ET.fromstring(r.content)
    docs = []
    for item in raiz.findall("rss:item", NS):
        data = _texto(item, "dc:date")[:10]
        if data and not (desde <= data <= ate):
            continue
        link = _texto(item, "rss:link") or item.get(f"{{{NS['rdf']}}}about", "")
        corpo = _texto(item, "rss:description") + " " + _texto(item, "content:encoded")
        resumo = re.sub(r"<[^>]+>", " ", html.unescape(corpo))
        docs.append({
            "id": f"planalto:{link.rstrip('/').rsplit('/', 1)[-1]}",
            "fonte": "Planalto (notícias)",
            "tipo": "Notícia",
            "titulo": _texto(item, "rss:title"),
            "resumo": " ".join(resumo.split())[:LIMITE_RESUMO],
            "url": link,
            "data": data or desde,
        })
    return docs
