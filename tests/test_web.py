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
