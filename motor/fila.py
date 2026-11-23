"""Fila de revisão consolidada: uma sugestão por promessa e status, com todas as evidências.

Até 23/11/2026 cada notícia virava um item: a fila passou de 460, quase tudo
"não iniciada → em andamento" repetido para as mesmas promessas, sem que ninguém
desse conta de revisar. Agora:
- entra só o que tem documento oficial ou foi APURADO (texto lido, citação conferida);
  notícia sem apuração não entra (o acompanhamento por notícias apura a promessa depois);
- a mesma promessa + status sugerido vira um item só, que acumula as evidências;
- "não iniciada → em andamento" é aplicado sozinho quando veículos independentes
  (sites de partido não contam) confirmam o passo e nenhuma checagem de fatos o desmente;
- item sem evidência nova há dias_validade_fila dias sai da fila (fica arquivado).
Parcial, cumprida e abandonada continuam sempre com revisão humana.
"""
import hashlib
import logging
import re
from datetime import date, timedelta
from urllib.parse import urlsplit

from . import armazem, regras

log = logging.getLogger(__name__)

ARQUIVADOS = armazem.ESTADO / "fila_arquivada.json"
# promessas que o acompanhamento por notícias (motor/execucao.py) apura primeiro: chave "prioridade" do estado dele
ESTADO_ACOMPANHAMENTO = armazem.ESTADO / "execucao.json"
VALIDADE_PADRAO = 30
VEICULOS_PADRAO = 2
# Governo, Legislativo, Judiciário, Ministério Público e mídia estatal (EBC) não são imprensa independente.
NAO_INDEPENDENTE = re.compile(r"(^|\.)(gov\.br|leg\.br|jus\.br|mp\.br|ebc\.com\.br|google\.com)$")


def chave(promessa_id: str, status: str) -> str:
    return hashlib.sha1(f"{promessa_id}|{status}".encode()).hexdigest()[:10]


def admitir(item: dict) -> bool:
    """Documento oficial ou notícia apurada; checagem que desmente não barra (vai para o humano ver)."""
    return bool(item["documento"].get("oficial")) or (item.get("apuracao") or {}).get("status") == "apurada"


def _evidencia(item: dict) -> dict:
    return {k: item[k] for k in ("documento", "evidencia", "justificativa", "confianca", "provedor", "criado_em",
                                 "apuracao", "segunda_opiniao", "checagem") if item.get(k)}


def _forca(ev: dict) -> tuple:
    return (bool(ev["documento"].get("oficial")), (ev.get("apuracao") or {}).get("status") == "apurada",
            ev.get("confianca") or 0)


def _principal(item: dict) -> None:
    """Os campos de cima do item (o que o painel mostra primeiro) vêm da evidência mais forte."""
    melhor = max(item["evidencias"], key=_forca)
    for k in ("documento", "evidencia", "justificativa", "confianca", "provedor", "apuracao", "segunda_opiniao"):
        if melhor.get(k) is not None:
            item[k] = melhor[k]
        else:
            item.pop(k, None)
    item["confianca"] = max(e.get("confianca") or 0 for e in item["evidencias"])


def adicionar(fila: list[dict], item: dict) -> dict:
    """Junta o item ao que já existe para a mesma promessa e status (ou cria). Devolve o item da fila."""
    k = chave(item["promessa_id"], item["status_sugerido"])
    atual = next((i for i in fila if i["id"] == k), None)
    ev = _evidencia(item)
    if atual is None:
        atual = {k2: v for k2, v in item.items() if k2 not in ("apuracao", "segunda_opiniao", "checagem")}
        atual.update(id=k, evidencias=[], atualizado_em=item.get("criado_em") or armazem.hoje())
        fila.append(atual)
    ja = {regras.chave_url(e["documento"].get("url")) for e in atual["evidencias"]}
    if regras.chave_url(ev["documento"].get("url")) not in ja:
        atual["evidencias"].append(ev)
        atual["atualizado_em"] = max(atual.get("atualizado_em") or "", ev.get("criado_em") or armazem.hoje())
    if any(e["documento"].get("oficial") for e in atual["evidencias"]):
        atual.pop("origem", None)
    else:
        atual["origem"] = "noticias"
    _principal(atual)
    return atual


def priorizar(ids) -> None:
    """Notícia sugeriu mudança mas não foi apurada: a promessa passa à frente no rodízio de amanhã."""
    estado = armazem.ler_json(ESTADO_ACOMPANHAMENTO, {})
    atuais = estado.setdefault("prioridade", [])
    atuais += [i for i in dict.fromkeys(ids) if i not in atuais]
    armazem.gravar_json(ESTADO_ACOMPANHAMENTO, estado)


def confirmacoes_independentes(item: dict) -> dict[str, str]:
    """{domínio: veículo} das matérias cujo texto foi lido e confirmou o passo, com citação conferida.
    Conta por domínio (o mesmo site com dois nomes conta uma vez); partido e governo não contam."""
    from .campanha import partidaria  # campanha importa muita coisa; aqui só precisa disto
    achados = {}
    for e in item.get("evidencias", []):
        for t in (e.get("apuracao") or {}).get("trechos", []):
            dominio = (urlsplit(t.get("url") or "").hostname or "").removeprefix("www.")
            if dominio and not partidaria(t.get("url")) and not NAO_INDEPENDENTE.search(dominio):
                achados.setdefault(dominio, t.get("veiculo") or dominio)
    return achados


def veiculos_confirmados(item: dict) -> set[str]:
    return set(confirmacoes_independentes(item))


def desmentido(item: dict) -> bool:
    return any(e.get("checagem") for e in item.get("evidencias", []))


def pode_auto(item: dict, promessa: dict, minimo: int = VEICULOS_PADRAO) -> bool:
    return (item["status_sugerido"] == "em_andamento" and promessa["status"] == "nao_iniciada"
            and len(veiculos_confirmados(item)) >= minimo and not desmentido(item))


def arrumar(fila: list[dict], promessas: list[dict], cfg: dict | None = None, hoje: str | None = None) -> tuple[list[dict], dict]:
    """Consolida, aplica o que já tem confirmação suficiente e tira o vencido ou sem prova.

    Idempotente: roda no fim de todo sync (e migra a fila antiga, de um item por notícia).
    """
    cfg = cfg or {}
    hoje = hoje or armazem.hoje()
    corte = (date.fromisoformat(hoje) - timedelta(days=cfg.get("dias_validade_fila", VALIDADE_PADRAO))).isoformat()
    minimo = cfg.get("veiculos_para_auto", VEICULOS_PADRAO)
    por_id = {p["id"]: p for p in promessas}
    nova, arquivados, sem_prova = [], [], []
    stats = {"aplicados": 0, "sem_prova": 0, "vencidos": 0, "superados": 0}
    for item in fila:
        evs = item.get("evidencias") or [_evidencia(item)]  # item antigo: a evidência é o próprio item
        for ev in evs:
            if not admitir(ev):
                stats["sem_prova"] += 1
                arquivados.append(ev | {"promessa_id": item["promessa_id"], "motivo": "sem_prova"})
                sem_prova.append(item["promessa_id"])
                continue
            adicionar(nova, {**{k: v for k, v in item.items() if k != "evidencias"}, **ev})
    restantes = []
    for item in nova:
        p = por_id.get(item["promessa_id"])
        if p is None or p["status"] == item["status_sugerido"]:
            stats["superados"] += 1
            continue
        item["status_atual"] = p["status"]
        if pode_auto(item, p, minimo):
            fontes = confirmacoes_independentes(item)
            trechos = [t for e in item["evidencias"] for t in (e.get("apuracao") or {}).get("trechos", [])
                       if (urlsplit(t.get("url") or "").hostname or "").removeprefix("www.") in fontes]
            av = {"evidencia": trechos[0]["trecho"] if trechos else item.get("evidencia", ""),
                  "confianca": item.get("confianca"),
                  "justificativa": f"Confirmado no texto de {len(fontes)} veículos independentes: " + ", ".join(sorted(fontes.values())),
                  "apuracao": {"status": "apurada", "confirmacoes": len(trechos), "trechos": trechos}}
            regras.aplicar_status(p, "em_andamento", item["documento"], av, item.get("provedor", ""), "noticias_confirmadas")
            stats["aplicados"] += 1
            log.info("AUTO (veículos confirmam) %s → em_andamento", p["id"])
            continue
        if (item.get("atualizado_em") or item.get("criado_em") or hoje) < corte:
            stats["vencidos"] += 1
            arquivados.append(item | {"motivo": "vencido"})
            continue
        restantes.append(item)
    if sem_prova:
        priorizar(sem_prova)
    if arquivados:
        antigos = armazem.ler_json(ARQUIVADOS, [])
        armazem.gravar_json(ARQUIVADOS, antigos + [a | {"arquivado_em": hoje} for a in arquivados])
    return restantes, stats


def sincronizar() -> dict:
    """Passo do sync diário: arruma a fila no volume e grava promessas (se algo foi aplicado)."""
    cfg = armazem.ler_yaml("coleta.yaml").get("regras", {})
    promessas = armazem.ler_json(armazem.PROMESSAS, [])
    antes = armazem.ler_json(armazem.FILA, [])
    fila, stats = arrumar(antes, promessas, cfg)
    armazem.gravar_json(armazem.FILA, fila)
    if stats["aplicados"]:
        armazem.gravar_json(armazem.PROMESSAS, promessas)
    stats |= {"antes": len(antes), "depois": len(fila)}
    log.info("Fila de revisão: %s", stats)
    return stats
