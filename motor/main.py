"""Execução diária: coleta → filtro → IA (cadeia com fallback) → regras → grava.

Uso:
    python -m motor.main                 # desde a última execução até hoje
    python -m motor.main --desde 2027-01-01 --ate 2027-01-31
    python -m motor.main --sem-ia        # só coleta e filtra (não gasta cota)
"""
import argparse
import logging
from datetime import date, timedelta

from . import armazem, regras
from .classificador import classificar
from .coletores import coletar_tudo
from .filtro import candidatas
from .llm import Cadeia, SemProvedorDisponivel

log = logging.getLogger("motor")


def segunda_opiniao(cadeia, presidente, doc, promessa, av, provedor) -> dict:
    """Reclassifica o documento só com esta promessa, preferindo outro provedor (outra empresa)."""
    try:
        r = classificar(cadeia, presidente, doc, [promessa], evitar=(provedor,))
    except SemProvedorDisponivel:
        return {"concorda": False, "motivo": "sem segundo provedor disponível"}
    a2 = next((a for a in r.dados["avaliacoes"] if a["promessa_id"] == promessa["id"]), None)
    concorda = bool(a2 and a2["relevante"] and a2["status_sugerido"] == av["status_sugerido"])
    return {"concorda": concorda, "provedor": r.provedor, "status": a2["status_sugerido"] if a2 else None}


def periodo(args, cfg, execucao: dict) -> tuple[dict[str, str], str]:
    """Data inicial de cada coletor: a última coleta bem-sucedida dele.

    Reabre o último dia: atos publicados tarde não se perdem (vistos.json deduplica).
    Um coletor que falhou (ex.: DOU fora do ar) recoleta o período perdido na próxima vez.
    """
    ate = args.ate or armazem.hoje()
    padrao = args.desde or (date.fromisoformat(ate) - timedelta(days=cfg["dias_primeira_coleta"])).isoformat()
    ultimas = {} if args.desde else execucao.get("coletores", {})
    return {nome: ultimas.get(nome, padrao) for nome in cfg["coletores"]}, ate


def executar(args) -> dict:
    cfg = armazem.ler_yaml("coleta.yaml")
    presidente = armazem.ler_yaml("presidente.yaml")
    promessas = armazem.ler_json(armazem.PROMESSAS, [])
    por_id = {p["id"]: p for p in promessas}
    fila = armazem.ler_json(armazem.FILA, [])
    na_fila = {i["id"] for i in fila}
    vistos = set(armazem.ler_json(armazem.VISTOS, []))
    pendentes = armazem.ler_json(armazem.PENDENTES, [])

    execucao = armazem.ler_json(armazem.EXECUCAO, {})
    desde, ate = periodo(args, cfg, execucao)
    log.info("Coletando até %s", ate)
    docs, relatorio_coleta = coletar_tudo(desde, ate, cfg)
    coletores_ok = execucao.get("coletores", {}) | {
        nome: ate for nome, r in relatorio_coleta.items() if isinstance(r, int)}

    novos = [d for d in docs if d["id"] not in vistos]
    if novos and not args.dry_run:
        caminho = armazem.documentos_do_dia(armazem.hoje())
        existentes = armazem.ler_json(caminho, [])
        armazem.gravar_json(caminho, existentes + novos)

    # Trabalho do dia = pendentes de execuções anteriores (ainda válidos) + novos com candidata.
    limite_pendente = (date.today() - timedelta(days=cfg["dias_validade_pendente"])).isoformat()
    trabalho = [p for p in pendentes if p["doc"]["data"] >= limite_pendente]
    noticias = 0
    for d in novos:
        c = candidatas(d, promessas, cfg["max_promessas_por_documento"])
        if not c:
            continue
        if not d.get("oficial"):
            noticias += 1
            if noticias > cfg.get("max_noticias_por_execucao", 60):
                continue
        trabalho.append({"doc": d, "candidatas": c})
    # Oficiais primeiro: se a cota acabar, o que sobra para amanhã são as notícias.
    trabalho.sort(key=lambda item: not item["doc"].get("oficial"))
    vistos.update(d["id"] for d in novos)

    stats = {"coleta": relatorio_coleta, "novos": len(novos), "para_ia": len(trabalho),
             "classificados": 0, "automatico": 0, "fila": 0, "descartada": 0, "provedores": {}}

    restantes = trabalho
    if not args.sem_ia and trabalho:
        cadeia = Cadeia.da_config()
        log.info("Provedores disponíveis: %s", ", ".join(cadeia.ativos()) or "nenhum")
        limite = cfg["max_documentos_por_execucao"]
        feitos = 0
        for item in trabalho[:limite]:
            doc = item["doc"]
            alvo = [por_id[c["id"]] for c in item["candidatas"] if c["id"] in por_id]
            try:
                res = classificar(cadeia, presidente, doc, alvo)
            except SemProvedorDisponivel:
                log.warning("Todos os provedores esgotados: %d documentos ficam para amanhã",
                            len(trabalho) - feitos)
                break
            feitos += 1
            stats["classificados"] += 1
            stats["provedores"][res.provedor] = stats["provedores"].get(res.provedor, 0) + 1
            for av in res.dados["avaliacoes"]:
                p = por_id[av["promessa_id"]]
                if not doc.get("oficial") and av["relevante"]:
                    regras.registrar_imprensa(p, doc, av)
                decisao = regras.decidir(av, p, doc, cfg["regras"], presidente)
                if decisao == regras.AUTO:
                    # segunda opinião, de outro provedor: só aplica sozinho se os dois concordarem
                    av["segunda_opiniao"] = segunda_opiniao(cadeia, presidente, doc, p, av, res.provedor)
                    if not av["segunda_opiniao"]["concorda"]:
                        decisao = regras.FILA
                        log.info("DIVERGÊNCIA %s: %s diz %s → fila", p["id"],
                                 av["segunda_opiniao"].get("provedor"), av["segunda_opiniao"].get("status"))
                stats[decisao] += 1
                if decisao == regras.AUTO:
                    regras.aplicar_status(p, av["status_sugerido"], doc, av, res.provedor, regras.AUTO)
                    log.info("AUTO  %s → %s (%s)", p["id"], av["status_sugerido"], doc["titulo"])
                elif decisao == regras.FILA:
                    novo_item = regras.item_fila(av, p, doc, res.provedor)
                    if novo_item["id"] not in na_fila:
                        fila.append(novo_item)
                        na_fila.add(novo_item["id"])
                        log.info("FILA  %s → %s? (%s)", p["id"], av["status_sugerido"], doc["titulo"])
        restantes = trabalho[feitos:]
        if not args.dry_run:
            cadeia.uso.salvar()

    if not args.dry_run:
        armazem.gravar_json(armazem.PROMESSAS, promessas)
        armazem.gravar_json(armazem.FILA, fila)
        armazem.gravar_json(armazem.VISTOS, sorted(vistos))
        armazem.gravar_json(armazem.PENDENTES, restantes)
        armazem.gravar_json(armazem.EXECUCAO, {"executado_em": armazem.agora(), "coletores": coletores_ok,
                                               "resumo": stats | {"pendentes": len(restantes)}})
    stats["pendentes"] = len(restantes)
    return stats


def main():
    ap = argparse.ArgumentParser(description="Sync diário das promessas")
    ap.add_argument("--desde", help="AAAA-MM-DD")
    ap.add_argument("--ate", help="AAAA-MM-DD")
    ap.add_argument("--sem-ia", action="store_true", help="só coleta e filtra")
    ap.add_argument("--dry-run", action="store_true", help="não grava nada")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    stats = executar(args)
    log.info("Resumo: %s", stats)


if __name__ == "__main__":
    main()
