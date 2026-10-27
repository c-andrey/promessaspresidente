import base64
import threading
from datetime import datetime, timezone
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

from motor import armazem, campanha, painel, saude
from motor.trava import Ocupado, trava
from web import build

PROMESSA = {"id": "p1", "origem": "plano_tse", "titulo": "Promessa um", "categoria": "C", "prazo": None,
            "fonte": {"tipo": "plano_de_governo_tse", "url": "u", "pagina": 1, "trecho": "t"},
            "palavras_chave": ["x"], "status": "nao_iniciada", "atualizado_em": None, "referencias": [], "historico": []}
ITEM = {"id": "f1", "promessa_id": "p1", "promessa": "Promessa um", "status_atual": "nao_iniciada",
        "status_sugerido": "em_andamento", "confianca": 0.7, "evidencia": "trecho", "justificativa": "porque sim",
        "documento": {"id": "d", "fonte": "Câmara", "tipo": "PL", "titulo": "PL 1/2027", "url": "https://c/1",
                      "data": "2027-01-10", "oficial": True}, "provedor": "groq", "criado_em": "2027-01-10"}
CANDIDATA = {"id": "c1", "titulo": "Criar X", "categoria": "C", "contexto": "debate", "o_que_disse": "Vai criar X.",
             "palavras_chave": ["x"], "status": "pendente",
             "fontes": [{"veiculo": "G1", "titulo": "Debate", "url": "https://g1.globo.com/1", "data": "2026-10-20"},
                        {"veiculo": "Partido dos Trabalhadores", "titulo": "Lula", "url": "https://pt.org.br/1",
                         "data": "2026-10-20"}]}


@pytest.fixture
def volume(tmp_path, monkeypatch):
    for nome in ("REFERENCIAS", "CANDIDATAS", "APROVADAS", "REJEITADAS"):
        monkeypatch.setattr(campanha, nome, tmp_path / getattr(campanha, nome).name)
    for nome, arq in (("PROMESSAS", "promessas.json"), ("FILA", "fila.json"), ("EXECUCAO", "exec.json")):
        monkeypatch.setattr(armazem, nome, tmp_path / arq)
    monkeypatch.setattr(armazem, "DADOS", tmp_path)
    monkeypatch.setattr(armazem, "ESTADO", tmp_path / "estado")
    monkeypatch.setattr(painel.revisar, "REJEITADOS", tmp_path / "rejeitados.json")
    monkeypatch.setattr(campanha, "resolver_fontes", lambda c, d: None)
    monkeypatch.setattr(build, "DIST", tmp_path / "site")
    monkeypatch.setenv("REVISAO_SENHA", "s3nha-forte")
    armazem.gravar_json(armazem.PROMESSAS, [PROMESSA])
    armazem.gravar_json(armazem.FILA, [ITEM])
    armazem.gravar_json(campanha.CANDIDATAS, [CANDIDATA])
    return tmp_path


@pytest.fixture
def servidor(volume):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), painel.Painel)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def pedir(porta, metodo, caminho, senha=None, corpo=None):
    c = HTTPConnection("127.0.0.1", porta, timeout=10)
    cab = {"Content-Type": "application/x-www-form-urlencoded"}
    if senha:
        cab["Authorization"] = "Basic " + base64.b64encode(f"revisor:{senha}".encode()).decode()
    c.request(metodo, caminho, body=urlencode(corpo) if corpo else None, headers=cab)
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read().decode()


def test_painel_pede_senha(servidor, monkeypatch):
    monkeypatch.setattr(painel.time, "sleep", lambda s: None)
    assert pedir(servidor, "GET", "/revisar/")[0] == 401
    assert pedir(servidor, "GET", "/revisar/", senha="errada")[0] == 401
    status, cab, html = pedir(servidor, "GET", "/revisar/", senha="s3nha-forte")
    assert status == 200 and cab["X-Robots-Tag"].startswith("noindex")
    assert "Promessa um" in html and "Criar X" in html
    assert "1 veículo(s) de imprensa" in html and "site do partido" in html  # site de partido não conta


def test_painel_desligado_sem_senha(servidor, monkeypatch):
    monkeypatch.delenv("REVISAO_SENHA")
    assert pedir(servidor, "GET", "/revisar/", senha="")[0] == 503


def test_formulario_sem_token_e_recusado(servidor):
    status, _, _ = pedir(servidor, "POST", "/revisar/decidir", "s3nha-forte",
                         {"tipo": "fila", "id": "f1", "acao": "aprovar", "token": "x"})
    assert status == 403
    assert armazem.ler_json(armazem.FILA, []) == [ITEM]


def test_aprovar_mudanca_aplica_status_e_regera_site(servidor):
    status, cab, _ = pedir(servidor, "POST", "/revisar/decidir", "s3nha-forte",
                           {"tipo": "fila", "id": "f1", "acao": "aprovar", "status": "parcial",
                            "nota": "só metade", "token": painel.token_formulario()})
    assert status == 303 and cab["Location"].startswith("/revisar/?msg=")
    p = armazem.ler_json(armazem.PROMESSAS, [])[0]
    assert p["status"] == "parcial" and p["historico"][-1]["origem"] == "revisao" and p["historico"][-1]["nota"] == "só metade"
    assert armazem.ler_json(armazem.FILA, []) == []
    assert "Parcialmente cumprida" in (build.DIST / "promessa" / "p1.html").read_text()


def test_publicar_declaracao(servidor):
    pedir(servidor, "POST", "/revisar/decidir", "s3nha-forte",
          {"tipo": "candidata", "id": "c1", "acao": "aprovar", "token": painel.token_formulario()})
    assert armazem.ler_json(campanha.CANDIDATAS, []) == []
    assert any(p["id"] == "c1" for p in armazem.ler_json(armazem.PROMESSAS, []))


def test_sync_rodando_nao_deixa_gravar(volume):
    with trava():
        msg = painel.decidir({"tipo": "fila", "id": "f1", "acao": "rejeitar"})
    assert "sync diário está gravando" in msg
    assert armazem.ler_json(armazem.FILA, []) == [ITEM]
    with trava(esperar=False):
        pass  # soltou


def test_trava_ocupada_entre_processos(volume):
    import subprocess
    import sys
    codigo = ("import fcntl,sys,time; f=open(sys.argv[1],'w'); fcntl.flock(f, fcntl.LOCK_EX); "
              "print('ok', flush=True); time.sleep(5)")
    (volume / "estado").mkdir(exist_ok=True)
    p = subprocess.Popen([sys.executable, "-c", codigo, str(volume / "estado" / "sync.lock")], stdout=subprocess.PIPE)
    try:
        p.stdout.readline()
        with pytest.raises(Ocupado):
            with trava(esperar=False):
                pass
    finally:
        p.kill()


def test_saude_aponta_atraso_e_chave_recusada():
    agora = datetime(2027, 1, 3, 12, tzinfo=timezone.utc)
    ok = {"inicio": "2027-01-03T10:00:00+00:00", "fim": "2027-01-03T10:30:00+00:00",
          "etapas": {"coleta": "ok"}, "ocorrencias": [{"tipo": "sem_ia", "detalhe": "x"}]}
    assert saude.problemas(ok, agora, 1) == ([], ["todas as IAs esgotaram: x"])
    atrasado = ok | {"fim": "2027-01-01T10:30:00+00:00", "etapas": {"coleta": "falhou"},
                     "ocorrencias": [{"tipo": "chave_recusada", "detalhe": "groq-qwen"}]}
    graves, _ = saude.problemas(atrasado, agora, 1)
    assert len(graves) == 3 and "chave de IA recusada: groq-qwen" in graves
    assert saude.problemas(atrasado | {"etapas": {}, "ocorrencias": []}, agora, 2) == ([], [])


def test_fonte_fora_um_dia_e_so_aviso():
    agora = datetime(2027, 1, 3, 12, tzinfo=timezone.utc)
    estado = {"inicio": "2027-01-03T10:00:00+00:00", "fim": "2027-01-03T10:30:00+00:00", "etapas": {},
              "ocorrencias": [{"tipo": "coletor_instavel", "detalhe": "camara, sem coletar há 1 dia(s)"}]}
    graves, avisos = saude.problemas(estado, agora, 1)
    assert graves == [] and avisos == ["fonte instável hoje: camara, sem coletar há 1 dia(s)"]
