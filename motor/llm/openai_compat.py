"""Provedor genérico para qualquer API compatível com /chat/completions.

Cobre GitHub Models, Gemini, Groq, Mistral, Cloudflare Workers AI, OpenRouter e
Ollama: muda só base_url, modelo e chave na configuração.
"""
import json
import os
import re

import httpx

from .erros import CotaEsgotada, ErroAutenticacao, ErroTemporario, RespostaInvalida


def expandir_env(texto: str) -> str:
    return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), texto)


class ProvedorOpenAI:
    def __init__(self, cfg: dict):
        self.nome = cfg["nome"]
        self.modelo = cfg["modelo"]
        self.base_url = expandir_env(cfg["base_url"]).rstrip("/")
        self.chave_env = cfg.get("chave_env")
        self.limite_diario = cfg.get("limite_diario")
        self.intervalo = cfg.get("intervalo_segundos", 0)
        self.timeout = cfg.get("timeout_segundos", 90)
        self.modo_json = cfg.get("modo_json", True)

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
            raise ErroTemporario(str(e)) from e

        if r.status_code == 400 and self.modo_json and "response_format" in r.text:
            # Provedor/modelo não aceita modo JSON: desliga e tenta de novo sem ele.
            self.modo_json = False
            return self.completar(sistema, usuario, schema)
        if r.status_code in (402, 429):
            raise CotaEsgotada(f"{r.status_code}: {r.text[:200]}")
        if r.status_code in (401, 403):
            # GitHub Models devolve 403 quando a cota diária acaba.
            if "rate" in r.text.lower() or "quota" in r.text.lower() or "limit" in r.text.lower():
                raise CotaEsgotada(f"{r.status_code}: {r.text[:200]}")
            raise ErroAutenticacao(f"{r.status_code}: {r.text[:200]}")
        if r.status_code >= 500:
            raise ErroTemporario(f"{r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:
            raise RespostaInvalida(f"{r.status_code}: {r.text[:200]}")

        try:
            return r.json()["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError) as e:
            raise RespostaInvalida(f"formato inesperado: {r.text[:200]}") from e
