"""Fila de revisão humana.

    python -m motor.revisar                         # lista a fila
    python -m motor.revisar aprovar ID              # aplica o status sugerido
    python -m motor.revisar aprovar ID --status parcial --nota "só 6 ministérios"
    python -m motor.revisar rejeitar ID
"""
import argparse
import sys

from . import armazem, regras

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

    item = next((i for i in fila if i["id"] == args.id), None)
    if not item:
        sys.exit(f"Item {args.id} não está na fila.")
    fila.remove(item)

    if args.acao == "aprovar":
        promessas = armazem.ler_json(armazem.PROMESSAS, [])
        p = next(p for p in promessas if p["id"] == item["promessa_id"])
        regras.aplicar_status(p, args.status or item["status_sugerido"], item["documento"], item,
                              item["provedor"], "revisao", args.nota)
        armazem.gravar_json(armazem.PROMESSAS, promessas)
        print(f"{p['id']} agora está como {p['status']}.")
    else:
        armazem.gravar_json(REJEITADOS, armazem.ler_json(REJEITADOS, []) + [item])
        print("Rejeitado.")
    armazem.gravar_json(armazem.FILA, fila)


if __name__ == "__main__":
    main()
