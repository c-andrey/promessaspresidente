from motor.coletores import noticias
from motor.regras import MAX_IMPRENSA, registrar_imprensa

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Governo envia PEC da maioridade penal - G1</title><link>https://n/1</link>
  <pubDate>Mon, 19 Oct 2026 10:00:00 GMT</pubDate><description>&lt;a&gt;texto&lt;/a&gt;</description><source url="x">G1</source></item>
<item><title>Antiga</title><link>https://n/2</link><pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""


class Resp:
    content = RSS

    def raise_for_status(self):
        pass


class Cliente:
    def get(self, url):
        return Resp()


def test_itens_filtra_data_limpa_titulo_e_usa_veiculo():
    docs = noticias._itens(Cliente(), "u", "Feed", "2026-10-01", "2026-10-25")
    assert len(docs) == 1
    d = docs[0]
    assert d["titulo"] == "Governo envia PEC da maioridade penal"
    assert d["fonte"] == "G1" and d["data"] == "2026-10-19" and d["resumo"] == "texto"


def test_registrar_imprensa_sem_repetir_e_com_teto():
    p = {}
    av = {"justificativa": "j"}
    for i in range(MAX_IMPRENSA + 5):
        registrar_imprensa(p, {"fonte": "G1", "titulo": f"t{i}", "url": f"u{i}", "data": f"2026-10-{i % 28 + 1:02d}"}, av)
    registrar_imprensa(p, {"fonte": "G1", "titulo": "dup", "url": "u0", "data": "2026-10-01"}, av)
    assert len(p["imprensa"]) == MAX_IMPRENSA
    assert p["imprensa"][0]["data"] >= p["imprensa"][-1]["data"]
