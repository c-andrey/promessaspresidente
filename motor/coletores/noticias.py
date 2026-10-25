"""Notícias de jornais: buscas no Google Notícias (RSS, sem chave) + feeds RSS de veículos.

O Google Notícias agrega G1, Folha, Estadão, CNN, UOL etc. numa busca só. A API de
busca do Google (Custom Search) foi fechada para clientes novos, por isso o RSS.
Notícia é contexto, não prova: a fonte é marcada como não oficial na configuração,
nunca muda status sozinha e alimenta a seção "Na imprensa" de cada promessa.
"""
import hashlib
import html
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

from .http import cliente

GOOGLE_NEWS = "https://news.google.com/rss/search?q={q}+when:{dias}d&hl=pt-BR&gl=BR&ceid=BR:pt-419"


def _limpar(texto: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html.unescape(texto or "")).split())


def _data(item) -> str | None:
    bruto = item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date")
    if not bruto:
        return None
    try:
        return parsedate_to_datetime(bruto).date().isoformat()
    except (TypeError, ValueError):
        return bruto[:10] if re.match(r"\d{4}-\d{2}-\d{2}", bruto) else None


def _itens(c, url: str, nome_fonte: str, desde: str, ate: str) -> list[dict]:
    r = c.get(url)
    r.raise_for_status()
    docs = []
    for item in ET.fromstring(r.content).iter("item"):
        data = _data(item)
        if not data or not desde <= data <= ate:
            continue
        link = (item.findtext("link") or "").strip()
        veiculo = item.findtext("source") or nome_fonte
        titulo = _limpar(item.findtext("title"))
        # Google Notícias põe " - Veículo" no fim do título
        if veiculo and titulo.endswith(f" - {veiculo}"):
            titulo = titulo[: -len(veiculo) - 3]
        docs.append({
            "id": "noticia:" + hashlib.sha1(link.encode()).hexdigest()[:16],
            "fonte": veiculo,
            "tipo": "Notícia",
            "titulo": titulo,
            "resumo": _limpar(item.findtext("description"))[:800],
            "url": link,
            "data": data,
        })
    return docs


def coletar(desde: str, ate: str, cfg: dict) -> list[dict]:
    dias = cfg.get("janela_busca_dias", 3)
    alvos = [(GOOGLE_NEWS.format(q=quote_plus(b), dias=dias), "Google Notícias") for b in cfg.get("buscas", [])]
    alvos += [(f["url"], f["nome"]) for f in cfg.get("feeds", [])]
    vistos, docs = set(), []
    with cliente() as c:
        for url, nome in alvos:
            for d in _itens(c, url, nome, desde, ate):
                # a mesma matéria aparece em várias buscas: deduplica por id e por título
                chave = d["titulo"].lower()
                if d["id"] in vistos or chave in vistos:
                    continue
                vistos.update((d["id"], chave))
                docs.append(d)
    return docs
