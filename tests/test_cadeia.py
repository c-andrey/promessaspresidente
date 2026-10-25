import pytest

from motor.llm import limites
from motor.llm.cadeia import Cadeia, RegistroUso, extrair_json
from motor.llm.erros import (CotaEsgotada, ErroAutenticacao, ErroTemporario, LimiteTemporario,
                             RespostaInvalida, SemProvedorDisponivel, Sobrecarga)

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


class Relogio:
    """Relógio falso: dormir só avança o tempo."""

    def __init__(self):
        self.t, self.dormido = 1000.0, []

    def __call__(self):
        return self.t

    def dormir(self, s):
        self.dormido.append(round(s, 1))
        self.t += s


class Falso:
    def __init__(self, nome, respostas, limite=None, chave=True, rpm=None, tpm=None, tokens=0):
        self.nome, self.respostas, self.limite_diario = nome, list(respostas), limite
        self.intervalo, self.rpm, self.tpm, self.tokens_usados = 0, rpm, tpm, tokens
        self.chave, self.chamadas = chave, 0

    def disponivel(self):
        return self.chave

    def completar(self, sistema, usuario, schema):
        self.chamadas += 1
        r = self.respostas.pop(0) if self.respostas else '{"ok": true}'
        if isinstance(r, Exception):
            raise r
        return r


def cadeia(*provedores, relogio=None):
    relogio = relogio or Relogio()
    return Cadeia(list(provedores), RegistroUso(caminho=None), dormir=relogio.dormir, relogio=relogio)


def pedir(c):
    return c.completar_json("s", "u", SCHEMA).provedor


def test_usa_o_primeiro_quando_funciona():
    a, b = Falso("a", ['{"ok": true}']), Falso("b", [])
    assert pedir(cadeia(a, b)) == "a" and b.chamadas == 0


def test_cota_do_dia_passa_para_o_proximo_e_nao_volta():
    a, b = Falso("a", [CotaEsgotada("429 dia")]), Falso("b", [])
    c = cadeia(a, b)
    assert pedir(c) == "b" and pedir(c) == "b"
    assert a.chamadas == 1 and c.uso.hoje["a"]["esgotado"]


def test_limite_diario_preventivo():
    a, b = Falso("a", [], limite=1), Falso("b", [])
    c = cadeia(a, b)
    assert pedir(c) == "a" and pedir(c) == "b"


def test_limite_por_minuto_espera_e_repete_no_mesmo():
    r = Relogio()
    a, b = Falso("a", [LimiteTemporario("429", 20)]), Falso("b", [])
    assert pedir(cadeia(a, b, relogio=r)) == "a"
    assert a.chamadas == 2 and b.chamadas == 0 and 21 in r.dormido


def test_limite_com_espera_longa_pausa_e_segue():
    a, b = Falso("a", [LimiteTemporario("429", 600)]), Falso("b", [])
    c = cadeia(a, b)
    assert pedir(c) == "b"
    assert pedir(c) == "b" and a.chamadas == 1  # continua pausado
    assert not c.uso.hoje["a"]["esgotado"]       # pausa não é cota do dia


def test_sobrecarga_pausa_e_volta_depois():
    r = Relogio()
    a, b = Falso("a", [Sobrecarga("503")]), Falso("b", [])
    c = cadeia(a, b, relogio=r)
    assert pedir(c) == "b"
    r.t += 301
    assert pedir(c) == "a"


def test_todos_pausados_espera_o_primeiro_liberar():
    r = Relogio()
    a = Falso("a", [LimiteTemporario("429", 90)])  # acima de ESPERA_CURTA: pausa 90 s
    c = cadeia(a, relogio=r)
    assert pedir(c) == "a" and a.chamadas == 2
    assert any(s >= 90 for s in r.dormido)


def test_ritmo_por_tokens_por_minuto():
    r = Relogio()
    a = Falso("a", [], tpm=5000, tokens=4600)
    c = cadeia(a, relogio=r)
    pedir(c)
    pedir(c)  # 4600 + estimativa (~630) > 5000: precisa esperar a janela de 60 s
    assert r.dormido and max(r.dormido) >= 59


def test_ritmo_longo_faz_rodizio_para_provedor_livre():
    r = Relogio()
    a = Falso("a", [], tpm=5000, tokens=4600)
    b = Falso("b", [])
    c = cadeia(a, b, relogio=r)
    assert [pedir(c), pedir(c), pedir(c)] == ["a", "b", "b"]
    assert not r.dormido  # não ficou esperando o "a"
    r.t += 61
    assert pedir(c) == "a"  # janela liberou: volta para o primeiro da ordem


def test_ritmo_por_requisicoes_por_minuto():
    r = Relogio()
    a = Falso("a", [], rpm=12)
    c = cadeia(a, relogio=r)
    pedir(c)
    pedir(c)
    assert 5.0 in r.dormido


def test_resposta_invalida_tenta_de_novo_antes_de_pular():
    a = Falso("a", ["isso não é json", '```json\n{"ok": true}\n```'])
    assert pedir(cadeia(a)) == "a" and a.chamadas == 2


def test_fora_do_schema_conta_como_invalida():
    a, b = Falso("a", ['{"x": 1}'] * 3), Falso("b", [])
    assert pedir(cadeia(a, b)) == "b"


def test_erro_de_rede_repete_e_autenticacao_desliga():
    a = Falso("a", [ErroTemporario("timeout"), '{"ok": true}'])
    assert pedir(cadeia(a)) == "a"
    b, d = Falso("b", [ErroAutenticacao("401")]), Falso("d", [])
    c = cadeia(b, d)
    pedir(c)
    pedir(c)
    assert b.chamadas == 1


def test_sem_chave_e_pulado_e_tudo_esgotado_levanta():
    a = Falso("a", [], chave=False)
    b = Falso("b", [CotaEsgotada("x")])
    with pytest.raises(SemProvedorDisponivel):
        pedir(cadeia(a, b))
    assert a.chamadas == 0


def test_validador_extra():
    def validar(d):
        raise RespostaInvalida("não")
    with pytest.raises(SemProvedorDisponivel):
        cadeia(Falso("a", []), Falso("b", [])).completar_json("s", "u", SCHEMA, validar=validar)


def test_extrair_json_com_lixo_em_volta():
    assert extrair_json('Claro! Aqui está: {"ok": true} Espero ter ajudado') == {"ok": True}


# --- interpretação dos 429 de cada provedor ---

def test_segundos():
    assert limites.segundos("1m26.4s") == pytest.approx(86.4)
    assert limites.segundos("7.66s") == pytest.approx(7.66)
    assert limites.segundos("250ms") == pytest.approx(0.25)
    assert limites.segundos("30") == 30
    assert limites.segundos(None) is None


def test_429_groq_por_minuto_e_por_dia():
    por_minuto = limites.classificar_429({"retry-after": "7"}, '{"error":{"message":"Rate limit reached on tokens per minute (TPM)"}}')
    assert por_minuto == (False, 7)
    diario, _ = limites.classificar_429({"retry-after": "300"}, '{"error":{"message":"Rate limit reached on tokens per day (TPD)"}}')
    assert diario


def test_429_gemini_usa_quota_id_e_retry_delay():
    corpo_min = '[{"error":{"code":429,"details":[{"quotaId":"GenerateRequestsPerMinutePerProjectPerModel-FreeTier"},{"retryDelay":"32s"}]}}]'
    assert limites.classificar_429({}, corpo_min) == (False, 32)
    corpo_dia = '[{"error":{"code":429,"details":[{"quotaId":"GenerateRequestsPerDayPerProjectPerModel-FreeTier"},{"retryDelay":"20s"}]}}]'
    assert limites.classificar_429({}, corpo_dia)[0]


def test_mensagem_curta_de_lista_gemini():
    corpo = '[{"error": {"code": 503, "message": "This model is currently experiencing high demand."}}]'
    assert limites.mensagem_curta(corpo) == "This model is currently experiencing high demand."
