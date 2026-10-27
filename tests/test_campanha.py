import json

import pytest

from motor import armazem, campanha
from motor.llm.cadeia import Cadeia, RegistroUso
from motor.llm.erros import SemProvedorDisponivel

CFG = {"buscas": ['"{nome}" promete'], "temas": ["saúde"], "veiculos": ["g1.globo.com"],
       "janelas_amplo": [["2026-06-01", "2026-07-01"]], "max_referencias_por_promessa": 3,
       "aprovacao_automatica_min_veiculos": 3, "periodo_campanha": ["2026-06-01", "2026-10-25"]}
PRESIDENTE = {"nome_busca": "Lula"}
PLANO = [{"id": "fim-escala-6x1", "origem": "plano_tse", "titulo": "Fim da escala 6x1", "categoria": "Trabalho e renda",
          "fonte": {"trecho": "fim da escala 6x1"}, "palavras_chave": ["escala 6x1"], "referencias": []}]


@pytest.fixture
def volume(tmp_path, monkeypatch):
    for nome in ("REFERENCIAS", "CANDIDATAS", "APROVADAS", "REJEITADAS"):
        monkeypatch.setattr(campanha, nome, tmp_path / getattr(campanha, nome).name)
    monkeypatch.setattr(campanha, "ESTADO", tmp_path / "campanha.json")
    monkeypatch.setattr(armazem, "PROMESSAS", tmp_path / "promessas.json")
    monkeypatch.setattr(armazem, "SEMENTE", tmp_path / "semente" / "promessas.json")
    monkeypatch.setattr(armazem, "DADOS", tmp_path)
    monkeypatch.setattr(campanha.time, "sleep", lambda s: None)
    monkeypatch.setattr(campanha.apuracao, "resolver_url", lambda c, url: url)
    return tmp_path


def apuracao_fixa(status):
    return lambda *a, **k: {"status": status, "confirmacoes": int(status == "apurada"), "lidas": 1,
                            "trechos": [{"url": "u0", "veiculo": "G1", "trecho": "vou zerar a fila do INSS"}]
                            if status == "apurada" else []}


def doc(titulo, veiculo, url):
    return {"titulo": titulo, "fonte": veiculo, "url": url, "data": "2026-10-20", "resumo": ""}


class IAFalsa:
    """Extração devolve uma promessa nova por reportagem; consolidação junta tudo num grupo."""
    nome, limite_diario, intervalo = "falsa", None, 0

    def disponivel(self):
        return True

    def completar(self, sistema, usuario, schema):
        if "agrupa" in sistema:
            n = usuario.count("\n[")
            return json.dumps({"grupos": [{"indices": list(range(n + 1)), "titulo": "Zerar fila do INSS",
                                           "categoria": "Trabalho e renda", "id_conhecida": ""}]})
        n = usuario.split("REPORTAGENS:\n")[1].count("\n") + 1
        return json.dumps({"promessas": [{
            "indice": i, "id_existente": "", "titulo": "Zerar a fila do INSS", "categoria": "Trabalho e renda",
            "contexto": "entrevista", "o_que_disse": "Disse que vai zerar a fila.", "palavras_chave": ["fila do inss"]}
            for i in range(n)]})


def test_filtro_de_manchetes():
    assert campanha.CARA_DE_PROMESSA.search("Lula promete isenção do IR")
    assert campanha.CARA_DE_PROMESSA.search("Lula diz que vai acabar com a escala 6x1")
    assert not campanha.CARA_DE_PROMESSA.search("Lula visita obra em Pernambuco")


def test_teto_de_chamadas():
    class Base:
        def completar_json(self, *a, **k):
            return "ok"
    c = campanha.CadeiaComTeto(Base(), 2)
    c.completar_json()
    c.completar_json()
    with pytest.raises(SemProvedorDisponivel):
        c.completar_json()


def test_declaracao_com_3_veiculos_e_citacao_conferida_e_publicada_sozinha(volume, monkeypatch):
    monkeypatch.setattr(campanha.apuracao, "apurar", apuracao_fixa("apurada"))
    # títulos diferentes: a mesma manchete republicada (agência) é deduplicada e conta uma vez só
    docs = [doc(f"Lula promete zerar fila do INSS ({v})", v, f"u{i}") for i, v in enumerate(["G1", "Folha", "Estadão"])]
    monkeypatch.setattr(campanha, "buscar", lambda c, q, p: docs)
    armazem.gravar_json(armazem.PROMESSAS, PLANO)
    cadeia = Cadeia([IAFalsa()], RegistroUso(caminho=None))

    r = campanha.buscar_declaracoes(cadeia, CFG, PRESIDENTE, PLANO, ("2026-10-20", "2026-10-25"))
    assert r["aprovadas_automaticamente"] == 1 and r["pendentes"] == 0

    campanha.aplicar_no_volume()
    publicada = next(p for p in armazem.ler_json(armazem.PROMESSAS, []) if p["origem"] == "declaracao")
    assert publicada["fonte"]["veiculos"] == 3 and publicada["fonte"]["aprovacao"] == "automatica"
    assert len(publicada["referencias"]) == 2


def test_3_veiculos_sem_citacao_conferida_espera_revisao(volume, monkeypatch):
    monkeypatch.setattr(campanha.apuracao, "apurar", apuracao_fixa("nao_confirmada"))
    docs = [doc(f"Lula promete zerar fila do INSS ({v})", v, f"u{i}") for i, v in enumerate(["G1", "Folha", "Estadão"])]
    monkeypatch.setattr(campanha, "buscar", lambda c, q, p: docs)
    cadeia = Cadeia([IAFalsa()], RegistroUso(caminho=None))
    r = campanha.buscar_declaracoes(cadeia, CFG, PRESIDENTE, PLANO, ("2026-10-20", "2026-10-25"))
    assert r["aprovadas_automaticamente"] == 0 and r["pendentes"] == 1
    assert armazem.ler_json(campanha.CANDIDATAS, [])[0]["apuracao"]["status"] == "nao_confirmada"


def test_declaracao_com_1_veiculo_espera_revisao(volume, monkeypatch):
    monkeypatch.setattr(campanha, "buscar", lambda c, q, p: [doc("Lula promete zerar fila do INSS", "G1", "u1")])
    cadeia = Cadeia([IAFalsa()], RegistroUso(caminho=None))
    r = campanha.buscar_declaracoes(cadeia, CFG, PRESIDENTE, PLANO, ("2026-10-20", "2026-10-25"))
    assert r["aprovadas_automaticamente"] == 0 and r["pendentes"] == 1


def test_mesclar_semente_sem_apagar_e_respeitando_decisoes(volume, monkeypatch):
    semente = volume / "semente"
    semente.mkdir()
    armazem.gravar_json(semente / "referencias_campanha.json", {"p1": [{"url": "a", "data": "2026-10-01"}]})
    armazem.gravar_json(semente / "promessas_candidatas.json", [{"id": "c1"}, {"id": "c2"}])
    armazem.gravar_json(campanha.REFERENCIAS, {"p1": [{"url": "b", "data": "2026-10-02"}]})
    armazem.gravar_json(campanha.REJEITADAS, [{"id": "c2"}])
    monkeypatch.setattr(armazem, "ler_yaml", lambda nome: {"max_referencias_por_promessa": 8})

    campanha.mesclar_semente()
    assert {r["url"] for r in armazem.ler_json(campanha.REFERENCIAS, {})["p1"]} == {"a", "b"}
    assert [c["id"] for c in armazem.ler_json(campanha.CANDIDATAS, [])] == ["c1"]  # c2 foi rejeitada no volume


def test_juntar_referencias_sem_repetir_e_com_teto():
    lista = [{"url": "a", "data": "2026-10-01"}]
    entrou = campanha.juntar_referencias(lista, [{"url": "a", "data": "x"}, {"url": "b", "data": "2026-10-05"},
                                                 {"url": "c", "data": "2026-10-03"}], 2)
    assert entrou == 2 and [r["url"] for r in lista] == ["b", "c"]


def test_mesma_materia_com_link_do_google_e_link_real_nao_duplica():
    lista = [{"url": "https://news.google.com/rss/articles/x", "titulo": "Lula promete PEC", "data": "2026-10-01"}]
    entrou = campanha.juntar_referencias(lista, [{"url": "https://g1.globo.com/a", "titulo": "Lula  promete PEC",
                                                  "data": "2026-10-01"}], 8)
    assert entrou == 0 and len(lista) == 1 and lista[0]["url"] == "https://g1.globo.com/a"


def test_teto_no_meio_da_apuracao_nao_derruba_a_etapa(volume, monkeypatch):
    """Bug de 26/10/2026: o teto diário estourava dentro de apurar() e a etapa campanha inteira falhava."""
    class Resp:
        provedor, dados = "falsa", {"confirmadas": [{"indice": 0, "o_que_disse": "Disse que vai acabar com a 6x1."}]}

    class Cadeia1:
        def completar_json(self, *a, **k):
            return Resp()

    def sem_teto(*a, **k):
        raise SemProvedorDisponivel("teto diário da campanha (60 chamadas)")

    monkeypatch.setattr(campanha, "buscar", lambda c, q, p: [doc("Lula promete fim da 6x1", "G1", "u1")])
    monkeypatch.setattr(campanha.apuracao, "apurar", sem_teto)
    estado = {}
    assert campanha.atualizar_referencias(Cadeia1(), CFG, PRESIDENTE, PLANO, estado, 5) == 0
    assert "fim-escala-6x1" not in estado["referencias_verificadas"]  # volta no próximo sync
