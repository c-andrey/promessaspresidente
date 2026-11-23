import httpx

from motor import checagem
from motor.coletores import busca, noticias

GOOGLE = b"""<rss><channel><item><title>Lula envia PEC - G1</title><link>https://news.google.com/rss/articles/x</link>
<pubDate>Thu, 19 Nov 2026 10:00:00 GMT</pubDate><source url="g">G1</source></item></channel></rss>"""
BING = b"""<rss xmlns:News="https://www.bing.com/news/search?q=x&amp;format=rss"><channel>
<item><title>Lula assina decreto da 6x1</title>
<link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;url=https%3a%2f%2fwww.correiobraziliense.com.br%2fmateria.html&amp;mkt=pt-br</link>
<pubDate>Thu, 19 Nov 2026 01:10:00 GMT</pubDate><News:Source>Correio Braziliense</News:Source></item>
<item><title>Velha</title><link>https://x/velha</link><pubDate>Thu, 01 Jan 2026 01:10:00 GMT</pubDate></item></channel></rss>"""


def cliente(google_status):
    pedidos = []

    def responder(req):
        pedidos.append(req.url.host)
        if req.url.host == "news.google.com":
            return httpx.Response(google_status, content=GOOGLE)
        return httpx.Response(200, content=BING)
    return httpx.Client(transport=httpx.MockTransport(responder)), pedidos


def relogio_falso():
    t = [1000.0]
    return (lambda s: t.__setitem__(0, t[0] + s)), (lambda: t[0])


def test_google_responde():
    busca._estado.update(ultima=0.0, fora_ate=0.0)
    c, pedidos = cliente(200)
    dormir, relogio = relogio_falso()
    docs = busca.buscar(c, '"Lula" after:2026-11-15', "2026-11-15", "2026-11-23", dormir, relogio)
    assert [d["fonte"] for d in docs] == ["G1"] and pedidos == ["news.google.com"]


def test_google_bloqueado_vai_para_o_bing_com_link_real():
    busca._estado.update(ultima=0.0, fora_ate=0.0)
    c, pedidos = cliente(429)
    dormir, relogio = relogio_falso()
    docs = busca.buscar(c, '"Lula" "6x1" after:2026-11-15', "2026-11-15", "2026-11-23", dormir, relogio)
    assert pedidos == ["news.google.com", "news.google.com", "www.bing.com"]
    assert docs[0]["url"] == "https://www.correiobraziliense.com.br/materia.html"
    assert docs[0]["fonte"] == "Correio Braziliense" and len(docs) == 1  # a velha fica fora do período
    busca.buscar(c, '"Lula"', "2026-11-15", "2026-11-23", dormir, relogio)
    assert pedidos[-1] == "www.bing.com"  # Google fica de fora por um tempo
    assert busca._sem_operadores_de_data('"Lula" after:2026-11-15 before:2026-11-23') == '"Lula"'


def test_coletor_de_noticias_segue_quando_uma_fonte_falha(monkeypatch):
    monkeypatch.setattr(noticias.armazem, "ler_yaml", lambda n: {"nome_busca": "Lula"})
    monkeypatch.setattr(busca, "buscar", lambda c, q, d, a: (_ for _ in ()).throw(RuntimeError("fora")))
    monkeypatch.setattr(noticias, "_itens", lambda c, u, n, d, a: [{"id": "noticia:1", "titulo": "t", "url": u}])
    docs = noticias.coletar("2026-11-20", "2026-11-23", {"buscas": ['"{nome}"'], "feeds": [{"nome": "G1", "url": "u"}]})
    assert len(docs) == 1


def test_checagem_so_com_chave_e_so_o_que_desmente(monkeypatch):
    assert checagem.desmentidos(None, "Lula assina decreto") == []
    monkeypatch.setenv("FACTCHECK_API_KEY", "k")
    resposta = {"claims": [{"text": "Lula acabou com a 6x1", "claimReview": [
        {"publisher": {"name": "Lupa"}, "url": "https://lupa/1", "textualRating": "Falso"},
        {"publisher": {"name": "Aos Fatos"}, "url": "https://af/1", "textualRating": "Verdadeiro"}]}]}
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=resposta)))
    assert checagem.desmentidos(c, "Lula acabou com a 6x1") == [
        {"afirmacao": "Lula acabou com a 6x1", "avaliacao": "Falso", "agencia": "Lupa", "url": "https://lupa/1"}]
