"""Cadeia de provedores com fallback.

Tenta cada provedor na ordem da configuração. Cota estourada tira o provedor
do dia (persistido em data/estado/uso_llm.json, para a próxima execução já
começar pulando ele). Erros temporários e respostas inválidas ganham uma nova
tentativa antes de passar para o próximo.
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field

import jsonschema

from .. import armazem
from .anthropic_claude import ProvedorAnthropic
from .erros import (CotaEsgotada, ErroAutenticacao, ErroTemporario,
                    RespostaInvalida, SemProvedorDisponivel)
from .openai_compat import ProvedorOpenAI

log = logging.getLogger(__name__)

TIPOS = {"openai": ProvedorOpenAI, "anthropic": ProvedorAnthropic}
TENTATIVAS_POR_PROVEDOR = 2
FALHAS_PARA_DESISTIR = 3


@dataclass
class Resultado:
    dados: dict
    provedor: str


class RegistroUso:
    """Contagem diária de chamadas por provedor, persistida entre execuções."""

    def __init__(self, caminho=armazem.USO_LLM, dia: str | None = None):
        self.caminho = caminho
        self.dia = dia or armazem.hoje()
        tudo = armazem.ler_json(caminho, {}) if caminho else {}
        self.hoje = tudo.get(self.dia, {})

    def _p(self, nome):
        return self.hoje.setdefault(nome, {"chamadas": 0, "esgotado": False})

    def esgotado(self, nome: str, limite: int | None) -> bool:
        p = self._p(nome)
        return p["esgotado"] or (limite is not None and p["chamadas"] >= limite)

    def registrar(self, nome: str) -> None:
        self._p(nome)["chamadas"] += 1

    def marcar_esgotado(self, nome: str) -> None:
        self._p(nome)["esgotado"] = True

    def salvar(self) -> None:
        if self.caminho:
            # Guarda só o dia corrente: histórico de uso não interessa ao repositório.
            armazem.gravar_json(self.caminho, {self.dia: self.hoje})


def extrair_json(texto: str) -> dict:
    texto = texto.strip()
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto)
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        inicio, fim = texto.find("{"), texto.rfind("}")
        if inicio == -1 or fim <= inicio:
            raise RespostaInvalida(f"sem JSON na resposta: {texto[:200]}")
        try:
            return json.loads(texto[inicio:fim + 1])
        except json.JSONDecodeError as e:
            raise RespostaInvalida(f"JSON quebrado: {e}") from e


@dataclass
class Cadeia:
    provedores: list
    uso: RegistroUso
    dormir: callable = time.sleep
    _fora: set = field(default_factory=set)
    _falhas: dict = field(default_factory=dict)
    _ultima_chamada: dict = field(default_factory=dict)

    @classmethod
    def da_config(cls, uso: RegistroUso | None = None) -> "Cadeia":
        cfg = armazem.ler_yaml("provedores.yaml")
        provedores = [TIPOS[p["tipo"]](p) for p in cfg["provedores"] if p.get("ativo")]
        return cls(provedores, uso or RegistroUso())

    def ativos(self) -> list[str]:
        return [p.nome for p in self.provedores if self._utilizavel(p)]

    def _utilizavel(self, p) -> bool:
        return (p.nome not in self._fora and p.disponivel()
                and not self.uso.esgotado(p.nome, p.limite_diario))

    def _respeitar_intervalo(self, p) -> None:
        ultima = self._ultima_chamada.get(p.nome)
        if ultima and p.intervalo:
            espera = p.intervalo - (time.monotonic() - ultima)
            if espera > 0:
                self.dormir(espera)
        self._ultima_chamada[p.nome] = time.monotonic()

    def completar_json(self, sistema: str, usuario: str, schema: dict,
                       validar=None) -> Resultado:
        for p in self.provedores:
            if not self._utilizavel(p):
                continue
            for tentativa in range(TENTATIVAS_POR_PROVEDOR):
                try:
                    self._respeitar_intervalo(p)
                    self.uso.registrar(p.nome)
                    dados = extrair_json(p.completar(sistema, usuario, schema))
                    try:
                        jsonschema.validate(dados, schema)
                    except jsonschema.ValidationError as e:
                        raise RespostaInvalida(f"fora do schema: {e.message}") from e
                    if validar:
                        validar(dados)
                    self._falhas[p.nome] = 0
                    return Resultado(dados, p.nome)
                except CotaEsgotada as e:
                    log.warning("[%s] cota esgotada, passando adiante: %s", p.nome, e)
                    self.uso.marcar_esgotado(p.nome)
                    break
                except ErroAutenticacao as e:
                    log.error("[%s] erro de autenticação, ignorado nesta execução: %s", p.nome, e)
                    self._fora.add(p.nome)
                    break
                except ErroTemporario as e:
                    self._falhas[p.nome] = self._falhas.get(p.nome, 0) + 1
                    log.warning("[%s] falha temporária (%d): %s", p.nome, self._falhas[p.nome], e)
                    if self._falhas[p.nome] >= FALHAS_PARA_DESISTIR:
                        self._fora.add(p.nome)
                        break
                    self.dormir(2 ** (tentativa + 1))
                except RespostaInvalida as e:
                    log.warning("[%s] resposta inválida (tentativa %d): %s", p.nome, tentativa + 1, e)
        raise SemProvedorDisponivel("nenhum provedor conseguiu responder")
