"""Fila de revisão humana.

    python -m motor.revisar                         # lista a fila
    python -m motor.revisar aprovar ID              # aplica o status sugerido
    python -m motor.revisar aprovar ID --status parcial --nota "só 6 ministérios"
    python -m motor.revisar rejeitar ID
"""
import argparse
import sys

from . import armazem, regras
from .trava import trava

REJEITADOS = armazem.ESTADO / "rejeitados.json"


def listar(fila):
    if not fila:
        print("Fila vazia.")
        return
    for i in fila:
        doc = i["documento"]
        print(f"[{i['id']}] {i['promessa_id']}: {i['status_atual']} → {i['status_sugerido']} "
              f"(confiança {i['confianca']:.2f}, {i['provedor']})")
        print(f"    {doc['fonte']} · {doc['titulo']} · {doc['data']}")
        print(f"    {doc['url']}")
        print(f"    evidência: {i['evidencia'][:200]}")
        print(f"    por quê: {i['justificativa']}\n")


def decidir(item_id: str, aprovar: bool, status: str | None = None, nota: str | None = None) -> str:
    """Aprova (aplica o status, ou outro escolhido) ou rejeita um item da fila. Quem chama segura a trava."""
    fila = armazem.ler_json(armazem.FILA, [])
    item = next((i for i in fila if i["id"] == item_id), None)
    if not item:
        raise KeyError(f"Item {item_id} não está na fila.")
    fila.remove(item)
    if aprovar:
        promessas = armazem.ler_json(armazem.PROMESSAS, [])
        p = next(p for p in promessas if p["id"] == item["promessa_id"])
        regras.aplicar_status(p, status or item["status_sugerido"], item["documento"], item,
                              item["provedor"], "revisao", nota or None)
        armazem.gravar_json(armazem.PROMESSAS, promessas)
        msg = f"{p['id']} agora está como {p['status']}."
    else:
        armazem.gravar_json(REJEITADOS, armazem.ler_json(REJEITADOS, []) + [item | {"rejeitado_em": armazem.hoje()}])
        msg = "Rejeitado."
    armazem.gravar_json(armazem.FILA, fila)
    return msg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("acao", nargs="?", choices=["listar", "aprovar", "rejeitar"], default="listar")
    ap.add_argument("id", nargs="?")
    ap.add_argument("--status", choices=armazem.STATUS, help="sobrescreve o status sugerido")
    ap.add_argument("--nota", help="observação do revisor, publicada no histórico")
    args = ap.parse_args()

    fila = armazem.ler_json(armazem.FILA, [])
    if args.acao == "listar":
        listar(fila)
        return

    try:
        with trava():
            print(decidir(args.id, args.acao == "aprovar", args.status, args.nota))
    except KeyError as e:
        sys.exit(e.args[0])


if __name__ == "__main__":
    main()
