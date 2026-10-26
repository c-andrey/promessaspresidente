import json

from motor import apuracao
from motor.llm.cadeia import Cadeia, RegistroUso

PAGINA = """<html><head><script>var x=1</script></head><body><nav><p>Menu menu menu menu menu menu menu menu</p></nav>
<article><p>O presidente Lula disse nesta terça que vai acabar com a escala 6x1 no primeiro ano do novo mandato.</p>
<p>Curto.</p><p>A proposta enfrenta resistência de parte do empresariado, segundo entidades do comércio.</p></article>
<footer><p>Todos os direitos reservados ao portal de notícias exemplo.</p></footer></body></html>"""


def test_extrair_texto_pega_paragrafos_e_ignora_menu_e_rodape():
    texto = apuracao.extrair_texto(PAGINA)
    assert "escala 6x1" in texto and "resistência" in texto
    assert "Menu" not in texto and "direitos reservados" not in texto and "Curto" not in texto


def test_trecho_confere_ignora_acento_caixa_e_aspas():
    texto = apuracao.extrair_texto(PAGINA)
    assert apuracao.trecho_confere("“vai acabar com a ESCALA 6x1 no primeiro ano”", texto)
    assert not apuracao.trecho_confere("vai acabar com a escala 6x1 em 2027", texto)  # inventado
    assert not apuracao.trecho_confere("escala 6x1", texto)  # curto demais para provar algo


class Cliente:
    def get(self, url):
        return type("R", (), {"status_code": 200, "text": PAGINA, "url": url})()


class Verificador:
    def __init__(self, nome, trecho, veredito="confirma"):
        self.nome, self.trecho, self.veredito = nome, trecho, veredito
        self.limite_diario, self.intervalo, self.chamadas = None, 0, 0

    def disponivel(self):
        return True

    def completar(self, sistema, usuario, schema):
        self.chamadas += 1
        return json.dumps({"veredito": self.veredito, "trecho_literal": self.trecho, "explicacao": "x"})


def apurar(*provedores, evitar=()):
    cadeia = Cadeia(list(provedores), RegistroUso(caminho=None), dormir=lambda s: None)
    return apuracao.apurar(cadeia, Cliente(), "Lula prometeu acabar com a escala 6x1",
                           [{"url": "https://g1.globo.com/x", "veiculo": "G1"}], ["escala 6x1"], evitar=evitar)


def test_citacao_real_confirma():
    r = apurar(Verificador("gemini", "vai acabar com a escala 6x1 no primeiro ano do novo mandato"))
    assert r["status"] == "apurada" and r["confirmacoes"] == 1 and r["trechos"][0]["veiculo"] == "G1"


def test_citacao_inventada_pela_ia_e_descartada():
    r = apurar(Verificador("gemini", "Lula garantiu que a escala 6x1 acaba em janeiro de 2027"))
    assert r["status"] == "nao_confirmada" and r["confirmacoes"] == 0


def test_segunda_opiniao_prefere_outra_empresa():
    groq = Verificador("groq-gpt-oss-120b", "vai acabar com a escala 6x1 no primeiro ano do novo mandato")
    gemini = Verificador("gemini-lite", "vai acabar com a escala 6x1 no primeiro ano do novo mandato")
    r = apurar(groq, gemini, evitar=("groq-qwen",))
    assert r["provedores"] == ["gemini-lite"] and groq.chamadas == 0


def test_contradicao():
    r = apurar(Verificador("gemini", "A proposta enfrenta resistência de parte do empresariado", "contradiz"))
    assert r["status"] == "contradita"
