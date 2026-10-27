"""O que o presidente disse que ia fazer, além do plano do TSE: referências e declarações.

Duas frentes, rodadas todo dia pelo sync (agendador) e também à mão:

  Referências   para cada promessa do plano, reportagens em que ele prometeu, defendeu ou
                detalhou aquela proposta (rodízio: algumas promessas por dia).
  Declarações   promessas ditas em entrevistas, debates, discursos… que NÃO estão no plano.
                Busca → filtro de manchetes (sem IA) → extração (IA) → consolidação das
                repetidas → aprovação automática se confirmada por N veículos; senão, fila.

  python -m motor.campanha sincronizar              # o mesmo que o sync diário faz
  python -m motor.campanha buscar [--amplo]         # declarações (--amplo: pesquisa grande)
  python -m motor.campanha referencias [--todas]    # referências (--todas: período inteiro)
  python -m motor.campanha listar                   # candidatas pendentes, mais citadas primeiro
  python -m motor.campanha resolver-links           # links do Google Notícias → URL real da matéria
  python -m motor.campanha reconsolidar             # junta candidatas que são a mesma promessa
  python -m motor.campanha aprovar ID [ID...]
  python -m motor.campanha rejeitar ID [ID...]

Na VPS: docker compose exec sync python -m motor.campanha listar
Estado em DADOS_DIR (volume); os arquivos de mesmo nome em data/ do repositório são semente.
"""
import argparse
import copy
import functools
import logging
import re
import time
import unicodedata
from datetime import date, timedelta
from urllib.parse import quote_plus, urlsplit

from . import apuracao, armazem
from .coletores.http import cliente
from .coletores.noticias import _itens
from .llm import Cadeia, SemProvedorDisponivel
from .llm.erros import RespostaInvalida
from .trava import trava

log = logging.getLogger("campanha")

REFERENCIAS = armazem.DADOS / "referencias_campanha.json"
CANDIDATAS = armazem.DADOS / "promessas_candidatas.json"
APROVADAS = armazem.DADOS / "promessas_declaracoes.json"
REJEITADAS = armazem.DADOS / "declaracoes_rejeitadas.json"
ESTADO = armazem.ESTADO / "campanha.json"
ARQUIVOS = (REFERENCIAS, CANDIDATAS, APROVADAS, REJEITADAS)

URL = "https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
LOTE_EXTRACAO = 20
LOTE_CONSOLIDACAO = 50
REFS_POR_BUSCA = 12
CONTEXTOS = ["entrevista", "debate", "sabatina", "discurso", "rede_social", "propaganda_eleitoral", "outro"]

# Filtro sem IA: só manchetes/resumos com cara de promessa seguem para a extração.
CARA_DE_PROMESSA = re.compile(
    r"promet|promess|\bvai\b|\bvamos\b|\bvou\b|se (re)?eleit|propõe|propoe|proposta|garante|"
    r"compromisso|anuncia que vai|quer (criar|acabar|ampliar|reduzir|zerar)|plano de governo|"
    r"no (meu|próximo) governo|primeiro dia|defende (criar|acabar|ampliar|reduzir)", re.I)

PALAVRAS_VAZIAS = {"o", "a", "os", "as", "de", "do", "da", "dos", "das", "para", "e", "em", "no", "na",
                   "nos", "nas", "um", "uma", "com", "por", "ao", "aos", "sobre"}


def configuracao() -> tuple[dict, dict]:
    return armazem.ler_yaml("campanha.yaml"), armazem.ler_yaml("presidente.yaml")


def slug(texto: str) -> str:
    """"Retomar o benefício fiscal para eventos culturais" → "retomar-beneficio-fiscal-eventos-culturais"."""
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return "-".join([w for w in re.split(r"[^a-z0-9]+", t) if w and w not in PALAVRAS_VAZIAS][:6])


@functools.cache
def _dominios_partidarios() -> tuple[str, ...]:
    return tuple(armazem.ler_yaml("site.yaml").get("fontes_partidarias", []))


def partidaria(url: str | None) -> bool:
    """Site de partido: aparece como referência, mas não é imprensa (não conta como veículo)."""
    host = urlsplit(url or "").hostname or ""
    return any(host == d or host.endswith("." + d) for d in _dominios_partidarios())


def veiculos_distintos(fontes: list[dict]) -> int:
    return len({f["veiculo"].strip().lower() for f in fontes if not partidaria(f.get("url"))})


# --- busca ------------------------------------------------------------------------

def buscar(c, consulta: str, periodo) -> list[dict]:
    """Google Notícias (RSS). Volta no máximo ~100 itens por busca: períodos curtos trazem mais."""
    q = f"{consulta} after:{periodo[0]} before:{periodo[1]}"
    try:
        docs = _itens(c, URL.format(q=quote_plus(q)), "Google Notícias", str(periodo[0]), str(periodo[1]))
    except Exception as e:  # noqa: BLE001
        log.warning("busca falhou (%s): %s", consulta, e)
        return []
    time.sleep(1)  # educação com o Google Notícias
    return docs


def lista_numerada(docs: list[dict]) -> str:
    return "\n".join(f"[{i}] {d['titulo']} | {d['fonte']} | {d['data']} | {d['resumo'][:300]}"
                     for i, d in enumerate(docs))


def consultas_declaracoes(cfg: dict, nome: str, periodo, amplo: bool = False) -> list[tuple[str, tuple]]:
    consultas = [b.format(nome=nome) for b in cfg["buscas"]]
    consultas += [f'"{nome}" promete {t}' for t in cfg["temas"]]
    if not amplo:
        return [(q, periodo) for q in consultas]
    consultas += [f'"{nome}" {termo} site:{v}' for v in cfg["veiculos"] for termo in ("promete", '"se reeleito"')]
    return [(q, tuple(j)) for q in consultas for j in cfg["janelas_amplo"]]


def coletar(consultas) -> list[dict]:
    docs, vistos = [], set()
    with cliente() as c:
        for n, (q, periodo) in enumerate(consultas, 1):
            for d in buscar(c, q, periodo):
                if d["titulo"].lower() not in vistos and d["url"] not in vistos:
                    vistos.update((d["titulo"].lower(), d["url"]))
                    docs.append(d)
            if n % 25 == 0:
                log.info("busca %d/%d: %d reportagens únicas até aqui", n, len(consultas), len(docs))
    filtrados = [d for d in docs if CARA_DE_PROMESSA.search(f"{d['titulo']} {d['resumo']}")]
    log.info("%d reportagens únicas, %d com cara de promessa", len(docs), len(filtrados))
    return filtrados


# --- teto de IA ----------------------------------------------------------------------

class CadeiaComTeto:
    """Limita as chamadas de IA da campanha no dia: o acompanhamento oficial tem prioridade."""

    def __init__(self, cadeia: Cadeia, teto: int | None):
        self.cadeia, self.teto, self.usadas = cadeia, teto, 0

    def completar_json(self, *args, **kwargs):
        if self.teto is not None and self.usadas >= self.teto:
            raise SemProvedorDisponivel(f"teto diário da campanha ({self.teto} chamadas)")
        self.usadas += 1
        return self.cadeia.completar_json(*args, **kwargs)


# --- referências ------------------------------------------------------------------------

def sistema_referencias(nome: str) -> str:
    return f"""Você verifica declarações públicas de forma apartidária.
Recebe UMA promessa de {nome} e uma lista numerada de reportagens (título, veículo, data, resumo).
Marque somente as reportagens em que o próprio {nome}, seu vice, a campanha ou o governo dele PROMETE, DEFENDE ou DETALHA essa mesma proposta para o futuro.
NÃO marque: reportagens só sobre o tema, críticas de adversários, análises sem fala dele, ou propostas diferentes.
Para cada marcada, escreva em "o_que_disse" uma frase neutra resumindo o que foi dito sobre a proposta (sem adjetivos).
Na dúvida, não marque."""


SCHEMA_REF = {
    "type": "object",
    "properties": {"confirmadas": {"type": "array", "items": {
        "type": "object",
        "properties": {"indice": {"type": "integer"}, "o_que_disse": {"type": "string"}},
        "required": ["indice", "o_que_disse"], "additionalProperties": False}}},
    "required": ["confirmadas"], "additionalProperties": False,
}


def _chave_titulo(r: dict) -> str:
    return " ".join(r.get("titulo", "").lower().split())


def juntar_referencias(lista: list[dict], novas: list[dict], maximo: int) -> int:
    """Acrescenta sem repetir matéria (mesma URL ou mesmo título: o link do Google Notícias e a
    URL real são a mesma reportagem); mantém as mais recentes. Devolve quantas entraram."""
    vistas = {r["url"] for r in lista} | {_chave_titulo(r) for r in lista if r.get("titulo")}
    entrou = []
    for r in novas:
        if r["url"] in vistas or (r.get("titulo") and _chave_titulo(r) in vistas):
            # mesma matéria: se a nova traz a URL real, ela substitui o link do Google Notícias
            antiga = next((x for x in lista if x.get("titulo") and _chave_titulo(x) == _chave_titulo(r)), None)
            if antiga and "news.google.com" in antiga["url"] and "news.google.com" not in r["url"]:
                antiga.update(r)
            continue
        vistas |= {r["url"], _chave_titulo(r)}
        entrou.append(r)
    lista.extend(entrou)
    lista.sort(key=lambda r: r["data"] or "", reverse=True)
    del lista[maximo:]
    return len(entrou)


def atualizar_referencias(cadeia, cfg, presidente, promessas, estado, quantas: int | None,
                          periodo_inteiro: bool = False) -> int:
    """Rodízio: as promessas verificadas há mais tempo primeiro; busca desde a última verificação."""
    nome = presidente["nome_busca"]
    refs = armazem.ler_json(REFERENCIAS, {})
    verificadas = estado.setdefault("referencias_verificadas", {})
    plano = [p for p in promessas if p.get("origem", "plano_tse") == "plano_tse"]
    fila = sorted(plano, key=lambda p: verificadas.get(p["id"], ""))[:quantas]
    sistema, hoje, novas, feitas = sistema_referencias(nome), armazem.hoje(), 0, 0
    with cliente() as c:
        for n, p in enumerate(fila, 1):
            inicio = cfg["periodo_campanha"][0] if periodo_inteiro else verificadas.get(p["id"], cfg["periodo_campanha"][0])
            termos = sorted(p["palavras_chave"], key=lambda k: -len(k))[:3]
            consulta = f'"{nome}" (' + " OR ".join(f'"{t}"' for t in termos) + ")"
            docs = buscar(c, consulta, (inicio, hoje))[:REFS_POR_BUSCA]
            if docs:
                usuario = (f"PROMESSA: {p['titulo']}\nTEXTO ORIGINAL: {p['fonte']['trecho']}\n\n"
                           f"REPORTAGENS:\n{lista_numerada(docs)}")

                def validar(d, total=len(docs)):
                    if any(not 0 <= x["indice"] < total for x in d["confirmadas"]):
                        raise RespostaInvalida("índice fora da lista")

                try:
                    r = cadeia.completar_json(sistema, usuario, SCHEMA_REF, validar=validar)
                except SemProvedorDisponivel as e:
                    log.warning("Referências: parei em %d/%d (%s)", n - 1, len(fila), e)
                    break
                achadas, esgotou = [], False
                for x in r.dados["confirmadas"]:
                    d = docs[x["indice"]]
                    ref = {"veiculo": d["fonte"], "titulo": d["titulo"], "url": d["url"], "data": d["data"],
                           "o_que_disse": x["o_que_disse"]}
                    try:
                        ref["apuracao"] = apuracao.apurar(
                            cadeia, c, f"{nome} prometeu, defendeu ou detalhou esta proposta: {p['titulo']}",
                            [ref], p["palavras_chave"] + [nome], evitar=(r.provedor,), max_leituras=1)
                    except SemProvedorDisponivel as e:  # teto do dia no meio da apuração: guarda o que já foi apurado
                        log.warning("Referências: parei em %d/%d, na apuração (%s)", n - 1, len(fila), e)
                        esgotou = True
                        break
                    if ref["apuracao"]["status"] in ("nao_confirmada", "contradita"):
                        continue  # a reportagem inteira não sustenta: fora
                    if ref["apuracao"]["trechos"]:
                        ref["url"] = ref["apuracao"]["trechos"][0]["url"]
                    else:
                        ref["url"] = apuracao.resolver_url(c, ref["url"])
                    achadas.append(ref)
                novas += juntar_referencias(refs.setdefault(p["id"], []), achadas, cfg["max_referencias_por_promessa"])
                if esgotou:  # promessa não marcada como verificada: volta no próximo sync
                    armazem.gravar_json(REFERENCIAS, refs)
                    break
            verificadas[p["id"]] = hoje
            feitas += 1
            armazem.gravar_json(REFERENCIAS, refs)  # salva a cada passo: dá para interromper
    log.info("Referências: %d promessas verificadas, %d reportagens novas", feitas, novas)
    return novas


# --- declarações -----------------------------------------------------------------------

def sistema_extracao(nome: str) -> str:
    return f"""Você identifica promessas de forma apartidária.
Recebe reportagens numeradas sobre {nome} e a lista de promessas que JÁ estão no plano de governo dele (id: título).
Extraia apenas promessas CONCRETAS e VERIFICÁVEIS ditas pelo PRÓPRIO {nome} (não por vice, aliados, assessores ou adversários):
uma ação de governo específica ("vou criar X", "vou acabar com Y", "vou reduzir Z para W", "no primeiro dia vou...").
Ignore frases genéricas ("vamos melhorar a saúde"), críticas a adversários, opiniões e promessas atribuídas a terceiros.
Se ele já governa, anúncio de algo que o governo JÁ FEZ ou já lançou não é promessa; promessa é o que ele diz que vai fazer.
Para cada promessa:
- id_existente: o id da promessa do plano que ela repete, ou "" se não estiver no plano;
- titulo: verbo no infinitivo + ação concreta (ex.: "Criar o programa X para Y");
- contexto: onde foi dita ({", ".join(CONTEXTOS)});
- o_que_disse: uma frase neutra resumindo o que ele disse;
- palavras_chave: 3 a 6 termos ESPECÍFICOS que apareceriam numa lei, decreto ou notícia sobre o cumprimento
  (nomes de programas, siglas, expressões de 2+ palavras como "escala 6x1"). Nunca termos genéricos
  sozinhos ("lei", "imposto", "reajuste", "economia", "governo")."""


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

SISTEMA_CONSOLIDACAO = """Você agrupa promessas repetidas.
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


def com_categorias(schema: dict, categorias: list[str]) -> dict:
    """Cópia do schema com "categoria" restrita às categorias do site."""
    s = copy.deepcopy(schema)
    itens = next(iter(s["properties"].values()))["items"]
    itens["properties"]["categoria"] = {"type": "string", "enum": categorias}
    return s


def extrair(cadeia, docs, plano, nome) -> list[dict]:
    catalogo = "\n".join(f"{p['id']}: {p['titulo']}" for p in plano)
    categorias = sorted({p["categoria"] for p in plano})
    schema, sistema = com_categorias(SCHEMA_EXTRACAO, categorias), sistema_extracao(nome)
    ids, achadas = {p["id"] for p in plano}, []
    for i in range(0, len(docs), LOTE_EXTRACAO):
        lote = docs[i:i + LOTE_EXTRACAO]
        usuario = (f"CATEGORIAS: {', '.join(categorias)}\n\nPROMESSAS DO PLANO:\n{catalogo}\n\n"
                   f"REPORTAGENS:\n{lista_numerada(lote)}")

        def validar(d, total=len(lote)):
            if any(not 0 <= x["indice"] < total for x in d["promessas"]):
                raise RespostaInvalida("índice fora do lote")

        try:
            r = cadeia.completar_json(sistema, usuario, schema, validar=validar)
        except SemProvedorDisponivel as e:
            log.warning("Extração: parei no lote %d (%s); seguindo com o que já foi extraído", i // LOTE_EXTRACAO + 1, e)
            break
        for x in r.dados["promessas"]:
            d = lote[x["indice"]]
            achadas.append({**x, "id_existente": x["id_existente"] if x["id_existente"] in ids else "",
                            "fonte": {k: d[k] for k in ("titulo", "url", "data")} | {"veiculo": d["fonte"]}})
    return achadas


def consolidar(cadeia, novas, conhecidas: dict[str, str], categorias: list[str]) -> list[dict]:
    """Agrupa a mesma promessa vinda de várias reportagens; liga a candidatas já conhecidas."""
    lista_conhecidas = "\n".join(f"{k}: {v}" for k, v in conhecidas.items()) or "(nenhuma)"
    schema = com_categorias(SCHEMA_CONSOLIDACAO, categorias)
    sozinho = lambda a: {"titulo": a["titulo"], "categoria": a["categoria"], "id_conhecida": "", "membros": [a]}  # noqa: E731
    grupos = []
    for i in range(0, len(novas), LOTE_CONSOLIDACAO):
        lote = novas[i:i + LOTE_CONSOLIDACAO]
        itens = "\n".join(f"[{j}] {a['titulo']} — {a['o_que_disse'][:160]}" for j, a in enumerate(lote))

        def validar(d, total=len(lote)):
            if any(not 0 <= j < total for g in d["grupos"] for j in g["indices"]):
                raise RespostaInvalida("índice fora do lote")

        try:
            r = cadeia.completar_json(
                SISTEMA_CONSOLIDACAO,
                f"CATEGORIAS: {', '.join(categorias)}\n\nJÁ CONHECIDAS:\n{lista_conhecidas}\n\nPROMESSAS:\n{itens}",
                schema, validar=validar)
        except SemProvedorDisponivel:
            log.warning("Consolidação sem IA disponível: cada promessa vira um grupo")
            grupos += [sozinho(a) for a in lote]
            continue
        usados = set()
        for g in r.dados["grupos"]:
            membros = [lote[j] for j in dict.fromkeys(g["indices"]) if j not in usados]
            usados.update(g["indices"])
            if membros:
                grupos.append({**g, "membros": membros})
        grupos += [sozinho(a) for j, a in enumerate(lote) if j not in usados]  # índice esquecido pela IA
    return grupos


def buscar_declaracoes(cadeia, cfg, presidente, plano, periodo, amplo=False) -> dict:
    nome = presidente["nome_busca"]
    candidatas = {c["id"]: c for c in armazem.ler_json(CANDIDATAS, [])}
    aprovadas = {c["id"]: c for c in armazem.ler_json(APROVADAS, [])}
    rejeitadas = {c["id"]: c for c in armazem.ler_json(REJEITADAS, [])}

    docs = coletar(consultas_declaracoes(cfg, nome, periodo, amplo))
    achadas = extrair(cadeia, docs, plano, nome)
    do_plano = [a for a in achadas if a["id_existente"]]
    fora = [a for a in achadas if not a["id_existente"]]

    refs = armazem.ler_json(REFERENCIAS, {})
    reforcos = sum(juntar_referencias(refs.setdefault(a["id_existente"], []),
                                      [a["fonte"] | {"o_que_disse": a["o_que_disse"]}],
                                      cfg["max_referencias_por_promessa"]) for a in do_plano)
    armazem.gravar_json(REFERENCIAS, refs)

    conhecidas = {k: v["titulo"] for d in (candidatas, aprovadas, rejeitadas) for k, v in d.items()}
    novas_candidatas = 0
    for g in consolidar(cadeia, fora, conhecidas, sorted({p["categoria"] for p in plano})) if fora else []:
        fontes = list({m["fonte"]["url"]: m["fonte"] for m in g["membros"]}.values())
        alvo = g["id_conhecida"] if g["id_conhecida"] in conhecidas else slug(g["titulo"])
        if alvo in rejeitadas:
            continue
        base = aprovadas.get(alvo) or candidatas.get(alvo)
        if base:  # mais fontes para algo já conhecido
            urls = {f["url"] for f in base["fontes"]}
            base["fontes"] += [f for f in fontes if f["url"] not in urls]
            continue
        principal = max(g["membros"], key=lambda m: len(m["o_que_disse"]))
        candidatas[alvo] = {
            "id": alvo, "titulo": g["titulo"], "categoria": g["categoria"],
            "contexto": principal["contexto"], "o_que_disse": principal["o_que_disse"],
            "palavras_chave": sorted({k.lower() for m in g["membros"] for k in m["palavras_chave"]})[:10],
            "fontes": fontes, "status": "pendente", "encontrada_em": armazem.hoje(),
        }
        novas_candidatas += 1

    armazem.gravar_json(CANDIDATAS, list(candidatas.values()))
    if novas_candidatas:
        reconsolidar(cadeia, sorted({p["categoria"] for p in plano}))
        candidatas = {c["id"]: c for c in armazem.ler_json(CANDIDATAS, [])}

    # Aprovação automática: N veículos diferentes E apuração com citação literal no texto da matéria.
    minimo = cfg["aprovacao_automatica_min_veiculos"]
    automaticas = []
    with cliente() as c_http:
        for c in [c for c in candidatas.values() if veiculos_distintos(c["fontes"]) >= minimo]:
            if c.get("apuracao", {}).get("status") != "apurada":
                try:
                    c["apuracao"] = apuracao.apurar(cadeia, c_http, f"{nome} prometeu: {c['titulo']}",
                                                    c["fontes"], c["palavras_chave"] + [nome])
                except SemProvedorDisponivel:
                    break
            if c["apuracao"]["status"] == "apurada":
                resolver_fontes(c_http, c)
                automaticas.append(c)
    for c in automaticas:
        del candidatas[c["id"]]
        aprovadas[c["id"]] = c | {"status": "aprovada", "aprovacao": "automatica", "aprovada_em": armazem.hoje()}
        log.info("Declaração aprovada automaticamente (%d veículos, citação conferida): %s",
                 veiculos_distintos(c["fontes"]), c["titulo"])

    armazem.gravar_json(CANDIDATAS, sorted(candidatas.values(), key=lambda c: -veiculos_distintos(c["fontes"])))
    armazem.gravar_json(APROVADAS, list(aprovadas.values()))
    resumo = {"reportagens": len(docs), "declaracoes": len(achadas), "referencias_do_plano": reforcos,
              "candidatas_novas": novas_candidatas, "aprovadas_automaticamente": len(automaticas),
              "pendentes": len(candidatas)}
    log.info("Declarações: %s", resumo)
    return resumo


def reconsolidar(cadeia, categorias: list[str]) -> int:
    """Junta candidatas pendentes que são a mesma promessa (vindas de buscas/lotes diferentes)."""
    candidatas = armazem.ler_json(CANDIDATAS, [])
    if len(candidatas) < 2:
        return 0
    membros = [{"titulo": c["titulo"], "o_que_disse": c["o_que_disse"], "categoria": c["categoria"], "cand": c}
               for c in candidatas]
    juntadas, resultado = 0, []
    for g in consolidar(cadeia, membros, {}, categorias):
        grupo = sorted((m["cand"] for m in g["membros"]), key=lambda c: -veiculos_distintos(c["fontes"]))
        base = grupo[0]
        for outra in grupo[1:]:
            urls = {f["url"] for f in base["fontes"]}
            base["fontes"] += [f for f in outra["fontes"] if f["url"] not in urls]
            base["palavras_chave"] = sorted(set(base["palavras_chave"]) | set(outra["palavras_chave"]))[:10]
            base.pop("apuracao", None)  # mais fontes: vale apurar de novo
            juntadas += 1
        resultado.append(base)
    armazem.gravar_json(CANDIDATAS, sorted(resultado, key=lambda c: -veiculos_distintos(c["fontes"])))
    log.info("Reconsolidação: %d candidatas juntadas a outras; %d pendentes", juntadas, len(resultado))
    return juntadas


# --- links reais ---------------------------------------------------------------------------

def resolver_fontes(c, declaracao: dict) -> None:
    for f in declaracao["fontes"]:
        f["url"] = apuracao.resolver_url(c, f["url"])


def resolver_links_pendentes(limite: int | None = None) -> int:
    """Troca links news.google.com pela URL real da matéria em referências, declarações e "Na imprensa"."""
    feitos = 0
    with cliente() as c:
        def resolver(item):
            nonlocal feitos
            if (limite is None or feitos < limite) and "news.google.com" in item.get("url", ""):
                item["url"] = apuracao.resolver_url(c, item["url"])
                feitos += 1
                time.sleep(0.5)

        refs = armazem.ler_json(REFERENCIAS, {})
        for lista in refs.values():
            for r in lista:
                resolver(r)
        armazem.gravar_json(REFERENCIAS, refs)
        aprovadas = armazem.ler_json(APROVADAS, [])
        for d in aprovadas:
            for f in d["fontes"]:
                resolver(f)
        armazem.gravar_json(APROVADAS, aprovadas)
        promessas = armazem.ler_json(armazem.PROMESSAS, [])
        for p in promessas:
            for n in p.get("imprensa", []):
                resolver(n)
        armazem.gravar_json(armazem.PROMESSAS, promessas)
    log.info("Links resolvidos para a URL real: %d", feitos)
    return feitos


# --- aplicar no volume ------------------------------------------------------------------

def promessa_de_declaracao(d: dict) -> dict:
    principal, *outras = d["fontes"]
    return {
        "id": d["id"],
        "origem": "declaracao",
        "titulo": d["titulo"],
        "categoria": d["categoria"],
        "prazo": None,
        "fonte": {"tipo": "declaracao", "contexto": d["contexto"], "url": principal["url"],
                  "veiculo": principal["veiculo"], "titulo": principal["titulo"],
                  "data": principal["data"], "trecho": d["o_que_disse"],
                  "veiculos": veiculos_distintos(d["fontes"]), "aprovacao": d.get("aprovacao", "manual")},
        "palavras_chave": d["palavras_chave"],
        "status": "nao_iniciada",
        "atualizado_em": None,
        "referencias": [f | {"o_que_disse": d["o_que_disse"]} for f in outras],
        "historico": [],
    }


def aplicar_no_volume() -> None:
    """Leva referências e declarações aprovadas para as promessas que o site publica."""
    if not armazem.PROMESSAS.exists():
        return
    promessas = armazem.ler_json(armazem.PROMESSAS, [])
    por_id = {p["id"]: p for p in promessas}
    refs = armazem.ler_json(REFERENCIAS, {})
    for pid, lista in refs.items():
        if pid in por_id and por_id[pid].get("origem", "plano_tse") == "plano_tse":
            por_id[pid]["referencias"] = lista
    for d in armazem.ler_json(APROVADAS, []):
        nova = promessa_de_declaracao(d)
        if d["id"] in por_id:  # já publicada: atualiza fontes, mantém status e histórico
            por_id[d["id"]].update({k: nova[k] for k in ("titulo", "categoria", "fonte", "palavras_chave", "referencias")})
        else:
            promessas.append(nova)
    armazem.gravar_json(armazem.PROMESSAS, promessas)


def mesclar_semente() -> None:
    """Junta os arquivos de campanha do repositório (data/) aos do volume, sem apagar nada."""
    raiz = armazem.SEMENTE.parent
    if raiz.resolve() == armazem.DADOS.resolve():
        return
    maximo = armazem.ler_yaml("campanha.yaml")["max_referencias_por_promessa"]
    refs, semente_refs = armazem.ler_json(REFERENCIAS, {}), armazem.ler_json(raiz / REFERENCIAS.name, {})
    for pid, lista in semente_refs.items():
        juntar_referencias(refs.setdefault(pid, []), lista, maximo)
    armazem.gravar_json(REFERENCIAS, refs)

    decididas = {c["id"] for arq in (APROVADAS, REJEITADAS) for c in armazem.ler_json(arq, [])}
    for arq in (APROVADAS, REJEITADAS, CANDIDATAS):
        atuais = {c["id"]: c for c in armazem.ler_json(arq, [])}
        for c in armazem.ler_json(raiz / arq.name, []):
            if c["id"] not in atuais and (arq != CANDIDATAS or c["id"] not in decididas):
                atuais[c["id"]] = c
        armazem.gravar_json(arq, list(atuais.values()))
    # candidata que foi decidida (na semente ou no volume) sai da fila
    candidatas = [c for c in armazem.ler_json(CANDIDATAS, [])
                  if c["id"] not in {d["id"] for arq in (APROVADAS, REJEITADAS) for d in armazem.ler_json(arq, [])}]
    armazem.gravar_json(CANDIDATAS, candidatas)


# --- rotina diária ---------------------------------------------------------------------

def sincronizar() -> dict:
    cfg, presidente = configuracao()
    if not cfg.get("ativo", True):
        return {}
    estado = armazem.ler_json(ESTADO, {})
    hoje = date.today()
    desde = estado.get("declaracoes_ate") or (hoje - timedelta(days=cfg["janela_diaria_dias"])).isoformat()
    promessas = armazem.ler_json(armazem.PROMESSAS, [])
    plano = [p for p in promessas if p.get("origem", "plano_tse") == "plano_tse"]

    base = Cadeia.da_config()
    cadeia = CadeiaComTeto(base, cfg["max_chamadas_ia_por_dia"])
    resumo = {"declaracoes": buscar_declaracoes(cadeia, cfg, presidente, plano, (desde, hoje.isoformat()))}
    estado["declaracoes_ate"] = hoje.isoformat()
    resumo["referencias_novas"] = atualizar_referencias(cadeia, cfg, presidente, promessas, estado,
                                                        cfg["referencias_por_dia"])
    resumo["chamadas_ia"] = cadeia.usadas
    base.uso.salvar()
    armazem.gravar_json(ESTADO, estado)
    aplicar_no_volume()
    resumo["links_resolvidos"] = resolver_links_pendentes(cfg.get("links_resolvidos_por_dia", 300))
    return resumo


# --- linha de comando -------------------------------------------------------------------

def aprovar(ids) -> None:
    _mover(ids, APROVADAS, "aprovada")


def rejeitar(ids) -> None:
    _mover(ids, REJEITADAS, "rejeitada")


def _mover(ids, destino, status):
    candidatas = armazem.ler_json(CANDIDATAS, [])
    alvo = armazem.ler_json(destino, [])
    for i in ids:
        c = next((c for c in candidatas if c["id"] == i), None)
        if not c:
            print(f"não está entre as candidatas: {i}")
            continue
        candidatas.remove(c)
        if status == "aprovada":
            with cliente() as c_http:
                resolver_fontes(c_http, c)
        alvo.append(c | {"status": status, "aprovacao": "manual", "aprovada_em": armazem.hoje()})
        print(f"{status}: {i}")
    armazem.gravar_json(CANDIDATAS, candidatas)
    armazem.gravar_json(destino, alvo)
    aplicar_no_volume()


def main():
    ap = argparse.ArgumentParser(description="Referências e declarações de campanha")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sincronizar")
    b = sub.add_parser("buscar")
    b.add_argument("--amplo", action="store_true", help="buscas × temas × veículos × janelas de tempo")
    r = sub.add_parser("referencias")
    r.add_argument("--todas", action="store_true", help="todas as promessas, período de campanha inteiro")
    sub.add_parser("listar")
    sub.add_parser("resolver-links", help="troca links do Google Notícias pela URL real da matéria")
    sub.add_parser("reconsolidar", help="junta candidatas pendentes que são a mesma promessa")
    for nome in ("aprovar", "rejeitar"):
        sub.add_parser(nome).add_argument("ids", nargs="+")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for ruido in ("httpx", "motor.llm.cadeia"):
        logging.getLogger(ruido).setLevel(logging.WARNING)

    cfg, presidente = configuracao()
    if args.cmd == "sincronizar":
        print(sincronizar())
    elif args.cmd in ("buscar", "referencias"):
        promessas = armazem.ler_json(armazem.PROMESSAS, None) or armazem.ler_json(armazem.SEMENTE, [])
        plano = [p for p in promessas if p.get("origem", "plano_tse") == "plano_tse"]
        base = Cadeia.da_config()
        if args.cmd == "buscar":
            buscar_declaracoes(base, cfg, presidente, plano, tuple(cfg["periodo_campanha"]), amplo=args.amplo)
        else:
            estado = armazem.ler_json(ESTADO, {})
            atualizar_referencias(base, cfg, presidente, plano, estado, None if args.todas else cfg["referencias_por_dia"],
                                  periodo_inteiro=args.todas)
            armazem.gravar_json(ESTADO, estado)
        base.uso.salvar()
        aplicar_no_volume()
    elif args.cmd == "reconsolidar":
        promessas = armazem.ler_json(armazem.PROMESSAS, None) or armazem.ler_json(armazem.SEMENTE, [])
        base = Cadeia.da_config()
        reconsolidar(base, sorted({p["categoria"] for p in promessas}))
        base.uso.salvar()
    elif args.cmd == "resolver-links":
        resolver_links_pendentes()
        aplicar_no_volume()
    elif args.cmd == "listar":
        for c in armazem.ler_json(CANDIDATAS, []):
            f = c["fontes"][0]
            print(f"[{c['id']}] {c['titulo']}  ({c['categoria']}, {c['contexto']}, "
                  f"{veiculos_distintos(c['fontes'])} veículo(s))")
            print(f"    {c['o_que_disse']}")
            print(f"    {f['veiculo']} {f['data']}: {f['titulo'][:90]}\n")
    elif args.cmd == "aprovar":
        with trava():
            aprovar(args.ids)
    else:
        with trava():
            rejeitar(args.ids)


if __name__ == "__main__":
    main()
