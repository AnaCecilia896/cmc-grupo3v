# Relatório Semanal — Cantucci / Grupo 3V

Dashboard de CMV/CMC semanal das unidades, em modo somente leitura no Streamlit Cloud.

## Deploy

1. Conta no Streamlit Cloud (https://share.streamlit.io) → New app
2. Repository: `Guidesordi/relatorio-semanal`
3. Main file: `relatorio_semanal.py`
4. Advanced settings → Secrets → cole:
   ```toml
   app_password = "<senha-dos-gerentes>"
   ```
5. Deploy

## Limitações em cloud

- **Atualização de dados** (botão na app) só funciona localmente. No cloud o banco fica congelado na versão do último commit.
- Pra atualizar dados no cloud: rodar `atualizar_dados.py` localmente → `git add banco_central.db` → `git commit` → `git push`. A próxima carga do cloud já reflete.

## Local

```
pip install -r requirements.txt
streamlit run relatorio_semanal.py --server.port 8504
```
