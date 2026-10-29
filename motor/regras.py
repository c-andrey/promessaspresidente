"""Decide o que fazer com cada avaliação da IA.

Princípio: a IA sugere, as regras decidem. Só o passo mais seguro
(nao_iniciada → em_andamento com fonte oficial e confiança alta) é aplicado
sozinho. Cumprida, parcial e abandonada sempre passam por revisão humana.
"""
import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit

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


def chave_url(url: str | None) -> str:
    """Mesma matéria com URL diferente: www., versão AMP, parâmetros de rastreio, barra no fim."""
    u = urlsplit(url or "")
    host = (u.hostname or "").removeprefix("www.").removeprefix("amp.")
    caminho = re.sub(r"(/amp|\.amp|/amp\.html)$", "", u.path.rstrip("/")).rstrip("/")
    # ?id=123 identifica matéria em muitos sites; só os parâmetros de rastreio saem
    query = urlencode(sorted((k, v) for k, v in parse_qsl(u.query)
                             if not re.match(r"utm_|fbclid|gclid|amp$|outputType|ref$", k)))
    return f"{host}{caminho}".lower() + (f"?{query}" if query else "")


def chave_titulo(titulo: str | None) -> str:
    """Título sem acento, pontuação, caixa e o " - Veículo" que alguns agregadores põem no fim."""
    t = unicodedata.normalize("NFKD", (titulo or "").lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def chaves(n: dict) -> set[str]:
    return {k for k in ("u:" + chave_url(n.get("url")), "t:" + chave_titulo(n.get("titulo"))) if len(k) > 2}


def _google(n: dict) -> bool:
    return "news.google.com" in (n.get("url") or "")


def sem_repetir(lista: list[dict], excluir: set[str] = frozenset()) -> list[dict]:
    """Uma entrada por matéria (mesma URL normalizada ou mesmo título), na ordem original.
    Se a repetida tem a URL real e a guardada só o link do Google Notícias, fica a real.
    `excluir`: chaves de matérias que já aparecem em outra seção da promessa."""
    saida, dono = [], {}
    for n in lista:
        ks = chaves(n)
        if ks & excluir:
            continue
        anterior = next((dono[k] for k in ks if k in dono), None)
        if anterior is None:
            saida.append(n)
            for k in ks:
                dono[k] = n
        elif _google(anterior) and not _google(n):
            anterior.update({k: v for k, v in n.items() if v})
            for k in chaves(anterior):
                dono[k] = anterior
    return saida


def mais_recentes(lista: list[dict]) -> list[dict]:
    """Da matéria mais nova para a mais antiga (sem data vai para o fim)."""
    return sorted(lista, key=lambda n: n.get("data") or "", reverse=True)


def limpar_noticias(promessa: dict) -> None:
    """Sem matéria repetida dentro de cada seção, nem em "Na imprensa" o que já está nas referências."""
    fonte = promessa.get("fonte") or {}
    principal = chaves(fonte) if fonte.get("titulo") else set()  # declaração: a matéria em que foi dita
    promessa["referencias"] = mais_recentes(sem_repetir(promessa.get("referencias") or [], principal))
    if promessa.get("imprensa"):
        ja = principal.union(*map(chaves, promessa["referencias"]))
        promessa["imprensa"] = mais_recentes(sem_repetir(promessa["imprensa"], ja))


def registrar_imprensa(promessa: dict, doc: dict, av: dict) -> None:
    """Notícia relacionada vira referência "Na imprensa" (mais recentes primeiro, sem repetir)."""
    lista = promessa.setdefault("imprensa", [])
    nova = {k: doc.get(k) for k in ("fonte", "titulo", "url", "data")} | {"resumo": av["justificativa"]}
    ja = set().union(*map(chaves, lista + (promessa.get("referencias") or []))) if lista or promessa.get("referencias") else set()
    if chaves(nova) & ja:
        return
    lista.append(nova)
    lista[:] = mais_recentes(lista)
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
