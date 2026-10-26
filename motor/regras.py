"""Decide o que fazer com cada avaliação da IA.

Princípio: a IA sugere, as regras decidem. Só o passo mais seguro
(nao_iniciada → em_andamento com fonte oficial e confiança alta) é aplicado
sozinho. Cumprida, parcial e abandonada sempre passam por revisão humana.
"""
import hashlib

from . import armazem

AUTO, FILA, DESCARTE = "automatico", "fila", "descartada"


def inicio_acompanhamento(presidente: dict) -> str:
    """Desde quando atos do governo contam: acompanhar_desde (reeleito) ou a posse."""
    return str(presidente.get("acompanhar_desde") or presidente["mandato_inicio"])


def decidir(av: dict, promessa: dict, doc: dict, regras: dict, presidente: dict) -> str:
    sugerido = av["status_sugerido"]
    if not av["relevante"] or sugerido == "sem_mudanca" or sugerido == promessa["status"]:
        return DESCARTE
    if av["confianca"] < regras["confianca_fila"]:
        return DESCARTE
    pode_auto = (
        sugerido == "em_andamento"
        and promessa["status"] == "nao_iniciada"
        and doc.get("oficial")
        and av["confianca"] >= regras["confianca_auto"]
        and doc["data"] >= inicio_acompanhamento(presidente)
    )
    return AUTO if pode_auto else FILA


def resumo_documento(doc: dict) -> dict:
    return {k: doc.get(k) for k in ("id", "fonte", "tipo", "titulo", "url", "data")}


def aplicar_status(promessa: dict, novo: str, doc: dict, av: dict, provedor: str, origem: str,
                   nota: str | None = None) -> None:
    promessa["historico"].append({
        "data": armazem.hoje(),
        "de": promessa["status"],
        "para": novo,
        "documento": resumo_documento(doc),
        "evidencia": av.get("evidencia", ""),
        "justificativa": av.get("justificativa", ""),
        "confianca": av.get("confianca"),
        "provedor": provedor,
        "origem": origem,
        **({"nota": nota} if nota else {}),
        **{k: av[k] for k in ("segunda_opiniao", "apuracao") if av.get(k)},
    })
    promessa["status"] = novo
    promessa["atualizado_em"] = armazem.hoje()


MAX_IMPRENSA = 15


def registrar_imprensa(promessa: dict, doc: dict, av: dict) -> None:
    """Notícia relacionada vira referência "Na imprensa" (mais recentes primeiro, sem repetir)."""
    lista = promessa.setdefault("imprensa", [])
    if any(n["url"] == doc["url"] for n in lista):
        return
    lista.append({k: doc.get(k) for k in ("fonte", "titulo", "url", "data")} | {"resumo": av["justificativa"]})
    lista.sort(key=lambda n: n["data"] or "", reverse=True)
    del lista[MAX_IMPRENSA:]


def item_fila(av: dict, promessa: dict, doc: dict, provedor: str) -> dict:
    chave = hashlib.sha1(f"{promessa['id']}|{doc['id']}".encode()).hexdigest()[:10]
    return {
        "id": chave,
        "promessa_id": promessa["id"],
        "promessa": promessa["titulo"],
        "status_atual": promessa["status"],
        "status_sugerido": av["status_sugerido"],
        "confianca": av["confianca"],
        "evidencia": av["evidencia"],
        "justificativa": av["justificativa"],
        "documento": resumo_documento(doc) | {"oficial": doc.get("oficial", False)},
        "provedor": provedor,
        "criado_em": armazem.hoje(),
        **{k: av[k] for k in ("segunda_opiniao", "apuracao") if av.get(k)},
    }
