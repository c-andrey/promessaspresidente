from datetime import date

from motor import armazem
from motor.filtro import candidatas
from motor.regras import AUTO, DESCARTE, FILA, decidir

PROMESSAS = armazem.ler_json(armazem.PROMESSAS, [])
REGRAS = {"confianca_auto": 0.85, "confianca_fila": 0.5}
PRESIDENTE = {"mandato_inicio": date(2027, 1, 1)}


def doc(titulo, resumo="", oficial=True, data="2027-03-01"):
    return {"id": "x", "titulo": titulo, "resumo": resumo, "oficial": oficial, "data": data}


def test_promessas_tem_campos_basicos():
    assert len(PROMESSAS) > 100
    for p in PROMESSAS:
        assert p["palavras_chave"] and p["fonte"]["trecho"] and p["status"] in armazem.STATUS


def test_filtro_acha_promessa_ignorando_acento_e_caixa():
    c = candidatas(doc("PEC 8/2025", "Altera a Constituicao para o fim da ESCALA 6X1 e jornada de 40 horas"), PROMESSAS)
    assert c[0]["id"] == "fim-escala-6x1"


def test_filtro_respeita_limite_de_palavra():
    # "ita" (ITA no Ceará) não pode casar com "Itaipu" nem "capital"
    c = candidatas(doc("Lei sobre a Itaipu Binacional e o capital social"), PROMESSAS)
    assert "ita-ceara" not in [x["id"] for x in c]


def test_filtro_sem_relacao():
    assert candidatas(doc("Abre crédito extraordinário para o Ministério X"), PROMESSAS) == []


def av(status="em_andamento", conf=0.9, relevante=True):
    return {"status_sugerido": status, "confianca": conf, "relevante": relevante}


P = {"status": "nao_iniciada"}


def test_regras_auto_so_no_caso_seguro():
    assert decidir(av(), P, doc("x"), REGRAS, PRESIDENTE) == AUTO
    assert decidir(av(conf=0.7), P, doc("x"), REGRAS, PRESIDENTE) == FILA
    assert decidir(av(), P, doc("x", oficial=False), REGRAS, PRESIDENTE) == FILA
    assert decidir(av(), P, doc("x", data="2026-11-10"), REGRAS, PRESIDENTE) == FILA
    assert decidir(av("cumprida", 0.99), P, doc("x"), REGRAS, PRESIDENTE) == FILA
    assert decidir(av("abandonada", 0.99), P, doc("x"), REGRAS, PRESIDENTE) == FILA


def test_reeleito_conta_atos_desde_acompanhar_desde():
    reeleito = {"mandato_inicio": date(2027, 1, 1), "acompanhar_desde": date(2026, 10, 26)}
    assert decidir(av(), P, doc("x", data="2026-11-10"), REGRAS, reeleito) == AUTO
    assert decidir(av(), P, doc("x", data="2026-10-20"), REGRAS, reeleito) == FILA


def test_regras_descartes():
    assert decidir(av(relevante=False), P, doc("x"), REGRAS, PRESIDENTE) == DESCARTE
    assert decidir(av("sem_mudanca"), P, doc("x"), REGRAS, PRESIDENTE) == DESCARTE
    assert decidir(av(conf=0.3), P, doc("x"), REGRAS, PRESIDENTE) == DESCARTE
    assert decidir(av("em_andamento"), {"status": "em_andamento"}, doc("x"), REGRAS, PRESIDENTE) == DESCARTE
