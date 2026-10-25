import json

from motor import agendador, armazem


def test_semear_copia_volume_vazio_e_preserva_status(tmp_path, monkeypatch):
    semente = tmp_path / "semente.json"
    volume = tmp_path / "volume" / "promessas.json"
    monkeypatch.setattr(armazem, "SEMENTE", semente)
    monkeypatch.setattr(armazem, "PROMESSAS", volume)
    base = {"categoria": "c", "prazo": None, "fonte": {}, "palavras_chave": ["x"], "historico": []}
    semente.write_text(json.dumps([{"id": "a", "titulo": "A", "status": "nao_iniciada", **base}]))

    agendador.semear()
    assert armazem.ler_json(volume, [])[0]["id"] == "a"

    # Status muda no volume; repositório corrige o título e ganha uma promessa nova.
    atual = armazem.ler_json(volume, [])
    atual[0]["status"] = "em_andamento"
    atual[0]["historico"] = [{"para": "em_andamento"}]
    armazem.gravar_json(volume, atual)
    semente.write_text(json.dumps([
        {"id": "a", "titulo": "A corrigido", "status": "nao_iniciada", **base},
        {"id": "b", "titulo": "B", "status": "nao_iniciada", **base},
    ]))

    agendador.semear()
    por_id = {p["id"]: p for p in armazem.ler_json(volume, [])}
    assert por_id["a"]["titulo"] == "A corrigido"
    assert por_id["a"]["status"] == "em_andamento" and por_id["a"]["historico"]
    assert "b" in por_id
