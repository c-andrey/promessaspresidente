from web import build

from motor import armazem

PLANO = {"id": "p1", "origem": "plano_tse", "titulo": "Promessa do plano", "categoria": "C", "prazo": None,
         "fonte": {"tipo": "plano_de_governo_tse", "url": "u", "pagina": 3, "trecho": "trecho do plano"},
         "palavras_chave": ["x"], "status": "nao_iniciada", "atualizado_em": None, "referencias": [], "historico": []}
DECLARACAO = PLANO | {
    "id": "d1", "origem": "declaracao", "titulo": "Promessa de entrevista",
    "fonte": {"tipo": "declaracao", "contexto": "debate", "url": "https://n/1", "veiculo": "G1",
              "titulo": "Debate na Globo", "data": "2026-10-20", "trecho": "Disse que vai criar X."},
}


def test_site_separa_plano_e_declaracao(tmp_path, monkeypatch):
    armazem.gravar_json(tmp_path / "promessas.json", [PLANO, DECLARACAO])
    monkeypatch.setattr(armazem, "PROMESSAS", tmp_path / "promessas.json")
    monkeypatch.setattr(armazem, "EXECUCAO", tmp_path / "exec.json")
    monkeypatch.setattr(build, "DIST", tmp_path / "site")
    build.main()

    index = (tmp_path / "site" / "index.html").read_text()
    assert "1 do plano de governo registrado no TSE e 1 feitos em entrevistas" in index
    assert 'data-origem="declaracao"' in index and 'data-origem="plano_tse"' in index
    assert not (tmp_path / "site" / "dados.json").exists()

    pagina = (tmp_path / "site" / "promessa" / "d1.html").read_text()
    assert "não consta no plano de governo registrado no TSE" in pagina and "Dito em debate" in pagina
    assert "página 3" in (tmp_path / "site" / "promessa" / "p1.html").read_text()


def test_feed_mudancas_selos_e_compartilhamento(tmp_path, monkeypatch):
    mudou = PLANO | {"status": "em_andamento", "historico": [{
        "data": "2026-11-03", "de": "nao_iniciada", "para": "em_andamento", "justificativa": "Enviou o projeto.",
        "evidencia": "", "origem": "revisao",
        "documento": {"fonte": "Câmara", "titulo": "PL 9/2026", "url": "https://c/9", "data": "2026-11-02"}}],
        "referencias": [{"veiculo": "Partido dos Trabalhadores", "titulo": "Lula promete", "url": "https://pt.org.br/a",
                         "data": "2026-09-01", "o_que_disse": "Disse."}]}
    armazem.gravar_json(tmp_path / "promessas.json", [mudou])
    monkeypatch.setattr(armazem, "PROMESSAS", tmp_path / "promessas.json")
    monkeypatch.setattr(armazem, "EXECUCAO", tmp_path / "exec.json")
    monkeypatch.setattr(build, "DIST", tmp_path / "site")
    monkeypatch.setattr(build, "date", type("D", (build.date,), {"today": staticmethod(lambda: build.date(2026, 11, 10))}))
    monkeypatch.setenv("SITE_URL", "https://cumpriu.exemplo/")
    build.main()

    site = tmp_path / "site"
    feed = (site / "feed.xml").read_text()
    assert "<rss" in feed and "https://cumpriu.exemplo/promessa/p1.html" in feed and "Em andamento" in feed
    mudancas = (site / "mudancas.html").read_text()
    assert "Semana de 02/11/2026 a 08/11/2026" in mudancas and "antes da posse" in mudancas
    pagina = (site / "promessa" / "p1.html").read_text()
    assert 'property="og:url" content="https://cumpriu.exemplo/promessa/p1.html"' in pagina
    assert 'og:image" content="https://cumpriu.exemplo/static/og.png"' in pagina
    assert "site do partido" in pagina and "antes da posse" in pagina
    assert (site / "static" / "og.png").exists()
