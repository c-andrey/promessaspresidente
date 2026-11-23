"""Busca de notícias por palavra-chave: Google Notícias, com o Bing Notícias de reserva.

Os dois são RSS gratuitos e sem chave. O Google agrega mais veículos e aceita
after:/before:, mas devolve 429 quando recebe muitas buscas seguidas (coleta,
campanha e acompanhamento somam dezenas por sync). Por isso:
- as buscas no Google têm um intervalo mínimo entre si;
- um 429 ganha uma nova tentativa depois de uma pausa; se repetir, o Google fica
  de fora por um tempo e as buscas vão para o Bing;
- o Bing não tem filtro de data na busca: o período é aplicado nos itens.
(O GDELT, outra opção gratuita, devolveu 429 até no primeiro pedido em 23/11/2026.)
"""
import logging
import re
import time
from urllib.parse import quote_plus

import httpx

from .noticias import _itens

log = logging.getLogger(__name__)

GOOGLE = "https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
BING = "https://www.bing.com/news/search?q={q}&format=rss&setlang=pt-BR&cc=BR"
INTERVALO_GOOGLE = 3        # segundos entre buscas no Google
PAUSA_429 = 30              # espera antes de repetir uma busca que levou 429
GOOGLE_FORA = 15 * 60       # depois de dois 429 seguidos, tempo só no Bing

_estado = {"ultima": 0.0, "fora_ate": 0.0}


def _sem_operadores_de_data(consulta: str) -> str:
    return " ".join(re.sub(r"\b(after|before|when):\S+", " ", consulta).split())


def _google(c, consulta: str, desde: str, ate: str, dormir, relogio) -> list[dict] | None:
    """Itens do Google, ou None se ele estiver bloqueando (429)."""
    if relogio() < _estado["fora_ate"]:
        return None
    for tentativa in range(2):
        espera = _estado["ultima"] + INTERVALO_GOOGLE - relogio()
        if espera > 0:
            dormir(espera)
        _estado["ultima"] = relogio()
        try:
            return _itens(c, GOOGLE.format(q=quote_plus(consulta)), "Google Notícias", desde, ate)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429:
                raise
            if tentativa == 0:
                log.info("Google Notícias: 429, nova tentativa em %d s", PAUSA_429)
                dormir(PAUSA_429)
    _estado["fora_ate"] = relogio() + GOOGLE_FORA
    log.warning("Google Notícias bloqueando (429): próximas buscas no Bing por %d min", GOOGLE_FORA // 60)
    return None


def buscar(c, consulta: str, desde: str, ate: str, dormir=time.sleep, relogio=time.monotonic) -> list[dict]:
    """Notícias da consulta entre desde e ate (AAAA-MM-DD). Erro de rede propaga."""
    docs = _google(c, consulta, desde, ate, dormir, relogio)
    if docs is None:
        docs = _itens(c, BING.format(q=quote_plus(_sem_operadores_de_data(consulta))), "Bing Notícias", desde, ate)
    return docs
