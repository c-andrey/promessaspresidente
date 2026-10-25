"""Referências de campanha para as promessas (imprensa, entrevistas, debates).

Duas tarefas, rodadas sob demanda (não fazem parte do sync diário):

  python fontes/buscar_referencias.py referencias   # reportagens em que o candidato fez/repetiu cada promessa
  python fontes/buscar_referencias.py candidatas    # promessas ditas em público que NÃO estão no plano

Busca no Google Notícias (RSS) no período de campanha e usa a cadeia de IAs
(config/provedores.yaml) para separar o que é o candidato prometendo do que é só
cobertura do tema. Resultados:
  data/referencias_campanha.json  → entra em promessas.json pelo gerar_promessas_flavio.py
  data/promessas_candidatas.json  → revisão humana antes de virar promessa
"""
import hashlib
import json
import logging
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from motor import armazem  # noqa: E402
from motor.coletores.http import cliente  # noqa: E402
from motor.coletores.noticias import _itens  # noqa: E402
from motor.llm import Cadeia, SemProvedorDisponivel  # noqa: E402
from motor.llm.erros import RespostaInvalida  # noqa: E402

log = logging.getLogger("referencias")

CANDIDATO = "Flávio Bolsonaro"
PERIODO = ("2026-06-01", "2026-10-25")  # pré-campanha, campanha e 2º turno
REFERENCIAS = RAIZ / "data" / "referencias_campanha.json"
CANDIDATAS = RAIZ / "data" / "promessas_candidatas.json"
URL = "https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
MAX_POR_PROMESSA = 5

BUSCAS_CANDIDATAS = [
    f'"{CANDIDATO}" promete',
    f'"{CANDIDATO}" "se eleito"',
    f'"{CANDIDATO}" proposta',
    f'"{CANDIDATO}" debate',
    f'"{CANDIDATO}" entrevista promete',
    'Flávio "vou acabar com"',
    'Flávio "no meu governo"',
    f'"{CANDIDATO}" sabatina',
]


def buscar(c, consulta: str) -> list[dict]:
    q = f"{consulta} after:{PERIODO[0]} before:{PERIODO[1]}"
    try:
        docs = _itens(c, URL.format(q=quote_plus(q)), "Google Notícias", *PERIODO)
    except Exception as e:  # noqa: BLE001
        log.warning("busca falhou (%s): %s", consulta, e)
        return []
    time.sleep(1)  # educação com o Google Notícias
    return docs


def consulta_da_promessa(p: dict) -> str:
    termos = sorted(p["palavras_chave"], key=lambda k: -len(k))[:3]
    return f'"{CANDIDATO}" (' + " OR ".join(f'"{t}"' for t in termos) + ")"


# --- 1. referências para promessas existentes -----------------------------------

SISTEMA_REF = f"""Você verifica cobertura de campanha eleitoral, de forma apartidária.
Recebe UMA promessa do plano de governo de {CANDIDATO} e uma lista numerada de reportagens (título, veículo, data, resumo).
Marque somente as reportagens em que o próprio candidato, seu vice ou a coordenação da campanha PROMETE, DEFENDE ou DETALHA essa mesma proposta.
NÃO marque: reportagens só sobre o tema, críticas de adversários, análises sem fala da campanha, ou propostas diferentes.
Para cada marcada, escreva em "o_que_disse" uma frase neutra resumindo o que a campanha disse sobre a proposta (sem adjetivos).
Na dúvida, não marque."""

SCHEMA_REF = {
    "type": "object",
    "properties": {"confirmadas": {"type": "array", "items": {
        "type": "object",
        "properties": {"indice": {"type": "integer"}, "o_que_disse": {"type": "string"}},
        "required": ["indice", "o_que_disse"], "additionalProperties": False}}},
    "required": ["confirmadas"], "additionalProperties": False,
}


def lista_numerada(docs: list[dict]) -> str:
    return "\n".join(f"[{i}] {d['titulo']} | {d['fonte']} | {d['data']} | {d['resumo'][:300]}"
                     for i, d in enumerate(docs))


def referencias():
    promessas = armazem.ler_json(armazem.SEMENTE, [])
    feitas = armazem.ler_json(REFERENCIAS, {})
    cadeia = Cadeia.da_config()
    log.info("Provedores: %s", ", ".join(cadeia.ativos()))
    with cliente() as c:
        for n, p in enumerate(promessas, 1):
            if p["id"] in feitas:
                continue
            docs = buscar(c, consulta_da_promessa(p))[:12]
            if not docs:
                feitas[p["id"]] = []
                continue
            usuario = (f"PROMESSA: {p['titulo']}\nTEXTO DO PLANO: {p['fonte']['trecho']}\n\n"
                       f"REPORTAGENS:\n{lista_numerada(docs)}")

            def validar(d, total=len(docs)):
                if any(not 0 <= x["indice"] < total for x in d["confirmadas"]):
                    raise RespostaInvalida("índice fora da lista")

            try:
                r = cadeia.completar_json(SISTEMA_REF, usuario, SCHEMA_REF, validar=validar)
            except SemProvedorDisponivel:
                log.warning("Provedores esgotados: parei em %d/%d (rode de novo amanhã, continua daqui)", n, len(promessas))
                break
            refs = []
            for x in r.dados["confirmadas"][:MAX_POR_PROMESSA]:
                d = docs[x["indice"]]
                refs.append({"veiculo": d["fonte"], "titulo": d["titulo"], "url": d["url"],
                             "data": d["data"], "o_que_disse": x["o_que_disse"]})
            feitas[p["id"]] = sorted(refs, key=lambda x: x["data"], reverse=True)
            log.info("[%d/%d] %s: %d de %d reportagens confirmadas (%s)",
                     n, len(promessas), p["id"], len(refs), len(docs), r.provedor)
            armazem.gravar_json(REFERENCIAS, feitas)  # salva a cada passo: dá para interromper
    cadeia.uso.salvar()
    com = sum(1 for v in feitas.values() if v)
    log.info("Pronto: %d promessas com referência, %d sem, %d faltando",
             com, len(feitas) - com, len(promessas) - len(feitas))


# --- 2. promessas fora do plano -------------------------------------------------

SISTEMA_CAND = f"""Você identifica promessas de campanha de forma apartidária.
Recebe uma lista numerada de reportagens da campanha de {CANDIDATO} e a lista de promessas que JÁ estão no plano de governo (id: título).
Extraia apenas promessas CONCRETAS e VERIFICÁVEIS feitas pelo próprio candidato (não por aliados, assessores ou adversários):
uma ação de governo específica ("vou criar X", "vou acabar com Y", "vou reduzir Z para W").
Ignore frases genéricas ("vamos melhorar a saúde"), críticas e opiniões.
Para cada uma, diga se corresponde a uma promessa já existente (id_existente) ou se é nova (id_existente vazio).
"titulo" no estilo: verbo no infinitivo + ação concreta (ex.: "Criar o programa X para Y")."""

SCHEMA_CAND = {
    "type": "object",
    "properties": {"promessas": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "titulo": {"type": "string"}, "categoria": {"type": "string"},
            "id_existente": {"type": "string"}, "indice": {"type": "integer"},
            "o_que_disse": {"type": "string"}},
        "required": ["titulo", "categoria", "id_existente", "indice", "o_que_disse"],
        "additionalProperties": False}}},
    "required": ["promessas"], "additionalProperties": False,
}


def candidatas():
    promessas = armazem.ler_json(armazem.SEMENTE, [])
    catalogo = "\n".join(f"{p['id']}: {p['titulo']}" for p in promessas)
    ids = {p["id"] for p in promessas}
    categorias = sorted({p["categoria"] for p in promessas})
    cadeia = Cadeia.da_config()
    docs, vistos = [], set()
    with cliente() as c:
        for b in BUSCAS_CANDIDATAS:
            for d in buscar(c, b):
                if d["titulo"].lower() not in vistos:
                    vistos.add(d["titulo"].lower())
                    docs.append(d)
    log.info("%d reportagens encontradas", len(docs))

    achadas = []
    for i in range(0, len(docs), 25):
        lote = docs[i:i + 25]
        usuario = (f"CATEGORIAS POSSÍVEIS: {', '.join(categorias)}\n\nPROMESSAS JÁ NO PLANO:\n{catalogo}\n\n"
                   f"REPORTAGENS:\n{lista_numerada(lote)}")
        try:
            r = cadeia.completar_json(SISTEMA_CAND, usuario, SCHEMA_CAND)
        except SemProvedorDisponivel:
            log.warning("Provedores esgotados no lote %d", i // 25)
            break
        for x in r.dados["promessas"]:
            if not 0 <= x["indice"] < len(lote):
                continue
            d = lote[x["indice"]]
            achadas.append({**{k: x[k] for k in ("titulo", "categoria", "o_que_disse")},
                            "id_existente": x["id_existente"] if x["id_existente"] in ids else "",
                            "fonte": {"veiculo": d["fonte"], "titulo": d["titulo"], "url": d["url"], "data": d["data"]}})
        log.info("lote %d: %d promessas (%s)", i // 25 + 1, len(r.dados["promessas"]), r.provedor)
    cadeia.uso.salvar()

    novas = [a for a in achadas if not a["id_existente"]]
    for a in novas:
        a["id"] = re.sub(r"[^a-z0-9]+", "-", armazem_slug(a["titulo"]))[:50].strip("-")
    armazem.gravar_json(CANDIDATAS, {"novas": novas, "reforcam_existentes": [a for a in achadas if a["id_existente"]]})
    log.info("Candidatas: %d novas, %d reforçam promessas existentes → %s",
             len(novas), len(achadas) - len(novas), CANDIDATAS.relative_to(RAIZ))


def armazem_slug(texto: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", texto.lower())
    return "".join(ch for ch in t if not unicodedata.combining(ch))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    {"referencias": referencias, "candidatas": candidatas}[sys.argv[1] if len(sys.argv) > 1 else "referencias"]()
