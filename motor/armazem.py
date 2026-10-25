"""Leitura e escrita dos arquivos JSON/YAML do repositório.

Todo o estado do projeto vive em data/ e é versionado no git: cada mudança de
status vira um commit, e o histórico do repositório é a trilha de auditoria.
"""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "config"
DADOS = RAIZ / "data"
ESTADO = DADOS / "estado"

PROMESSAS = DADOS / "promessas.json"
FILA = DADOS / "fila_revisao.json"
VISTOS = ESTADO / "vistos.json"
PENDENTES = ESTADO / "pendentes.json"
USO_LLM = ESTADO / "uso_llm.json"
EXECUCAO = ESTADO / "ultima_execucao.json"

STATUS = ["nao_iniciada", "em_andamento", "parcial", "cumprida", "abandonada"]


def ler_json(caminho: Path, padrao):
    if not caminho.exists():
        return padrao
    return json.loads(caminho.read_text(encoding="utf-8"))


def gravar_json(caminho: Path, dados) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    texto = json.dumps(dados, ensure_ascii=False, indent=2, sort_keys=False)
    caminho.write_text(texto + "\n", encoding="utf-8")


def ler_yaml(nome: str) -> dict:
    return yaml.safe_load((CONFIG / nome).read_text(encoding="utf-8"))


def hoje() -> str:
    return date.today().isoformat()


def agora() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def documentos_do_dia(dia: str) -> Path:
    return DADOS / "documentos" / f"{dia}.json"
