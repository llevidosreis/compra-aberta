from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from src.clients.opencnpj_client import DadosEmpresa
from src.load.load_dimensoes import upsert_empresas_enriquecidas


@patch("src.load.load_dimensoes.executar_muitos")
def test_upsert_atualiza_porte_mei_e_data_da_fonte(executar_muitos):
    empresa = DadosEmpresa(
        cnpj="31748439000120",
        razao_social="Empresa",
        nome_fantasia=None,
        situacao_cadastral="Ativa",
        data_inicio_atividade=None,
        cnae_principal=None,
        cnaes_secundarios=[],
        uf="CE",
        municipio="Fortaleza",
        porte_empresa="Empresa de Pequeno Porte (EPP)",
        opcao_mei="N",
        fonte_atualizada_em=datetime(2026, 10, 1, tzinfo=timezone.utc),
        encontrado=True,
    )

    upsert_empresas_enriquecidas(MagicMock(), [empresa])

    sql, linhas = executar_muitos.call_args.args[1:]
    assert "ON CONFLICT (cnpj) DO UPDATE SET" in sql
    assert "opcao_mei = EXCLUDED.opcao_mei" in sql
    assert "fonte_atualizada_em = EXCLUDED.fonte_atualizada_em" in sql
    assert "porte_empresa = EXCLUDED.porte_empresa" in sql
    assert linhas[0][7:10] == (
        "Empresa de Pequeno Porte (EPP)",
        "N",
        datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
