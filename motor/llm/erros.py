class ErroProvedor(Exception):
    """Base dos erros que a cadeia sabe tratar."""


class CotaEsgotada(ErroProvedor):
    """Cota do DIA acabou (429 diário, 402, limite_diario): provedor fora até amanhã."""


class LimiteTemporario(ErroProvedor):
    """Limite por minuto (429 com espera curta): vale esperar `espera` segundos e tentar de novo."""

    def __init__(self, msg: str, espera: float):
        super().__init__(msg)
        self.espera = espera


class Sobrecarga(ErroProvedor):
    """Provedor sobrecarregado (503/502/500/504): pausa alguns minutos e segue para o próximo."""


class ErroAutenticacao(ErroProvedor):
    """Chave inválida ou sem permissão: provedor fora desta execução."""


class ErroTemporario(ErroProvedor):
    """Timeout ou rede: vale uma nova tentativa."""


class RespostaInvalida(ErroProvedor):
    """O modelo respondeu, mas não com o JSON esperado (ou recusou)."""


class SemProvedorDisponivel(Exception):
    """Todos os provedores da cadeia estão esgotados ou falharam."""
