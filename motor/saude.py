"""Saúde do sync: o que deu errado na última execução, para o aviso diário e o check do GitHub Actions.

    python -m motor.saude        # sai com erro se o sync atrasou, uma etapa falhou ou uma chave foi recusada
"""
import os
import sys
from datetime import datetime, timezone

from . import armazem

# Preenchida durante o sync (cadeia de IA, coletores); o agendador grava e limpa no fim.
OCORRENCIAS: list[dict] = []
# chave recusada é grave (precisa de ação); IA esgotada só atrasa o trabalho para o dia seguinte
GRAVES = {"chave_recusada", "coletor"}
FOLGA_HORAS = 6
DIAS_COLETOR_FORA = 3  # fonte fora por mais tempo que isso vira problema grave


def caminho():
    return armazem.ESTADO / "saude.json"


def registrar(tipo: str, detalhe: str) -> None:
    item = {"tipo": tipo, "detalhe": detalhe}
    if item not in OCORRENCIAS:
        OCORRENCIAS.append(item)


def gravar(inicio: str, etapas: dict) -> dict:
    estado = {"inicio": inicio, "fim": armazem.agora(), "etapas": etapas, "ocorrencias": list(OCORRENCIAS)}
    armazem.gravar_json(caminho(), estado)
    OCORRENCIAS.clear()
    return estado


def problemas(estado: dict | None, agora: datetime, intervalo_dias: int) -> tuple[list[str], list[str]]:
    """(graves, avisos) em frases curtas."""
    if not estado:
        return ["o sync ainda não terminou nenhuma vez"], []
    graves, avisos = [], []
    horas = (agora - datetime.fromisoformat(estado["fim"])).total_seconds() / 3600
    if horas > intervalo_dias * 24 + FOLGA_HORAS:
        graves.append(f"o último sync terminou há {horas:.0f} h")
    falhas = [nome for nome, r in estado["etapas"].items() if r == "falhou"]
    if falhas:
        graves.append("etapas com erro: " + ", ".join(falhas))
    for o in estado["ocorrencias"]:
        frase = {"chave_recusada": "chave de IA recusada", "coletor": "fonte fora do ar", "coletor_instavel": "fonte instável hoje",
                 "sem_ia": "todas as IAs esgotaram"}.get(o["tipo"], o["tipo"])
        (graves if o["tipo"] in GRAVES else avisos).append(f"{frase}: {o['detalhe']}")
    return graves, avisos


def main():
    intervalo = max(1, int(os.environ.get("INTERVALO_SYNC_DIAS", "1")))
    estado = armazem.ler_json(caminho(), None)
    if estado is None and (fim := armazem.ler_json(armazem.EXECUCAO, {}).get("executado_em")):
        estado = {"inicio": fim, "fim": fim, "etapas": {}, "ocorrencias": []}  # sync de antes deste registro
    graves, avisos = problemas(estado, datetime.now(timezone.utc), intervalo)
    if estado:
        print(f"último sync: {estado['inicio']} → {estado['fim']} · etapas: {estado['etapas']}")
    for a in avisos:
        print(f"aviso: {a}")
    for g in graves:
        print(f"PROBLEMA: {g}")
    sys.exit(1 if graves else 0)


if __name__ == "__main__":
    main()
