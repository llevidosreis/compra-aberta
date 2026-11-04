from unittest.mock import MagicMock

import pytest

from src.cache.cnpj_cache import cnpjs_pendentes_de_consulta


def test_cache_limita_cnpjs_pendentes_ao_periodo_e_municipio():
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [("12345678000195",)]
    config = MagicMock(opencnpj_dias_validade_cache=30)

    encontrados = cnpjs_pendentes_de_consulta(
        conn,
        config,
        data_inicio="2025-01-01",
        data_fim="2025-12-31",
        codigo_municipio="010",
    )

    sql, parametros = cursor.execute.call_args.args
    assert encontrados == {"12345678000195"}
    assert "EXISTS" in sql
    assert "f.data_realizacao_licitacao BETWEEN %s AND %s" in sql
    assert "f.codigo_municipio = %s" in sql
    assert parametros[1:4] == ("2025-01-01", "2025-12-31", "010")


def test_cache_rejeita_periodo_incompleto():
    with pytest.raises(ValueError, match="informados juntos"):
        cnpjs_pendentes_de_consulta(
            MagicMock(),
            MagicMock(opencnpj_dias_validade_cache=30),
            data_inicio="2025-01-01",
        )
