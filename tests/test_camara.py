import httpx
import pytest

from motor.coletores import camara

PROPOSICAO = {"id": 1, "siglaTipo": "MSC", "numero": 817, "ano": 2026, "ementa": "Tratado", "dataApresentacao": "2026-10-28T10:00"}


def _cliente(respostas):
    fila = list(respostas)

    def responder(req):
        r = fila.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    return httpx.Client(transport=httpx.MockTransport(responder))


def test_repete_quando_a_camara_devolve_504():
    c = _cliente([httpx.Response(504), httpx.ReadTimeout("lento"),
                  httpx.Response(200, json={"dados": [PROPOSICAO], "links": []})])
    esperas = []
    corpo = camara._pagina(c, camara.API, {}, dormir=esperas.append)
    assert corpo["dados"][0]["id"] == 1 and esperas == [15, 30]


def test_desiste_depois_das_tentativas():
    c = _cliente([httpx.Response(504)] * camara.TENTATIVAS)
    with pytest.raises(RuntimeError, match="504"):
        camara._pagina(c, camara.API, {}, dormir=lambda s: None)


def test_erro_do_pedido_nao_repete():
    c = _cliente([httpx.Response(400)])
    with pytest.raises(httpx.HTTPStatusError):
        camara._pagina(c, camara.API, {}, dormir=lambda s: None)
