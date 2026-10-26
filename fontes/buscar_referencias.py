"""Referências de campanha para as promessas do plano (imprensa, entrevistas, debates).

Rodado sob demanda (não faz parte do sync diário):

  python fontes/buscar_referencias.py    # reportagens em que o candidato fez/repetiu cada promessa

Promessas ditas em público que NÃO estão no plano: fontes/declaracoes.py.

Busca no Google Notícias (RSS) no período de campanha e usa a cadeia de IAs
(config/provedores.yaml) para separar o que é o candidato prometendo do que é só
cobertura do tema. Resultados:
  data/referencias_campanha.json  → entra em promessas.json pelo gerar_promessas.py
"""
import logging
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

CANDIDATO = armazem.ler_yaml("presidente.yaml")["nome_busca"]
PERIODO = ("2026-06-01", "2026-10-25")  # pré-campanha, campanha e 2º turno
REFERENCIAS = RAIZ / "data" / "referencias_campanha.json"
URL = "https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
MAX_POR_PROMESSA = 5


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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    referencias()
