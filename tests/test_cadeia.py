import pytest

from motor.llm.cadeia import Cadeia, RegistroUso, extrair_json
from motor.llm.erros import (CotaEsgotada, ErroAutenticacao, ErroTemporario,
                             RespostaInvalida, SemProvedorDisponivel)

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


class Falso:
    def __init__(self, nome, respostas, limite=None, chave=True):
        self.nome, self.respostas, self.limite_diario = nome, list(respostas), limite
        self.intervalo, self.chave, self.chamadas = 0, chave, 0

    def disponivel(self):
        return self.chave

    def completar(self, sistema, usuario, schema):
        self.chamadas += 1
        r = self.respostas.pop(0) if self.respostas else '{"ok": true}'
        if isinstance(r, Exception):
            raise r
        return r


def cadeia(*provedores):
    return Cadeia(list(provedores), RegistroUso(caminho=None), dormir=lambda s: None)


def test_usa_o_primeiro_quando_funciona():
    a, b = Falso("a", ['{"ok": true}']), Falso("b", [])
    r = cadeia(a, b).completar_json("s", "u", SCHEMA)
    assert r.provedor == "a" and b.chamadas == 0


def test_cota_esgotada_passa_para_o_proximo_e_nao_volta():
    a, b = Falso("a", [CotaEsgotada("429")]), Falso("b", [])
    c = cadeia(a, b)
    assert c.completar_json("s", "u", SCHEMA).provedor == "b"
    assert c.completar_json("s", "u", SCHEMA).provedor == "b"
    assert a.chamadas == 1
    assert c.uso.hoje["a"]["esgotado"]


def test_limite_diario_preventivo():
    a, b = Falso("a", [], limite=1), Falso("b", [])
    c = cadeia(a, b)
    assert c.completar_json("s", "u", SCHEMA).provedor == "a"
    assert c.completar_json("s", "u", SCHEMA).provedor == "b"


def test_resposta_invalida_tenta_de_novo_antes_de_pular():
    a = Falso("a", ["isso não é json", '```json\n{"ok": true}\n```'])
    assert cadeia(a).completar_json("s", "u", SCHEMA).provedor == "a"
    assert a.chamadas == 2


def test_fora_do_schema_conta_como_invalida():
    a, b = Falso("a", ['{"x": 1}', '{"x": 1}']), Falso("b", [])
    assert cadeia(a, b).completar_json("s", "u", SCHEMA).provedor == "b"


def test_erro_temporario_repete_e_autenticacao_desliga():
    a = Falso("a", [ErroTemporario("503"), '{"ok": true}'])
    assert cadeia(a).completar_json("s", "u", SCHEMA).provedor == "a"
    b, d = Falso("b", [ErroAutenticacao("401")]), Falso("d", [])
    c = cadeia(b, d)
    c.completar_json("s", "u", SCHEMA)
    c.completar_json("s", "u", SCHEMA)
    assert b.chamadas == 1


def test_sem_chave_e_pulado_e_tudo_esgotado_levanta():
    a = Falso("a", [], chave=False)
    b = Falso("b", [CotaEsgotada("x")])
    with pytest.raises(SemProvedorDisponivel):
        cadeia(a, b).completar_json("s", "u", SCHEMA)
    assert a.chamadas == 0


def test_validador_extra():
    def validar(d):
        raise RespostaInvalida("não")
    a, b = Falso("a", []), Falso("b", [])
    with pytest.raises(SemProvedorDisponivel):
        cadeia(a, b).completar_json("s", "u", SCHEMA, validar=validar)


def test_extrair_json_com_lixo_em_volta():
    assert extrair_json('Claro! Aqui está: {"ok": true} Espero ter ajudado') == {"ok": True}
