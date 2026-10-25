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
    for d in novos:
        c = candidatas(d, promessas, cfg["max_promessas_por_documento"])
        if c:
            trabalho.append({"doc": d, "candidatas": c})
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
                decisao = regras.decidir(av, p, doc, cfg["regras"], presidente)
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
