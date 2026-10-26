"""Gera data/promessas.json para o presidente configurado em config/presidente.yaml.

Fontes, por presidente, em fontes/<pasta>/:
  plano.pdf / plano.txt   plano de governo registrado no TSE e o texto extraído
  promessas.py            lista P (id, categoria, título, página, trecho literal, prazo, palavras-chave) e URL_TSE

Cada promessa do plano carrega a página e um trecho literal do PDF: o script confere se
todo trecho existe no texto extraído e aborta se não existir. Também junta:
  data/referencias_campanha.json   reportagens da campanha (fontes/buscar_referencias.py)
  data/promessas_declaracoes.json  declarações aprovadas fora do plano (fontes/declaracoes.py)

Uso: python fontes/gerar_promessas.py
"""
import importlib.util
import json
import re
import sys
import unicodedata
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
SAIDA = RAIZ / "data" / "promessas.json"
REFERENCIAS = RAIZ / "data" / "referencias_campanha.json"
DECLARACOES = RAIZ / "data" / "promessas_declaracoes.json"


def normalizar(t: str) -> str:
    t = unicodedata.normalize("NFKC", t)
    t = t.replace("\t", " ").replace(" ", " ")
    return re.sub(r"\s+", " ", t).strip()


def texto_do_plano(caminho: Path) -> str:
    texto = normalizar(caminho.read_text(encoding="utf-8"))
    # O PDF quebra frases entre páginas: tira os marcadores (e o número da página) para a checagem.
    texto = re.sub(r"=== PAGINA \d+ === \d+ (PROGRAMA DE GOVERNO )?", "", texto)
    return re.sub(r"=== PAGINA \d+ === ", "", texto)


def carregar_lista(pasta: Path):
    spec = importlib.util.spec_from_file_location("promessas_presidente", pasta / "promessas.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo.P, modulo.URL_TSE


def main():
    cfg = yaml.safe_load((RAIZ / "config" / "presidente.yaml").read_text(encoding="utf-8"))
    pasta = RAIZ / cfg["plano_de_governo"]["pasta"]
    lista, url_tse = carregar_lista(pasta)
    texto = texto_do_plano(pasta / "plano.txt")

    ids, faltando, promessas = set(), [], []
    for (pid, cat, titulo, pag, trecho, prazo, kws) in lista:
        assert pid not in ids, f"id duplicado: {pid}"
        ids.add(pid)
        if normalizar(trecho) not in texto:
            faltando.append(pid)
        promessas.append({
            "id": pid,
            "origem": "plano_tse",
            "titulo": titulo,
            "categoria": cat,
            "prazo": prazo,
            "fonte": {"tipo": "plano_de_governo_tse", "url": url_tse, "pagina": pag, "trecho": trecho},
            "palavras_chave": sorted(set(k.lower() for k in kws)),
            "status": "nao_iniciada",
            "atualizado_em": None,
            "referencias": [],
            "historico": [],
        })
    if faltando:
        print("Trechos não encontrados no PDF:", *faltando, sep="\n  ")
        sys.exit(1)

    for d in json.loads(DECLARACOES.read_text(encoding="utf-8")) if DECLARACOES.exists() else []:
        assert d["id"] not in ids, f"id de declaração repete o plano: {d['id']}"
        principal, *outras = d["fontes"]
        promessas.append({
            "id": d["id"],
            "origem": "declaracao",
            "titulo": d["titulo"],
            "categoria": d["categoria"],
            "prazo": None,
            "fonte": {"tipo": "declaracao", "contexto": d["contexto"], "url": principal["url"],
                      "veiculo": principal["veiculo"], "titulo": principal["titulo"],
                      "data": principal["data"], "trecho": d["o_que_disse"]},
            "palavras_chave": d["palavras_chave"],
            "status": "nao_iniciada",
            "atualizado_em": None,
            "referencias": [f | {"o_que_disse": d["o_que_disse"]} for f in outras],
            "historico": [],
        })

    # Preserva status/histórico/imprensa já registrados (mesmo presidente, mesmo id).
    if SAIDA.exists():
        antigos = {p["id"]: p for p in json.loads(SAIDA.read_text(encoding="utf-8"))}
        for p in promessas:
            for campo in ("status", "atualizado_em", "historico", "imprensa"):
                if campo in antigos.get(p["id"], {}):
                    p[campo] = antigos[p["id"]][campo]

    referencias = json.loads(REFERENCIAS.read_text(encoding="utf-8")) if REFERENCIAS.exists() else {}
    for p in promessas:
        if p["origem"] == "plano_tse":
            p["referencias"] = referencias.get(p["id"], [])

    SAIDA.write_text(json.dumps(promessas, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plano = sum(1 for p in promessas if p["origem"] == "plano_tse")
    print(f"{cfg['nome']}: {len(promessas)} promessas em {SAIDA.relative_to(RAIZ)} — "
          f"{plano} do plano (trechos conferidos no PDF) + {len(promessas) - plano} declarações aprovadas")


if __name__ == "__main__":
    main()
