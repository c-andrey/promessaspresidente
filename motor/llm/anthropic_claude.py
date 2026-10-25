"""Provedor Claude (pago), pensado para ficar no fim da cadeia.

Usa structured outputs (output_config.format), então a resposta já vem como
JSON válido no schema pedido.
"""
import os

import anthropic

from . import limites
from .erros import (CotaEsgotada, ErroAutenticacao, ErroTemporario, LimiteTemporario,
                    RespostaInvalida, Sobrecarga)


class ProvedorAnthropic:
    def __init__(self, cfg: dict):
        self.nome = cfg["nome"]
        self.modelo = cfg["modelo"]
        self.chave_env = cfg.get("chave_env", "ANTHROPIC_API_KEY")
        self.limite_diario = cfg.get("limite_diario")
        self.rpm = cfg.get("rpm")
        self.tpm = cfg.get("tpm")
        self.intervalo = cfg.get("intervalo_segundos", 0)
        self.esforco = cfg.get("esforco", "low")
        self.max_tokens = cfg.get("max_tokens", 8000)
        self.tokens_usados = 0
        self.pausa_sugerida = 0.0
        self.cota_acabou = False
        self._cliente = None

    def disponivel(self) -> bool:
        return bool(os.environ.get(self.chave_env))

    @property
    def cliente(self) -> anthropic.Anthropic:
        if self._cliente is None:
            self._cliente = anthropic.Anthropic(api_key=os.environ[self.chave_env])
        return self._cliente

    def completar(self, sistema: str, usuario: str, schema: dict) -> str:
        try:
            resp = self.cliente.messages.create(
                model=self.modelo,
                max_tokens=self.max_tokens,
                # Instruções idênticas em toda chamada: cache barateia o prefixo.
                system=[{"type": "text", "text": sistema, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": usuario}],
                output_config={
                    "effort": self.esforco,
                    "format": {"type": "json_schema", "schema": schema},
                },
            )
        except anthropic.AuthenticationError as e:
            raise ErroAutenticacao(str(e)) from e
        except anthropic.PermissionDeniedError as e:
            raise ErroAutenticacao(str(e)) from e
        except anthropic.RateLimitError as e:
            espera = limites.segundos(e.response.headers.get("retry-after")) or 30
            raise LimiteTemporario(str(e), espera) from e
        except anthropic.BadRequestError as e:
            # Saldo insuficiente chega como 400; tratamos como cota.
            if "credit" in str(e).lower() or "billing" in str(e).lower():
                raise CotaEsgotada(str(e)) from e
            raise RespostaInvalida(str(e)) from e
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                raise Sobrecarga(f"HTTP {e.status_code}") from e
            raise RespostaInvalida(str(e)) from e
        except anthropic.APIConnectionError as e:
            raise ErroTemporario(str(e)) from e

        if resp.stop_reason == "refusal":
            raise RespostaInvalida(f"recusa: {resp.stop_details}")
        if resp.stop_reason == "max_tokens":
            raise RespostaInvalida("resposta truncada (max_tokens)")
        self.tokens_usados = resp.usage.input_tokens + resp.usage.output_tokens
        return next((b.text for b in resp.content if b.type == "text"), "")
