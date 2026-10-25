class ErroProvedor(Exception):
    """Base dos erros que a cadeia sabe tratar."""


class CotaEsgotada(ErroProvedor):
    """429/402 ou limite de uso: provedor fora pelo resto do dia."""


class ErroAutenticacao(ErroProvedor):
    """Chave inválida ou sem permissão: provedor fora desta execução."""


class ErroTemporario(ErroProvedor):
    """5xx, timeout, rede: vale tentar de novo."""


class RespostaInvalida(ErroProvedor):
    """O modelo respondeu, mas não com o JSON esperado (ou recusou)."""


class SemProvedorDisponivel(Exception):
    """Todos os provedores da cadeia estão esgotados ou falharam."""
