from motor import agendador, armazem


def test_troca_de_presidente_arquiva_volume_e_recomeca(tmp_path, monkeypatch):
    estado = tmp_path / "estado"
    for nome, valor in {"DADOS": tmp_path, "ESTADO": estado, "PROMESSAS": tmp_path / "promessas.json",
                        "FILA": tmp_path / "fila.json", "VISTOS": estado / "vistos.json",
                        "PENDENTES": estado / "pendentes.json", "EXECUCAO": estado / "exec.json",
                        "PRESIDENTE_ATUAL": estado / "presidente.json", "ARQUIVO": tmp_path / "arquivo",
                        "SEMENTE": tmp_path / "semente.json"}.items():
        monkeypatch.setattr(armazem, nome, valor)
    configurado = {"nome": "Lula"}
    monkeypatch.setattr(armazem, "ler_yaml", lambda nome: configurado)

    # volume antigo, de antes do registro de presidente (Flávio)
    armazem.gravar_json(armazem.PROMESSAS, [{"id": "antiga", "status": "em_andamento"}])
    armazem.gravar_json(armazem.FILA, [{"id": "f"}])
    armazem.gravar_json(armazem.VISTOS, ["doc:1"])

    agendador.trocar_presidente_se_preciso()
    assert not armazem.PROMESSAS.exists() and not armazem.VISTOS.exists()
    arquivado = next((tmp_path / "arquivo").iterdir())
    assert arquivado.name.startswith("anterior-")
    assert armazem.ler_json(arquivado / "promessas.json", [])[0]["id"] == "antiga"
    assert armazem.ler_json(armazem.PRESIDENTE_ATUAL, {})["nome"] == "Lula"

    # mesmo presidente: não mexe em nada
    armazem.gravar_json(armazem.PROMESSAS, [{"id": "nova"}])
    agendador.trocar_presidente_se_preciso()
    assert armazem.ler_json(armazem.PROMESSAS, [])[0]["id"] == "nova"

    # trocou de novo: arquiva com o nome registrado
    configurado["nome"] = "Outro"
    agendador.trocar_presidente_se_preciso()
    assert any(p.name.startswith("lula-") for p in (tmp_path / "arquivo").iterdir())
