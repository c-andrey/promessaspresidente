"""Legislação federal (leis, LCs, decretos, MPs, emendas) pela API de dados abertos do Senado.

Fonte estável e acessível de qualquer lugar, ao contrário do in.gov.br. A
indexação atrasa alguns dias, então a consulta volta `dias_retroativos` antes
da data pedida; vistos.json evita reprocessar o que já entrou.
"""
from datetime import date, datetime, timedelta

from .http import cliente

API = "https://legis.senado.leg.br/dadosabertos/legislacao/lista"
NORMA = "https://legis.senado.leg.br/norma/{id}"


def coletar(desde: str, ate: str, cfg: dict) -> list[dict]:
    inicio = date.fromisoformat(desde) - timedelta(days=cfg.get("dias_retroativos", 15))
    fim = date.fromisoformat(ate)
    docs = []
    with cliente() as c:
        for ano in range(inicio.year, fim.year + 1):
            for tipo in cfg.get("tipos", ["LEI", "LCP", "DEC", "MPV", "EMC"]):
                r = c.get(API, params={"tipo": tipo, "ano": ano}, headers={"Accept": "application/json"})
                r.raise_for_status()
                lista = (r.json().get("ListaDocumento", {}).get("documentos") or {}).get("documento", [])
                if isinstance(lista, dict):  # um único resultado vem como objeto
                    lista = [lista]
                for n in lista:
                    assinatura = datetime.strptime(n["dataassinatura"], "%d/%m/%Y").date()
                    if not inicio <= assinatura <= fim:
                        continue
                    docs.append({
                        "id": f"norma:{n['norma']}",
                        "fonte": "Legislação federal (Senado)",
                        "tipo": n.get("descricao", tipo),
                        "titulo": n["normaNome"],
                        "resumo": n.get("ementa", ""),
                        "url": NORMA.format(id=n["id"]),
                        "data": assinatura.isoformat(),
                    })
    return docs
