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

from . import armazem
from . import main as pipeline

log = logging.getLogger("agendador")

HORA_SYNC = os.environ.get("HORA_SYNC", "07:00")
# Campos que vêm do plano de governo: o repositório manda. Status e histórico: o volume manda.
CAMPOS_DO_PLANO = ("origem", "titulo", "categoria", "prazo", "fonte", "palavras_chave", "referencias")


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


def rodou_hoje() -> bool:
    executado = armazem.ler_json(armazem.EXECUCAO, {}).get("executado_em")
    if not executado:
        return False
    return datetime.fromisoformat(executado).astimezone().date() == date.today()


def horario_de_hoje() -> datetime:
    h, m = map(int, HORA_SYNC.split(":"))
    return datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)


def sincronizar() -> None:
    try:
        stats = pipeline.executar(argparse.Namespace(desde=None, ate=None, sem_ia=False, dry_run=False))
        log.info("Sync concluído: %s", stats)
    except Exception:  # noqa: BLE001 — o serviço não pode morrer por um dia ruim
        log.exception("Sync falhou; tenta de novo no próximo horário")
    build.main()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agora", action="store_true", help="roda um sync agora e sai")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    semear()
    if args.agora:
        sincronizar()
        return

    build.main()
    if not rodou_hoje() and datetime.now() >= horario_de_hoje():
        sincronizar()
    while True:
        alvo = horario_de_hoje()
        if alvo <= datetime.now():
            alvo += timedelta(days=1)
        log.info("Próximo sync: %s", alvo.isoformat(timespec="minutes"))
        time.sleep(max(1, (alvo - datetime.now()).total_seconds()))
        sincronizar()


if __name__ == "__main__":
    main()
