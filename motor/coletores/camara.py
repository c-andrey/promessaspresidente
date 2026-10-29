"""Proposições de autoria do Poder Executivo na Câmara dos Deputados.

Pega projetos de lei, PECs e medidas provisórias enviados pelo presidente, além
das mensagens (MSC), que incluem comunicações de sanção e de veto.

O filtro por autor deixa a API lenta de vez em quando (a mesma consulta leva
0,1 s ou estoura em 504 no gateway). Cada página tem algumas tentativas com
espera crescente antes de o coletor falhar.
"""
import logging
import time

import httpx

from .http import cliente

log = logging.getLogger(__name__)

API = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"
FICHA = "https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={id}"
TENTATIVAS = 4


def _pagina(c: httpx.Client, url: str, params: dict | None, dormir=time.sleep) -> dict:
    for tentativa in range(TENTATIVAS):
        try:
            r = c.get(url, params=params, headers={"Accept": "application/json"})
            if r.status_code < 500:
                r.raise_for_status()
                return r.json()
            erro = f"HTTP {r.status_code}"
        except (httpx.TimeoutException, httpx.TransportError) as e:
            erro = str(e) or type(e).__name__
        if tentativa + 1 == TENTATIVAS:
            raise RuntimeError(f"Câmara: {erro}")
        log.info("[camara] %s (tentativa %d)", erro, tentativa + 1)
        dormir(15 * (tentativa + 1))


def coletar(desde: str, ate: str, cfg: dict) -> list[dict]:
    params = {
        "autor": cfg.get("autor", "Poder Executivo"),
        "dataApresentacaoInicio": desde,
        "dataApresentacaoFim": ate,
        "itens": 100,
        "ordem": "ASC",
        "ordenarPor": "id",
    }
    docs, url = [], API
    with cliente(timeout=90) as c:
        while url:
            corpo = _pagina(c, url, params if url == API else None)
            for p in corpo.get("dados", []):
                docs.append({
                    "id": f"camara:{p['id']}",
                    "fonte": "Câmara dos Deputados",
                    "tipo": p["siglaTipo"],
                    "titulo": f"{p['siglaTipo']} {p['numero']}/{p['ano']}",
                    "resumo": p.get("ementa") or "",
                    "url": FICHA.format(id=p["id"]),
                    "data": (p.get("dataApresentacao") or desde)[:10],
                })
            url = next((l["href"] for l in corpo.get("links", []) if l["rel"] == "next"), None)
    return docs
