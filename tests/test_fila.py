from motor import armazem, fila


def promessa(status="nao_iniciada"):
    return {"id": "p1", "titulo": "Fim da 6x1", "status": status, "historico": []}


def item(url, veiculo, apuracao="apurada", oficial=False, status="em_andamento", criado="2026-11-20", **extra):
    trechos = [{"url": url, "veiculo": veiculo, "trecho": "o governo enviou o projeto"}] if apuracao == "apurada" else []
    return {"id": url[-6:], "promessa_id": "p1", "promessa": "Fim da 6x1", "status_atual": "nao_iniciada",
            "status_sugerido": status, "confianca": 0.8, "evidencia": "e", "justificativa": "j", "provedor": "groq",
            "documento": {"fonte": veiculo, "titulo": f"t {url}", "url": url, "data": "2026-11-19", "oficial": oficial},
            "criado_em": criado, "origem": "noticias",
            **({"apuracao": {"status": apuracao, "trechos": trechos}} if apuracao else {}), **extra}


def isolar(tmp_path, monkeypatch):
    monkeypatch.setattr(fila, "ARQUIVADOS", tmp_path / "arq.json")
    monkeypatch.setattr(fila, "ESTADO_ACOMPANHAMENTO", tmp_path / "exec.json")


def test_junta_por_promessa_e_status_e_tira_o_sem_prova(tmp_path, monkeypatch):
    isolar(tmp_path, monkeypatch)
    antiga = [item("https://g1.globo.com/a", "G1"), item("https://g1.globo.com/a?utm_source=x", "G1"),
              item("https://uol.com.br/b", "UOL", apuracao=None), item("https://folha.com/c", "Folha", apuracao="nao_confirmada"),
              item("https://oglobo.com/d", "O Globo", status="cumprida")]
    nova, stats = fila.arrumar(antiga, [promessa()], hoje="2026-11-23")
    assert stats["sem_prova"] == 2
    assert [(i["status_sugerido"], len(i["evidencias"])) for i in nova] == [("em_andamento", 1), ("cumprida", 1)]
    assert armazem.ler_json(fila.ESTADO_ACOMPANHAMENTO, {})["prioridade"] == ["p1"]  # o sem prova vai ser apurado
    assert len(armazem.ler_json(fila.ARQUIVADOS, [])) == 2


def test_dois_veiculos_confirmados_aplicam_em_andamento(tmp_path, monkeypatch):
    isolar(tmp_path, monkeypatch)
    p = promessa()
    nova, stats = fila.arrumar([item("https://g1.globo.com/a", "G1"), item("https://uol.com.br/b", "UOL")], [p], hoje="2026-11-23")
    assert nova == [] and stats["aplicados"] == 1
    assert p["status"] == "em_andamento" and p["historico"][-1]["origem"] == "noticias_confirmadas"


def test_site_de_partido_e_checagem_impedem_o_automatico(tmp_path, monkeypatch):
    isolar(tmp_path, monkeypatch)
    p = promessa()
    com_partido = [item("https://g1.globo.com/a", "G1"), item("https://pt.org.br/b", "PT")]
    nova, stats = fila.arrumar(com_partido, [p], hoje="2026-11-23")
    assert stats["aplicados"] == 0 and len(nova) == 1
    desmentida = [item("https://g1.globo.com/a", "G1"),
                  item("https://uol.com.br/b", "UOL", checagem=[{"agencia": "Lupa", "avaliacao": "Falso"}])]
    nova, stats = fila.arrumar(desmentida, [p], hoje="2026-11-23")
    assert stats["aplicados"] == 0 and p["status"] == "nao_iniciada"


def test_cumprida_nunca_e_automatica_e_item_vencido_sai(tmp_path, monkeypatch):
    isolar(tmp_path, monkeypatch)
    p = promessa()
    itens = [item("https://g1.globo.com/a", "G1", status="cumprida"), item("https://uol.com.br/b", "UOL", status="cumprida"),
             item("https://folha.com/c", "Folha", status="parcial", criado="2026-10-01")]
    nova, stats = fila.arrumar(itens, [p], hoje="2026-11-23")
    assert [i["status_sugerido"] for i in nova] == ["cumprida"] and stats["vencidos"] == 1 and p["status"] == "nao_iniciada"


def test_documento_oficial_entra_sem_apuracao_e_status_ja_aplicado_sai(tmp_path, monkeypatch):
    isolar(tmp_path, monkeypatch)
    oficial = item("https://camara.leg.br/x", "Câmara", apuracao=None, oficial=True, status="parcial")
    nova, _ = fila.arrumar([oficial], [promessa()], hoje="2026-11-23")
    assert len(nova) == 1 and "origem" not in nova[0]
    nova, stats = fila.arrumar([oficial], [promessa("parcial")], hoje="2026-11-23")
    assert nova == [] and stats["superados"] == 1


def test_governo_e_mesmo_dominio_nao_contam_e_desfazer_volta(tmp_path, monkeypatch):
    isolar(tmp_path, monkeypatch)
    p = promessa()
    itens = [item("https://www.gov.br/x", "GOV.BR"), item("https://agenciabrasil.ebc.com.br/y", "Agência Brasil"),
             item("https://g1.globo.com/a", "G1"), item("https://g1.globo.com/b", "G1 Política")]
    nova, stats = fila.arrumar(itens, [p], hoje="2026-11-23")
    assert stats["aplicados"] == 0 and fila.veiculos_confirmados(nova[0]) == {"g1.globo.com"}
    nova, stats = fila.arrumar(itens + [item("https://uol.com.br/c", "UOL")], [p], hoje="2026-11-23")
    assert stats["aplicados"] == 1 and "G1, UOL" in p["historico"][-1]["justificativa"]

    from motor import revisar
    monkeypatch.setattr(armazem, "PROMESSAS", tmp_path / "promessas.json")
    monkeypatch.setattr(revisar, "REJEITADOS", tmp_path / "rej.json")
    armazem.gravar_json(armazem.PROMESSAS, [p])
    assert revisar.desfazer("p1") == "p1 voltou para nao_iniciada."
    assert armazem.ler_json(armazem.PROMESSAS, [])[0]["historico"] == []
