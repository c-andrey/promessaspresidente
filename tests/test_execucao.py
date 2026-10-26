import json

from motor import armazem, execucao
from motor.llm.cadeia import Cadeia, RegistroUso

PRESIDENTE = {"nome": "Lula", "nome_busca": "Lula", "mandato_inicio": "2027-01-01", "acompanhar_desde": "2026-10-26"}
CFG = {"max_noticias_por_promessa": 10, "buscar_desde": "2026-06-01"}


def promessa():
    return {"id": "fim-escala-6x1", "titulo": "Fim da escala 6x1", "status": "nao_iniciada", "categoria": "Trabalho",
            "fonte": {"trecho": "fim da escala 6x1"}, "palavras_chave": ["escala 6x1"], "historico": []}


class IA:
    nome, limite_diario, intervalo = "groq-x", None, 0

    def disponivel(self):
        return True

    def completar(self, sistema, usuario, schema):
        return json.dumps({"materias": [
            {"indice": 0, "relacionada": True, "indica": "em_andamento", "confianca": 0.8, "resumo": "Governo envia PEC."},
            {"indice": 1, "relacionada": False, "indica": "nada", "confianca": 0.9, "resumo": "Outro assunto."}]})


def test_links_na_imprensa_e_sugestao_apurada_na_fila(monkeypatch):
    docs = [{"titulo": "Governo envia PEC do fim da 6x1", "fonte": "G1", "url": "https://news.google.com/rss/articles/a",
             "data": "2026-11-03", "resumo": ""},
            {"titulo": "Outra coisa", "fonte": "UOL", "url": "u2", "data": "2026-11-03", "resumo": ""}]
    monkeypatch.setattr(execucao, "buscar", lambda c, q, p: docs)
    monkeypatch.setattr(execucao.apuracao, "resolver_url", lambda c, u: "https://g1.globo.com/materia")
    monkeypatch.setattr(execucao.apuracao, "apurar", lambda *a, **k: {
        "status": "apurada", "trechos": [{"url": "https://g1.globo.com/materia", "veiculo": "G1",
                                          "trecho": "o governo enviou ao Congresso a PEC"}]})
    p, fila, estado = promessa(), [], {}
    stats = execucao.acompanhar(Cadeia([IA()], RegistroUso(caminho=None)), PRESIDENTE, CFG, [p], fila, estado, None)

    assert stats == {"verificadas": 1, "links": 1, "sugestoes": 1}
    assert p["imprensa"][0]["url"] == "https://g1.globo.com/materia"  # link real, não o do Google Notícias
    assert p["status"] == "nao_iniciada"                              # notícia nunca muda status sozinha
    assert fila[0]["origem"] == "noticias" and fila[0]["apuracao"]["status"] == "apurada"
    assert estado["verificadas"]["fim-escala-6x1"] == armazem.hoje()


def test_noticia_antes_de_acompanhar_desde_so_vira_link(monkeypatch):
    docs = [{"titulo": "Campanha: Lula promete PEC da 6x1", "fonte": "G1", "url": "u1", "data": "2026-10-10", "resumo": ""},
            {"titulo": "x", "fonte": "UOL", "url": "u2", "data": "2026-10-10", "resumo": ""}]
    monkeypatch.setattr(execucao, "buscar", lambda c, q, p: docs)
    monkeypatch.setattr(execucao.apuracao, "resolver_url", lambda c, u: u)
    p, fila = promessa(), []
    execucao.acompanhar(Cadeia([IA()], RegistroUso(caminho=None)), PRESIDENTE, CFG, [p], fila, {}, None)
    assert len(p["imprensa"]) == 1 and fila == []
