"""
Configurações do sistema CMV.
Preencha PLANILHAS com os IDs das abas de cada unidade.
"""

CREDENTIALS_FILE = "credentials.json"
DB_FILE = "banco_central.db"

# API cantuccidados.com.br
CANTUCCI_API  = "https://cantuccidados.com.br/api"
CANTUCCI_USER = "ana"
CANTUCCI_PASS = "ana57!"

# Atlas — sistema de compras Grupo 3V (Supabase)
ATLAS_SUPABASE_URL = "https://zouyezwghyellwdclrdy.supabase.co"
ATLAS_ANON_KEY     = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpvdXllendnaHllbGx3ZGNscmR5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzA4NTAxNzUsImV4cCI6MjA4NjQyNjE3NX0"
    ".rcXshA3KpS6LlVM3fJ6oaMkTuoYNmKHC6Lb2Z-QuQb0"
)
ATLAS_EMAIL    = "cmv@cantucci.com.br"
ATLAS_PASSWORD = "cmv54321"
# Mapeamento slug banco → restaurant_id no Atlas
ATLAS_RESTAURANTES = {
    "aguas-claras": "55ebe96c-183e-4c1c-8cf9-922b4cbac2ab",  # Italiano Aguas Claras
    "spq-norte":    "ff7d3f8f-4e9a-43f9-83d1-4470692a04f8",  # Superquadra Norte
    "koji":         "bb307d5d-cec9-4c49-b504-0212d5e57a83",  # Koji
}

# Mapeamento slug DB → id(s) de loja na API (autenticar com form encoding em /auth/login)
# str = loja única; list[str] = múltiplas lojas somadas (ex: Mané)
CANTUCCI_LOJAS = {
    "asa-norte":    "cantucci_an",
    "aguas-claras": "cantucci_ac",
    "asa-sul":      "cantucci_as",
    "spq-norte":    "superquadra",
    "mane":         ["Superquadra Mané", "Véi Chico Mané"],
    # koji: API retorna 0 para todas as datas
}
# Usar faturamento_servico (inclui taxa de serviço) — campo correto para CMV/CMC

# IDs das planilhas Google Sheets por unidade
# Encontre o ID na URL: docs.google.com/spreadsheets/d/<ID>/edit
PLANILHAS = {
    "mane": {
        "nome": "Mané",
        "sheet_id": "1rjbRjfEwWNY2Ok9qrBLge77Z81dtdezObX-ltDwPYRA",
    },
    "aguas-claras": {
        "nome": "Aguas Claras",
        "sheet_id": "1Vd125JANs6eyxq6sCh2HE3lJjmIGGvw-vIaKrfDgWBs",
    },
    "asa-norte": {
        "nome": "Asa Norte",
        "sheet_id": "1edainvwdABQ_hpNz86rVAHD8-BeT0svWjwi_NTOL3pY",
    },
    "asa-sul": {
        "nome": "Asa Sul",
        "sheet_id": "1oDwZEG2yeljo_iqNYI3eie2UHqKM7b9-MSVzYDUvMw4",
    },
    "spq-norte": {
        "nome": "SPQ Norte",
        "sheet_id": "1CAH7zURiQYhoSOK5DJzh31uqRKKjX4s6dwC1SsittHo",
    },
}

# Planilha de Desperdício (Google Forms → Sheets)
DESPERDICIO_SHEET_ID = "1qX36AZptjemPuwzoYq9n3QB7AD3NibhSizLXG9BtFKM"
DESPERDICIO_ABA      = "Respostas ao formulário 1"

# IDs das planilhas DRE por unidade (aba "Dados F360")
# Preencher após receber os IDs
DRE_PLANILHAS = {
    "mane":        {"nome": "Mané",         "sheet_id": "1eYLR4h_Pd6vGpaeS5SjDvhCg2upS8ubwmbiC3ISFBmc"},
    "aguas-claras":{"nome": "Aguas Claras", "sheet_id": "1h7MuiAPBBs-B2J68Rbpl1jL7o8cXimXRaW1Yv33v0DA"},
    "asa-norte":   {"nome": "Asa Norte",    "sheet_id": "1cX54z8MXXvFQchYQLArse65WtMo1KTUTx6ixSuWhVIk"},
    "asa-sul":     {"nome": "Asa Sul",      "sheet_id": "1ZpSm2I69axdeaLjEMLKrHe_tzMuinxV-L0BjsKkIWdQ"},
    "spq-norte":   {"nome": "SPQ Norte",    "sheet_id": "1De7XShzfnG7QnPVfXX81VPRtY7PtNiyyjWhLN0G_U68"},
    "koji":        {"nome": "Koji",         "sheet_id": "1YIHrTfLyKCxwtJskIybwQVikcIz3rVGYUBfXduu1Rys"},
}

DRE_ABA = "DRE"

# Planilhas mensais por unidade (novo formato: CONTAGENS + COMPRAS + VENDAS)
# Pasta Google Drive: https://drive.google.com/drive/folders/1uDzktpe5Cu9gL785cw87oc1AHVqpQEZX
# IDs dos arquivos xlsx de maio/2026 (pasta 05.26):
DRIVE_FOLDER_ID = "1uDzktpe5Cu9gL785cw87oc1AHVqpQEZX"
XLSX_MENSAIS = {
    "2026-05": {
        "asa-norte":    "1obP1S2PWpikE1MTVbIlICeGo0dhIjVtz",  # 05.26 - Cantucci Asa Norte.xlsx
        "asa-sul":      "1k4c6yKClSUio0askECm11qv58aep5eur",  # 05.26 - Cantucci Asa Sul.xlsx
        "aguas-claras": "1rNey0_QLo5a8TodIeXbg1TGKLap-U5Tl",  # 05.26 - Cantucci Aguas Claras.xlsx
        "koji":         "1Cnn2Ht4NUPlGqH0iUoelv3hblAdbuFnu",  # 05.26 - Koji.xlsx
        "spq-norte":    "1YtrW7ydCCavxsYLO_IyyRxf4ZLJg7Za5",  # 05.26 - SPQ Norte.xlsx
        "mane":         "1T78kTduTcLkfcKVNB3TEa7AuMBOGBoTu",  # 05.26 - SPQ Mane.xlsx
    },
}

# Planilhas mensais Google Sheets (formato antigo, Sheets nativo — mantido para retrocompatibilidade)
PLANILHAS_MENSAIS = {
    "asa-norte": {
        "nome": "Asa Norte",
        "sheet_id": "15jEIhjU08g4GJ-_LFI6VMNMdQr8p0hgMxMmevIlhSgI",
    },
    "aguas-claras": {
        "nome": "Aguas Claras",
        "sheet_id": "1zfTUlLDd4ye9_y1YApcJCX6qVxoKkLZC1tOfAEwQ3PY",
    },
    "asa-sul": {
        "nome": "Asa Sul",
        "sheet_id": "15zMXyRPNcM61IMnaqlHb5eISUTFo7-PUo2wRMd0m7ak",
    },
    "koji": {
        "nome": "Koji",
        "sheet_id": "19s5p5FXJXwurg-8TZn3hWFaJUdYj6hZ0E9wPgV-r7bI",
    },
    "mane": {
        "nome": "Mane",
        "sheet_id": "1ooKTg4iwkFiFdmoIjbBS4giFfXGtTtLB57ovKZsy0Ik",
    },
    "spq-norte": {
        "nome": "SPQ Norte",
        "sheet_id": "1AnKj8PE1RZQGLHpxiRYYN5JqKmU5Y1eN5rjOOObxGl0",
    },
}

# Nomes exatos das abas CMV em cada planilha
ABAS = {
    "base_dados":    "BASE DE DADOS CENTRAL",
    "contagem":      "CONTAGEM",
    "compras":       "Lista de Compras",
    "metas_compras": "METAS DE COMPRAS",
    "vendas":        "VENDAS",
    "cmv":           "CMV",
    "desvios":       "DESVIOS",
    "metas":         "Metas",
}
