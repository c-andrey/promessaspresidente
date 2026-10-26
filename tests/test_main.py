"""Execução diária de ponta a ponta com coleta e IA simuladas, gravando em pasta temporária."""
import argparse
import json
import shutil

import pytest

from motor import armazem, main
from motor.llm.cadeia import Cadeia, RegistroUso


class ProvedorFalso:
    nome, limite_diario, intervalo = "falso", None, 0

    def disponivel(self):
        return True

    def completar(self, sistema, usuario, schema):
        doc_titulo = json.loads(usuario.split("DOCUMENTO:\n")[1].split("\n\nPROMESSAS")[0])["titulo"]
        status = "em_andamento" if doc_titulo.startswith("PEC") else "cumprida"
        return json.dumps({"avaliacoes": [{
            "promessa_id": "fim-escala-6x1", "relevante": True, "status_sugerido": status,
            "confianca": 0.95, "evidencia": "acaba com a escala 6x1", "justificativa": "teste"}]})


DOCS = [
    {"id": "camara:1", "fonte": "Câmara dos Deputados", "oficial": True, "tipo": "PEC",
     "titulo": "PEC 1/2027", "resumo": "Acaba com a escala 6x1 e reduz a jornada para 40 horas.", "url": "u1", "data": "2027-02-01"},
    {"id": "camara:2", "fonte": "Câmara dos Deputados", "oficial": True, "tipo": "MSC",
     "titulo": "MSC 2/2027", "resumo": "Comunica a promulgação da emenda que acaba com a escala 6x1.", "url": "u2", "data": "2027-02-02"},
    {"id": "camara:3", "fonte": "Câmara dos Deputados", "oficial": True, "tipo": "MPV",
     "titulo": "MPV 3/2027", "resumo": "Abre crédito extraordinário.", "url": "u3", "data": "2027-02-02"},
]


@pytest.fixture
def dados_tmp(tmp_path, monkeypatch):
    estado = tmp_path / "estado"
    shutil.copy(armazem.PROMESSAS, tmp_path / "promessas.json")
    for nome, valor in {"DADOS": tmp_path, "ESTADO": estado, "PROMESSAS": tmp_path / "promessas.json",
                        "FILA": tmp_path / "fila.json", "VISTOS": estado / "vistos.json",
                        "PENDENTES": estado / "pendentes.json", "USO_LLM": estado / "uso.json",
                        "EXECUCAO": estado / "exec.json"}.items():
        monkeypatch.setattr(armazem, nome, valor)
    monkeypatch.setattr(armazem, "documentos_do_dia", lambda dia: tmp_path / f"docs-{dia}.json")
    monkeypatch.setattr(main, "coletar_tudo", lambda desde, ate, cfg: ([dict(d) for d in DOCS], {"camara": 3}))
    monkeypatch.setattr(main.Cadeia, "da_config",
                        classmethod(lambda cls: Cadeia([ProvedorFalso()], RegistroUso(caminho=None))))
    return tmp_path


def args(**kw):
    return argparse.Namespace(**({"desde": "2027-02-01", "ate": "2027-02-02", "sem_ia": False, "dry_run": False} | kw))


def test_fluxo_completo(dados_tmp):
    stats = main.executar(args())
    assert stats["novos"] == 3 and stats["para_ia"] == 2  # MPV de crédito não tem candidata
    assert stats["automatico"] == 1 and stats["fila"] == 1

    promessa = next(p for p in armazem.ler_json(armazem.PROMESSAS, []) if p["id"] == "fim-escala-6x1")
    assert promessa["status"] == "em_andamento"
    assert promessa["historico"][0]["documento"]["url"] == "u1"

    fila = armazem.ler_json(armazem.FILA, [])
    assert fila[0]["status_sugerido"] == "cumprida"  # cumprida nunca é automática

    # Segunda execução: tudo já visto, nada novo.
    assert main.executar(args())["novos"] == 0


def test_sem_provedor_deixa_pendente(dados_tmp, monkeypatch):
    monkeypatch.setattr(main.Cadeia, "da_config", classmethod(lambda cls: Cadeia([], RegistroUso(caminho=None))))
    stats = main.executar(args())
    assert stats["classificados"] == 0 and stats["pendentes"] == 2
    assert len(armazem.ler_json(armazem.PENDENTES, [])) == 2
