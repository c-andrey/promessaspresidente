"""Notícias de jornais: buscas no Google Notícias (RSS, sem chave; Bing de reserva) + feeds RSS de veículos.

O Google Notícias agrega G1, Folha, Estadão, CNN, UOL etc. numa busca só. A API de
busca do Google (Custom Search) foi fechada para clientes novos, por isso o RSS.
Notícia é contexto, não prova: a fonte é marcada como não oficial na configuração,
nunca muda status sozinha e alimenta a seção "Na imprensa" de cada promessa.
"""
import hashlib
import html
import logging
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlsplit

from .. import armazem
from .http import cliente

log = logging.getLogger(__name__)


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


def _link_real(link: str) -> str:
    """O Bing embrulha a matéria em bing.com/news/apiclick.aspx?...&url=<matéria>."""
    if "bing.com/news/apiclick" in link:
        return parse_qs(urlsplit(link).query).get("url", [link])[0]
    return link


def _veiculo(item) -> str | None:
    """<source> no Google e nos feeds; <News:Source> (com namespace) no Bing."""
    return item.findtext("source") or next((e.text for e in item if e.tag.endswith("}Source") and e.text), None)


def _itens(c, url: str, nome_fonte: str, desde: str, ate: str) -> list[dict]:
    r = c.get(url)
    r.raise_for_status()
    docs = []
    for item in ET.fromstring(r.content).iter("item"):
        data = _data(item)
        if not data or not desde <= data <= ate:
            continue
        link = _link_real((item.findtext("link") or "").strip())
        veiculo = _veiculo(item) or nome_fonte
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
    """Buscas + feeds. Uma fonte com erro não derruba as outras; o coletor só falha se todas falharem."""
    from .busca import buscar  # busca importa _itens daqui

    presidente = armazem.ler_yaml("presidente.yaml")["nome_busca"]
    # after: vale no Google; no Bing (reserva) o período é aplicado nos itens
    alvos = [(b.format(nome=presidente), lambda c, q: buscar(c, f"{q} after:{desde}", desde, ate)) for b in cfg.get("buscas", [])]
    alvos += [(f["nome"], lambda c, _, u=f["url"], n=f["nome"]: _itens(c, u, n, desde, ate)) for f in cfg.get("feeds", [])]
    vistos, docs, erros = set(), [], []
    with cliente() as c:
        for nome, pegar in alvos:
            try:
                achados = pegar(c, nome)
            except Exception as e:  # noqa: BLE001
                erros.append(f"{nome}: {e}")
                log.warning("[noticias] %s falhou: %s", nome, e)
                continue
            for d in achados:
                # a mesma matéria aparece em várias buscas: deduplica por id e por título
                chave = d["titulo"].lower()
                if d["id"] in vistos or chave in vistos:
                    continue
                vistos.update((d["id"], chave))
                docs.append(d)
    if erros and len(erros) == len(alvos):
        raise RuntimeError("; ".join(erros))
    return docs
