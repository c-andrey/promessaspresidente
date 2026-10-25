"""Promessas ditas em público (entrevistas, debates, sabatinas, discursos) que NÃO estão no plano do TSE.

  python fontes/declaracoes.py buscar              # busca, extrai e consolida → data/promessas_candidatas.json
  python fontes/declaracoes.py listar              # candidatas pendentes, mais citadas primeiro
  python fontes/declaracoes.py aprovar ID [ID...]  # vira promessa (origem "declaracao")
  python fontes/declaracoes.py rejeitar ID [ID...]
  python fontes/gerar_promessas_flavio.py          # depois de aprovar: regenera data/promessas.json

Etapas do "buscar":
  1. buscas direcionadas no Google Notícias no período de campanha (debates por emissora,
     sabatinas, entrevistas, "promete", "se eleito"…);
  2. a cadeia de IAs extrai só promessas concretas ditas pelo próprio candidato;
  3. o que repete uma promessa do plano vira referência dela (data/referencias_campanha.json);
  4. o resto é consolidado (a mesma promessa em 5 jornais = 1 candidata com 5 fontes) e
     espera revisão humana. Nada entra no site sem "aprovar".
"""
import argparse
import copy
import logging
import re
import sys
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from buscar_referencias import (CANDIDATO, REFERENCIAS, buscar,  # noqa: E402
                                lista_numerada)

from motor import armazem  # noqa: E402
from motor.coletores.http import cliente  # noqa: E402
from motor.llm import Cadeia, SemProvedorDisponivel  # noqa: E402
from motor.llm.erros import RespostaInvalida  # noqa: E402

log = logging.getLogger("declaracoes")

CANDIDATAS = RAIZ / "data" / "promessas_candidatas.json"
APROVADAS = RAIZ / "data" / "promessas_declaracoes.json"
REJEITADAS = RAIZ / "data" / "declaracoes_rejeitadas.json"
MAX_REFERENCIAS = 8
LOTE_EXTRACAO = 20
LOTE_CONSOLIDACAO = 50

C = CANDIDATO
BUSCAS = [
    f'"{C}" promete', f'"{C}" "se eleito"', f'"{C}" propõe', f'"{C}" anuncia',
    f'"{C}" "vou criar"', f'"{C}" "vamos acabar"', f'"{C}" "primeiro dia" governo',
    f'"{C}" debate', f'"{C}" debate Band', f'"{C}" debate Globo', f'"{C}" debate SBT',
    f'"{C}" debate Record', f'"{C}" sabatina', f'"{C}" entrevista Jornal Nacional',
    f'"{C}" entrevista', f'"{C}" podcast', f'"{C}" live', f'"{C}" discurso comício',
    f'"{C}" horário eleitoral', f'"{C}" carta compromisso',
]
CONTEXTOS = ["entrevista", "debate", "sabatina", "discurso", "rede_social", "propaganda_eleitoral", "outro"]


PALAVRAS_VAZIAS = {"o", "a", "os", "as", "de", "do", "da", "dos", "das", "para", "e", "em", "no", "na",
                   "nos", "nas", "um", "uma", "com", "por", "ao", "aos", "sobre"}


def slug(texto: str) -> str:
    """"Retomar o benefício fiscal para eventos culturais" → "retomar-beneficio-fiscal-eventos-culturais"."""
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    palavras = [w for w in re.split(r"[^a-z0-9]+", t) if w and w not in PALAVRAS_VAZIAS]
    return "-".join(palavras[:6])


# --- 2. extração ------------------------------------------------------------------

SISTEMA_EXTRACAO = f"""Você identifica promessas de campanha de forma apartidária.
Recebe reportagens numeradas sobre a campanha de {C} e a lista de promessas que JÁ estão no plano de governo dele (id: título).
Extraia apenas promessas CONCRETAS e VERIFICÁVEIS ditas pelo PRÓPRIO candidato (não por vice, aliados, assessores ou adversários):
uma ação de governo específica ("vou criar X", "vou acabar com Y", "vou reduzir Z para W", "no primeiro dia vou...").
Ignore frases genéricas ("vamos melhorar a saúde"), críticas a adversários, opiniões e promessas atribuídas a terceiros.
Para cada promessa:
- id_existente: o id da promessa do plano que ela repete, ou "" se não estiver no plano;
- titulo: verbo no infinitivo + ação concreta (ex.: "Criar o programa X para Y");
- contexto: onde foi dita ({", ".join(CONTEXTOS)});
- o_que_disse: uma frase neutra resumindo o que ele disse;
- palavras_chave: 3 a 6 termos ESPECÍFICOS que apareceriam numa lei, decreto ou notícia sobre o cumprimento
  (nomes de programas, siglas, expressões de 2+ palavras como "escala 6x1"). Nunca termos genéricos
  sozinhos ("lei", "imposto", "reajuste", "economia", "governo")."""

def com_categorias(schema: dict, categorias: list[str]) -> dict:
    """Cópia do schema com "categoria" restrita às categorias do site."""
    s = copy.deepcopy(schema)
    itens = next(iter(s["properties"].values()))["items"]
    itens["properties"]["categoria"] = {"type": "string", "enum": categorias}
    return s


SCHEMA_EXTRACAO = {
    "type": "object",
    "properties": {"promessas": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "indice": {"type": "integer"}, "id_existente": {"type": "string"},
            "titulo": {"type": "string"}, "categoria": {"type": "string"},
            "contexto": {"type": "string", "enum": CONTEXTOS}, "o_que_disse": {"type": "string"},
            "palavras_chave": {"type": "array", "items": {"type": "string"}}},
        "required": ["indice", "id_existente", "titulo", "categoria", "contexto", "o_que_disse", "palavras_chave"],
        "additionalProperties": False}}},
    "required": ["promessas"], "additionalProperties": False,
}

# --- 4. consolidação --------------------------------------------------------------

SISTEMA_CONSOLIDACAO = """Você agrupa promessas de campanha repetidas.
Recebe promessas numeradas extraídas de reportagens diferentes e uma lista de promessas JÁ CONHECIDAS (id: título).
Agrupe as que são a MESMA proposta (mesma ação de governo, mesmo que com palavras diferentes). Propostas parecidas mas diferentes ficam em grupos separados.
Para cada grupo: os índices, um título final (verbo no infinitivo + ação concreta), a categoria e, se o grupo for uma promessa já conhecida, o id dela em id_conhecida (senão "").
Todo índice deve aparecer em exatamente um grupo."""

SCHEMA_CONSOLIDACAO = {
    "type": "object",
    "properties": {"grupos": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "indices": {"type": "array", "items": {"type": "integer"}},
            "titulo": {"type": "string"}, "categoria": {"type": "string"}, "id_conhecida": {"type": "string"}},
        "required": ["indices", "titulo", "categoria", "id_conhecida"], "additionalProperties": False}}},
    "required": ["grupos"], "additionalProperties": False,
}


def _coletar() -> list[dict]:
    docs, vistos = [], set()
    with cliente() as c:
        for b in BUSCAS:
            achados = buscar(c, b)
            novos = 0
            for d in achados:
                if d["titulo"].lower() not in vistos and d["url"] not in vistos:
                    vistos.update((d["titulo"].lower(), d["url"]))
                    docs.append(d)
                    novos += 1
            log.info("busca %-45s %3d resultados, %3d novos", b[:45], len(achados), novos)
    return docs


def _extrair(cadeia, docs, plano) -> list[dict]:
    catalogo = "\n".join(f"{p['id']}: {p['titulo']}" for p in plano)
    categorias = sorted({p["categoria"] for p in plano})
    schema = com_categorias(SCHEMA_EXTRACAO, categorias)
    ids = {p["id"] for p in plano}
    achadas = []
    for i in range(0, len(docs), LOTE_EXTRACAO):
        lote = docs[i:i + LOTE_EXTRACAO]
        usuario = (f"CATEGORIAS: {', '.join(categorias)}\n\nPROMESSAS DO PLANO:\n{catalogo}\n\n"
                   f"REPORTAGENS:\n{lista_numerada(lote)}")

        def validar(d, total=len(lote)):
            if any(not 0 <= x["indice"] < total for x in d["promessas"]):
                raise RespostaInvalida("índice fora do lote")

        try:
            r = cadeia.completar_json(SISTEMA_EXTRACAO, usuario, schema, validar=validar)
        except SemProvedorDisponivel:
            log.warning("Provedores esgotados na extração (lote %d); seguindo com o que já foi extraído", i // LOTE_EXTRACAO + 1)
            break
        for x in r.dados["promessas"]:
            d = lote[x["indice"]]
            achadas.append({**x, "id_existente": x["id_existente"] if x["id_existente"] in ids else "",
                            "fonte": {k: d[k] for k in ("titulo", "url", "data")} | {"veiculo": d["fonte"]}})
        log.info("extração lote %d/%d: %d promessas (%s)", i // LOTE_EXTRACAO + 1,
                 -(-len(docs) // LOTE_EXTRACAO), len(r.dados["promessas"]), r.provedor)
    return achadas


def _reforcar_plano(achadas) -> int:
    """Declarações que repetem promessa do plano viram referência dela."""
    refs = armazem.ler_json(REFERENCIAS, {})
    novas = 0
    for a in achadas:
        lista = refs.setdefault(a["id_existente"], [])
        if any(r["url"] == a["fonte"]["url"] for r in lista) or len(lista) >= MAX_REFERENCIAS:
            continue
        lista.append(a["fonte"] | {"o_que_disse": a["o_que_disse"]})
        lista.sort(key=lambda r: r["data"], reverse=True)
        novas += 1
    armazem.gravar_json(REFERENCIAS, refs)
    return novas


def _consolidar(cadeia, novas, conhecidas: dict[str, str], categorias: list[str]) -> list[dict]:
    """Agrupa a mesma promessa vinda de várias reportagens; liga a candidatas já conhecidas."""
    lista_conhecidas = "\n".join(f"{k}: {v}" for k, v in conhecidas.items()) or "(nenhuma)"
    schema = com_categorias(SCHEMA_CONSOLIDACAO, categorias)
    grupos = []
    for i in range(0, len(novas), LOTE_CONSOLIDACAO):
        lote = novas[i:i + LOTE_CONSOLIDACAO]
        itens = "\n".join(f"[{j}] {a['titulo']} — {a['o_que_disse'][:160]}" for j, a in enumerate(lote))

        def validar(d, total=len(lote)):
            if any(not 0 <= j < total for g in d["grupos"] for j in g["indices"]):
                raise RespostaInvalida("índice fora do lote")

        try:
            r = cadeia.completar_json(SISTEMA_CONSOLIDACAO,
                                      f"CATEGORIAS: {', '.join(categorias)}\n\nJÁ CONHECIDAS:\n{lista_conhecidas}\n\nPROMESSAS:\n{itens}",
                                      schema, validar=validar)
            usados = set()
            for g in r.dados["grupos"]:
                membros = [lote[j] for j in dict.fromkeys(g["indices"]) if j not in usados]
                usados.update(g["indices"])
                if membros:
                    grupos.append({**g, "membros": membros})
            # índice esquecido pela IA vira grupo próprio
            grupos += [{"titulo": a["titulo"], "categoria": a["categoria"], "id_conhecida": "", "membros": [a]}
                       for j, a in enumerate(lote) if j not in usados]
        except SemProvedorDisponivel:
            log.warning("Provedores esgotados na consolidação: lote sem agrupar")
            grupos += [{"titulo": a["titulo"], "categoria": a["categoria"], "id_conhecida": "", "membros": [a]} for a in lote]
    return grupos


def cmd_buscar(_args):
    plano = armazem.ler_json(armazem.SEMENTE, [])
    plano = [p for p in plano if p.get("origem", "plano_tse") == "plano_tse"]
    candidatas = {c["id"]: c for c in armazem.ler_json(CANDIDATAS, [])}
    aprovadas = {c["id"]: c for c in armazem.ler_json(APROVADAS, [])}
    rejeitadas = {c["id"]: c for c in armazem.ler_json(REJEITADAS, [])}
    cadeia = Cadeia.da_config()
    log.info("Provedores: %s", ", ".join(cadeia.ativos()))

    docs = _coletar()
    log.info("%d reportagens únicas", len(docs))
    achadas = _extrair(cadeia, docs, plano)
    do_plano = [a for a in achadas if a["id_existente"]]
    fora = [a for a in achadas if not a["id_existente"]]
    log.info("%d declarações repetem o plano (%d referências novas), %d fora do plano",
             len(do_plano), _reforcar_plano(do_plano), len(fora))

    conhecidas = {k: v["titulo"] for d in (candidatas, aprovadas, rejeitadas) for k, v in d.items()}
    for g in _consolidar(cadeia, fora, conhecidas, sorted({p["categoria"] for p in plano})):
        fontes = list({m["fonte"]["url"]: m["fonte"] for m in g["membros"]}.values())
        alvo_id = g["id_conhecida"] if g["id_conhecida"] in conhecidas else slug(g["titulo"])
        if alvo_id in rejeitadas:
            continue
        base = aprovadas.get(alvo_id) or candidatas.get(alvo_id)
        if base:  # mais fontes para algo já conhecido
            urls = {f["url"] for f in base["fontes"]}
            base["fontes"] += [f for f in fontes if f["url"] not in urls]
            continue
        principal = max(g["membros"], key=lambda m: len(m["o_que_disse"]))
        candidatas[alvo_id] = {
            "id": alvo_id, "titulo": g["titulo"], "categoria": g["categoria"],
            "contexto": principal["contexto"], "o_que_disse": principal["o_que_disse"],
            "palavras_chave": sorted({k.lower() for m in g["membros"] for k in m["palavras_chave"]})[:10],
            "fontes": fontes, "status": "pendente",
        }
    cadeia.uso.salvar()
    ordem = sorted(candidatas.values(), key=lambda c: -len(c["fontes"]))
    armazem.gravar_json(CANDIDATAS, ordem)
    armazem.gravar_json(APROVADAS, list(aprovadas.values()))
    log.info("%d candidatas pendentes em %s", len(ordem), CANDIDATAS.relative_to(RAIZ))


def cmd_listar(_args):
    for c in armazem.ler_json(CANDIDATAS, []):
        f = c["fontes"][0]
        print(f"[{c['id']}] {c['titulo']}  ({c['categoria']}, {c['contexto']}, {len(c['fontes'])} fonte(s))")
        print(f"    {c['o_que_disse']}")
        print(f"    {f['veiculo']} {f['data']}: {f['titulo'][:90]}\n")


def _mover(ids, destino):
    candidatas = armazem.ler_json(CANDIDATAS, [])
    alvo = armazem.ler_json(destino, [])
    for i in ids:
        c = next((c for c in candidatas if c["id"] == i), None)
        if not c:
            print(f"não está entre as candidatas: {i}")
            continue
        candidatas.remove(c)
        alvo.append(c | {"status": "aprovada" if destino == APROVADAS else "rejeitada"})
        print(f"{'aprovada' if destino == APROVADAS else 'rejeitada'}: {i}")
    armazem.gravar_json(CANDIDATAS, candidatas)
    armazem.gravar_json(destino, alvo)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("buscar").set_defaults(f=cmd_buscar)
    sub.add_parser("listar").set_defaults(f=cmd_listar)
    for nome, destino in (("aprovar", APROVADAS), ("rejeitar", REJEITADAS)):
        p = sub.add_parser(nome)
        p.add_argument("ids", nargs="+")
        p.set_defaults(f=lambda a, d=destino: _mover(a.ids, d))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for ruido in ("httpx", "motor.llm.cadeia"):
        logging.getLogger(ruido).setLevel(logging.WARNING)
    args.f(args)


if __name__ == "__main__":
    main()
