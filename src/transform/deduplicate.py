"""
Deduplicação de um lote antes do upsert.

O Postgres recusa um único comando "INSERT ... ON CONFLICT DO UPDATE"
que tente afetar a mesma linha (mesma chave) duas vezes dentro do
mesmo lote. Isso pode acontecer porque a paginação da API do TCE-CE
não garante exclusividade entre páginas em todos os cenários, então
deduplicamos por chave natural antes de montar o lote — mantendo
sempre o último registro visto para aquela chave.
"""
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def deduplicar_por_chave(registros: list[T], chave: Callable[[T], tuple]) -> list[T]:
    vistos: dict[tuple, T] = {}
    for registro in registros:
        vistos[chave(registro)] = registro
    return list(vistos.values())
