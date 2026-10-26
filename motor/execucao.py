"""Acompanhamento por promessa nas notícias: o governo fez algo relacionado?

Rodízio diário (as promessas verificadas há mais tempo primeiro). Para cada promessa:
  1. busca no Google Notícias pelo presidente/governo + palavras-chave da promessa,
     desde a última verificação (na primeira vez, desde o início da campanha);
  2. a IA marca as matérias relacionadas → viram links "Na imprensa" (URL real da matéria);
  3. se alguma matéria indica mudança de status, entra a APURAÇÃO (texto inteiro, citação
     literal conferida por código, segunda opinião de outro provedor) e a sugestão vai
     para a fila de revisão já com o resultado. Notícia nunca muda status sozinha.

  python -m motor.execucao [--todas]     # --todas: passa por todas as promessas agora
"""
import argparse
import logging
from datetime import date

from . import apuracao, armazem, regras
from .campanha import CadeiaComTeto, buscar, lista_numerada
from .coletores.http import cliente
from .llm import Cadeia, SemProvedorDisponivel
from .llm.erros import RespostaInvalida

log = logging.getLogger("execucao")

ESTADO = armazem.ESTADO / "execucao.json"
DESCRICAO = {
    "em_andamento": "deu um passo concreto (enviou projeto, publicou decreto, lançou ou estruturou programa) para cumprir",
    "parcial": "cumpriu em parte (parte da medida já está em vigor)",
    "cumprida": "cumpriu (a medida prometida está em vigor)",
    "abandonada": "desistiu, vetou, retirou ou agiu no sentido contrário de",
}

SCHEMA = {
    "type": "object",
    "properties": {"materias": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "indice": {"type": "integer"},
            "relacionada": {"type": "boolean"},
            "indica": {"type": "string", "enum": ["nada", "em_andamento", "parcial", "cumprida", "abandonada"]},
            "confianca": {"type": "number"},
            "resumo": {"type": "string"}},
        "required": ["indice", "relacionada", "indica", "confianca", "resumo"],
        "additionalProperties": False}}},
    "required": ["materias"], "additionalProperties": False,
}


def sistema(presidente: dict) -> str:
    return f"""Você acompanha, de forma apartidária, se o governo de {presidente['nome']} cumpre uma promessa.
Recebe UMA promessa e reportagens numeradas (título, veículo, data, resumo). Para cada reportagem:
- relacionada: trata desta promessa (ação, debate, projeto, programa, crítica sobre ela)? Na dúvida, false.
- indica: o que a reportagem mostra sobre o GOVERNO FEDERAL em relação à promessa:
  nada | em_andamento (passo concreto) | parcial (parte em vigor) | cumprida (em vigor) | abandonada (desistiu/vetou/fez o contrário).
  Declaração ou intenção não é passo concreto: use nada. Atos anteriores a {regras.inicio_acompanhamento(presidente)} não contam: use nada.
  Antes da posse ({presidente['mandato_inicio']}), anúncio da transição ou plano para o próximo mandato também é nada; ato publicado conta.
- confianca: 0 a 1. resumo: uma frase neutra sobre o que a reportagem diz da promessa."""


def acompanhar(cadeia, presidente, cfg, promessas, fila, estado, quantas: int | None) -> dict:
    nome = presidente["nome_busca"]
    verificadas = estado.setdefault("verificadas", {})
    na_fila = {i["id"] for i in fila}
    hoje = armazem.hoje()
    inicio_padrao = cfg.get("buscar_desde", "2026-06-01")
    ordem = sorted(promessas, key=lambda p: verificadas.get(p["id"], ""))[:quantas]
    stats = {"verificadas": 0, "links": 0, "sugestoes": 0}
    with cliente() as c:
        for p in ordem:
            termos = sorted(p["palavras_chave"], key=lambda k: -len(k))[:3]
            consulta = f'("{nome}" OR "governo federal") (' + " OR ".join(f'"{t}"' for t in termos) + ")"
            docs = buscar(c, consulta, (verificadas.get(p["id"], inicio_padrao), hoje))[:cfg["max_noticias_por_promessa"]]
            if docs:
                usuario = (f"PROMESSA: {p['titulo']}\nTEXTO ORIGINAL: {p['fonte']['trecho']}\n"
                           f"STATUS ATUAL: {p['status']}\n\nREPORTAGENS:\n{lista_numerada(docs)}")

                def validar(d, total=len(docs)):
                    if any(not 0 <= m["indice"] < total or not 0 <= m["confianca"] <= 1 for m in d["materias"]):
                        raise RespostaInvalida("índice ou confiança fora do intervalo")

                try:
                    r = cadeia.completar_json(sistema(presidente), usuario, SCHEMA, validar=validar)
                except SemProvedorDisponivel as e:
                    log.warning("Acompanhamento por notícias: parei em %s (%s)", p["id"], e)
                    break
                sugestoes = {}
                for m in r.dados["materias"]:
                    if not m["relacionada"]:
                        continue
                    d = docs[m["indice"]]
                    regras.registrar_imprensa(p, {**d, "url": apuracao.resolver_url(c, d["url"])}, {"justificativa": m["resumo"]})
                    stats["links"] += 1
                    if (m["indica"] != "nada" and m["indica"] != p["status"] and m["confianca"] >= 0.5
                            and d["data"] >= regras.inicio_acompanhamento(presidente)):
                        sugestoes.setdefault(m["indica"], []).append((d, m))
                for status, itens in sugestoes.items():
                    fontes = [{**d, "veiculo": d["fonte"]} for d, _ in itens]
                    try:
                        apurado = apuracao.apurar(cadeia, c, f"O governo de {nome} {DESCRICAO[status]} a promessa: {p['titulo']}",
                                                  fontes, p["palavras_chave"], evitar=(r.provedor,))
                    except SemProvedorDisponivel:
                        apurado = {"status": "nao_apurada"}
                    d, m = max(itens, key=lambda x: x[1]["confianca"])
                    av = {"status_sugerido": status, "confianca": m["confianca"],
                          "evidencia": (apurado.get("trechos") or [{"trecho": m["resumo"]}])[0]["trecho"],
                          "justificativa": m["resumo"]}
                    item = regras.item_fila(av, p, {**d, "oficial": False, "id": d.get("id") or d["url"]}, r.provedor) | {
                        "origem": "noticias", "apuracao": apurado}
                    if item["id"] not in na_fila and apurado["status"] != "contradita":
                        fila.append(item)
                        na_fila.add(item["id"])
                        stats["sugestoes"] += 1
                        log.info("FILA (notícias, apuração: %s) %s → %s?", apurado["status"], p["id"], status)
            verificadas[p["id"]] = hoje
            stats["verificadas"] += 1
    return stats


def sincronizar(quantas: int | None = None) -> dict:
    cfg = armazem.ler_yaml("coleta.yaml").get("acompanhamento_noticias", {})
    if not cfg.get("ativo", True):
        return {}
    presidente = armazem.ler_yaml("presidente.yaml")
    promessas = armazem.ler_json(armazem.PROMESSAS, [])
    fila = armazem.ler_json(armazem.FILA, [])
    estado = armazem.ler_json(ESTADO, {})
    base = Cadeia.da_config()
    cadeia = CadeiaComTeto(base, cfg.get("max_chamadas_ia_por_dia"))
    stats = acompanhar(cadeia, presidente, cfg, promessas, fila, estado,
                       quantas if quantas is not None else cfg["promessas_por_dia"])
    stats["chamadas_ia"] = cadeia.usadas
    base.uso.salvar()
    armazem.gravar_json(armazem.PROMESSAS, promessas)
    armazem.gravar_json(armazem.FILA, fila)
    armazem.gravar_json(ESTADO, estado)
    log.info("Acompanhamento por notícias: %s", stats)
    return stats


def main():
    ap = argparse.ArgumentParser(description="Acompanhamento por promessa nas notícias")
    ap.add_argument("--todas", action="store_true", help="todas as promessas agora (sem teto diário)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for ruido in ("httpx", "motor.llm.cadeia"):
        logging.getLogger(ruido).setLevel(logging.WARNING)
    if args.todas:
        cfg = armazem.ler_yaml("coleta.yaml").get("acompanhamento_noticias", {})
        cfg_sem_teto = {**cfg, "max_chamadas_ia_por_dia": None}
        presidente = armazem.ler_yaml("presidente.yaml")
        promessas, fila, estado = (armazem.ler_json(armazem.PROMESSAS, []), armazem.ler_json(armazem.FILA, []),
                                   armazem.ler_json(ESTADO, {}))
        base = Cadeia.da_config()
        print(acompanhar(base, presidente, cfg_sem_teto, promessas, fila, estado, None))
        base.uso.salvar()
        armazem.gravar_json(armazem.PROMESSAS, promessas)
        armazem.gravar_json(armazem.FILA, fila)
        armazem.gravar_json(ESTADO, estado)
    else:
        print(sincronizar())


if __name__ == "__main__":
    main()
