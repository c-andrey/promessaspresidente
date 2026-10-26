"""Cadeia de provedores com fallback e controle de ritmo.

Para cada pedido, tenta os provedores na ordem da configuração:
- ritmo preventivo: respeita rpm (requisições/min) e tpm (tokens/min) de cada um,
  para não tomar 429 à toa;
- 429 por minuto (espera curta): espera e tenta de novo no mesmo provedor;
- 429 do dia / limite_diario / 402: provedor fora até amanhã (persistido em uso_llm.json);
- 503/502/500 (sobrecarga): pausa o provedor alguns minutos e segue para o próximo;
- resposta fora do schema: uma nova tentativa, depois o próximo.
Se todos estiverem só pausados, espera o primeiro liberar (até ESPERA_GLOBAL_MAX).
"""
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field

import jsonschema

from .. import armazem, saude
from .anthropic_claude import ProvedorAnthropic
from .erros import (CotaEsgotada, ErroAutenticacao, ErroTemporario, LimiteTemporario,
                    RespostaInvalida, SemProvedorDisponivel, Sobrecarga)
from .openai_compat import ProvedorOpenAI

log = logging.getLogger(__name__)

TIPOS = {"openai": ProvedorOpenAI, "anthropic": ProvedorAnthropic}
TENTATIVAS_POR_PROVEDOR = 3
FALHAS_PARA_DESISTIR = 3
ESPERA_CURTA = 65          # 429 por minuto até isso: espera no mesmo provedor
ESPERA_RITMO_TOLERADA = 10 # ritmo pede mais que isso: usa outro provedor livre
PAUSA_SOBRECARGA = 300     # 503: provedor descansa 5 min
PAUSA_REDE = 120           # timeout repetido
ESPERA_GLOBAL_MAX = 180    # todos pausados: espera o primeiro liberar se for até isso
ESPERAS_GLOBAIS = 3


@dataclass
class Resultado:
    dados: dict
    provedor: str


def familia(nome: str) -> str:
    """Empresa por trás do provedor: "groq-gpt-oss-120b" → "groq", "gemini-lite" → "gemini"."""
    return nome.split("-")[0]


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
            # Guarda só o dia corrente: histórico de uso não interessa.
            armazem.gravar_json(self.caminho, {self.dia: self.hoje})


def extrair_json(texto: str) -> dict:
    texto = texto.strip()
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto)
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        inicio, fim = texto.find("{"), texto.rfind("}")
        if inicio == -1 or fim <= inicio:
            raise RespostaInvalida(f"sem JSON na resposta: {texto[:120]}")
        try:
            return json.loads(texto[inicio:fim + 1])
        except json.JSONDecodeError as e:
            raise RespostaInvalida(f"JSON quebrado: {e}") from e


def estimar_tokens(*textos: str) -> int:
    # ~3 caracteres por token em português + folga para a resposta
    return sum(len(t) for t in textos) // 3 + 600


@dataclass
class Cadeia:
    provedores: list
    uso: RegistroUso
    dormir: callable = time.sleep
    relogio: callable = time.monotonic
    _fora: set = field(default_factory=set)
    _falhas: dict = field(default_factory=dict)
    _pausado_ate: dict = field(default_factory=dict)
    _ultima_chamada: dict = field(default_factory=dict)
    _janela_tokens: dict = field(default_factory=dict)

    @classmethod
    def da_config(cls, uso: RegistroUso | None = None) -> "Cadeia":
        cfg = armazem.ler_yaml("provedores.yaml")
        provedores = [TIPOS[p["tipo"]](p) for p in cfg["provedores"] if p.get("ativo")]
        return cls(provedores, uso or RegistroUso())

    def ativos(self) -> list[str]:
        return [p.nome for p in self.provedores if self._utilizavel(p)]

    # --- estado de cada provedor ---------------------------------------------------

    def _utilizavel(self, p) -> bool:
        return (p.nome not in self._fora and p.disponivel()
                and not self.uso.esgotado(p.nome, p.limite_diario))

    def _livre_em(self, p) -> float:
        return max(0.0, self._pausado_ate.get(p.nome, 0) - self.relogio())

    def _pausar(self, p, segundos: float) -> None:
        self._pausado_ate[p.nome] = max(self._pausado_ate.get(p.nome, 0), self.relogio() + segundos)

    def _esgotar(self, p, motivo: str) -> None:
        log.warning("[%s] cota do dia acabou (%s) → fora até amanhã", p.nome, motivo)
        self.uso.marcar_esgotado(p.nome)

    # --- ritmo preventivo ----------------------------------------------------------

    def _espera_ritmo(self, p, estimativa: int) -> float:
        """Quantos segundos o provedor precisa esperar para respeitar rpm/tpm agora."""
        agora = self.relogio()
        espera = 0.0
        intervalo = max(getattr(p, "intervalo", 0) or 0, 60 / p.rpm if getattr(p, "rpm", None) else 0)
        ultima = self._ultima_chamada.get(p.nome)
        if ultima is not None and intervalo:
            espera = intervalo - (agora - ultima)
        tpm = getattr(p, "tpm", None)
        if tpm:
            janela = self._janela_tokens.setdefault(p.nome, deque())
            while janela and agora - janela[0][0] >= 60:
                janela.popleft()
            usado = sum(t for _, t in janela)
            for instante, tokens in janela:  # quanto esperar até sobrar espaço na janela
                if usado + estimativa <= tpm:
                    break
                usado -= tokens
                espera = max(espera, instante + 60 - agora)
        return max(0.0, espera)

    def _respeitar_ritmo(self, p, estimativa: int) -> None:
        espera = self._espera_ritmo(p, estimativa)
        if espera > 0:
            if espera > 5:
                log.info("[%s] ritmo: aguardando %.0f s", p.nome, espera)
            self.dormir(espera)
        self._ultima_chamada[p.nome] = self.relogio()

    def _registrar_tokens(self, p, tokens: int) -> None:
        if getattr(p, "tpm", None):
            self._janela_tokens.setdefault(p.nome, deque()).append((self.relogio(), tokens))

    # --- chamada ----------------------------------------------------------------------

    def completar_json(self, sistema: str, usuario: str, schema: dict,
                       validar=None, evitar=()) -> Resultado:
        """evitar: provedores (ou famílias, ex. "groq") que só entram se nenhum outro responder —
        é assim que sai a segunda opinião de outra empresa."""
        estimativa = estimar_tokens(sistema, usuario, json.dumps(schema))
        evitar = {familia(e) for e in evitar}
        for _ in range(ESPERAS_GLOBAIS + 1):
            tentados = set()
            while True:
                livres = [p for p in self.provedores if p.nome not in tentados
                          and self._utilizavel(p) and not self._livre_em(p)]
                candidatos = [p for p in livres if familia(p.nome) not in evitar] or livres
                if not candidatos:
                    break
                # Rodízio: na ordem da config, o primeiro que pode chamar já (ou quase);
                # se todos precisam esperar o ritmo, o que libera antes.
                esperas = {p.nome: self._espera_ritmo(p, estimativa) for p in candidatos}
                p = next((p for p in candidatos if esperas[p.nome] <= ESPERA_RITMO_TOLERADA),
                         min(candidatos, key=lambda p: esperas[p.nome]))
                tentados.add(p.nome)
                r = self._tentar(p, sistema, usuario, schema, validar, estimativa)
                if r:
                    return r
            pausados = [self._livre_em(p) for p in self.provedores if self._utilizavel(p) and self._livre_em(p)]
            if not pausados or min(pausados) > ESPERA_GLOBAL_MAX:
                break
            log.info("Todos os provedores disponíveis estão em pausa: aguardando %.0f s", min(pausados))
            self.dormir(min(pausados) + 1)
        saude.registrar("sem_ia", "o que sobrou fica para o próximo sync")
        raise SemProvedorDisponivel("nenhum provedor conseguiu responder")

    def _tentar(self, p, sistema, usuario, schema, validar, estimativa) -> Resultado | None:
        for tentativa in range(TENTATIVAS_POR_PROVEDOR):
            try:
                self._respeitar_ritmo(p, estimativa)
                texto = p.completar(sistema, usuario, schema)
                self.uso.registrar(p.nome)
                self._registrar_tokens(p, getattr(p, "tokens_usados", 0) or estimativa)
                if getattr(p, "cota_acabou", False):
                    self._esgotar(p, "cabeçalho remaining-requests = 0")
                if getattr(p, "pausa_sugerida", 0):
                    self._pausar(p, p.pausa_sugerida)
                dados = extrair_json(texto)
                try:
                    jsonschema.validate(dados, schema)
                except jsonschema.ValidationError as e:
                    raise RespostaInvalida(f"fora do schema: {e.message[:120]}") from e
                if validar:
                    validar(dados)
                self._falhas[p.nome] = 0
                return Resultado(dados, p.nome)
            except CotaEsgotada as e:
                self._esgotar(p, str(e))
                return None
            except LimiteTemporario as e:
                if e.espera <= ESPERA_CURTA:
                    log.info("[%s] limite por minuto → aguardando %.0f s", p.nome, e.espera)
                    self.dormir(e.espera + 1)
                    continue
                log.warning("[%s] limite com espera longa (%.0f s) → pausado, seguindo adiante", p.nome, e.espera)
                self._pausar(p, e.espera)
                return None
            except Sobrecarga as e:
                log.warning("[%s] sobrecarregado (%s) → pausa de %d min, seguindo adiante",
                            p.nome, e, PAUSA_SOBRECARGA // 60)
                self._pausar(p, PAUSA_SOBRECARGA)
                return None
            except ErroAutenticacao as e:
                log.error("[%s] chave recusada (%s) → ignorado nesta execução", p.nome, e)
                saude.registrar("chave_recusada", p.nome)
                self._fora.add(p.nome)
                return None
            except ErroTemporario as e:
                self._falhas[p.nome] = self._falhas.get(p.nome, 0) + 1
                if self._falhas[p.nome] >= FALHAS_PARA_DESISTIR:
                    log.warning("[%s] rede falhou %d vezes seguidas → ignorado nesta execução",
                                p.nome, self._falhas[p.nome])
                    self._fora.add(p.nome)
                    return None
                log.info("[%s] %s → nova tentativa", p.nome, e)
                self.dormir(2 ** (tentativa + 1))
            except RespostaInvalida as e:
                log.info("[%s] resposta inválida (tentativa %d): %s", p.nome, tentativa + 1, e)
        return None
