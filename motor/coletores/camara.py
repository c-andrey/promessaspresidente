"""Proposições de autoria do Poder Executivo na Câmara dos Deputados.

Pega projetos de lei, PECs e medidas provisórias enviados pelo presidente, além
das mensagens (MSC), que incluem comunicações de sanção e de veto.
"""
from .http import cliente

API = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"
FICHA = "https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={id}"


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
    with cliente() as c:
        while url:
            r = c.get(url, params=params if url == API else None, headers={"Accept": "application/json"})
            r.raise_for_status()
            corpo = r.json()
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
