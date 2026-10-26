"""Processo do serviço `sync` no docker compose.

Ao subir: semeia/atualiza as promessas no volume a partir do repositório e gera
o site. Depois roda o pipeline todo dia em HORA_SYNC (fuso do contêiner, TZ)
e regera o site. Se o contêiner subir depois do horário e o sync do dia ainda
não rodou, roda na hora.

    docker compose exec sync python -m motor.agendador --agora   # força um sync agora
"""
import argparse
import logging
import os
import time
from datetime import date, datetime, timedelta

from web import build

from . import armazem, campanha, execucao
from . import main as pipeline

log = logging.getLogger("agendador")

HORA_SYNC = os.environ.get("HORA_SYNC", "07:00")
# 1 = todo dia; 2 = a cada 48 h (no mesmo horário)
INTERVALO_DIAS = max(1, int(os.environ.get("INTERVALO_SYNC_DIAS", "1")))
# Campos que vêm do plano de governo: o repositório manda. Status e histórico: o volume manda.
# (referências e declarações não entram aqui: motor/campanha.py mescla sem apagar o que o volume já tem)
CAMPOS_DO_PLANO = ("origem", "titulo", "categoria", "prazo", "fonte", "palavras_chave")


def trocar_presidente_se_preciso() -> None:
    """Se o presidente configurado mudou, arquiva os dados do anterior no volume e recomeça.

    Vai para data/arquivo/<nome>-<data>/: promessas, fila e o estado do pipeline (vistos,
    pendentes, execução). O uso diário das IAs fica, para não estourar a cota do dia.
    """
    nome = armazem.ler_yaml("presidente.yaml")["nome"]
    registrado = armazem.ler_json(armazem.PRESIDENTE_ATUAL, {}).get("nome")
    if registrado == nome:
        return
    if armazem.PROMESSAS.exists() and armazem.SEMENTE.resolve() != armazem.PROMESSAS.resolve():
        destino = armazem.ARQUIVO / f"{(registrado or 'anterior').lower().replace(' ', '-')}-{armazem.hoje()}"
        destino.mkdir(parents=True, exist_ok=True)
        # caminhos da campanha calculados agora, a partir do armazém (não os fixados na importação)
        da_campanha = ([armazem.DADOS / a.name for a in campanha.ARQUIVOS]
                       + [armazem.ESTADO / campanha.ESTADO.name, armazem.ESTADO / execucao.ESTADO.name])
        for caminho in (armazem.PROMESSAS, armazem.FILA, armazem.VISTOS, armazem.PENDENTES,
                        armazem.EXECUCAO, armazem.ESTADO / "rejeitados.json", *da_campanha):
            if caminho.exists():
                caminho.rename(destino / caminho.name)
        log.warning("Presidente mudou (%s → %s): dados anteriores arquivados em %s",
                    registrado or "?", nome, destino)
    armazem.gravar_json(armazem.PRESIDENTE_ATUAL, {"nome": nome, "desde": armazem.hoje()})


def semear() -> None:
    """Copia promessas novas do repositório para o volume e atualiza os textos das existentes."""
    semente = armazem.ler_json(armazem.SEMENTE, [])
    if armazem.SEMENTE.resolve() == armazem.PROMESSAS.resolve():
        return
    atuais = armazem.ler_json(armazem.PROMESSAS, None)
    if atuais is None:
        armazem.gravar_json(armazem.PROMESSAS, semente)
        log.info("Volume vazio: %d promessas copiadas do repositório", len(semente))
        return
    por_id = {p["id"]: p for p in atuais}
    novas = mudou = 0
    for s in semente:
        p = por_id.get(s["id"])
        if p is None:
            atuais.append(s)
            novas += 1
            continue
        for campo in CAMPOS_DO_PLANO:
            if p.get(campo) != s.get(campo):
                p[campo] = s.get(campo)
                mudou += 1
    if novas or mudou:
        armazem.gravar_json(armazem.PROMESSAS, atuais)
        log.info("Promessas sincronizadas com o repositório: %d novas, %d campos atualizados", novas, mudou)


def sync_em_dia() -> bool:
    """Já rodou dentro do intervalo (hoje, ou ontem quando o intervalo é de 2 dias)?"""
    executado = armazem.ler_json(armazem.EXECUCAO, {}).get("executado_em")
    if not executado:
        return False
    return (date.today() - datetime.fromisoformat(executado).astimezone().date()).days < INTERVALO_DIAS


def horario_de_hoje() -> datetime:
    h, m = map(int, HORA_SYNC.split(":"))
    return datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)


def sincronizar() -> None:
    try:
        stats = pipeline.executar(argparse.Namespace(desde=None, ate=None, sem_ia=False, dry_run=False))
        log.info("Sync concluído: %s", stats)
    except Exception:  # noqa: BLE001 — o serviço não pode morrer por um dia ruim
        log.exception("Sync falhou; tenta de novo no próximo horário")
    try:  # cumprimento de cada promessa nas notícias (rodízio), com apuração
        log.info("Acompanhamento por notícias: %s", execucao.sincronizar())
    except Exception:  # noqa: BLE001
        log.exception("Acompanhamento por notícias falhou; tenta de novo no próximo horário")
    try:  # depois: referências e declarações usam o que sobrou da cota
        log.info("Campanha: %s", campanha.sincronizar())
    except Exception:  # noqa: BLE001
        log.exception("Campanha falhou; tenta de novo no próximo horário")
    build.main()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agora", action="store_true", help="roda um sync agora e sai")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    trocar_presidente_se_preciso()
    semear()
    campanha.mesclar_semente()
    campanha.aplicar_no_volume()
    if args.agora:
        sincronizar()
        return

    build.main()
    if not sync_em_dia() and datetime.now() >= horario_de_hoje():
        sincronizar()
    while True:
        alvo = horario_de_hoje()
        if alvo <= datetime.now():
            alvo += timedelta(days=1)
        log.info("Próximo sync: %s", alvo.isoformat(timespec="minutes"))
        time.sleep(max(1, (alvo - datetime.now()).total_seconds()))
        if sync_em_dia():  # intervalo de 2 dias: pula o dia do meio
            log.info("Intervalo de %d dias: sync de hoje pulado", INTERVALO_DIAS)
            continue
        sincronizar()


if __name__ == "__main__":
    main()
