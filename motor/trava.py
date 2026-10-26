"""Uma escrita por vez nos dados: o sync diário, o painel de revisão e os comandos manuais.

Trava de arquivo (flock) no volume: vale entre contêineres que montam o mesmo volume.
Não é reentrante: quem já está dentro da trava não deve pedir de novo.
"""
import fcntl
from contextlib import contextmanager

from . import armazem


class Ocupado(Exception):
    """Outro processo (em geral o sync diário) está gravando agora."""


@contextmanager
def trava(esperar: bool = True):
    caminho = armazem.ESTADO / "sync.lock"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if esperar else fcntl.LOCK_NB))
        except BlockingIOError:
            raise Ocupado("sync em andamento") from None
        yield  # fechar o arquivo solta a trava
