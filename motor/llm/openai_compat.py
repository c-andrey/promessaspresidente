"""Provedor genérico para qualquer API compatível com /chat/completions.

Cobre GitHub Models, Gemini, Groq, Mistral, Cloudflare Workers AI, OpenRouter e
Ollama: muda só base_url, modelo, chave e limites na configuração.
"""
import json
import os
import re

import httpx

from . import limites
from .erros import (CotaEsgotada, ErroAutenticacao, ErroTemporario, LimiteTemporario,
                    RespostaInvalida, Sobrecarga)


def expandir_env(texto: str) -> str:
    return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), texto)


class ProvedorOpenAI:
    def __init__(self, cfg: dict):
        self.nome = cfg["nome"]
        self.modelo = cfg["modelo"]
        self.base_url = expandir_env(cfg["base_url"]).rstrip("/")
        self.chave_env = cfg.get("chave_env")
        self.limite_diario = cfg.get("limite_diario")
        self.rpm = cfg.get("rpm")
        self.tpm = cfg.get("tpm")
        self.intervalo = cfg.get("intervalo_segundos", 0)
        self.timeout = cfg.get("timeout_segundos", 90)
        self.modo_json = cfg.get("modo_json", True)
        self.max_saida = cfg.get("max_tokens_saida", 2000)
        # Preenchidos a cada chamada bem-sucedida, lidos pela cadeia.
        self.tokens_usados = 0
        self.pausa_sugerida = 0.0
        self.cota_acabou = False

    def disponivel(self) -> bool:
        if "${" in self.base_url or "//accounts//" in self.base_url:
            return False
        return self.chave_env is None or bool(os.environ.get(self.chave_env))

    def completar(self, sistema: str, usuario: str, schema: dict) -> str:
        sistema_com_schema = (
            f"{sistema}\n\nResponda SOMENTE com um objeto JSON válido neste schema:\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )
        corpo = {
            "model": self.modelo,
            "temperature": 0,
            "max_tokens": self.max_saida,
            "messages": [
                {"role": "system", "content": sistema_com_schema},
                {"role": "user", "content": usuario},
            ],
        }
        if self.modo_json:
            corpo["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        if self.chave_env:
            headers["Authorization"] = f"Bearer {os.environ[self.chave_env]}"

        try:
            r = httpx.post(f"{self.base_url}/chat/completions", json=corpo,
                           headers=headers, timeout=self.timeout)
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            raise ErroTemporario(type(e).__name__) from e

        msg = f"HTTP {r.status_code}: {limites.mensagem_curta(r.text)}"
        if r.status_code == 400 and self.modo_json and "response_format" in r.text:
            # Provedor/modelo não aceita modo JSON: desliga e tenta de novo sem ele.
            self.modo_json = False
            return self.completar(sistema, usuario, schema)
        if r.status_code == 429:
            diario, espera = limites.classificar_429(r.headers, r.text)
            if diario:
                raise CotaEsgotada(msg)
            raise LimiteTemporario(msg, espera)
        if r.status_code == 402:
            raise CotaEsgotada(msg)
        if r.status_code in (401, 403):
            # GitHub Models devolve 403 quando a cota diária acaba.
            if re.search(r"rate|quota|limit", r.text, re.I):
                raise CotaEsgotada(msg)
            raise ErroAutenticacao(msg)
        if r.status_code in (500, 502, 503, 504, 529):
            raise Sobrecarga(msg)
        if r.status_code >= 400:
            raise RespostaInvalida(msg)

        self.cota_acabou, self.pausa_sugerida = limites.pausa_pelos_cabecalhos(r.headers)
        try:
            dados = r.json()
            self.tokens_usados = (dados.get("usage") or {}).get("total_tokens", 0)
            return dados["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError) as e:
            raise RespostaInvalida(f"formato inesperado: {r.text[:200]}") from e
