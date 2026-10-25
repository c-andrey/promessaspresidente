"""Diário Oficial da União, seção 1, via página de leitura do in.gov.br.

A página traz um JSON embutido (<script id="params">) com todos os atos do dia.
O in.gov.br recusa conexões de fora do Brasil e oscila bastante (CDN devolvendo
502). Por isso:
- a variável indicada em `proxy_env` (ex.: socks5://127.0.0.1:1080, túnel SSH
  pela VPS em São Paulo) é usada como proxy, quando definida;
- cada página tem algumas tentativas com espera crescente;
- se um dia falhar de vez, o coletor inteiro falha e o período é recoletado na
  próxima execução (main.py só avança a data dos coletores que deram certo).
"""
import json
import logging
import os
import re
import time
from datetime import date, timedelta

import httpx

from .http import cliente

log = logging.getLogger(__name__)

LEITURA = "https://www.in.gov.br/leiturajornal"
ATO = "https://www.in.gov.br/web/dou/-/{slug}"
PARAMS = re.compile(r'<script id="params" type="application/json">\s*(.*?)\s*</script>', re.S)
TENTATIVAS = 4


def _dias(desde: str, ate: str):
    d, fim = date.fromisoformat(desde), date.fromisoformat(ate)
    while d <= fim:
        if d.weekday() < 5:  # DOU ordinário sai em dias úteis
            yield d
        d += timedelta(days=1)


def extrair_atos(pagina: str) -> list[dict] | None:
    """Lista de atos do dia; None se a página não trouxe o JSON (erro do CDN, página parcial)."""
    m = PARAMS.search(pagina)
    if not m:
        return None
    return json.loads(m.group(1)).get("jsonArray", [])


def _baixar_dia(c: httpx.Client, dia: date, secao: str, dormir=time.sleep) -> list[dict]:
    erro = None
    for tentativa in range(TENTATIVAS):
        try:
            r = c.get(LEITURA, params={"data": dia.strftime("%d-%m-%Y"), "secao": secao})
            if r.status_code == 200:
                atos = extrair_atos(r.text)
                if atos is not None:
                    return atos
                erro = "página sem o JSON dos atos"
            else:
                erro = f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            erro = str(e) or type(e).__name__
        log.info("[dou] %s %s: %s (tentativa %d)", dia, secao, erro, tentativa + 1)
        dormir(5 * (tentativa + 1))
    raise RuntimeError(f"DOU {dia} {secao}: {erro}")


def coletar(desde: str, ate: str, cfg: dict) -> list[dict]:
    tipos = {t.lower() for t in cfg.get("tipos_ato", [])}
    proxy = os.environ.get(cfg.get("proxy_env", ""), "") or None
    docs = []
    with cliente(timeout=60, proxy=proxy) as c:
        for dia in _dias(desde, ate):
            for secao in cfg.get("secoes", ["do1"]):
                for ato in _baixar_dia(c, dia, secao):
                    tipo = ato.get("artType", "")
                    if tipos and tipo.lower() not in tipos:
                        continue
                    docs.append({
                        "id": f"dou:{ato.get('urlTitle') or ato.get('classPK')}",
                        "fonte": "Diário Oficial da União",
                        "tipo": tipo,
                        "titulo": ato.get("title", ""),
                        "resumo": re.sub(r"\s+", " ", ato.get("content", "")).strip(),
                        "url": ATO.format(slug=ato.get("urlTitle", "")),
                        "data": dia.isoformat(),
                    })
    return docs
