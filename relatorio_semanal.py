"""
relatorio_semanal.py — Relatório Semanal Cantucci Asa Norte  (reformulado)
Uso: streamlit run relatorio_semanal.py --server.port 8502
"""

import sqlite3
import io
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, date, timedelta
import calendar
import json
import os
import subprocess
from config import DB_FILE
from classificar import classificar as _classificar_codigo, CATS_OPERACIONAIS

_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE = os.path.join(_DIR, "ultima_atualizacao.json")

# ── Integração cantuccidados.com.br ──────────────────────────────────────────
CANTUCCI_API  = "https://cantuccidados.com.br/api"
CANTUCCI_USER = "ana"
CANTUCCI_PASS = "ana57!"
_token_cache: dict = {}

# Mapeamento slug DB → id(s) de loja na API cantuccidados
# Valor pode ser str (uma loja) ou list[str] (múltiplas lojas somadas)
LOJA_POR_SLUG = {
    "asa-norte":    "cantucci_an",
    "aguas-claras": "cantucci_ac",
    "asa-sul":      "cantucci_as",
    "spq-norte":    "superquadra",
    "mane":         ["Superquadra Mané", "Véi Chico Mané"],  # duas unidades somadas
    # koji: sem dados na API
}

def _cantucci_login() -> str | None:
    """Login na API via form encoding; retorna JWT token."""
    try:
        import requests
    except ImportError:
        return None
    try:
        r = requests.post(
            f"{CANTUCCI_API}/auth/login",
            data={"username": CANTUCCI_USER, "password": CANTUCCI_PASS},
            timeout=6,
        )
        if r.status_code == 200:
            d = r.json()
            return d.get("access_token") or d.get("token") or d.get("accessToken")
    except Exception:
        pass
    return None

def _get_token() -> str | None:
    """Retorna JWT válido; re-autentica se expirado."""
    import time, base64 as _b64, json as _json
    c = _token_cache
    if c.get("token") and c.get("exp", 0) > time.time() + 300:
        return c["token"]
    token = _cantucci_login()
    if token:
        try:
            pad = token.split(".")[1]
            pad += "=" * (-len(pad) % 4)
            exp = _json.loads(_b64.urlsafe_b64decode(pad)).get("exp", time.time() + 3600)
        except Exception:
            exp = time.time() + 3600
        c.update({"token": token, "exp": exp})
    return c.get("token")

@st.cache_data(ttl=1800)
def fetch_fat_api(periodo: str, slug: str = "asa-norte", ei_data: str = None, ef_data: str = None) -> dict | None:
    """
    Busca faturamento_servico diário via API cantuccidados para o período YYYY-MM e slug.
    Quando o slug mapeia para múltiplas lojas, soma os valores diários.
    Retorna {"total": float, "por_dia": {"YYYY-MM-DD": float}} ou None se falhar.
    Cache de 30 min.

    Se ei_data/ef_data forem informados (mesma regra do CMV — janela do
    inventário, EI-inclusivo/EF-exclusivo), usa essa janela em vez do mês
    calendário: o dia do fechamento (ef_data) pertence ao mês SEGUINTE, não
    a este.
    """
    loja_cfg = LOJA_POR_SLUG.get(slug)
    if not loja_cfg:
        return None
    lojas = loja_cfg if isinstance(loja_cfg, list) else [loja_cfg]
    try:
        import requests
        from concurrent.futures import ThreadPoolExecutor
    except ImportError:
        return None

    token = _get_token()
    if not token:
        return None

    ano, mes = int(periodo[:4]), int(periodo[5:7])
    headers  = {"Authorization": f"Bearer {token}"}
    if ei_data:
        primeiro = datetime.strptime(ei_data, "%Y-%m-%d").date()
    else:
        primeiro = date(ano, mes, 1)
    if ef_data:
        ultimo = min(datetime.strptime(ef_data, "%Y-%m-%d").date() - timedelta(days=1), date.today())
    else:
        ultimo = min(date(ano, mes, calendar.monthrange(ano, mes)[1]), date.today())

    dias = []
    d = primeiro
    while d <= ultimo:
        dias.append(d)
        d += timedelta(days=1)

    def _buscar_dia_loja(args):
        d, loja = args
        try:
            r = requests.get(
                f"{CANTUCCI_API}/gestao/diario",
                params={"data": d.strftime("%Y-%m-%d"), "loja": loja,
                        "turno": "todos", "tipo": "TODOS"},
                headers=headers,
                timeout=10,
            )
            if r.status_code == 200:
                atual = r.json().get("atual", {})
                fat = atual.get("faturamento_servico") or atual.get("faturamento") or 0
                return d.strftime("%Y-%m-%d"), float(fat)
            if r.status_code == 401:
                _token_cache.clear()
        except Exception:
            pass
        return d.strftime("%Y-%m-%d"), 0.0

    # Gera pares (dia, loja) para paralelizar todas as chamadas de uma vez
    tarefas = [(d, loja) for d in dias for loja in lojas]
    por_dia: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for data_str, fat in ex.map(_buscar_dia_loja, tarefas):
            por_dia[data_str] = por_dia.get(data_str, 0.0) + fat

    total = sum(por_dia.values())
    if total == 0:
        return None
    return {"total": round(total, 2), "por_dia": por_dia}

# ── Configuração da página ────────────────────────────────────────────────────

st.set_page_config(
    page_title="Relatório Semanal – Cantucci Asa Norte",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Gate de senha (Streamlit Cloud) ──────────────────────────────────────────
from web_auth import login_gate
if not login_gate():
    st.stop()

META_CMV        = 29.5
META_CMC        = 28.0

# Metas CMC por unidade
META_CMC_GRUPO = {
    "asa-norte":    28.0,
    "aguas-claras": 28.0,
    "asa-sul":      28.0,
    "koji":         28.0,
    "mane":         28.0,
    "spq-norte":    28.0,
}

# Meta faturamento por unidade (0 = sem meta cadastrada ainda)
META_FAT_POR_UNIDADE = {
    "asa-norte":    764_203.0,
    "aguas-claras": 0,
    "asa-sul":      0,
    "koji":         0,
    "mane":         0,
    "spq-norte":    0,
}

# Slug da unidade padrão (sobrescrito pelo seletor na interface)
_SLUG_DEFAULT = "asa-norte"

# ── Paleta identidade visual ──────────────────────────────────────────────────
VI_FUNDO    = "#1f2f3a"   # fundo geral
VI_CARD     = "#f5f3e7"   # caixas creme
VI_TEXTO    = "#1f2f3a"   # texto nas caixas
VI_SUBTXT   = "#6b5e52"   # texto secundário nas caixas
VI_BORDA    = "#2e4a5a"   # bordas e divisores
VI_BRANCO   = "#e8dfc8"   # texto sobre fundo escuro
VI_SECAO    = "#b0a898"   # cabeçalho de seção (sobre fundo escuro)
COR_BOM     = "#9fb982"   # verde-sálvia (bom)   — para bordas/ícones em fundo escuro
COR_ATENC   = "#df931b"   # âmbar (atenção)      — para bordas/ícones em fundo escuro
COR_CRIT    = "#98092b"   # vermelho-escuro       — para bordas/ícones em fundo escuro
COR_TRI     = "#e0daa3"   # trigo (destaque neutro)

# Versões escuras para TEXTO em fundo claro (cards creme) — garante contraste WCAG AA
COR_BOM_TXT   = "#2a6b3c"   # verde escuro — texto "bom" sobre VI_CARD
COR_ATENC_TXT = "#8a4e00"   # âmbar escuro — texto "atenção" sobre VI_CARD
COR_CRIT_TXT  = "#7f0000"   # vermelho     — texto "crítico" sobre VI_CARD

# Filtro SQL para excluir compras operacionais (não entram no CMC/CMV).
# Classificação por CÓDIGO Atlas (fonte de verdade): operacionais são as
# categorias 400 (Alimentação Funcionários) e 301 (Material de Limpeza),
# identificadas pelo sku_codigo no formato "XX.YYY-NNN".
# Para linhas antigas sem código (VMarket/XLSX legado), cai no filtro por nome
# usando 'FUNCION' (prefixo ASCII que cobre "FUNCIONÁRIOS" acentuado do Atlas).
_SQL_EXCL_OP = (
    "AND NOT ("
    "  (sku_codigo IS NOT NULL AND sku_codigo != '' "
    "     AND (sku_codigo LIKE '__.400-%' OR sku_codigo LIKE '__.301-%')) "
    "  OR ((sku_codigo IS NULL OR sku_codigo = '') "
    "     AND (UPPER(COALESCE(secao,'')) LIKE '%LIMPEZA%' "
    "          OR UPPER(COALESCE(secao,'')) LIKE '%FUNCION%')) "
    ")"
)

# Aliases mantidos por compatibilidade com resto do código
AZUL_ESCURO = VI_FUNDO
AZUL_CARD   = VI_CARD
AZUL_BORDA  = VI_BORDA
AZUL_CLARO  = COR_BOM
AZUL_TEXTO  = VI_SUBTXT
AMARELO     = COR_TRI
AMARELO_ESC = COR_ATENC
BRANCO      = VI_BRANCO

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown(f"""
<style>
/* Fundo geral */
.stApp                             {{ background-color:{VI_FUNDO}; color:{VI_BRANCO}; }}
.stApp header                      {{ background-color:{VI_FUNDO}; }}
[data-testid="stSidebar"]          {{ background-color:#162530; }}
[data-testid="stMarkdownContainer"] p {{ color:{VI_BRANCO}; }}

/* Cabeçalho */
.header-bar {{
    background:{VI_CARD};
    border-radius:12px;
    padding:18px 28px;
    border-bottom:4px solid {COR_BOM};
    margin-bottom:24px;
    display:flex; align-items:center; gap:16px;
    box-shadow:0 3px 12px rgba(0,0,0,.35);
}}
.header-title {{ font-size:1.5rem; font-weight:800; color:{VI_TEXTO}; }}
.header-sub   {{ font-size:1rem;   color:{VI_SUBTXT}; }}

/* Cards KPI */
.kpi {{
    background:{VI_CARD};
    border-radius:12px;
    padding:20px 22px;
    border-left:5px solid {VI_BORDA};
    height:100%;
    box-shadow:0 2px 8px rgba(0,0,0,.3);
}}
.kpi.bom    {{ border-left-color:{COR_BOM}; }}
.kpi.atencao{{ border-left-color:{COR_ATENC}; }}
.kpi.critico{{ border-left-color:{COR_CRIT}; }}
.kpi-label  {{ font-size:.75rem; color:{VI_SUBTXT}; text-transform:uppercase;
               letter-spacing:.07em; margin-bottom:4px; font-weight:600; }}
.kpi-valor  {{ font-size:2.2rem; font-weight:800; line-height:1;
               margin-bottom:4px; color:{VI_TEXTO}; }}
.kpi-valor.bom    {{ color:#2a6b3c; }}
.kpi-valor.atencao{{ color:#8a4e00; }}
.kpi-valor.critico{{ color:{COR_CRIT}; }}
.kpi-meta   {{ font-size:.78rem; color:{VI_SUBTXT}; }}
.kpi-delta  {{ font-size:.82rem; font-weight:600; margin-top:4px; color:{VI_SUBTXT}; }}

/* Seção / divisor */
.secao {{
    font-size:.85rem; font-weight:700; color:{VI_SECAO};
    text-transform:uppercase; letter-spacing:.09em;
    margin:28px 0 10px 0; padding-bottom:8px;
    border-bottom:1px solid {VI_BORDA};
}}

/* Pills */
.pill {{ display:inline-block; padding:3px 10px; border-radius:20px;
         font-size:.78rem; font-weight:700; }}
.pill-verde  {{ background:{COR_BOM}33; color:#2a6b3c; border:1px solid {COR_BOM}88; }}
.pill-amber  {{ background:{COR_ATENC}33; color:#8a4e00; border:1px solid {COR_ATENC}88; }}
.pill-crit   {{ background:{COR_CRIT}22; color:{COR_CRIT}; border:1px solid {COR_CRIT}55; }}
.pill-cinza  {{ background:#ffffff11; color:{VI_SECAO}; border:1px solid {VI_BORDA}; }}

/* Tabela comparativa grupo */
.grp-table {{ width:100%; border-collapse:collapse; font-size:13px; }}
.grp-table thead th {{
    background:{VI_FUNDO}; color:{COR_TRI}; font-weight:700;
    padding:10px 13px; text-align:left; font-size:11.5px;
    text-transform:uppercase; letter-spacing:.5px; cursor:pointer;
}}
.grp-table thead th:hover {{ background:{VI_BORDA}; }}
.grp-table tbody tr {{ transition:background .12s; }}
.grp-table tbody tr:nth-child(even) {{ background:#ede9da; }}
.grp-table tbody tr:hover {{ background:#d9d4c5; }}
.grp-table tbody td {{
    padding:9px 13px; color:{VI_TEXTO}; border-bottom:1px solid #cdc8b9;
}}
</style>
""", unsafe_allow_html=True)

# ── Status de atualização ─────────────────────────────────────────────────────

def _ler_status() -> dict:
    """Lê ultima_atualizacao.json; retorna dict vazio se não existir."""
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _rodar_atualizacao(apenas_faturamento: bool = False) -> bool:
    """
    Dispara atualizar_dados.py em background.
    - Escreve em_andamento=True antes de spawnar (evita re-clique)
    - Redireciona stdout/stderr para o arquivo de log
    - Retorna True se o processo foi iniciado com sucesso
    """
    # Escreve status imediatamente para o botão sumir antes mesmo do processo iniciar
    _salvar_status_inicio(apenas_faturamento)

    script  = os.path.join(_DIR, "atualizar_dados.py")
    log_dir = os.path.join(_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "atualizar_dados.log")

    args = [script]
    if apenas_faturamento:
        args.append("--apenas-faturamento")

    try:
        with open(log_file, "a", encoding="utf-8", errors="replace") as lf:
            lf.write(f"\n{'='*60}\n[{datetime.now():%Y-%m-%d %H:%M:%S}] Iniciado pelo dashboard\n{'='*60}\n")
            subprocess.Popen(
                ["python"] + args,
                cwd=_DIR,
                stdout=lf,
                stderr=lf,
                # Não usa CREATE_NO_WINDOW — pode suprimir erros no Windows Store Python
            )
        return True
    except Exception as e:
        # Se falhou ao spawnar, reseta o status imediatamente
        _salvar_status_erro(str(e))
        return False


def _salvar_status_inicio(apenas_faturamento: bool):
    """Escreve status de 'em andamento' antes de spawnar o processo."""
    try:
        status = {
            "inicio": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "em_andamento": True,
            "periodos": [date.today().strftime("%Y-%m")],
            "apenas_faturamento": apenas_faturamento,
        }
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _salvar_status_erro(erro: str):
    """Escreve status de erro quando o processo não pôde ser iniciado."""
    try:
        status = {
            "inicio": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "fim": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "em_andamento": False,
            "sucesso": False,
            "erro_fatal": erro,
        }
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _fmt_timestamp(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        hoje = date.today()
        if dt.date() == hoje:
            return f"hoje {dt.strftime('%H:%M')}"
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return ts or "—"


# ── Helpers ───────────────────────────────────────────────────────────────────

def conn():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def _cls_cmv(v):
    if v <= META_CMV:            return "bom"
    if v <= META_CMV + 2:        return "atencao"
    return "critico"

def _cls_cmc(v):
    if v <= META_CMC:            return "bom"
    if v <= META_CMC + 2:        return "atencao"
    return "critico"

def _cls_fat(v):
    meta = META_FAT_POR_UNIDADE.get(_SLUG_DEFAULT, 0)
    if meta <= 0: return "atencao"
    p = v / meta
    if p >= 1.0:  return "bom"
    if p >= 0.85: return "atencao"
    return "critico"

def kpi(label, valor, meta_txt, cls, delta=""):
    st.markdown(f"""
    <div class="kpi {cls}">
      <div class="kpi-label">{label}</div>
      <div class="kpi-valor {cls}">{valor}</div>
      <div class="kpi-meta">Meta: {meta_txt}</div>
      {"<div class='kpi-delta'>" + delta + "</div>" if delta else ""}
    </div>""", unsafe_allow_html=True)

def secao(titulo):
    st.markdown(f'<div class="secao">{titulo}</div>', unsafe_allow_html=True)

def df_to_excel_bytes(df: pd.DataFrame, sheet_name: str = "Dados") -> bytes:
    """Converte um DataFrame para .xlsx em memória — usuários acham mais
    fácil de abrir do que CSV (sem se preocupar com separador/codificação)."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    return buf.getvalue()

def graf_layout(fig, height=300):
    fig.update_layout(
        paper_bgcolor=AZUL_CARD, plot_bgcolor=AZUL_CARD,
        font=dict(color=VI_TEXTO, size=12),
        margin=dict(t=30, b=30, l=10, r=10),
        height=height,
    )
    fig.update_xaxes(gridcolor="#cdc8b9", zeroline=False)
    fig.update_yaxes(gridcolor="#cdc8b9", zeroline=False)
    return fig

def normalizar_secao(s):
    import unicodedata, re
    def _n(x): return unicodedata.normalize("NFD", str(x or "")).encode("ascii","ignore").decode().upper().strip()
    n = _n(s)
    MAP = [
        (["CARNE VERMELHA","CARNES VERMELHA"],"Carnes Vermelhas"),
        (["CARNE BRANCA","CARNES BRANCA"], "Carnes Brancas"),
        (["PESCADO","FRUTO DO MAR"],"Pescados"),
        (["ALCOOLICO","ALCOOLICOS","VINHO","ESPUMANTE","CERVEJA","DESTILADO"],"Beb. Alcoólicas"),
        (["BEBIDA NAO","BEBIDAS NAO","NAO-ALCOO","NAO ALCOO"],"Beb. N/Alcoólicas"),
        (["HORTIFRUTI"],"Hortifruti"),
        (["LATICINIOS","LATICINIO","OVOS","QUEIJO"],"Laticínios/Ovos"),
        (["CONGELADO"],"Congelados"),
        (["CONFEITARIA"],"Confeitaria"),
        (["SECO","CONDIMENTO","CONSERVA","OLEO","ESPECIARIA","TEMPERO"],"Secos/Condimentos"),
        (["PROCESSADO"],"Processados"),
        (["DESCARTA"],"Descartáveis"),
        (["EMBALA"],"Embalagens"),
        (["MATERIAL DE LIMPEZA","LIMPEZA"],"Mat. Limpeza"),
        (["ALIMENTACAO FUNCIONARIO"],"Alim. Funcionarios"),
        (["USO INTERNO","USO MENSAL","CONSUMO INTERNO"],"Uso Interno"),
    ]
    for kws, cat in MAP:
        if any(kw in n for kw in kws): return cat
    return "Outros"

# ── Dados ─────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def get_unidades():
    """Retorna lista de (slug, nome) de todas as unidades."""
    db = conn()
    rows = db.execute("SELECT slug, nome FROM unidades ORDER BY nome").fetchall()
    db.close()
    return rows

@st.cache_data(ttl=120)
def get_uid(slug):
    db = conn()
    r = db.execute("SELECT id FROM unidades WHERE slug=?", (slug,)).fetchone()
    db.close()
    return r[0] if r else None

@st.cache_data(ttl=120)
def get_periodos():
    """
    Retorna lista de períodos disponíveis (YYYY-MM), ordenada do mais recente ao mais antigo.
    Inclui qualquer mês que tenha dados em cmv_resumo, compras ou vendas_produtos —
    mesmo que o CMV ainda não tenha sido calculado (ex: mês atual sem 2 contagens ainda).
    """
    db = conn()
    rows = db.execute("""
        SELECT DISTINCT periodo FROM (
            SELECT periodo                                    FROM cmv_resumo
            UNION
            SELECT strftime('%Y-%m', data)       AS periodo  FROM compras        WHERE data IS NOT NULL
            UNION
            SELECT strftime('%Y-%m', data_inicio) AS periodo FROM vendas_produtos WHERE data_inicio IS NOT NULL
        )
        ORDER BY periodo DESC
    """).fetchall()
    db.close()
    return [r[0] for r in rows]

@st.cache_data(ttl=120)
def load_compras_categoria_semana(uid: int, data_ini: str, data_fim: str) -> pd.DataFrame:
    """Compras por categoria para um intervalo de datas (visão semanal).
    Classifica por CÓDIGO Atlas (sku_codigo) com fallback por nome de seção."""
    db = conn()
    raw = pd.read_sql(
        f"""
        SELECT sku_codigo, secao, SUM(valor_total) AS compras
        FROM compras
        WHERE unidade_id = ? AND data >= ? AND data <= ?
          AND valor_total > 0 AND quantidade > 0
          AND (status_pedido = 'conferido' OR status_pedido IS NULL)
          {_SQL_EXCL_OP}
        GROUP BY sku_codigo, secao
        """,
        db, params=[uid, data_ini, data_fim]
    )
    db.close()
    if raw.empty:
        return pd.DataFrame(columns=["categoria", "compras"])
    raw["categoria"] = raw.apply(lambda r: _classificar_codigo(r["sku_codigo"], r["secao"]), axis=1)
    df = raw.groupby("categoria", as_index=False)["compras"].sum().sort_values("compras", ascending=False)
    return df.reset_index(drop=True)

@st.cache_data(ttl=120)
def load_cmv_mes(uid, periodo):
    db = conn()
    df = pd.read_sql(
        "SELECT categoria, estoque_inicial, compras, estoque_final, cmv_valor, faturamento, cmv_percentual, "
        "ei_data, ef_data "
        "FROM cmv_resumo WHERE unidade_id=? AND periodo=? ORDER BY cmv_valor DESC",
        db, params=[uid, periodo]
    )
    db.close()
    return df

@st.cache_data(ttl=120)
def load_fat_semanal(uid, periodo):
    db = conn()
    # Se a unidade tem linhas de API para o período, usa apenas elas (evita dupla contagem com Sheets)
    has_api = db.execute(
        "SELECT COUNT(*) FROM vendas_produtos WHERE unidade_id=? AND periodo=? AND produto='Faturamento (API)'",
        (uid, periodo)
    ).fetchone()[0] > 0
    filtro = "AND produto='Faturamento (API)'" if has_api else "AND produto!='Faturamento (API)'"
    df = pd.read_sql(
        f"SELECT data_inicio, data_fim, SUM(valor_total) as fat, SUM(quantidade) as qtd "
        f"FROM vendas_produtos WHERE unidade_id=? AND tipo='VENDA' AND periodo=? {filtro} "
        f"GROUP BY data_inicio, data_fim ORDER BY data_inicio",
        db, params=[uid, periodo]
    )
    db.close()
    return df

@st.cache_data(ttl=120)
def load_compras_semana(uid, periodo):
    """
    Retorna compras agrupadas por semana separando 'conferido' e 'em_transito'.
    Apenas 'conferido' conta como compra efetiva no CMC.
    """
    db = conn()
    semanas = pd.read_sql(
        "SELECT DISTINCT data_inicio, data_fim FROM vendas_produtos "
        "WHERE unidade_id=? AND periodo=? ORDER BY data_inicio",
        db, params=[uid, periodo]
    )

    if semanas.empty:
        db.close()
        return pd.DataFrame(columns=["data_inicio", "data_fim", "comp", "em_transito"])

    # Busca pelo intervalo real das semanas (não pelo mês do período) — a
    # última semana pode se estender para o mês seguinte (ex.: 27/07–02/08)
    # para fechar segunda→domingo, e compras dos dias extras precisam entrar.
    _range_ini = semanas["data_inicio"].min()
    _range_fim = semanas["data_fim"].max()

    # Compras efetivas (conferidas) — excluindo categorias operacionais
    compras_conf = pd.read_sql(
        f"SELECT data, SUM(valor_total) as comp "
        f"FROM compras WHERE unidade_id=? AND data>=? AND data<=? "
        f"AND valor_total>0 AND (status_pedido='conferido' OR status_pedido IS NULL) {_SQL_EXCL_OP} "
        f"GROUP BY data",
        db, params=[uid, _range_ini, _range_fim]
    )
    # Compras em trânsito — excluindo categorias operacionais
    compras_tran = pd.read_sql(
        f"SELECT data, SUM(valor_total) as em_transito "
        f"FROM compras WHERE unidade_id=? AND data>=? AND data<=? "
        f"AND valor_total>0 AND status_pedido='em_transito' {_SQL_EXCL_OP} "
        f"GROUP BY data",
        db, params=[uid, _range_ini, _range_fim]
    )
    db.close()

    rows = []
    for _, s in semanas.iterrows():
        comp = 0.0
        if not compras_conf.empty:
            mask = (compras_conf["data"] >= s["data_inicio"]) & (compras_conf["data"] <= s["data_fim"])
            comp = float(compras_conf.loc[mask, "comp"].sum())
        tran = 0.0
        if not compras_tran.empty:
            mask = (compras_tran["data"] >= s["data_inicio"]) & (compras_tran["data"] <= s["data_fim"])
            tran = float(compras_tran.loc[mask, "em_transito"].sum())
        rows.append({"data_inicio": s["data_inicio"], "data_fim": s["data_fim"],
                     "comp": comp, "em_transito": tran})
    return pd.DataFrame(rows)

@st.cache_data(ttl=120)
def load_estoque_por_semana(uid: int, datas_inicio: tuple) -> dict:
    """
    Retorna {data_inicio: valor_estoque} para cada data em datas_inicio.
    Usa a contagem do Atlas (tipo semanal/inventario_mensal) exatamente nessa data.
    Valor = SUM(quantidade * custo_medio), onde custo_medio vem das compras da unidade.
    Se não houver contagem na data, retorna 0.0.
    """
    db = conn()
    custo = pd.read_sql(
        "SELECT insumo_id, SUM(valor_total) / NULLIF(SUM(quantidade), 0) AS cm "
        "FROM compras WHERE unidade_id=? AND valor_total > 0 AND quantidade > 0 "
        "GROUP BY insumo_id",
        db, params=[uid]
    )
    resultado = {}
    for data in datas_inicio:
        contagem = pd.read_sql(
            "SELECT insumo_id, quantidade FROM contagens WHERE unidade_id=? AND data=?",
            db, params=[uid, data]
        )
        if contagem.empty:
            resultado[data] = 0.0
            continue
        contagem["insumo_id"] = pd.to_numeric(contagem["insumo_id"], errors="coerce").astype("Int64")
        custo["insumo_id"]    = pd.to_numeric(custo["insumo_id"],    errors="coerce").astype("Int64")
        merged = contagem.merge(custo, on="insumo_id", how="left")
        merged["cm"] = merged["cm"].fillna(0)
        resultado[data] = float((merged["quantidade"] * merged["cm"]).sum())
    db.close()
    return resultado


@st.cache_data(ttl=120)
def load_top_produtos(uid, periodo, n=15):
    db = conn()
    df = pd.read_sql(
        "SELECT produto, SUM(valor_total) as fat, SUM(quantidade) as qtd "
        "FROM vendas_produtos WHERE unidade_id=? AND tipo='VENDA' AND periodo=? "
        "AND produto!='Faturamento (API)' "
        "GROUP BY produto ORDER BY fat DESC LIMIT ?",
        db, params=[uid, periodo, n]
    )
    db.close()
    return df

@st.cache_data(ttl=120)
def load_ei_ef_mes(uid, periodo):
    """
    Datas EI/EF do mês — mesma regra do CMV (calcular_cmv.py): prioriza
    inventario_mensal (fechamento pode cair no dia 1º do mês seguinte),
    cai para qualquer contagem do mês calendário. Lê de cmv_resumo quando
    já calculado (fonte única, evita a lógica duplicada divergir); só
    resolve ao vivo se o CMV do período ainda não foi gravado.
    """
    db = conn()
    r = db.execute(
        "SELECT ei_data, ef_data FROM cmv_resumo WHERE unidade_id=? AND periodo=? AND categoria='TOTAL'",
        (uid, periodo)
    ).fetchone()
    if r and r[0] and r[1]:
        db.close()
        return r[0], r[1]

    # CMV do período ainda não foi calculado — resolve ao vivo (mesmo
    # algoritmo de resolver_ei_ef em calcular_cmv.py). O EI pode ser o
    # próprio fechamento do mês ANTERIOR (ex.: 31/08 fecha agosto E abre
    # setembro), buscado numa janela de ~20 dias antes do mês para não
    # pular um mês inteiro sem contagem mensal própria.
    ano_p, mes_p = int(periodo[:4]), int(periodo[5:7])
    prev_ano, prev_mes = (ano_p, mes_p - 1) if mes_p > 1 else (ano_p - 1, 12)
    prox_ano, prox_mes = (ano_p, mes_p + 1) if mes_p < 12 else (ano_p + 1, 1)
    limite_inf    = f"{prev_ano:04d}-{prev_mes:02d}-20"
    limite_ei_sup = f"{periodo}-10"
    limite_sup    = f"{prox_ano:04d}-{prox_mes:02d}-10"

    todas_inv = [row[0] for row in db.execute(
        "SELECT DISTINCT data FROM contagens WHERE unidade_id=? AND tipo='inventario_mensal' "
        "AND data>=? AND data<=? ORDER BY data",
        (uid, limite_inf, limite_sup)
    ).fetchall()]
    candidatos_ei = [d for d in todas_inv if d <= limite_ei_sup]
    ei_inv = candidatos_ei[-1] if candidatos_ei else None
    ef_inv = next((d for d in todas_inv if ei_inv and d > ei_inv), None)

    ei_qq = ef_qq = None
    if not ei_inv or not ef_inv:
        datas_mes = [row[0] for row in db.execute(
            "SELECT DISTINCT data FROM contagens WHERE unidade_id=? AND strftime('%Y-%m',data)=? ORDER BY data",
            (uid, periodo)
        ).fetchall()]
        ei_qq = datas_mes[0] if datas_mes else None
        ef_qq = datas_mes[-1] if datas_mes else None

    db.close()
    return (ei_inv or ei_qq), (ef_inv or ef_qq)

@st.cache_data(ttl=120)
def load_compras_mes(uid, periodo):
    """Retorna apenas compras CONFERIDAS (efetivas) do mês, excluindo categorias
    operacionais. Usa a janela EI/EF do inventário (mesma regra do CMV) em vez
    do mês calendário, para que uma compra no dia do fechamento caia no mês
    correto."""
    ei_data, ef_data = load_ei_ef_mes(uid, periodo)
    db = conn()
    if ei_data and ef_data:
        df = pd.read_sql(
            "SELECT sku_codigo, secao, nome_insumo, quantidade, valor_unitario, valor_total, fornecedor, data "
            f"FROM compras WHERE unidade_id=? AND data>=? AND data<? AND valor_total>0 "
            f"AND (status_pedido='conferido' OR status_pedido IS NULL) {_SQL_EXCL_OP} "
            "ORDER BY data, valor_total DESC",
            db, params=[uid, ei_data, ef_data]
        )
    else:
        df = pd.read_sql(
            "SELECT sku_codigo, secao, nome_insumo, quantidade, valor_unitario, valor_total, fornecedor, data "
            f"FROM compras WHERE unidade_id=? AND strftime('%Y-%m', data)=? AND valor_total>0 "
            f"AND (status_pedido='conferido' OR status_pedido IS NULL) {_SQL_EXCL_OP} "
            "ORDER BY data, valor_total DESC",
            db, params=[uid, periodo]
        )
    db.close()
    return df

@st.cache_data(ttl=120)
def load_em_transito_mes(uid, periodo) -> float:
    """Retorna o valor total em trânsito (realizado/confirmado, não conferido) do mês,
    usando a mesma janela EI/EF do CMV."""
    ei_data, ef_data = load_ei_ef_mes(uid, periodo)
    db = conn()
    if ei_data and ef_data:
        r = db.execute(
            f"SELECT COALESCE(SUM(valor_total),0) FROM compras "
            f"WHERE unidade_id=? AND data>=? AND data<? "
            f"AND valor_total>0 AND status_pedido='em_transito' {_SQL_EXCL_OP}",
            (uid, ei_data, ef_data)
        ).fetchone()
    else:
        r = db.execute(
            f"SELECT COALESCE(SUM(valor_total),0) FROM compras "
            f"WHERE unidade_id=? AND strftime('%Y-%m', data)=? "
            f"AND valor_total>0 AND status_pedido='em_transito' {_SQL_EXCL_OP}",
            (uid, periodo)
        ).fetchone()
    db.close()
    return float(r[0]) if r else 0.0

@st.cache_data(ttl=120)
def load_top_produtos_semana(uid, data_inicio, data_fim, n=15):
    """
    Top produtos para uma semana específica.
    Usa overlap de datas (data_inicio armazenada <= data_fim selecionado
    E data_fim armazenada >= data_inicio selecionado) para tolerar pequenas
    diferenças entre semanas calendário e semanas de contagem.
    """
    db = conn()
    df = pd.read_sql(
        "SELECT produto, SUM(valor_total) as fat, SUM(quantidade) as qtd "
        "FROM vendas_produtos WHERE unidade_id=? AND tipo='VENDA' "
        "AND data_inicio <= ? AND data_fim >= ? AND produto!='Faturamento (API)' "
        "GROUP BY produto ORDER BY fat DESC LIMIT ?",
        db, params=[uid, data_fim, data_inicio, n]
    )
    db.close()
    return df

@st.cache_data(ttl=120)
def load_semana_kpis(uid, data_inicio, data_fim):
    """Retorna faturamento e compras (conferidas + em_transito) de uma semana específica."""
    db = conn()
    # Evita dupla contagem: se há linhas API, usa só elas para faturamento
    has_api = db.execute(
        "SELECT COUNT(*) FROM vendas_produtos WHERE unidade_id=? AND data_inicio=? AND data_fim=? AND produto='Faturamento (API)'",
        (uid, data_inicio, data_fim)
    ).fetchone()[0] > 0
    filtro = "AND produto='Faturamento (API)'" if has_api else "AND produto!='Faturamento (API)'"
    fat_r = db.execute(
        f"SELECT COALESCE(SUM(valor_total),0) FROM vendas_produtos "
        f"WHERE unidade_id=? AND tipo='VENDA' AND data_inicio=? AND data_fim=? {filtro}",
        (uid, data_inicio, data_fim)
    ).fetchone()
    comp_r = db.execute(
        f"SELECT COALESCE(SUM(valor_total),0) FROM compras "
        f"WHERE unidade_id=? AND data>=? AND data<=? AND valor_total>0 "
        f"AND (status_pedido='conferido' OR status_pedido IS NULL) {_SQL_EXCL_OP}",
        (uid, data_inicio, data_fim)
    ).fetchone()
    et_r = db.execute(
        f"SELECT COALESCE(SUM(valor_total),0) FROM compras "
        f"WHERE unidade_id=? AND data>=? AND data<=? AND valor_total>0 "
        f"AND status_pedido='em_transito' {_SQL_EXCL_OP}",
        (uid, data_inicio, data_fim)
    ).fetchone()
    db.close()
    return {
        "fat":          float(fat_r[0]) if fat_r else 0.0,
        "comp":         float(comp_r[0]) if comp_r else 0.0,
        "em_transito":  float(et_r[0]) if et_r else 0.0,
    }

@st.cache_data(ttl=120)
def load_compras_op(uid, data_ini, data_fim):
    """Retorna compras de Material de Limpeza e Alimentação Funcionários no período.
    Classifica por CÓDIGO Atlas (sku_codigo) com fallback por nome de seção."""
    db = conn()
    rows = db.execute("""
        SELECT sku_codigo, secao, COALESCE(SUM(valor_total), 0) as total
        FROM compras
        WHERE unidade_id=? AND data>=? AND data<=?
          AND (status_pedido='conferido' OR status_pedido IS NULL)
          AND valor_total > 0
        GROUP BY sku_codigo, secao
    """, (uid, data_ini, data_fim)).fetchall()
    db.close()
    limpeza     = 0.0
    alim_func   = 0.0
    for cod, sec, tot in rows:
        cat = _classificar_codigo(cod, sec)
        if cat == "Material de Limpeza":
            limpeza   += float(tot or 0)
        elif cat in CATS_OPERACIONAIS:  # "Alimentação Funcionários" (nome real do Atlas) + grafias antigas
            alim_func += float(tot or 0)
    return {"limpeza": limpeza, "uso_interno": alim_func}


@st.cache_data(ttl=120)
def load_grupo_cmc(periodo: str) -> pd.DataFrame:
    """
    Retorna DataFrame com todas as unidades: faturamento, compras VMarket (conferido), CMC%, meta, desvio.
    """
    db = conn()
    df_cmv = pd.read_sql("""
        SELECT u.id, u.nome, u.slug,
               c.faturamento, c.cmv_percentual
        FROM cmv_resumo c
        JOIN unidades u ON u.id = c.unidade_id
        WHERE c.periodo = ? AND c.categoria = 'TOTAL'
        ORDER BY c.faturamento DESC
    """, db, params=[periodo])

    # Apenas compras CONFERIDAS para cálculo do CMC%
    df_vm = pd.read_sql("""
        SELECT unidade_id, SUM(valor_total) as compras_vm
        FROM compras
        WHERE strftime('%Y-%m', data) = ?
          AND (status_pedido = 'conferido' OR status_pedido IS NULL)
          AND NOT (
              (sku_codigo IS NOT NULL AND sku_codigo != ''
                 AND (sku_codigo LIKE '__.400-%' OR sku_codigo LIKE '__.301-%'))
              OR ((sku_codigo IS NULL OR sku_codigo = '')
                 AND (UPPER(COALESCE(secao,'')) LIKE '%LIMPEZA%'
                      OR UPPER(COALESCE(secao,'')) LIKE '%FUNCION%'))
          )
        GROUP BY unidade_id
    """, db, params=[periodo])
    db.close()

    if df_cmv.empty:
        return pd.DataFrame()

    df = df_cmv.merge(df_vm, left_on="id", right_on="unidade_id", how="left")
    df["compras_vm"] = df["compras_vm"].fillna(0)
    df["meta_cmc"]   = df["slug"].map(META_CMC_GRUPO).fillna(28.0)
    df["cmc_pct"]    = df.apply(
        lambda r: (r["compras_vm"] / r["faturamento"] * 100) if r["faturamento"] else 0.0,
        axis=1,
    )
    df["desvio"]     = df["cmc_pct"] - df["meta_cmc"]
    return df

@st.cache_data(ttl=120)
def load_projecao_mensal(periodo: str) -> dict:
    """
    Retorna dict {slug: {projecao, meta_vendas, real_vendas, dias_com_dados, dias_faltantes}}
    lido da tabela projecao_mensal (gravada pelo importar_api_vendas).
    """
    db = conn()
    try:
        rows = db.execute("""
            SELECT u.slug, p.projecao, p.meta_vendas, p.real_vendas,
                   p.dias_com_dados, p.dias_faltantes, p.fat_projetado_faltante
            FROM projecao_mensal p
            JOIN unidades u ON u.id = p.unidade_id
            WHERE p.periodo = ?
        """, (periodo,)).fetchall()
    except Exception:
        rows = []
    db.close()
    return {
        row[0]: {
            "projecao":              float(row[1] or 0),
            "meta_vendas":           float(row[2] or 0),
            "real_vendas":           float(row[3] or 0),
            "dias_com_dados":        int(row[4] or 0),
            "dias_faltantes":        int(row[5] or 0),
            "fat_projetado_faltante":float(row[6] or 0),
        }
        for row in rows
    }


def _semanas_mes(periodo: str) -> tuple[int, int]:
    """
    Retorna (semanas_decorridas, semanas_total) para o período YYYY-MM.

    Semanas seguem o padrão Seg→Dom.
    semanas_decorridas = semanas que JÁ COMEÇARAM (incluindo a semana atual parcial).
    semanas_total      = total de semanas Seg→Dom no mês (incluindo parciais no início/fim).
    """
    import calendar as _cal
    from datetime import date as _d, timedelta as _td
    ano, mes = int(periodo[:4]), int(periodo[5:7])
    primeiro = _d(ano, mes, 1)
    ultimo   = _d(ano, mes, _cal.monthrange(ano, mes)[1])
    hoje     = _d.today()

    dec = total = 0
    ini = primeiro
    while ini <= ultimo:
        dias_ate_dom = (6 - ini.weekday()) % 7
        fim_sem = min(ini + _td(days=dias_ate_dom), ultimo)
        total += 1
        if ini <= hoje:
            dec += 1
        ini = fim_sem + _td(days=1)

    return max(dec, 1), max(total, 1)


@st.cache_data(ttl=120)
def load_quadro_compras(periodo: str,
                        semana_inicio: str = None,
                        semana_fim: str = None) -> pd.DataFrame:
    """
    Retorna DataFrame com acompanhamento de compras por unidade.
    Inclui dados mensais e (opcionalmente) semanais.
    Colunas: nome, slug, fat_mes, comp_mes, em_trans_mes, cmc_mes,
             comp_sem, em_trans_sem, fat_sem, cmc_sem, meta_sem, aderencia_sem,
             meta, meta_val, meta_val_fat,
             desvio, tendencia, semanas_dec, semanas_total,
             projecao
    """
    db  = conn()
    proj_dict = load_projecao_mensal(periodo)   # {slug: {...}}
    metas_json = _load_metas_json() if (semana_inicio and semana_fim) else {}
    units = pd.read_sql("SELECT id, nome, slug FROM unidades ORDER BY nome", db)

    rows = []
    for _, u in units.iterrows():
        uid_u    = int(u["id"])
        slug_u   = str(u["slug"])
        nome_u   = str(u["nome"])
        meta_pct = META_CMC_GRUPO.get(slug_u, 28.0)

        def _q(sql, params):
            r = db.execute(sql, params).fetchone()
            return float(r[0]) if r and r[0] is not None else 0.0

        # ── Mês ────────────────────────────────────────────────────
        # Usa a janela EI/EF do inventário (mesma regra do CMV), não o mês
        # calendário — uma compra/venda no dia do fechamento (ex.: 31/08 quando
        # o inventário de fechamento de agosto é 01/09) cai no mês correto.
        _ei_u, _ef_u = load_ei_ef_mes(uid_u, periodo)

        if _ei_u and _ef_u:
            comp_mes = _q(
                f"SELECT COALESCE(SUM(valor_total),0) FROM compras "
                f"WHERE unidade_id=? AND data>=? AND data<? "
                f"AND (status_pedido='conferido' OR status_pedido IS NULL) {_SQL_EXCL_OP}",
                (uid_u, _ei_u, _ef_u))
            em_trans_mes = _q(
                f"SELECT COALESCE(SUM(valor_total),0) FROM compras "
                f"WHERE unidade_id=? AND data>=? AND data<? "
                f"AND status_pedido='em_transito' {_SQL_EXCL_OP}",
                (uid_u, _ei_u, _ef_u))
            has_api_mes = _q(
                "SELECT COUNT(*) FROM vendas_produtos "
                "WHERE unidade_id=? AND data_inicio>=? AND data_inicio<? AND produto='Faturamento (API)'",
                (uid_u, _ei_u, _ef_u)) > 0
            fat_mes_filter = "AND produto='Faturamento (API)'" if has_api_mes else "AND produto!='Faturamento (API)'"
            fat_mes = _q(
                f"SELECT COALESCE(SUM(valor_total),0) FROM vendas_produtos "
                f"WHERE unidade_id=? AND data_inicio>=? AND data_inicio<? AND tipo='VENDA' {fat_mes_filter}",
                (uid_u, _ei_u, _ef_u))
        else:
            comp_mes = _q(
                f"SELECT COALESCE(SUM(valor_total),0) FROM compras "
                f"WHERE unidade_id=? AND strftime('%Y-%m',data)=? "
                f"AND (status_pedido='conferido' OR status_pedido IS NULL) {_SQL_EXCL_OP}",
                (uid_u, periodo))
            em_trans_mes = _q(
                f"SELECT COALESCE(SUM(valor_total),0) FROM compras "
                f"WHERE unidade_id=? AND strftime('%Y-%m',data)=? "
                f"AND status_pedido='em_transito' {_SQL_EXCL_OP}",
                (uid_u, periodo))
            has_api_mes = _q(
                "SELECT COUNT(*) FROM vendas_produtos "
                "WHERE unidade_id=? AND periodo=? AND produto='Faturamento (API)'",
                (uid_u, periodo)) > 0
            fat_mes_filter = "AND produto='Faturamento (API)'" if has_api_mes else "AND produto!='Faturamento (API)'"
            fat_mes = _q(
                f"SELECT COALESCE(SUM(valor_total),0) FROM vendas_produtos "
                f"WHERE unidade_id=? AND periodo=? AND tipo='VENDA' {fat_mes_filter}",
                (uid_u, periodo))

        # ── Semana ─────────────────────────────────────────────────
        comp_sem = em_trans_sem = fat_sem = meta_sem = 0.0
        aderencia_sem = None
        if semana_inicio and semana_fim:
            comp_sem = _q(
                f"SELECT COALESCE(SUM(valor_total),0) FROM compras "
                f"WHERE unidade_id=? AND data>=? AND data<=? "
                f"AND (status_pedido='conferido' OR status_pedido IS NULL) {_SQL_EXCL_OP}",
                (uid_u, semana_inicio, semana_fim))
            em_trans_sem = _q(
                f"SELECT COALESCE(SUM(valor_total),0) FROM compras "
                f"WHERE unidade_id=? AND data>=? AND data<=? "
                f"AND status_pedido='em_transito' {_SQL_EXCL_OP}",
                (uid_u, semana_inicio, semana_fim))
            has_api_sem = _q(
                "SELECT COUNT(*) FROM vendas_produtos "
                "WHERE unidade_id=? AND data_inicio=? AND data_fim=? AND produto='Faturamento (API)'",
                (uid_u, semana_inicio, semana_fim)) > 0
            fat_sem_filter = "AND produto='Faturamento (API)'" if has_api_sem else "AND produto!='Faturamento (API)'"
            fat_sem = _q(
                f"SELECT COALESCE(SUM(valor_total),0) FROM vendas_produtos "
                f"WHERE unidade_id=? AND tipo='VENDA' "
                f"AND data_inicio=? AND data_fim=? {fat_sem_filter}",
                (uid_u, semana_inicio, semana_fim))

            # Meta de compras da semana: usa a meta definida pelo master
            # (mesma fonte do painel "Definir metas de compras por semana"),
            # com fallback para CMC% da unidade sobre o faturamento da semana.
            meta_sem = metas_json.get(f"{uid_u}:{semana_inicio}", 0.0) or (fat_sem * meta_pct / 100)
            aderencia_sem = (comp_sem / meta_sem * 100) if meta_sem > 0 else None

        # ── Projeção e meta baseada na projeção ─────────────────────
        proj_info    = proj_dict.get(slug_u, {})
        projecao     = proj_info.get("projecao", 0.0)
        meta_vendas  = proj_info.get("meta_vendas", 0.0)

        # Meta de compras = 28% da projeção do mês todo
        base_meta     = projecao if projecao > 0 else fat_mes
        meta_val_proj = base_meta * meta_pct / 100

        # CMC% calculado sobre faturamento real
        cmc_mes  = (comp_mes / fat_mes * 100) if fat_mes > 0 else None
        cmc_sem  = (comp_sem / fat_sem * 100) if fat_sem > 0 else None

        # ── Tendência e desvio ───────────────────────────────────────
        # Tendência = (compras_acumuladas / semanas_decorridas) * semanas_total
        # Desvio    = Tendência − meta_val  (+ = estourará; − = dentro da meta)
        sem_dec, sem_total = _semanas_mes(periodo)
        tendencia = (comp_mes / sem_dec) * sem_total if comp_mes > 0 else 0.0
        desvio    = tendencia - meta_val_proj

        # Meta legacy (sobre fat_mes) — compatibilidade
        meta_val  = fat_mes * meta_pct / 100

        rows.append({
            "nome": nome_u, "slug": slug_u,
            "fat_mes": fat_mes, "comp_mes": comp_mes, "em_trans_mes": em_trans_mes,
            "cmc_mes": cmc_mes, "meta": meta_pct,
            "meta_val": meta_val_proj, "desvio": desvio,
            "tendencia": tendencia,
            "semanas_dec": sem_dec, "semanas_total": sem_total,
            "projecao": projecao, "meta_vendas": meta_vendas,
            "meta_val_fat": meta_val,
            "comp_sem": comp_sem, "em_trans_sem": em_trans_sem,
            "fat_sem": fat_sem, "cmc_sem": cmc_sem,
            "meta_sem": meta_sem, "aderencia_sem": aderencia_sem,
        })

    db.close()
    return pd.DataFrame(rows)


@st.cache_data(ttl=120)
def load_estoque_atual(uid, data_contagem: str = None):
    """
    Retorna estoque com preço histórico e cobertura de estoque.
    data_contagem: se fornecido, usa essa contagem específica; caso contrário usa a mais recente.
    """
    db = conn()
    if data_contagem:
        data_ef = data_contagem
    else:
        r = db.execute(
            "SELECT MAX(data) FROM contagens WHERE unidade_id=?", (uid,)
        ).fetchone()
        data_ef = r[0] if r else None
    if not data_ef:
        db.close()
        return pd.DataFrame(), data_ef

    df = pd.read_sql("""
        SELECT ct.insumo_id, ct.sku_item_id, i.nome, ct.quantidade,
               COALESCE(p.cm, 0) AS custo_medio,
               ct.quantidade * COALESCE(p.cm, 0) AS valor_estoque
        FROM contagens ct
        JOIN insumos i ON i.id = ct.insumo_id
        LEFT JOIN (
            SELECT insumo_id,
                   SUM(valor_total) / NULLIF(SUM(quantidade), 0) AS cm
            FROM compras
            WHERE unidade_id=? AND valor_total > 0 AND quantidade > 0
            GROUP BY insumo_id
        ) p ON p.insumo_id = ct.insumo_id
        WHERE ct.unidade_id=? AND ct.data=?
          AND ct.quantidade > 0
        ORDER BY valor_estoque DESC
    """, db, params=[uid, uid, data_ef])

    # Consumo médio diário por insumo (últimos 30 dias de compras como proxy)
    consumo_df = pd.read_sql("""
        SELECT insumo_id,
               SUM(quantidade) / 30.0 AS consumo_dia
        FROM compras
        WHERE unidade_id=?
          AND data >= DATE(?, '-30 days')
          AND quantidade > 0 AND valor_total > 0
        GROUP BY insumo_id
    """, db, params=[uid, data_ef])
    db.close()

    # Garante tipos compatíveis antes do merge (Atlas pode ter insumo_id NULL → object)
    df["insumo_id"]         = pd.to_numeric(df["insumo_id"],         errors="coerce").astype("Int64")
    consumo_df["insumo_id"] = pd.to_numeric(consumo_df["insumo_id"], errors="coerce").astype("Int64")
    df = df.merge(consumo_df, on="insumo_id", how="left")
    # Cobertura em dias: estoque_atual / consumo_diário
    df["cobertura_dias"] = df.apply(
        lambda r: round(r["quantidade"] / r["consumo_dia"])
        if (r.get("consumo_dia") or 0) > 0 else None,
        axis=1,
    )

    # Classificar por CÓDIGO Atlas via lookup de compras (sku_codigo + secao por insumo)
    db2 = conn()
    sec_df = pd.read_sql(
        "SELECT insumo_id, MAX(sku_codigo) as sku_codigo, MAX(secao) as secao FROM compras "
        "WHERE unidade_id=? AND (secao IS NOT NULL OR sku_codigo IS NOT NULL) GROUP BY insumo_id",
        db2, params=[uid]
    )
    db2.close()
    sec_df["insumo_id"] = pd.to_numeric(sec_df["insumo_id"], errors="coerce").astype("Int64")
    df = df.merge(sec_df, on="insumo_id", how="left")
    df["secao_norm"] = df.apply(lambda r: _classificar_codigo(r.get("sku_codigo"), r.get("secao")), axis=1)
    return df, data_ef


@st.cache_data(ttl=120)
def load_estoque_contagem_anterior(uid: int, data_atual: str) -> pd.DataFrame:
    """
    Retorna DataFrame {sku_item_id, qtd_estoque_ant} da contagem imediatamente
    anterior a data_atual. O casamento com a contagem atual é feito por
    sku_item_id — o insumo_id NÃO é estável entre importações de contagem
    (o mesmo produto pode receber insumo_id diferente a cada import).
    O valor (R$) é calculado no chamador usando o custo médio atual.
    Retorna DataFrame vazio se não houver contagem anterior.
    """
    db = conn()
    row = db.execute(
        "SELECT MAX(data) FROM contagens WHERE unidade_id=? AND data < ?",
        (uid, data_atual)
    ).fetchone()
    data_ant = row[0] if row else None
    if not data_ant:
        db.close()
        return pd.DataFrame(columns=["sku_item_id", "qtd_estoque_ant"])

    df = pd.read_sql("""
        SELECT sku_item_id, SUM(quantidade) AS qtd_estoque_ant
        FROM contagens
        WHERE unidade_id=? AND data=? AND sku_item_id IS NOT NULL
        GROUP BY sku_item_id
    """, db, params=[uid, data_ant])
    db.close()
    return df


@st.cache_data(ttl=120)
def calcular_cmv_semana(uid: int, data_ini: str, data_fim: str,
                        ei_data: str, ef_data: str) -> dict:
    """
    Calcula CMV para o período delimitado por duas contagens.
    CMV = (EI + Compras_conferidas) - EF
    CMV% = CMV / Faturamento * 100

    Retorna dict com: ei, ef, compras, cmv_valor, faturamento, cmv_pct, ok
    """
    db = conn()

    def _stock_value(data_contagem):
        """Calcula valor do estoque em uma data de contagem."""
        rows = db.execute("""
            SELECT ct.insumo_id, ct.quantidade,
                   COALESCE(p.cm, 0) AS custo_medio
            FROM contagens ct
            LEFT JOIN (
                SELECT insumo_id,
                       SUM(valor_total) / NULLIF(SUM(quantidade), 0) AS cm
                FROM compras
                WHERE unidade_id=? AND valor_total > 0 AND quantidade > 0
                GROUP BY insumo_id
            ) p ON p.insumo_id = ct.insumo_id
            WHERE ct.unidade_id=? AND ct.data=? AND ct.quantidade > 0
        """, (uid, uid, data_contagem)).fetchall()
        return sum(float(r[1]) * float(r[2]) for r in rows)

    ei_val = _stock_value(ei_data)
    ef_val = _stock_value(ef_data) if ef_data else 0.0

    # Compras conferidas no período (excluindo categorias operacionais)
    comp_r = db.execute(
        f"SELECT COALESCE(SUM(valor_total),0) FROM compras "
        f"WHERE unidade_id=? AND data>=? AND data<=? AND valor_total>0 "
        f"AND (status_pedido='conferido' OR status_pedido IS NULL) {_SQL_EXCL_OP}",
        (uid, data_ini, data_fim)
    ).fetchone()
    compras = float(comp_r[0]) if comp_r else 0.0

    # Faturamento do período:
    # As linhas de vendas_produtos podem ter datas diferentes das semanas de contagem.
    # Usamos proração: valor × (dias de sobreposição / total de dias da linha).
    has_api = db.execute(
        "SELECT COUNT(*) FROM vendas_produtos "
        "WHERE unidade_id=? AND periodo=? AND produto='Faturamento (API)'",
        (uid, data_ini[:7])
    ).fetchone()[0] > 0
    fat_filter = "AND produto='Faturamento (API)'" if has_api else "AND produto!='Faturamento (API)'"
    fat_rows = db.execute(
        f"SELECT valor_total, data_inicio, data_fim FROM vendas_produtos "
        f"WHERE unidade_id=? AND tipo='VENDA' {fat_filter} "
        f"AND data_inicio <= ? AND data_fim >= ?",
        (uid, data_fim, data_ini)
    ).fetchall()
    fat = 0.0
    for val, di, df in fat_rows:
        # dias de sobreposição entre a linha e nosso período
        overlap_ini = max(di, data_ini)
        overlap_fim = min(df, data_fim)
        from datetime import date as _d
        ovlp = (_d.fromisoformat(overlap_fim) - _d.fromisoformat(overlap_ini)).days + 1
        total = (_d.fromisoformat(df) - _d.fromisoformat(di)).days + 1
        if ovlp > 0 and total > 0:
            fat += float(val or 0) * ovlp / total
    db.close()

    cmv_val = ei_val + compras - ef_val
    cmv_pct = cmv_val / fat * 100 if fat > 0 else 0.0

    return {
        "ei": ei_val, "ef": ef_val, "compras": compras,
        "cmv_valor": cmv_val, "faturamento": fat,
        "cmv_pct": cmv_pct,
        "ok": ef_data is not None and fat > 0,
    }


# ── Metas semanais de compras ─────────────────────────────────────────────────
# Persistência via GitHub API: metas_semanais.json no repositório.
# Estrutura: {"uid:data_inicio": meta_valor, ...}
# Fallback: leitura do arquivo local quando GitHub não configurado.

_METAS_JSON_PATH = os.path.join(os.path.dirname(__file__), "metas_semanais.json")

def _gh_secrets() -> tuple[str, str] | None:
    """Retorna (token, repo) dos secrets do Streamlit, ou None se não configurado."""
    try:
        tok  = st.secrets.get("github_token", "")
        repo = st.secrets.get("github_repo", "")
        if tok and repo:
            return tok, repo
    except Exception:
        pass
    return None


def _gh_get_file() -> tuple[dict, str | None]:
    """GET metas_semanais.json do GitHub. Retorna (conteudo_dict, sha)."""
    import json as _json, base64 as _b64, urllib.request as _ur
    gh = _gh_secrets()
    if not gh:
        return {}, None
    token, repo = gh
    url = f"https://api.github.com/repos/{repo}/contents/metas_semanais.json"
    req = _ur.Request(url, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    })
    try:
        with _ur.urlopen(req, timeout=8) as r:
            data = _json.loads(r.read())
            sha = data.get("sha")
            content = _json.loads(_b64.b64decode(data["content"].replace("\n", "")).decode())
            return content, sha
    except Exception as e:
        raise RuntimeError(f"GET GitHub falhou: {e}") from e


def _load_metas_json() -> dict:
    """Lê metas_semanais.json: tenta GitHub API primeiro, fallback para arquivo local."""
    try:
        content, _ = _gh_get_file()
        return content
    except Exception:
        pass
    try:
        import json as _j
        with open(_METAS_JSON_PATH, "r", encoding="utf-8") as f:
            return _j.load(f)
    except Exception:
        return {}


def _save_metas_json(metas: dict) -> tuple[bool, str]:
    """
    Salva metas_semanais.json via GitHub API.
    Retorna (True, "") em sucesso ou (False, mensagem_erro) em falha.
    """
    import json as _json, base64 as _b64, urllib.request as _ur, urllib.error as _ue
    content_str = _json.dumps(metas, ensure_ascii=False, indent=2)

    try:
        with open(_METAS_JSON_PATH, "w", encoding="utf-8") as f:
            f.write(content_str)
    except Exception:
        pass

    gh = _gh_secrets()
    if not gh:
        return False, "github_token / github_repo não configurados nos Secrets do Streamlit."

    token, repo = gh
    api_url = f"https://api.github.com/repos/{repo}/contents/metas_semanais.json"

    try:
        _, sha = _gh_get_file()
    except Exception as e:
        return False, f"Não foi possível ler arquivo atual no GitHub: {e}"

    body: dict = {
        "message": "chore: atualiza metas semanais de compras",
        "content": _b64.b64encode(content_str.encode()).decode(),
    }
    if sha:
        body["sha"] = sha

    req_put = _ur.Request(
        api_url,
        data=_json.dumps(body).encode(),
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    try:
        with _ur.urlopen(req_put, timeout=15) as r:
            if r.status in (200, 201):
                return True, ""
            return False, f"GitHub respondeu HTTP {r.status}"
    except _ue.HTTPError as e:
        body_err = e.read().decode(errors="replace")
        return False, f"GitHub HTTP {e.code}: {body_err[:200]}"
    except Exception as e:
        return False, f"Erro de rede: {e}"


@st.cache_data(ttl=60)
def load_meta_semanal(uid: int, periodo: str) -> dict:
    """Retorna {data_inicio: meta_valor} para o período (lê do JSON persistido)."""
    all_metas = _load_metas_json()
    uid_key = str(uid)
    return {
        k.split(":", 1)[1]: float(v)
        for k, v in all_metas.items()
        if k.startswith(f"{uid_key}:") and k.split(":", 1)[1].startswith(periodo[:7])
    }


def salvar_meta_semanal(uid: int, data_inicio: str, data_fim: str, meta_valor: float):
    """Persiste a meta semanal no JSON do repositório via GitHub API."""
    all_metas = _load_metas_json()
    key = f"{uid}:{data_inicio}"
    if meta_valor == 0.0:
        all_metas.pop(key, None)
    else:
        all_metas[key] = meta_valor
    ok, erro = _save_metas_json(all_metas)
    st.cache_data.clear()
    if ok:
        st.toast("✅ Meta salva com sucesso!", icon="✅")
    else:
        st.error(f"❌ **Falha ao salvar meta no GitHub:** {erro}", icon="🚨")


@st.cache_data(ttl=120)
def load_meta_peso_categoria(uid: int) -> dict:
    """Retorna {categoria: peso_percentual} da unidade, para calcular a meta
    de compras por categoria (meta_semana * peso / 100)."""
    db = conn()
    rows = db.execute(
        "SELECT categoria, peso_percentual FROM meta_peso_categoria WHERE unidade_id=?",
        (uid,)
    ).fetchall()
    db.close()
    return {cat: float(peso) for cat, peso in rows}


@st.cache_data(ttl=120)
def load_desvios_setor(uid: int, ei_data: str | None, ef_data: str | None,
                       data_ini: str, data_fim: str,
                       insumo_ids: list[int]) -> pd.DataFrame:
    """
    Compara consumo teórico (PDV × fator) vs consumo real (EI + Compras − EF)
    para os insumo_ids fornecidos, no intervalo [data_ini, data_fim].
    ei_data / ef_data = datas das contagens que delimitam o período.

    O Atlas renomeia produtos ao longo do tempo (ex.: "Hamburguer Angus 160G
    |Und|" → "HAMBURGUER ANGUS 160G |UND|"), e como contagens/compras casam
    insumo_id por nome exato, cada renomeação cria um insumo_id NOVO para o
    mesmo produto físico — o insumo_id mapeado em venda_direta_pdv_map fica
    "congelado" na grafia antiga e some das contagens atuais ("Sem contagem").
    O sku_item_id (UUID do Atlas) é estável através de renomeações, então
    resolvemos a "família" de insumo_ids que compartilham o mesmo sku_item_id
    e buscamos EI/EF/Compras somando todos os apelidos da família.

    Retorna DataFrame com colunas:
      proteina, n_pratos, consumo_teo, ei_qty, compras_qty, ef_qty,
      consumo_real, desvio, custo_medio, desvio_rs, tem_contagem
    """
    db = conn()

    ids_sql = f"({','.join(str(x) for x in insumo_ids)})"

    # ── Insumos do setor com mapeamento PDV ──────────────────────────────────
    proteinas = pd.read_sql(f"""
        SELECT DISTINCT i.id AS insumo_id, i.nome AS proteina,
               COUNT(m.id) AS n_pratos
        FROM venda_direta_pdv_map m JOIN insumos i ON i.id = m.insumo_id
        WHERE m.insumo_id IN {ids_sql}
        GROUP BY i.id
    """, db)

    if proteinas.empty:
        db.close()
        return pd.DataFrame()

    seed_ids = [int(x) for x in proteinas["insumo_id"].tolist()]
    prot_ids_sql = f"({','.join(str(x) for x in seed_ids)})"

    # ── Resolve sku_item_id mais recente de cada insumo_id "semente" ──────────
    # (contagens e compras de QUALQUER unidade — o catálogo de SKUs do Atlas
    # é compartilhado entre as unidades do grupo, então o mesmo sku_item_id
    # aparece em produtos de restaurantes diferentes sem colisão de sentido).
    sku_hist = pd.read_sql(f"""
        SELECT insumo_id, sku_item_id, data FROM contagens
        WHERE insumo_id IN {prot_ids_sql} AND sku_item_id IS NOT NULL
        UNION ALL
        SELECT insumo_id, sku_item_id, data FROM compras
        WHERE insumo_id IN {prot_ids_sql} AND sku_item_id IS NOT NULL
    """, db)
    seed_sku: dict[int, str] = {}
    if not sku_hist.empty:
        for iid, grp in sku_hist.sort_values("data").groupby("insumo_id"):
            seed_sku[int(iid)] = grp.iloc[-1]["sku_item_id"]

    # ── Para cada sku resolvido, busca TODOS os insumo_id (apelidos) que ─────
    # compartilham esse mesmo sku_item_id, em qualquer unidade.
    alias_para_seed: dict[int, int] = {pid: pid for pid in seed_ids}
    skus_unicos = sorted(set(seed_sku.values()))
    if skus_unicos:
        placeholders = ",".join("?" for _ in skus_unicos)
        alias_rows = pd.read_sql(f"""
            SELECT DISTINCT insumo_id, sku_item_id FROM contagens
            WHERE sku_item_id IN ({placeholders}) AND insumo_id IS NOT NULL
            UNION
            SELECT DISTINCT insumo_id, sku_item_id FROM compras
            WHERE sku_item_id IN ({placeholders}) AND insumo_id IS NOT NULL
        """, db, params=skus_unicos + skus_unicos)
        sku_para_seed = {sku: seed for seed, sku in seed_sku.items()}
        for _, r in alias_rows.iterrows():
            seed = sku_para_seed.get(r["sku_item_id"])
            if seed is not None:
                alias_para_seed[int(r["insumo_id"])] = seed

    search_ids = sorted(alias_para_seed.keys())
    search_ids_sql = f"({','.join(str(x) for x in search_ids)})"

    def _remap_soma(df: pd.DataFrame, col_valor: str) -> pd.DataFrame:
        """Remapeia insumo_id (apelido) → insumo_id semente e soma por semente."""
        if df.empty:
            return pd.DataFrame(columns=["insumo_id", col_valor])
        out = df.copy()
        out["insumo_id"] = out["insumo_id"].map(alias_para_seed)
        return out.groupby("insumo_id", as_index=False)[col_valor].sum()

    # ── Consumo teórico: vendas × fator ──────────────────────────────────────
    teo_vendas = pd.read_sql(f"""
        SELECT m.insumo_id,
               SUM(vp.quantidade * m.fator) AS consumo_teo
        FROM vendas_produtos vp
        JOIN venda_direta_pdv_map m ON m.produto_pdv = vp.produto
        WHERE vp.unidade_id = {uid} AND vp.tipo = 'VENDA'
          AND vp.data_inicio >= '{data_ini}' AND vp.data_fim <= '{data_fim}'
          AND m.insumo_id IN {prot_ids_sql}
        GROUP BY m.insumo_id
    """, db)

    # Cancelamentos com motivo != 'CANCELAMENTO SEM DESPERDÍCIO' consomem
    # insumo (o prato foi produzido) mesmo sem gerar venda — somam ao
    # consumo teórico. "Sem desperdício" = cancelado antes de produzir,
    # não entra. Filtra só por motivo (não por situação), conforme definido
    # com o usuário.
    teo_cancel = pd.read_sql(f"""
        SELECT m.insumo_id,
               SUM(c.quantidade * m.fator) AS consumo_teo
        FROM cancelamentos c
        JOIN venda_direta_pdv_map m ON m.produto_pdv = c.produto
        WHERE c.unidade_id = {uid}
          AND c.data >= '{data_ini}' AND c.data <= '{data_fim}'
          AND (c.motivo IS NULL OR c.motivo != 'CANCELAMENTO SEM DESPERDÍCIO')
          AND m.insumo_id IN {prot_ids_sql}
        GROUP BY m.insumo_id
    """, db)

    teo = (pd.concat([teo_vendas, teo_cancel], ignore_index=True)
             .groupby("insumo_id", as_index=False)["consumo_teo"].sum())

    # ── EI / EF em unidades — soma todos os apelidos da família ──────────────
    def _qty(data_contagem):
        if not data_contagem:
            return pd.DataFrame(columns=["insumo_id", "qty"])
        raw = pd.read_sql(f"""
            SELECT insumo_id, quantidade AS qty
            FROM contagens
            WHERE unidade_id = {uid} AND data = '{data_contagem}'
              AND insumo_id IN {search_ids_sql}
        """, db)
        return _remap_soma(raw, "qty")

    ei_df = _qty(ei_data).rename(columns={"qty": "ei_qty"})
    ef_df = _qty(ef_data).rename(columns={"qty": "ef_qty"})

    # ── Compras no período — soma por família de apelidos, com fallback ──────
    # por nome (token match) para itens sem sku_item_id resolvido.
    import re as _re
    _STOP = {'UND','KG','LT','ML','G','PORCIONADO','PORCAO','PROCESSADO','INDIVIDUAL','UN','DE','DO','DA','E'}

    def _norm(s):
        """Remove tudo que não seja letra/número e converte para maiúsculo."""
        return _re.sub(r'[^A-Z0-9]', '', str(s or '').upper())

    def _tokens(nome):
        """Divide o nome original em tokens alfanuméricos e remove stopwords."""
        raw = _re.split(r'[\s\[\]\(\)\-\|\.]+', str(nome or '').upper())
        return [_norm(t) for t in raw if _norm(t) and _norm(t) not in _STOP and len(_norm(t)) > 1]

    def _match(compra_nome, tokens):
        cn = _norm(compra_nome)
        return all(tok in cn for tok in tokens)

    if ei_data and ef_data:
        # Contagens são feitas de manhã: representam o estoque no INÍCIO
        # daquele dia. Compras do dia da EF ainda não foram "vistas" pela
        # contagem (chegam depois da foto da manhã) e pertencem à próxima
        # janela — por isso data >= ei_data (inclui o dia da EI) e
        # data < ef_data (exclui o dia da EF), não '> ei' e '<= ef'.
        all_comp = pd.read_sql(f"""
            SELECT insumo_id, nome_insumo, sku_item_id, SUM(quantidade) AS qty
            FROM compras
            WHERE unidade_id = {uid}
              AND data >= '{ei_data}' AND data < '{ef_data}'
              AND quantidade > 0 AND valor_total > 0
              AND (status_pedido = 'conferido' OR status_pedido IS NULL)
            GROUP BY insumo_id, nome_insumo, sku_item_id
        """, db)

        comp_rows = []
        for _, prow in proteinas.iterrows():
            pid     = int(prow["insumo_id"])
            tokens  = _tokens(prow["proteina"])
            familia = {a for a, s in alias_para_seed.items() if s == pid}
            # Fallback por nome só entra para linhas SEM sku_item_id — quando
            # há sku confiável, o match por nome (substring, sem limite de
            # palavra) pode contaminar itens parecidos (ex.: "Heineken Long
            # Neck" casava com "Heineken 00 Álcool Long Neck", pois os tokens
            # do primeiro são subconjunto do segundo).
            sem_sku = all_comp["sku_item_id"].isna() | (all_comp["sku_item_id"] == "")
            matched = all_comp[
                all_comp["insumo_id"].isin(familia) |
                (sem_sku & all_comp["nome_insumo"].apply(lambda n: _match(n, tokens)))
            ]
            qty = matched["qty"].sum()
            comp_rows.append({"insumo_id": pid, "compras_qty": float(qty)})
        comp_df = pd.DataFrame(comp_rows)
    else:
        all_comp = pd.DataFrame()
        comp_df  = pd.DataFrame(columns=["insumo_id", "compras_qty"])

    # ── Custo médio por insumo (família de apelidos + fallback por nome) ─────
    custo_raw = pd.read_sql(f"""
        SELECT insumo_id, nome_insumo, sku_item_id,
               SUM(valor_total) AS vt, SUM(quantidade) AS qt
        FROM compras
        WHERE unidade_id = {uid} AND valor_total > 0 AND quantidade > 0
        GROUP BY insumo_id, nome_insumo, sku_item_id
    """, db)

    custo_rows = []
    for _, prow in proteinas.iterrows():
        pid     = int(prow["insumo_id"])
        tokens  = _tokens(prow["proteina"])
        familia = {a for a, s in alias_para_seed.items() if s == pid}
        sem_sku = custo_raw["sku_item_id"].isna() | (custo_raw["sku_item_id"] == "")
        matched = custo_raw[
            custo_raw["insumo_id"].isin(familia) |
            (sem_sku & custo_raw["nome_insumo"].apply(lambda n: _match(n, tokens)))
        ]
        tot_vt = matched["vt"].sum()
        tot_qt = matched["qt"].sum()
        custo_rows.append({"insumo_id": pid, "custo_medio": tot_vt / tot_qt if tot_qt > 0 else 0.0})
    custo_df = pd.DataFrame(custo_rows)

    db.close()

    # ── Junta tudo ────────────────────────────────────────────────────────────
    for df in [teo, ei_df, ef_df, comp_df, custo_df]:
        df["insumo_id"] = pd.to_numeric(df["insumo_id"], errors="coerce").astype("Int64")
    proteinas["insumo_id"] = pd.to_numeric(proteinas["insumo_id"], errors="coerce").astype("Int64")

    res = (proteinas
           .merge(teo,     on="insumo_id", how="left")
           .merge(ei_df,   on="insumo_id", how="left")
           .merge(ef_df,   on="insumo_id", how="left")
           .merge(comp_df, on="insumo_id", how="left")
           .merge(custo_df, on="insumo_id", how="left"))

    # tem_contagem = existe uma linha real de EI OU EF (mesmo que quantidade=0,
    # que é um resultado de contagem válido — estoque genuinamente zerado).
    # Precisa ser calculado ANTES do fillna, que preencheria com 0.0 tanto
    # "contagem encontrada com valor zero" quanto "contagem não encontrada",
    # tornando os dois casos indistinguíveis.
    res["tem_contagem"] = res.get("ei_qty").notna() | res.get("ef_qty").notna()

    for col in ["consumo_teo", "ei_qty", "ef_qty", "compras_qty", "custo_medio"]:
        res[col] = res.get(col, 0.0).fillna(0.0)

    res["consumo_real"] = res["ei_qty"] + res["compras_qty"] - res["ef_qty"]
    res["desvio"]       = res["consumo_real"] - res["consumo_teo"]
    res["desvio_rs"]    = res["desvio"] * res["custo_medio"]

    return res[res["consumo_teo"] > 0].sort_values("consumo_teo", ascending=False).reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
# INTERFACE
# ══════════════════════════════════════════════════════════════════════════════

unidades_lista = get_unidades()   # [(slug, nome), ...]
periodos       = get_periodos()
if not periodos:
    st.error("Nenhum período disponível."); st.stop()

# Dicionários de lookup
_slug_para_nome = {slug: nome for slug, nome in unidades_lista}
_nome_para_slug = {nome: slug for slug, nome in unidades_lista}
_nomes_unidade  = [nome for _, nome in unidades_lista]

# ── Cabeçalho + seletores ─────────────────────────────────────────────────────

_status_upd = _ler_status()
_em_andamento = _status_upd.get("em_andamento", False)

# ── Timeout: reseta se em_andamento > 10 min ─────────────────────────────────
if _em_andamento:
    try:
        _ini_dt  = datetime.strptime(_status_upd.get("inicio", ""), "%Y-%m-%d %H:%M:%S")
        _minutos = (datetime.now() - _ini_dt).total_seconds() / 60
    except Exception:
        _minutos = 0
    if _minutos > 10:
        _status_upd.update({
            "em_andamento": False, "sucesso": False,
            "erro_fatal": "Timeout — processo interrompido",
            "fim": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        try:
            with open(STATUS_FILE, "w", encoding="utf-8") as _sf:
                json.dump(_status_upd, _sf, ensure_ascii=False, indent=2)
        except Exception:
            pass
        _em_andamento = False

# ── Cache-busting: limpa quando um novo update termina ───────────────────────
# Compara o timestamp "fim" do último update com o que foi registrado na sessão.
# Isso funciona mesmo que o usuário abra a página depois do update terminar.
_ultimo_fim = _status_upd.get("fim", "")
if not _em_andamento and _ultimo_fim:
    _cached_fim = st.session_state.get("cache_cleared_at", "")
    if _ultimo_fim != _cached_fim:
        st.cache_data.clear()
        st.session_state["cache_cleared_at"] = _ultimo_fim

c_titulo, c_unidade, c_periodo, c_semana = st.columns([2.5, 1.5, 1, 1])

with c_titulo:
    # Barra de status de atualização
    _fim_ts    = _status_upd.get("fim") or _status_upd.get("inicio")
    _duracao   = _status_upd.get("duracao_segundos")
    _ok        = _status_upd.get("sucesso", True)

    if _em_andamento:
        _badge_txt   = "🔄 Atualizando…"
        _badge_bg    = COR_ATENC          # cor de fundo/borda (pode ser suave)
        _badge_fc    = COR_ATENC_TXT      # cor do texto (escura, contraste no card claro)
    elif _fim_ts:
        _badge_txt   = f"✅ Atualizado {_fmt_timestamp(_fim_ts)}"
        _badge_bg    = COR_BOM if _ok else COR_CRIT
        _badge_fc    = COR_BOM_TXT if _ok else COR_CRIT_TXT
        if _duracao:
            _badge_txt += f" ({_duracao:.0f}s)"
    else:
        _badge_txt   = "⚪ Sem dados de atualização"
        _badge_bg    = VI_SECAO
        _badge_fc    = VI_SUBTXT

    st.markdown(f"""
    <div class="header-bar">
      <span style="font-size:2rem;">📊</span>
      <div style="flex:1">
        <div class="header-title">Relatório Semanal</div>
        <div class="header-sub">Grupo Cantucci &nbsp;
          <span style="font-size:.75rem;background:{_badge_bg}22;color:{_badge_fc};
                       border:1px solid {_badge_bg}88;padding:2px 8px;border-radius:10px;
                       font-weight:600;">{_badge_txt}</span>
        </div>
      </div>
    </div>""", unsafe_allow_html=True)

with c_unidade:
    st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
    _unit_lock = st.session_state.get("unit_lock")
    if _unit_lock and _unit_lock in _slug_para_nome:
        # Acesso por senha de unidade — trava a seleção
        SLUG_SEL = _unit_lock
        _nome_locked = _slug_para_nome[SLUG_SEL]
        # nome_sel também precisa existir aqui — é usado em títulos, nomes de
        # arquivo CSV e no rodapé, fora de qualquer checagem de unit_lock.
        nome_sel = _nome_locked
        st.text_input("Unidade", value=f"🔒 {_nome_locked}", disabled=True,
                      help="Acesso restrito a esta unidade. Use a senha master pra ver todas.")
    else:
        nome_sel = st.selectbox("Unidade", options=_nomes_unidade,
                                index=_nomes_unidade.index(_slug_para_nome.get(_SLUG_DEFAULT, _nomes_unidade[0])))
        SLUG_SEL = _nome_para_slug[nome_sel]

with c_periodo:
    st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
    periodo = st.selectbox(
        "Período",
        options=periodos,
        format_func=lambda p: datetime.strptime(p, "%Y-%m").strftime("%B / %Y").title(),
    )

# uid da unidade selecionada
uid = get_uid(SLUG_SEL)
if not uid:
    st.error(f"Unidade '{SLUG_SEL}' não encontrada no banco."); st.stop()

# Metas da unidade selecionada
META_FAT   = META_FAT_POR_UNIDADE.get(SLUG_SEL, 0)
_meta_cmc  = META_CMC_GRUPO.get(SLUG_SEL, META_CMC)

# Semanas disponíveis — carregadas após uid
@st.cache_data(ttl=120)
def get_semanas_contagem(uid, periodo):
    """
    Retorna lista de semanas calendário (segunda→domingo) dentro do período.
    Cada semana = (data_inicio, data_fim, ei_data, ef_data)
      - data_inicio : segunda-feira da semana (ou dia 1 se o mês não começa na segunda)
      - data_fim    : domingo da semana, ou hoje se a semana ainda não fechou
      - ei_data     : última contagem semanal na data_inicio ou antes
      - ef_data     : primeira contagem semanal após data_fim (None = semana ainda aberta)

    A ÚLTIMA semana do mês NÃO é truncada no fim do mês — ela se estende até
    o domingo seguinte (mesmo cruzando para o mês seguinte), pois a análise
    de meta de CMC exige semana completa segunda→domingo. Uma semana cortada
    no meio (ex.: 27/07–31/07) subestima compras/faturamento daquela semana.

    Pelo mesmo motivo, a PRIMEIRA semana deste mês não começa no dia 1 se
    esses dias já foram cobertos pela última semana (estendida) do mês
    anterior — senão os dois meses mostrariam a mesma semana duplicada
    (ex.: 01–02/08 apareceria como semana de julho E de agosto).
    """
    db = conn()
    ano, mes = int(periodo[:4]), int(periodo[5:7])
    primeiro  = date(ano, mes, 1)
    ultimo    = date(ano, mes, calendar.monthrange(ano, mes)[1])
    hoje_date = date.today()

    # Dias do início deste mês já cobertos pela semana final do mês anterior
    _ultimo_ant = primeiro - timedelta(days=1)
    _dias_ate_dom_ant = (6 - _ultimo_ant.weekday()) % 7
    primeiro = max(primeiro, _ultimo_ant + timedelta(days=_dias_ate_dom_ant + 1))

    # Contagens semanais do mês anterior + atual + próximo (para encontrar ei/ef)
    mes_ant  = f"{ano}-{mes-1:02d}" if mes > 1 else f"{ano-1}-12"
    mes_prox = f"{ano}-{mes+1:02d}" if mes < 12 else f"{ano+1}-01"
    rows = db.execute(
        "SELECT DISTINCT data FROM contagens "
        "WHERE unidade_id=? AND strftime('%Y-%m',data) IN (?,?,?) AND tipo='semanal' "
        "ORDER BY data",
        (uid, mes_ant, periodo, mes_prox)
    ).fetchall()
    db.close()
    todas_contagens = [r[0] for r in rows]

    semanas = []
    ini = primeiro
    while ini <= min(ultimo, hoje_date):
        # Fim da semana = sempre o domingo seguinte (segunda→domingo completa),
        # mesmo que a semana ainda esteja em andamento ou cruze o fim do mês —
        # não trunca em "hoje": uma semana em curso precisa mostrar seu range
        # natural (ex.: hoje=10/08 → S3 10/08–16/08, não 10/08–10/08), senão
        # a label fica errada e a meta de compras parece ser de um único dia.
        dias_ate_dom = (6 - ini.weekday()) % 7
        fim = ini + timedelta(days=dias_ate_dom)
        ini_str = ini.isoformat()
        fim_str = fim.isoformat()

        # ei_data: última contagem na data de início ou antes
        ei_data = next((c for c in reversed(todas_contagens) if c <= ini_str), None)

        # ef_data: primeira contagem APÓS o fim desta semana (indica semana fechada)
        ef_data = None
        if fim < hoje_date:
            for c in todas_contagens:
                if c > fim_str:
                    ef_data = c
                    break

        semanas.append((ini_str, fim_str, ei_data, ef_data))
        ini = fim + timedelta(days=1)

    return semanas


# ─── Semanas por contagem ──────────────────────────────────────────────────────
semanas_raw = get_semanas_contagem(uid, periodo)

def _label_semana(i, s):
    ei_str  = datetime.strptime(s[0], "%Y-%m-%d").strftime("%d/%m")
    fim_str = datetime.strptime(s[1], "%Y-%m-%d").strftime("%d/%m")
    fechada = "✓" if s[3] else "→"  # EF disponível = semana fechada
    return f"S{i+1}  {ei_str} – {fim_str} {fechada}"

semana_opts = ["Todas as semanas"] + [_label_semana(i, s) for i, s in enumerate(semanas_raw)]

with c_semana:
    st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
    semana_sel = st.selectbox("Semana", options=semana_opts)

# Auto-refresh enquanto atualização está em andamento
if _em_andamento:
    st.markdown(
        '<meta http-equiv="refresh" content="10">',
        unsafe_allow_html=True,
    )

st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)

# Índice da semana selecionada (None = todas)
# semana_filtro = (data_inicio, data_fim, ei_data, ef_data)  ← 4-tupla
semana_idx    = semana_opts.index(semana_sel) - 1  # -1 = "todas"
semana_filtro = semanas_raw[semana_idx] if semana_idx >= 0 else None

# ── Carregar dados ─────────────────────────────────────────────────────────────

df_cmv       = load_cmv_mes(uid, periodo)
df_fat_sem   = load_fat_semanal(uid, periodo)
df_comp_sem  = load_compras_semana(uid, periodo)
_datas_inicio_sem = tuple(df_fat_sem["data_inicio"].tolist()) if not df_fat_sem.empty else ()
estoque_por_semana = load_estoque_por_semana(uid, _datas_inicio_sem)
df_top       = load_top_produtos(uid, periodo)
df_compras   = load_compras_mes(uid, periodo)
em_transito_mes = load_em_transito_mes(uid, periodo)
metas_semanais  = load_meta_semanal(uid, periodo)   # {data_inicio: meta_valor}

# ── UI master: editar metas semanais (apenas acesso sem unit_lock) ────────────
_is_master = st.session_state.get("unit_lock") is None
if _is_master and semanas_raw:
    with st.expander("⚙️ Definir metas de compras por semana — acesso master", expanded=False):
        st.markdown(
            f'<div style="font-size:.78rem;color:{VI_SECAO};margin-bottom:10px;">'
            "Defina o valor máximo de compras (R$) para cada semana de "
            f"<b>{datetime.strptime(periodo,'%Y-%m').strftime('%B/%Y').title()}</b> "
            f"na unidade <b>{nome_sel}</b>. "
            "A meta percentual mensal permanece inalterada."
            "</div>",
            unsafe_allow_html=True,
        )
        cols_meta = st.columns(len(semanas_raw))
        for i, (di, df_s, ei_d, ef_d) in enumerate(semanas_raw):
            label_s = f"S{i+1} · {datetime.strptime(di,'%Y-%m-%d').strftime('%d/%m')}–{datetime.strptime(df_s,'%Y-%m-%d').strftime('%d/%m')}"
            cur_val = metas_semanais.get(di, 0.0)
            with cols_meta[i]:
                novo = st.number_input(
                    label_s,
                    min_value=0.0,
                    value=cur_val,
                    step=500.0,
                    format="%.0f",
                    key=f"meta_sem_{uid}_{di}",
                    help="Meta de compras em R$ para esta semana. 0 = sem meta definida (usa CMC% do mês).",
                )
                if novo != cur_val:
                    salvar_meta_semanal(uid, di, df_s, novo)
                    st.rerun()
        st.markdown(
            f'<div style="font-size:.72rem;color:{VI_SECAO};margin-top:6px;">'
            "Meta = 0 → o sistema usa a meta CMC% mensal como referência."
            "</div>",
            unsafe_allow_html=True,
        )

# ── Análise de proteínas porcionadas ─────────────────────────────────────────
if semana_filtro:
    _prot_ei   = semana_filtro[2]
    _prot_ef   = semana_filtro[3]
    _prot_ini  = semana_filtro[0]
    _prot_fim  = semana_filtro[1]
else:
    # EI/EF do mês: mesma regra do CMV (calcular_cmv.py), lida via load_ei_ef_mes
    # (fonte única — prioriza inventario_mensal, evita a lógica duplicada divergir).
    _prot_ei, _prot_ef = load_ei_ef_mes(uid, periodo)
    # Janela de VENDAS/cancelamentos cobre o mês inteiro (todas as semanas
    # em vendas_produtos), independente da última contagem disponível.
    # Usar _prot_ef (última contagem) aqui excluiria semanas cujo intervalo
    # se estende além dela — ex.: a última semana do mês fecha no domingo
    # seguinte e pode passar da data da última contagem — subestimando o
    # teórico de TODOS os insumos (a semana inteira some do cálculo).
    # EI/EF continuam usando as datas de contagem (_prot_ei/_prot_ef), só
    # a janela de vendas/cancelamentos é ampliada.
    _db_prot   = conn()
    _vendas_range = _db_prot.execute(
        "SELECT MIN(data_inicio), MAX(data_fim) FROM vendas_produtos WHERE unidade_id=? AND periodo=?",
        (uid, periodo)
    ).fetchone()
    _db_prot.close()
    _prot_ini  = (_vendas_range[0] if _vendas_range else None) or _prot_ei or f"{periodo}-01"
    _prot_fim  = (_vendas_range[1] if _vendas_range else None) or _prot_ef or f"{periodo}-31"
# Insumos analisados por setor (IDs do banco_central.db)
_IDS_COZINHA = [1172, 1614, 1615, 1170, 1619, 2171, 234, 233, 442, 1164]
# FILE MIGNON: 150g=1172, 130g=1614, 180g=1615, 100g=1170
# Frango 160g=1619, Baby Beef 180g=2171 (substituiu Chorizo=1612 no cardapio),
# Burger 160g=234, Burger 100g=233, Salmao 180g=442, Babybeef 130g=1164
_IDS_BAR = [20, 18, 121, 122, 123, 124, 108, 107, 111, 486, 1, 299, 95, 96, 471, 472]
# Agua s/g=20, c/g=18, Coca KS orig=121, zero=122, 310ml orig=123, zero=124
# Heineken LN=108, 00alc=107, Chaka=111, Pouca Roupa=486
# 4 Estaciones=1, Malacara=299, Cafe desc=95, espresso=96
# Bodega Vieja=471, Casa Donoso=472

df_cozinha = load_desvios_setor(uid, _prot_ei, _prot_ef, _prot_ini, _prot_fim, _IDS_COZINHA)
df_bar     = load_desvios_setor(uid, _prot_ei, _prot_ef, _prot_ini, _prot_fim, _IDS_BAR)

# Estoque: usa a contagem de fechamento da semana selecionada (ef_data);
# em "Todas as semanas", usa o fechamento do PERÍODO selecionado (_prot_ef,
# já calculado acima) — nunca a contagem mais recente do banco como um todo,
# senão a aba de Estoque ignora o filtro de Período/Mês (ex.: mostraria
# a contagem de agosto mesmo com julho selecionado).
_ef_data_estoque = (semana_filtro[3] if (semana_filtro and semana_filtro[3])
                     else _prot_ef)
df_estoque, data_estoque = load_estoque_atual(uid, _ef_data_estoque)
df_estoque_ant = load_estoque_contagem_anterior(uid, data_estoque) if data_estoque else pd.DataFrame(columns=["insumo_id", "valor_estoque_ant"])

# Aplicar filtro semanal nos dados de produtos e compras
if semana_filtro:
    _si, _sf = semana_filtro[0], semana_filtro[1]
    df_top_display     = load_top_produtos_semana(uid, _si, _sf)
    df_compras_display = df_compras[
        (df_compras["data"] >= _si) &
        (df_compras["data"] <= _sf)
    ].copy()
    kpis_sem = load_semana_kpis(uid, _si, _sf)

    # CMV semanal: calcula apenas se a semana está fechada (ef_data disponível)
    _ei, _ef = semana_filtro[2], semana_filtro[3]
    if _ef:
        cmv_sem = calcular_cmv_semana(uid, _si, _sf, _ei, _ef)
    else:
        cmv_sem = None   # semana aberta, CMV não calculável ainda
else:
    df_top_display     = df_top
    df_compras_display = df_compras
    kpis_sem  = None
    cmv_sem   = None

# Linha TOTAL do CMV
total = df_cmv[df_cmv["categoria"] == "TOTAL"]
cats  = df_cmv[df_cmv["categoria"] != "TOTAL"]

has_cmv = not total.empty

if has_cmv:
    comp_mes = float(total["compras"].iloc[0])
    ei_mes   = float(total["estoque_inicial"].iloc[0])
    ef_mes   = float(total["estoque_final"].iloc[0])
    cmv_val  = float(total["cmv_valor"].iloc[0])
    fat_db   = float(total["faturamento"].iloc[0])
    _ei_data_mes = total["ei_data"].iloc[0] if "ei_data" in total.columns else None
    _ef_data_mes = total["ef_data"].iloc[0] if "ef_data" in total.columns else None
else:
    # Sem CMV calculado — usa faturamento de vendas_produtos (exclui linhas API)
    comp_mes = float(df_compras["valor_total"].sum()) if not df_compras.empty else 0.0
    ei_mes = ef_mes = cmv_val = 0.0
    r_fat = conn()
    _fat_row = r_fat.execute(
        "SELECT COALESCE(SUM(valor_total),0) FROM vendas_produtos "
        "WHERE unidade_id=? AND periodo=? AND tipo='VENDA' AND produto!='Faturamento (API)'",
        (uid, periodo)
    ).fetchone()
    r_fat.close()
    fat_db = float(_fat_row[0]) if _fat_row else 0.0
    _ei_data_mes, _ef_data_mes = load_ei_ef_mes(uid, periodo)

# API de faturamento em tempo real — para todas as unidades com loja mapeada
if LOJA_POR_SLUG.get(SLUG_SEL):
    with st.spinner("Buscando faturamento atualizado…"):
        api_data = fetch_fat_api(periodo, SLUG_SEL, _ei_data_mes, _ef_data_mes)
    if api_data and api_data["total"] > 0:
        fat_real      = api_data["total"]
        fat_fonte     = "🟢 ao vivo"
        fat_fonte_tip = "Faturamento buscado em tempo real de cantuccidados.com.br"
    else:
        fat_real      = fat_db
        fat_fonte     = "🟡 banco local"
        fat_fonte_tip = "API indisponível — usando dados importados do banco local"
else:
    fat_real      = fat_db
    fat_fonte     = "🟡 banco local"
    fat_fonte_tip = f"API não disponível para {nome_sel} — usando dados importados"

# Percentuais mensais
cmv_pct  = cmv_val  / fat_real * 100 if fat_real > 0 else 0.0
cmc_pct  = comp_mes / fat_real * 100 if fat_real > 0 else 0.0

# Período anterior (tendência)
idx_per     = periodos.index(periodo)
periodo_ant = periodos[idx_per + 1] if idx_per + 1 < len(periodos) else None
cmv_pct_ant = cmc_pct_ant = fat_ant = None
if periodo_ant:
    df_ant = load_cmv_mes(uid, periodo_ant)
    t_ant  = df_ant[df_ant["categoria"] == "TOTAL"]
    if not t_ant.empty:
        fat_ant     = float(t_ant["faturamento"].iloc[0])
        comp_ant    = float(t_ant["compras"].iloc[0])
        cmv_pct_ant = float(t_ant["cmv_percentual"].iloc[0])
        cmc_pct_ant = comp_ant / fat_ant * 100 if fat_ant > 0 else 0

def _delta(atual, ant, fmt="{:+.1f}pp", bom_se_menos=True):
    if ant is None: return ""
    diff = atual - ant
    seta = "↑" if diff > 0 else "↓"
    cor_sinal = (diff > 0) if not bom_se_menos else (diff < 0)
    cor = AZUL_CLARO if cor_sinal else AMARELO_ESC
    return f'<span style="color:{cor}">{seta} {fmt.format(abs(diff))} vs mês ant.</span>'

# Projeção de faturamento — usa API Cantucci quando disponível, fallback linear
ano_p, mes_p = int(periodo[:4]), int(periodo[5:7])
dias_mes = calendar.monthrange(ano_p, mes_p)[1]
if not df_fat_sem.empty:
    ultima_venda   = datetime.strptime(df_fat_sem["data_fim"].max(), "%Y-%m-%d").date()
    dias_com_venda = (ultima_venda - date(ano_p, mes_p, 1)).days + 1
else:
    dias_com_venda = 1
fat_proj_linear = fat_real / dias_com_venda * dias_mes if dias_com_venda > 0 else fat_real

# Projeção API para a unidade selecionada
_proj_unidade = load_projecao_mensal(periodo).get(SLUG_SEL, {})
fat_proj_api  = _proj_unidade.get("projecao", 0.0)
# Usa projeção da API se disponível e razoável (> 50% do real = não é zero-fill)
fat_proj = fat_proj_api if fat_proj_api > fat_real * 0.5 else fat_proj_linear
_usa_proj_api = fat_proj_api > fat_real * 0.5

# ── Abas principais ──────────────────────────────────────────────────────────
_tab_grupo, _tab_resumo, _tab_compras, _tab_evolucao, _tab_estoque, _tab_vendas = st.tabs([
    "📊 Grupo", "📌 Resumo", "🛒 Compras", "📅 Evolução", "📦 Estoque", "🍽️ Vendas",
])
_tab_grupo.__enter__()

# ════════════════════════════════════════════════════════════════════════════
# BLOCO 0 — Quadro de Acompanhamento de Compras (Grupo)
# ════════════════════════════════════════════════════════════════════════════

_sem_label = semana_sel if semana_filtro else "Mês completo"
_sem_ini   = semana_filtro[0] if semana_filtro else None
_sem_fim   = semana_filtro[1] if semana_filtro else None

secao(f"🛒 Acompanhamento de Compras — Grupo  ·  {_sem_label}")

df_quad = load_quadro_compras(periodo, _sem_ini, _sem_fim)

if df_quad.empty:
    st.info("Sem dados de compras para o período.")
else:
    # ── Totais do grupo ──────────────────────────────────────────────────────
    tot_fat    = df_quad["fat_mes"].sum()
    tot_proj   = df_quad["projecao"].sum()         # projeção total do grupo
    tot_comp   = df_quad["comp_mes"].sum()
    tot_trans  = df_quad["em_trans_mes"].sum()
    tot_cmc    = (tot_comp / tot_fat * 100) if tot_fat > 0 else 0.0
    tot_meta   = df_quad["meta_val"].sum()         # 28% da projeção (ou fat se sem proj)
    base_grupo = tot_proj if tot_proj > 0 else tot_fat
    tot_dev    = tot_comp - tot_meta if base_grupo > 0 else 0.0
    # Tendência total do grupo e desvio para a coluna da tabela
    # desvio por unidade = tendencia − meta_val → tot_tend_dev = tot_tend − tot_meta
    tot_tend     = df_quad["tendencia"].sum() if "tendencia" in df_quad.columns else tot_comp
    tot_tend_dev = (tot_tend - tot_meta)      if base_grupo > 0              else 0.0

    tot_comp_s = df_quad["comp_sem"].sum()
    tot_fat_s  = df_quad["fat_sem"].sum()
    tot_cmc_s  = (tot_comp_s / tot_fat_s * 100) if tot_fat_s > 0 else 0.0
    tot_meta_s = df_quad["meta_sem"].sum() if "meta_sem" in df_quad.columns else 0.0
    tot_ader_s = (tot_comp_s / tot_meta_s * 100) if tot_meta_s > 0 else None

    _tem_projecao = tot_proj > 0   # True quando projeção da API já foi importada

    # ── KPI cards ────────────────────────────────────────────────────────────
    def _kpi_grp(col, titulo, valor, sub, borda):
        with col:
            st.markdown(f"""
            <div style="background:{VI_CARD};border-radius:10px;padding:16px 18px;
                        border-left:5px solid {borda};box-shadow:0 2px 8px rgba(0,0,0,.3);">
              <div style="font-size:11px;font-weight:700;color:{VI_SUBTXT};text-transform:uppercase;
                          letter-spacing:.6px;margin-bottom:5px;">{titulo}</div>
              <div style="font-size:1.9rem;font-weight:800;color:{VI_TEXTO};line-height:1.1">{valor}</div>
              <div style="font-size:11px;color:{VI_SUBTXT};margin-top:3px">{sub}</div>
            </div>""", unsafe_allow_html=True)

    n_cols = 4 if semana_filtro else 4
    kg1, kg2, kg3, kg4 = st.columns(4)

    _cor_cmc = COR_BOM if tot_cmc <= 28 else (COR_ATENC if tot_cmc <= 31 else COR_CRIT)
    _cor_dev = COR_BOM if tot_dev <= 0 else (COR_ATENC if (base_grupo > 0 and tot_dev / base_grupo * 100 <= 3) else COR_CRIT)

    _sub_fat = f"{len(df_quad[df_quad['fat_mes'] > 0])} unidades c/ faturamento · {periodo}"
    _sub_fat += f"  (⚠ {len(df_quad[df_quad['fat_mes'] == 0])} sem dados)" if (df_quad['fat_mes'] == 0).any() else ""

    # KPI 1: Faturamento real
    _kpi_grp(kg1, "Faturamento Real (Grupo)",
             f"R$ {tot_fat:,.0f}", _sub_fat, COR_BOM)

    # KPI 2: Projeção do mês (API Cantucci)
    _sub_proj = "Projeção c/ serviço (base histórica 90d)" if _tem_projecao else "Sem projeção — execute a sincronização"
    _kpi_grp(kg2, "Projeção de Vendas do Mês",
             f"R$ {tot_proj:,.0f}" if _tem_projecao else "—",
             _sub_proj, COR_TRI)

    # KPI 3: Compras confirmadas
    _kpi_grp(kg3, "Compras Conferidas (Grupo)",
             f"R$ {tot_comp:,.0f}",
             f"Em trânsito: R$ {tot_trans:,.0f}" if tot_trans > 0 else "Apenas pedidos conferidos",
             COR_ATENC)

    # KPI 4: CMC% e desvio vs meta baseada na projeção
    _sub_cmc = (
        f"Meta 28% proj. · Desvio R$ {tot_dev:+,.0f}"
        if _tem_projecao and tot_fat > 0
        else (f"Meta 28% fat. · Desvio R$ {tot_dev:+,.0f}" if tot_fat > 0 else "Faturamento não disponível")
    )
    _kpi_grp(kg4, "CMC% Real (Grupo)",
             f"{tot_cmc:.1f}%" if tot_fat > 0 else "—",
             _sub_cmc, _cor_cmc)

    if semana_filtro:
        # Linha adicional com KPI semanal
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        kgs1, kgs2 = st.columns([1, 3])
        _cor_s = COR_BOM if tot_cmc_s <= 28 else (COR_ATENC if tot_cmc_s <= 31 else COR_CRIT)
        _kpi_grp(kgs1, f"CMC% Semana ({_sem_label})",
                 f"{tot_cmc_s:.1f}%" if tot_fat_s > 0 else "—",
                 f"Compras: R$ {tot_comp_s:,.0f}" if tot_comp_s > 0 else "Sem dados semanais",
                 _cor_s)

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    # ── Tabela por unidade ───────────────────────────────────────────────────
    def _badge_cmc(cmc, meta):
        if cmc is None: return '<span style="color:#aaa;font-style:italic">sem fat.</span>'
        d = cmc - meta
        if d <= 0:    bg, fc = "#c8e6c9", "#1b5e20"
        elif d <= 3:  bg, fc = "#fff3cd", "#7d4e00"
        else:         bg, fc = "#fce4e4", "#7f0000"
        return (f'<span style="background:{bg};color:{fc};font-weight:700;'
                f'padding:2px 8px;border-radius:12px;font-size:12px;">{cmc:.1f}%</span>')

    def _badge_dev(desvio, tendencia, meta_val):
        """
        desvio  = tendencia − meta_val
        negativo = tendência abaixo da meta (bom → verde)
        positivo = tendência acima da meta (ruim → amarelo/vermelho)
        """
        if desvio is None:
            return '<span style="color:#aaa;font-style:italic">—</span>'
        if meta_val > 0:
            excesso_pct = desvio / meta_val * 100
        else:
            excesso_pct = 0
        if desvio <= 0:         fc, sinal = "#1b5e20", "▼"
        elif excesso_pct <= 5:  fc, sinal = "#7d4e00", "▲"
        else:                   fc, sinal = "#7f0000", "▲"
        return f'<span style="color:{fc};font-weight:700;">{sinal} R$ {abs(desvio):,.0f}</span>'

    def _status_icon_proj(comp, meta_val, base):
        """Status baseado em compras vs meta de compras (28% da projeção)."""
        if base <= 0 or meta_val <= 0: return "⚪"
        if comp <= meta_val:   return "🟢"
        over = (comp - meta_val) / base * 100
        return "🟡" if over <= 3 else "🔴"

    # Cabeçalho dinâmico (com ou sem colunas semanais)
    # Índices das colunas para ordenação:
    # sem semana: 0=Unidade 1=FatReal 2=Projeção 3=Compras 4=CMC% 5=MetaProj 6=Desvio 7=Status
    # com semana: 0=Unidade 1=FatReal 2=Projeção 3=CompSem 4=CMC%Sem 5=CompMes 6=CMC%Mes 7=MetaProj 8=Desvio 9=Status

    rows_html = ""
    for _, r in df_quad.iterrows():
        destaque = "font-weight:700;background:#ede9da;" if r["slug"] == SLUG_SEL else ""
        et_badge = (f'<br><span style="font-size:10px;color:{COR_ATENC};">'
                    f'⏳ +R$ {r["em_trans_mes"]:,.0f} trânsito</span>') if r["em_trans_mes"] > 0 else ""

        proj_r    = r["projecao"]
        meta_v    = r["meta_val"]          # 28% da projeção (ou fat se sem proj)
        desvio_r  = r["desvio"]
        base_r    = proj_r if proj_r > 0 else r["fat_mes"]

        proj_cell = (f'<td style="text-align:right;color:#1a5276;">R$ {proj_r:,.0f}</td>'
                     if proj_r > 0
                     else '<td style="text-align:right;color:#aaa;font-style:italic">—</td>')
        meta_cell = (f'<td style="text-align:right;color:#2a6b3c;">R$ {meta_v:,.0f}</td>'
                     if base_r > 0
                     else '<td style="text-align:right;color:#aaa">—</td>')

        sem_cells = ""
        if semana_filtro:
            _ader = r["aderencia_sem"]
            _meta_s = r["meta_sem"]
            if _meta_s > 0:
                _cor_ader = COR_BOM if _ader <= 100 else (COR_ATENC if _ader <= 110 else COR_CRIT)
                _sub_meta = (f'<br><span style="font-size:10px;color:{VI_SECAO};">'
                             f'Meta: R$ {_meta_s:,.0f}</span>'
                             f'<br><span style="font-size:10px;color:{_cor_ader};font-weight:700;">'
                             f'Aderência: {_ader:.0f}%</span>')
            else:
                _sub_meta = ""
            sem_cells = f"""
          <td style="text-align:right;">R$ {r['comp_sem']:,.0f}{_sub_meta}</td>
          <td style="text-align:center;">{_badge_cmc(r['cmc_sem'], r['meta'])}</td>"""

        rows_html += f"""
        <tr style="{destaque}">
          <td style="font-weight:{'700' if r['slug']==SLUG_SEL else '500'}">
            {'▶ ' if r['slug']==SLUG_SEL else ''}{r['nome']}
          </td>
          <td style="text-align:right;">
            {'R$ ' + f"{r['fat_mes']:,.0f}" if r['fat_mes'] > 0 else '<span style="color:#aaa">—</span>'}
          </td>
          {proj_cell}{sem_cells}
          <td style="text-align:right;">R$ {r['comp_mes']:,.0f}{et_badge}</td>
          <td style="text-align:center;">{_badge_cmc(r['cmc_mes'], r['meta'])}</td>
          {meta_cell}
          <td style="text-align:right;">{_badge_dev(desvio_r, r["tendencia"], meta_v)}</td>
          <td style="text-align:center;font-size:16px;">{_status_icon_proj(r['comp_mes'], meta_v, base_r)}</td>
        </tr>"""

    # Linha de total
    tot_sem_cells = ""
    if semana_filtro:
        tot_cmc_s_badge = _badge_cmc(tot_cmc_s if tot_fat_s > 0 else None, 28.0)
        if tot_meta_s > 0:
            _cor_ader_tot = COR_BOM if tot_ader_s <= 100 else (COR_ATENC if tot_ader_s <= 110 else COR_CRIT)
            _tot_sub_meta = (f'<br><span style="font-size:10px;font-weight:400;color:{VI_SECAO};">'
                              f'Meta: R$ {tot_meta_s:,.0f}</span>'
                              f'<br><span style="font-size:10px;color:{_cor_ader_tot};">'
                              f'Aderência: {tot_ader_s:.0f}%</span>')
        else:
            _tot_sub_meta = ""
        tot_sem_cells = f"""
          <td style="text-align:right;font-weight:700;">R$ {tot_comp_s:,.0f}{_tot_sub_meta}</td>
          <td style="text-align:center;">{tot_cmc_s_badge}</td>"""

    tot_cmc_badge  = _badge_cmc(tot_cmc if tot_fat > 0 else None, 28.0)
    tot_dev_badge  = _badge_dev(tot_tend_dev if base_grupo > 0 else None, tot_tend, tot_meta)
    tot_meta_str   = f"R$ {tot_meta:,.0f}" if base_grupo > 0 else '<span style="color:#aaa">—</span>'
    tot_fat_str    = f"R$ {tot_fat:,.0f}" if tot_fat > 0 else '<span style="color:#aaa">—</span>'
    tot_proj_str   = f'<td style="text-align:right;color:#1a5276;font-weight:700;">R$ {tot_proj:,.0f}</td>' if _tem_projecao else '<td style="text-align:right;color:#aaa">—</td>'

    rows_html += f"""
        <tr style="border-top:2px solid #ccc;background:#e8e3d2;font-weight:700;">
          <td>TOTAL GRUPO</td>
          <td style="text-align:right;">{tot_fat_str}</td>
          {tot_proj_str}{tot_sem_cells}
          <td style="text-align:right;">R$ {tot_comp:,.0f}</td>
          <td style="text-align:center;">{tot_cmc_badge}</td>
          <td style="text-align:right;color:#2a6b3c;">{tot_meta_str}</td>
          <td style="text-align:right;">{tot_dev_badge}</td>
          <td style="text-align:center;font-size:16px;">{_status_icon_proj(tot_comp, tot_meta, base_grupo)}</td>
        </tr>"""

    # Colunas do cabeçalho
    _lbl_meta = "Meta 28% (proj.) ↕" if _tem_projecao else "Meta 28% (fat.) ↕"
    if semana_filtro:
        th_fat  = '<th onclick="sortQ(1)">Fat. Real ↕</th>'
        th_proj = '<th onclick="sortQ(2)">Projeção Mês ↕</th>'
        th_sem  = (f'<th onclick="sortQ(3)">Compras {_sem_label} ↕</th>'
                   f'<th onclick="sortQ(4)">CMC% Sem ↕</th>')
        th_mes  = '<th onclick="sortQ(5)">Compras Mês ↕</th><th onclick="sortQ(6)">CMC% Mês ↕</th>'
        th_rest = f'<th onclick="sortQ(7)">{_lbl_meta}</th><th onclick="sortQ(8)">Desvio ↕</th><th>Status</th>'
    else:
        th_fat  = '<th onclick="sortQ(1)">Fat. Real ↕</th>'
        th_proj = '<th onclick="sortQ(2)">Projeção Mês ↕</th>'
        th_sem  = ""
        th_mes  = '<th onclick="sortQ(3)">Compras Mês ↕</th><th onclick="sortQ(4)">CMC% ↕</th>'
        th_rest = f'<th onclick="sortQ(5)">{_lbl_meta}</th><th onclick="sortQ(6)">Desvio ↕</th><th>Status</th>'

    st.markdown(f"""
    <script>
    function sortQ(c){{
      var t=document.getElementById('quadroTbl'),rows=Array.from(t.querySelectorAll('tbody tr:not(:last-child)'));
      var asc=t.dataset.sort===String(c)+'_a';
      rows.sort(function(a,b){{
        var va=a.cells[c]?a.cells[c].innerText.replace(/[^0-9.,%+ -]/g,'').replace(',','.').trim():'';
        var vb=b.cells[c]?b.cells[c].innerText.replace(/[^0-9.,%+ -]/g,'').replace(',','.').trim():'';
        var na=parseFloat(va),nb=parseFloat(vb);
        if(!isNaN(na)&&!isNaN(nb))return asc?na-nb:nb-na;
        return asc?String(va).localeCompare(String(vb)):String(vb).localeCompare(String(va));
      }});
      var tbody=t.querySelector('tbody');
      rows.forEach(r=>tbody.insertBefore(r,tbody.lastElementChild));
      t.dataset.sort=asc?String(c)+'_d':String(c)+'_a';
    }}
    </script>
    <div style="background:{VI_CARD};border-radius:10px;overflow:hidden;
                box-shadow:0 2px 10px rgba(0,0,0,.3);padding:4px 0;">
    <table class="grp-table" id="quadroTbl" data-sort="">
      <thead><tr>
        <th onclick="sortQ(0)">Unidade ↕</th>
        {th_fat}{th_proj}{th_sem}{th_mes}{th_rest}
      </tr></thead>
      <tbody>{rows_html}</tbody>
    </table></div>
    <div style="font-size:11px;color:{VI_SECAO};margin-top:6px;padding-left:4px;">
      ⚪ Sem dados  &nbsp;|&nbsp;
      🟢 Compras ≤ 28% da projeção  &nbsp;|&nbsp;
      🟡 Compras entre 28% e 31% da projeção  &nbsp;|&nbsp;
      🔴 Compras &gt; 31% da projeção  &nbsp;|&nbsp;
      ⏳ Valor em trânsito = pedido realizado ainda não conferido
    </div>
    """, unsafe_allow_html=True)

st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
_tab_grupo.__exit__(None, None, None)
_tab_resumo.__enter__()

# ════════════════════════════════════════════════════════════════════════════
# BLOCO 1 — KPIs
# ════════════════════════════════════════════════════════════════════════════

# Decide se os KPIs mostram dados do mês ou da semana selecionada
if kpis_sem and semana_filtro:
    _fat_kpi  = kpis_sem["fat"]
    _comp_kpi = kpis_sem["comp"]
    _et_kpi   = kpis_sem["em_transito"]
    _cmc_kpi  = _comp_kpi / _fat_kpi * 100 if _fat_kpi > 0 else 0.0
    # CMV semanal: usa cálculo EI/EF da semana; fallback para mensal se aberta
    if cmv_sem and cmv_sem["ok"]:
        _cmv_kpi      = cmv_sem["cmv_pct"]
        _cmv_sem_ok   = True
    else:
        _cmv_kpi      = cmv_pct    # fallback: CMV mensal
        _cmv_sem_ok   = False
    _periodo_label = semana_sel
    _escopo = "semana"
else:
    _fat_kpi  = fat_real
    _comp_kpi = comp_mes
    _et_kpi   = em_transito_mes
    _cmv_kpi  = cmv_pct
    _cmc_kpi  = cmc_pct
    _cmv_sem_ok = False
    _periodo_label = datetime.strptime(periodo, "%Y-%m").strftime("%B / %Y").title()
    _escopo = "mes"

_unid_label = _nome_locked if (_unit_lock and _unit_lock in _slug_para_nome) else nome_sel
secao(f"📌 Indicadores — {_unid_label} · {_periodo_label}  "
      f"<span title='{fat_fonte_tip}' style='font-size:.75rem;font-weight:400;opacity:.8;cursor:help'>{fat_fonte}</span>")

c1, c2, c3, c4 = st.columns(4)

with c1:
    if _escopo == "semana" and cmv_sem:
        if cmv_sem["ok"]:
            # CMV calculado pela contagem da semana
            _det = (f'EI R${cmv_sem["ei"]:,.0f} + Comp R${cmv_sem["compras"]:,.0f}'
                    f' − EF R${cmv_sem["ef"]:,.0f}')
            kpi("CMV Semana",
                f"{_cmv_kpi:.1f}%",
                f"Meta: {META_CMV:.1f}%",
                _cls_cmv(_cmv_kpi),
                f'<span style="font-size:.70rem;color:{VI_SUBTXT};">{_det}</span>')
        else:
            # Semana aberta: sem contagem de fechamento
            kpi("CMV Semana", "—", f"Meta: {META_CMV:.1f}%", "cinza",
                '<span style="font-size:.72rem;color:#888">Semana aberta — aguarda próxima contagem</span>')
    elif has_cmv:
        kpi("CMV Realizado",
            f"{_cmv_kpi:.1f}%",
            f"Meta: {META_CMV:.1f}%",
            _cls_cmv(_cmv_kpi),
            _delta(_cmv_kpi, cmv_pct_ant))
    else:
        kpi("CMV Realizado", "—", f"Meta: {META_CMV:.1f}%", "cinza",
            '<span style="font-size:.72rem;color:#888">Necessita 2 contagens no mês</span>')

with c2:
    _cls_c = _cls_cmc(_cmc_kpi)
    _delta_cmc = _delta(_cmc_kpi, cmc_pct_ant) if _escopo == "mes" else ""
    _et_txt = (f'<div style="font-size:.75rem;color:{COR_ATENC};margin-top:5px;'
               f'border-top:1px solid #d9d4c5;padding-top:4px;">'
               f'⏳ Em trânsito: <b>R$ {_et_kpi:,.0f}</b>'
               f'<span style="font-size:.68rem;color:{VI_SUBTXT};"> (não conferidos)</span>'
               f'</div>') if _et_kpi > 0 else ""
    st.markdown(f"""
    <div class="kpi {_cls_c}">
      <div class="kpi-label">CMC Realizado</div>
      <div class="kpi-valor {_cls_c}">{_cmc_kpi:.1f}%</div>
      <div class="kpi-meta">Meta: {_meta_cmc:.1f}%</div>
      {"<div class='kpi-delta'>" + _delta_cmc + "</div>" if _delta_cmc else ""}
      {_et_txt}
    </div>""", unsafe_allow_html=True)

def _cls_fat_dyn(v, meta):
    if meta <= 0: return "atencao"
    p = v / meta
    if p >= 1.0: return "bom"
    if p >= 0.85: return "atencao"
    return "critico"

with c3:
    if META_FAT > 0:
        kpi(f"Faturamento{' (semana)' if _escopo=='semana' else ''}",
            f"R$ {_fat_kpi:,.0f}", f"R$ {META_FAT:,.0f}",
            _cls_fat_dyn(_fat_kpi, META_FAT),
            _delta(_fat_kpi, fat_ant, "R$ {:+,.0f}", bom_se_menos=False) if _escopo == "mes" else "")
    else:
        kpi(f"Faturamento{' (semana)' if _escopo=='semana' else ''}",
            f"R$ {_fat_kpi:,.0f}", "sem meta cadastrada", "atencao",
            _delta(_fat_kpi, fat_ant, "R$ {:+,.0f}", bom_se_menos=False) if _escopo == "mes" else "")

with c4:
    if _escopo == "semana":
        _meta_sem_val = metas_semanais.get(semana_filtro[0], 0.0) if semana_filtro else 0.0
        if _meta_sem_val > 0:
            _cls_c4  = "bom" if _comp_kpi <= _meta_sem_val else ("atencao" if _comp_kpi <= _meta_sem_val * 1.05 else "critico")
            _desvio_sem = _comp_kpi - _meta_sem_val
            _desvio_txt = (f'{"+" if _desvio_sem >= 0 else ""}R$ {_desvio_sem:,.0f} vs meta semanal')
            kpi("Compras Conferidas", f"R$ {_comp_kpi:,.0f}",
                f"Meta semanal: R$ {_meta_sem_val:,.0f}",
                _cls_c4,
                f'<span style="font-size:.75rem;color:{"#7f0000" if _desvio_sem > 0 else "#2a6b3c"};font-weight:600">{_desvio_txt}</span>')
        else:
            kpi("Compras Conferidas", f"R$ {_comp_kpi:,.0f}",
                f"Meta CMC {_meta_cmc:.0f}%  →  R$ {_fat_kpi * _meta_cmc / 100:,.0f}",
                _cls_cmc(_cmc_kpi), "")
    elif META_FAT > 0:
        pct_meta = fat_real / META_FAT * 100
        cls_proj = _cls_fat_dyn(fat_proj, META_FAT)
        _fonte_proj = "Cantucci API (base histórica 90d)" if _usa_proj_api else "Estimativa linear"
        kpi("Projeção p/ fim do mês",
            f"R$ {fat_proj:,.0f}", f"R$ {META_FAT:,.0f}",
            cls_proj,
            f'<span style="color:{AZUL_TEXTO}">{pct_meta:.0f}% da meta  ·  {_fonte_proj}</span>')
    else:
        _fonte_proj = "Cantucci API (base histórica 90d)" if _usa_proj_api else "Estimativa linear"
        kpi("Projeção p/ fim do mês", f"R$ {fat_proj:,.0f}", _fonte_proj,
            "atencao", "")

# ── Custos Operacionais (excluídos do CMV e CMC) ─────────────────────────────
_ano_op, _mes_op = int(periodo[:4]), int(periodo[5:7])
_op_ini = f"{periodo}-01"
_op_fim = f"{periodo}-{calendar.monthrange(_ano_op, _mes_op)[1]:02d}"
if semana_filtro:
    _op_ini = semana_filtro[0]
    _op_fim = semana_filtro[1]

_ops = load_compras_op(uid, _op_ini, _op_fim)
_op_limp  = _ops["limpeza"]
_op_uso   = _ops["uso_interno"]
_op_total = _op_limp + _op_uso

if _op_total > 0:
    _escopo_label = semana_sel if semana_filtro else datetime.strptime(periodo, "%Y-%m").strftime("%B/%Y").title()
    st.markdown(f"""
    <div style="background:{VI_CARD};border-radius:10px;padding:14px 18px;
                border-left:4px solid #6c757d;margin-top:14px;">
      <div style="font-size:.72rem;font-weight:700;color:{VI_SUBTXT};
                  text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px;">
        📋 Custos Operacionais — excluídos do CMV e CMC &nbsp;·&nbsp;
        <span style="font-weight:400">{_escopo_label}</span>
      </div>
      <div style="display:flex;gap:28px;align-items:center;flex-wrap:wrap;">
        <div>
          <div style="font-size:.7rem;color:{VI_SUBTXT}">Mat. de Limpeza</div>
          <div style="font-size:1.25rem;font-weight:700;color:{VI_TEXTO}">R$ {_op_limp:,.0f}</div>
        </div>
        <div>
          <div style="font-size:.7rem;color:{VI_SUBTXT}">Alim. Funcionários</div>
          <div style="font-size:1.25rem;font-weight:700;color:{VI_TEXTO}">R$ {_op_uso:,.0f}</div>
        </div>
        <div style="border-left:1px solid {VI_BORDA};padding-left:20px;">
          <div style="font-size:.7rem;color:{VI_SUBTXT}">Total operacional</div>
          <div style="font-size:1.35rem;font-weight:800;color:#6c757d">R$ {_op_total:,.0f}</div>
        </div>
      </div>
    </div>""", unsafe_allow_html=True)
_tab_resumo.__exit__(None, None, None)
_tab_evolucao.__enter__()

# ════════════════════════════════════════════════════════════════════════════
# BLOCO 2 — Faturamento Semanal
# ════════════════════════════════════════════════════════════════════════════

secao("📅 Faturamento vs Compras por Semana")

if df_fat_sem.empty:
    st.info("Sem dados de vendas semanais para este período.")
else:
    df_fat_sem = df_fat_sem.copy()
    df_fat_sem["semana"] = [f"S{i+1}" for i in range(len(df_fat_sem))]
    df_fat_sem["label"] = df_fat_sem.apply(
        lambda r: (f"S{df_fat_sem.index.get_loc(r.name)+1}  "
                   f"{datetime.strptime(r['data_inicio'],'%Y-%m-%d').strftime('%d/%m')}"
                   f"–{datetime.strptime(r['data_fim'],'%Y-%m-%d').strftime('%d/%m')}"),
        axis=1
    )
    df_fat_sem["dias"] = df_fat_sem.apply(
        lambda r: (datetime.strptime(r["data_fim"], "%Y-%m-%d") -
                   datetime.strptime(r["data_inicio"], "%Y-%m-%d")).days + 1, axis=1
    )
    df_fat_sem["fat_dia"] = df_fat_sem["fat"] / df_fat_sem["dias"]

    # Mescla compras por semana (conferidas + em_transito)
    if not df_comp_sem.empty:
        df_fat_sem = df_fat_sem.merge(
            df_comp_sem[["data_inicio","comp","em_transito"]], on="data_inicio", how="left")
    else:
        df_fat_sem["comp"]        = 0.0
        df_fat_sem["em_transito"] = 0.0
    df_fat_sem["comp"]        = df_fat_sem["comp"].fillna(0)
    df_fat_sem["em_transito"] = df_fat_sem.get("em_transito", pd.Series([0.0]*len(df_fat_sem))).fillna(0)

    # Meta de compra: usa meta semanal definida pelo master quando disponível;
    # fallback para CMC% mensal quando meta não foi definida (meta_sem=0)
    df_fat_sem["meta_comp"] = df_fat_sem["data_inicio"].apply(
        lambda di: metas_semanais.get(di, 0.0) or (
            df_fat_sem.loc[df_fat_sem["data_inicio"] == di, "fat"].iloc[0] * META_CMC / 100
        )
    )
    df_fat_sem["tem_meta_sem"] = df_fat_sem["data_inicio"].apply(
        lambda di: metas_semanais.get(di, 0.0) > 0
    )
    df_fat_sem["cmc_pct"]   = df_fat_sem.apply(
        lambda r: r["comp"] / r["fat"] * 100 if r["fat"] > 0 else 0, axis=1
    )

    col_graf, col_tab = st.columns([3, 2])

    with col_graf:
        fig = go.Figure()
        # Barras: faturamento
        fig.add_trace(go.Bar(
            name="Faturamento",
            x=df_fat_sem["label"],
            y=df_fat_sem["fat"],
            marker_color=COR_BOM,
            text=[f"R$ {v:,.0f}" for v in df_fat_sem["fat"]],
            textposition="outside",
            textfont=dict(size=10, color=COR_BOM),
        ))
        # Barras: compras realizadas
        fig.add_trace(go.Bar(
            name="Compras",
            x=df_fat_sem["label"],
            y=df_fat_sem["comp"],
            marker_color=COR_ATENC,
            text=[f"R$ {v:,.0f}" for v in df_fat_sem["comp"]],
            textposition="outside",
            textfont=dict(size=10, color=COR_ATENC),
        ))
        # Linha: meta de compra (semanal quando definida, senão CMC%)
        _tem_meta_sem = df_fat_sem["tem_meta_sem"].any()
        _leg_meta = "Meta semanal (definida)" if _tem_meta_sem else f"Meta compra ({META_CMC:.0f}%)"
        fig.add_trace(go.Scatter(
            name=_leg_meta,
            x=df_fat_sem["label"],
            y=df_fat_sem["meta_comp"],
            mode="lines+markers",
            line=dict(color=COR_TRI, dash="dash", width=2),
            marker=dict(size=7, color=COR_TRI),
        ))
        fig = graf_layout(fig, height=320)
        fig.update_layout(
            barmode="group",
            bargap=0.25,
            bargroupgap=0.05,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                        font=dict(color=BRANCO, size=11)),
            yaxis=dict(tickprefix="R$ ", gridcolor=AZUL_BORDA),
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_tab:
        rows = []
        for _, r in df_fat_sem.iterrows():
            cls_cmc = _cls_cmc(r["cmc_pct"])
            cor_cmc = {"bom": AZUL_CLARO, "atencao": AMARELO, "critico": AMARELO_ESC}[cls_cmc]
            desvio  = r["comp"] - r["meta_comp"]
            sinal   = "+" if desvio >= 0 else "−"
            rows.append({
                "sem":           r["semana"],
                "periodo":       f"{datetime.strptime(r['data_inicio'],'%Y-%m-%d').strftime('%d/%m')} – {datetime.strptime(r['data_fim'],'%Y-%m-%d').strftime('%d/%m')}",
                "fat":           r["fat"],
                "comp":          r["comp"],
                "em_transito":   float(r.get("em_transito", 0)),
                "meta_comp":     r["meta_comp"],
                "cmc_pct":       r["cmc_pct"],
                "cor_cmc":       cor_cmc,
                "desvio":        desvio,
                "sinal":         sinal,
                "estoque_ini":   estoque_por_semana.get(r["data_inicio"], 0.0),
                "data_inicio":   r["data_inicio"],
            })

        for row in rows:
            # cor_val: borda e ícones (pode ser a cor suave original)
            cor_val = COR_CRIT if row["cmc_pct"] > META_CMC + 3 else (COR_ATENC if row["cmc_pct"] > META_CMC else COR_BOM)
            # cor_val_txt / cor_dev_txt: texto em fundo claro → versões escuras para contraste WCAG
            cor_val_txt = COR_CRIT_TXT if row["cmc_pct"] > META_CMC + 3 else (COR_ATENC_TXT if row["cmc_pct"] > META_CMC else COR_BOM_TXT)
            cor_dev = COR_CRIT_TXT if row["desvio"] > 0 else COR_BOM_TXT
            et      = row["em_transito"]
            et_html = (
                f'<span style="grid-column:1/-1;font-size:.72rem;color:{COR_ATENC};'
                f'padding:2px 0 2px 4px;margin-top:-1px;border-top:1px dashed #d9d4c5;">'
                f'⏳ Em trânsito: <b>R$ {et:,.0f}</b>'
                f'<span style="font-size:.68rem;color:{VI_SUBTXT};"> (pedidos não conferidos)</span>'
                f'</span>'
            ) if et > 0 else ""
            st.markdown(
                f'<div style="background:{VI_CARD};border-radius:10px;padding:12px 16px;'
                f'margin-bottom:8px;border-left:4px solid {cor_val};'
                f'box-shadow:0 2px 6px rgba(0,0,0,.25);">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">'
                f'<span style="color:{VI_TEXTO};font-weight:700;font-size:.95rem;">{row["sem"]}'
                f'<span style="font-size:.78rem;font-weight:400;color:{VI_SUBTXT};margin-left:6px;">'
                f'{row["periodo"]}</span></span>'
                f'<span style="color:{cor_val_txt};font-weight:800;font-size:1.05rem;">'
                f'CMC {row["cmc_pct"]:.1f}%</span></div>'
                f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;font-size:.82rem;">'
                f'<span style="color:{VI_SUBTXT};">Faturamento</span>'
                f'<span style="color:{VI_TEXTO};text-align:right;font-weight:600;">R$ {row["fat"]:,.0f}</span>'
                + (
                    f'<span style="color:{VI_SUBTXT};">Estoque inicial (contagem)</span>'
                    f'<span style="color:{VI_TEXTO};text-align:right;font-weight:600;">R$ {row["estoque_ini"]:,.0f}</span>'
                    if row["estoque_ini"] > 0 else
                    f'<span style="color:{VI_SUBTXT};">Estoque inicial (contagem)</span>'
                    f'<span style="color:#aaa;text-align:right;font-weight:500;font-style:italic;">R$ 0,00</span>'
                ) +
                f'<span style="color:{VI_SUBTXT};">Compras realizadas</span>'
                f'<span style="color:#8a4e00;text-align:right;font-weight:600;">R$ {row["comp"]:,.0f}</span>'
                + (f'<span style="grid-column:1/-1;font-size:.72rem;color:{COR_ATENC};'
                   f'padding:2px 0 2px 4px;margin-top:-1px;border-top:1px dashed #d9d4c5;">'
                   f'&#9203; Em trânsito: <b>R$ {et:,.0f}</b>'
                   f'<span style="font-size:.68rem;color:{VI_SUBTXT};"> (pedidos não conferidos)</span>'
                   f'</span>' if et > 0 else '') +
                f'<span style="color:{VI_SUBTXT};">Meta compra ({META_CMC:.0f}%)</span>'
                f'<span style="color:#2a6b3c;text-align:right;font-weight:600;">R$ {row["meta_comp"]:,.0f}</span>'
                f'<span style="color:{VI_SUBTXT};">Desvio vs meta</span>'
                f'<span style="color:{cor_dev};text-align:right;font-weight:700;">'
                f'{row["sinal"]} R$ {abs(row["desvio"]):,.0f}</span>'
                f'</div></div>',
                unsafe_allow_html=True,
            )
_tab_evolucao.__exit__(None, None, None)
_tab_resumo.__enter__()

# ════════════════════════════════════════════════════════════════════════════
# BLOCO 3 — CMV / Compras por Categoria (respeita filtro semanal)
# ════════════════════════════════════════════════════════════════════════════

if semana_filtro:
    # ── Visão semanal: compras por categoria no período selecionado ───────────
    _s_ini, _s_fim = semana_filtro[0], semana_filtro[1]
    cats_sem = load_compras_categoria_semana(uid, _s_ini, _s_fim)
    # Já vem classificado por código (com fallback por nome) e agregado.
    # Faturamento da semana selecionada (nao do mes todo)
    _fat_sem_row = df_fat_sem[df_fat_sem["data_inicio"] == _s_ini] if not df_fat_sem.empty else pd.DataFrame()
    _fat_sem = float(_fat_sem_row["fat"].iloc[0]) if not _fat_sem_row.empty else 0.0

    # Meta por categoria = orçamento total da semana (meta definida pelo
    # master, ou fallback CMC% do faturamento — mesma regra do gráfico de
    # Evolução) × peso (%) da categoria nessa unidade.
    _meta_sem_total = metas_semanais.get(_s_ini, 0.0) or (_fat_sem * META_CMC / 100)
    _pesos_cat = load_meta_peso_categoria(uid)

    # Garante uma linha pra toda categoria com peso cadastrado, mesmo sem
    # nenhuma compra na semana — senão a meta dela nunca aparece na tabela,
    # só as categorias que já compraram algo.
    _cats_sem_compra = [c for c in _pesos_cat if c not in set(cats_sem["categoria"])]
    if _cats_sem_compra:
        cats_sem = pd.concat([
            cats_sem,
            pd.DataFrame({"categoria": _cats_sem_compra, "compras": 0.0}),
        ], ignore_index=True)

    cats_sem["pct"] = cats_sem["compras"] / _fat_sem * 100 if _fat_sem > 0 else 0
    cats_sem["meta"] = cats_sem["categoria"].map(
        lambda c: _meta_sem_total * _pesos_cat.get(c, 0.0) / 100
    )
    cats_sem = cats_sem.sort_values("compras", ascending=False, ignore_index=True)

    secao(f"🔬 Compras por Categoria — {semana_sel}")

    if cats_sem.empty:
        st.info("Sem compras registradas para a semana selecionada.")
    else:
        col_cat1, col_cat2 = st.columns([3, 2])

        with col_cat1:
            # Só categorias com compra real no gráfico — as sem compra ainda
            # (mostradas na tabela ao lado, com a meta) não rendem barra.
            df_plot = cats_sem[cats_sem["compras"] > 0].sort_values("compras", ascending=True).tail(12)
            fig2 = go.Figure(go.Bar(
                x=df_plot["compras"],
                y=df_plot["categoria"],
                orientation="h",
                marker_color=COR_BOM,
                text=[f"R$ {v:,.0f}  ({p:.1f}%)"
                      for v, p in zip(df_plot["compras"], df_plot["pct"])],
                textposition="inside",
                textfont=dict(size=11, color=AZUL_ESCURO),
            ))
            fig2 = graf_layout(fig2, height=340)
            fig2.update_layout(
                title=dict(text="Compras por categoria (R$ e % do fat. da semana)", font=dict(size=12)),
                xaxis=dict(tickprefix="R$ ", gridcolor=AZUL_BORDA),
            )
            st.plotly_chart(fig2, use_container_width=True)

        with col_cat2:
            df_cats_show = cats_sem.copy()
            df_cats_show["Compras"] = df_cats_show["compras"].map("R$ {:,.0f}".format)
            df_cats_show["% Fat."]  = df_cats_show["pct"].map("{:.1f}%".format)
            df_cats_show["Meta"]    = df_cats_show["meta"].map(
                lambda v: "R$ {:,.0f}".format(v) if v > 0 else "—"
            )
            st.dataframe(
                df_cats_show[["categoria", "Compras", "% Fat.", "Meta"]].rename(columns={"categoria": "Categoria"}),
                use_container_width=True, hide_index=True, height=340
            )
            if not _pesos_cat:
                st.caption("⚠️ Peso de categoria ainda não cadastrado para esta unidade — Meta fica em branco até definir.")

        _tot_sem = cats_sem["compras"].sum()
        _cmc_sem = _tot_sem / _fat_sem * 100 if _fat_sem > 0 else 0
        _meta_val_sem = META_CMC / 100 * _fat_sem
        _excesso_sem  = max(0, _cmc_sem - META_CMC)
        _excesso_val_sem = _excesso_sem / 100 * _fat_sem
        cor_brd = COR_BOM if _cmc_sem <= META_CMC else COR_CRIT
        st.markdown(f"""
        <div style="background:{VI_CARD};border-radius:8px;padding:12px 18px;
                    border-left:4px solid {cor_brd};margin-top:4px;
                    box-shadow:0 2px 6px rgba(0,0,0,.25);">
          <span style="color:{VI_SUBTXT};font-size:.8rem;font-weight:700;text-transform:uppercase;">
            Resumo de Compras — {semana_sel}</span><br>
          <span style="color:{VI_TEXTO};">
            Compras <b>R$ {_tot_sem:,.0f}</b> · Faturamento <b>R$ {_fat_sem:,.0f}</b>
            · CMC <b>{_cmc_sem:.1f}%</b> (meta {META_CMC:.0f}%)
          </span>
          {"<br><span style='color:" + COR_CRIT + ";font-size:.85rem;'>⚠ Excesso vs meta: R$ " + f"{_excesso_val_sem:,.0f}" + f" ({_excesso_sem:+.1f}pp)</span>" if _excesso_sem > 0 else "<br><span style='color:#2a6b3c;font-size:.85rem;'>✔ Dentro da meta</span>"}
          <br><span style="color:{VI_SUBTXT};font-size:.72rem;">⚠ Visão semanal mostra compras do período — CMV (com EI/EF) disponivel apenas na visao mensal.</span>
        </div>""", unsafe_allow_html=True)

else:
    # ── Visão mensal: CMV completo (EI + Compras − EF) ───────────────────────
    secao("🔬 CMV por Categoria")

    if cats.empty:
        if not has_cmv:
            st.info(f"CMV nao calculado para {nome_sel} em {periodo} — sao necessarias ao menos 2 contagens de estoque.")
        else:
            st.info("Sem detalhes por categoria.")
    else:
        col_cat1, col_cat2 = st.columns([3, 2])

        with col_cat1:
            df_plot = cats[cats["cmv_valor"] > 0].sort_values("cmv_valor", ascending=True).tail(12)
            cores_bar = [COR_CRIT if v > META_CMV * 0.4 else COR_BOM
                         for v in df_plot["cmv_percentual"]]
            fig2 = go.Figure(go.Bar(
                x=df_plot["cmv_valor"],
                y=df_plot["categoria"],
                orientation="h",
                marker_color=cores_bar,
                text=[f"R$ {v:,.0f}  ({p:.1f}%)"
                      for v, p in zip(df_plot["cmv_valor"], df_plot["cmv_percentual"])],
                textposition="inside",
                textfont=dict(size=11, color=AZUL_ESCURO),
            ))
            fig2 = graf_layout(fig2, height=340)
            fig2.update_layout(
                title=dict(text="Valor consumido (R$) e % do faturamento", font=dict(size=12)),
                xaxis=dict(tickprefix="R$ ", gridcolor=AZUL_BORDA),
            )
            st.plotly_chart(fig2, use_container_width=True)

        with col_cat2:
            df_cats_show = cats[cats["cmv_valor"] > 0].sort_values("cmv_valor", ascending=False).copy()
            df_cats_show["Compras"] = df_cats_show["compras"].map("R$ {:,.0f}".format)
            df_cats_show["CMV"]     = df_cats_show["cmv_valor"].map("R$ {:,.0f}".format)
            df_cats_show["CMV%"]    = df_cats_show["cmv_percentual"].map("{:.1f}%".format)
            st.dataframe(
                df_cats_show[["categoria", "Compras", "CMV", "CMV%"]].rename(columns={"categoria": "Categoria"}),
                use_container_width=True, hide_index=True, height=340
            )

        if has_cmv:
            excesso_cmv = max(0, cmv_pct - META_CMV)
            excesso_val = excesso_cmv / 100 * fat_real
            cor_brd = COR_BOM if cmv_pct <= META_CMV else COR_CRIT
            st.markdown(f"""
            <div style="background:{VI_CARD};border-radius:8px;padding:12px 18px;
                        border-left:4px solid {cor_brd};margin-top:4px;
                        box-shadow:0 2px 6px rgba(0,0,0,.25);">
              <span style="color:{VI_SUBTXT};font-size:.8rem;font-weight:700;text-transform:uppercase;">
                Resumo do CMV</span><br>
              <span style="color:{VI_TEXTO};">
                EI <b>R$ {ei_mes:,.0f}</b> + Compras <b>R$ {comp_mes:,.0f}</b> − EF <b>R$ {ef_mes:,.0f}</b>
                = CMV <b>R$ {cmv_val:,.0f}</b> ({cmv_pct:.1f}% do fat.)
              </span>
              {"<br><span style='color:" + COR_CRIT + ";font-size:.85rem;'>⚠ Excesso vs meta: R$ " + f"{excesso_val:,.0f}" + f" ({excesso_cmv:+.1f}pp)</span>" if excesso_cmv > 0 else "<br><span style='color:#2a6b3c;font-size:.85rem;'>✔ Dentro da meta</span>"}
            </div>""", unsafe_allow_html=True)
_tab_resumo.__exit__(None, None, None)
_tab_vendas.__enter__()

# ════════════════════════════════════════════════════════════════════════════
# BLOCO 4 — Top Produtos Vendidos + Compras (lado a lado)
# ════════════════════════════════════════════════════════════════════════════

semana_label = f" — {semana_sel}" if semana_filtro else ""
secao(f"🍽️ Produtos Mais Vendidos  ·  🛒 Compras por Categoria{semana_label}")

col_prod, col_comp = st.columns(2)

with col_prod:
    if df_top_display.empty:
        st.info("Sem dados de vendas.")
    else:
        df_top_show = df_top_display.copy()
        df_top_show["fat_pct"] = df_top_show["fat"] / fat_real * 100
        df_top_show["ticket"] = df_top_show["fat"] / df_top_show["qtd"].replace(0, 1)
        fig3 = px.bar(
            df_top_show.head(12),
            x="fat", y="produto",
            orientation="h",
            color_discrete_sequence=[COR_BOM],
            text=df_top_show.head(12)["fat"].map("R$ {:,.0f}".format),
        )
        fig3.update_traces(textposition="inside", textfont=dict(color=VI_TEXTO, size=10))
        fig3 = graf_layout(fig3, height=360)
        fig3.update_layout(
            title=dict(text="Top 12 por faturamento", font=dict(size=12)),
            yaxis=dict(categoryorder="total ascending"),
            xaxis=dict(tickprefix="R$ "),
            showlegend=False,
        )
        st.plotly_chart(fig3, use_container_width=True)

with col_comp:
    if df_compras_display.empty:
        st.info("Sem compras registradas.")
    else:
        df_compras_display["secao_norm"] = df_compras_display.apply(
            lambda r: _classificar_codigo(r.get("sku_codigo"), r.get("secao")), axis=1)
        comp_cat = (df_compras_display.groupby("secao_norm")["valor_total"]
                    .sum().reset_index()
                    .sort_values("valor_total", ascending=True))
        total_comp = comp_cat["valor_total"].sum()
        comp_cat["label"] = comp_cat.apply(
            lambda r: f"R$ {r['valor_total']:,.0f}  ({r['valor_total']/total_comp*100:.1f}%)", axis=1
        )

        fig4 = px.bar(
            comp_cat,
            x="valor_total",
            y="secao_norm",
            orientation="h",
            color_discrete_sequence=[COR_BOM],
            text="label",
        )
        fig4.update_traces(textposition="inside", textfont=dict(color=VI_TEXTO, size=10))
        fig4 = graf_layout(fig4, height=360)
        fig4.update_layout(
            title=dict(text=f"Compras por categoria — R$ {total_comp:,.0f} total",
                       font=dict(size=12)),
            xaxis=dict(tickprefix="R$ "),
            yaxis=dict(title=""),
            showlegend=False,
        )
        st.plotly_chart(fig4, use_container_width=True)

# ── Helper: renderiza um bloco de desvios (cozinha ou bar) ───────────────────
def _render_desvios(df_setor: pd.DataFrame, titulo: str, csv_key: str) -> None:
    secao(titulo)

    _prot_periodo = semana_sel if semana_filtro else datetime.strptime(periodo, "%Y-%m").strftime("%B/%Y").title()
    _prot_ei_lbl  = datetime.strptime(_prot_ei, "%Y-%m-%d").strftime("%d/%m") if _prot_ei else "—"
    _prot_ef_lbl  = datetime.strptime(_prot_ef, "%Y-%m-%d").strftime("%d/%m") if _prot_ef else "—"

    st.markdown(
        f'<div style="font-size:.78rem;color:{VI_SECAO};margin-bottom:10px;">'
        f'Período: <b>{_prot_periodo}</b> &nbsp;·&nbsp; '
        f'EI: contagem de <b>{_prot_ei_lbl}</b> &nbsp;·&nbsp; '
        f'EF: contagem de <b>{_prot_ef_lbl}</b> &nbsp;·&nbsp; '
        f'Desvio = Real − Teórico &nbsp;|&nbsp; '
        f'🔴 Desvio positivo = usou mais do que vendeu (quebra/desperdício/erro)'
        f'</div>', unsafe_allow_html=True
    )

    if df_setor.empty:
        st.info("Sem dados para o período — verifique se há vendas e contagens registradas.")
        return
    if not _prot_ei or not _prot_ef:
        st.warning("⚠️ Sem duas contagens no período para calcular consumo real.")
        return

    _sem_contagem = df_setor[~df_setor["tem_contagem"]]
    _com_contagem = df_setor[df_setor["tem_contagem"]].copy()

    if not _com_contagem.empty:
        _tot_teo    = _com_contagem["consumo_teo"].sum()
        _tot_real   = _com_contagem["consumo_real"].sum()
        _tot_desv   = _com_contagem["desvio"].sum()
        _tot_desv_r = _com_contagem["desvio_rs"].sum()
        _cls_desv   = "bom" if _tot_desv <= 0 else ("atencao" if _tot_desv <= _tot_teo * 0.05 else "critico")
        _pct_desv   = _tot_desv / _tot_teo * 100 if _tot_teo > 0 else 0

        kp1, kp2, kp3, kp4 = st.columns(4)
        with kp1:
            kpi("Consumo Teórico", f"{_tot_teo:.0f} un", "soma das vendas x fator", "cinza", "")
        with kp2:
            kpi("Consumo Real", f"{_tot_real:.0f} un", "EI + Compras - EF (contagens)", "cinza", "")
        with kp3:
            kpi("Desvio Total", f"{_tot_desv:+.0f} un", f"R$ {_tot_desv_r:+,.0f}", _cls_desv, "")
        with kp4:
            kpi("Desvio %", f"{_pct_desv:+.1f}%", "sobre consumo teorico", _cls_desv, "")

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        df_show = _com_contagem[[
            "proteina", "n_pratos", "consumo_teo", "ei_qty", "compras_qty",
            "ef_qty", "consumo_real", "desvio", "custo_medio", "desvio_rs"
        ]].copy()
        df_show.columns = [
            "Insumo", "Pratos", "Teorico (un)", "EI (un)", "Compras (un)",
            "EF (un)", "Real (un)", "Desvio (un)", "Custo Unit.", "Desvio (R$)"
        ]
        df_show["Teorico (un)"] = df_show["Teorico (un)"].map("{:.0f}".format)
        df_show["EI (un)"]      = df_show["EI (un)"].map("{:.0f}".format)
        df_show["Compras (un)"] = df_show["Compras (un)"].map("{:.0f}".format)
        df_show["EF (un)"]      = df_show["EF (un)"].map("{:.0f}".format)
        df_show["Real (un)"]    = df_show["Real (un)"].map("{:.0f}".format)
        df_show["Custo Unit."]  = df_show["Custo Unit."].map("R$ {:.2f}".format)
        _dev_raw = _com_contagem["desvio"].values
        _dev_rs  = _com_contagem["desvio_rs"].values
        df_show["Desvio (un)"] = [
            f"🔴 {v:+.0f}" if v > 0 else (f"🟢 {v:+.0f}" if v < 0 else "✔ 0")
            for v in _dev_raw
        ]
        df_show["Desvio (R$)"] = [
            f"🔴 R$ {v:+,.0f}" if v > 0 else (f"🟢 R$ {v:+,.0f}" if v < 0 else "R$ 0")
            for v in _dev_rs
        ]
        st.dataframe(df_show, use_container_width=True, hide_index=True)
        _xlsx = df_to_excel_bytes(df_show)
        st.download_button("⬇️ Baixar Excel", _xlsx,
                           file_name=f"{csv_key}_{nome_sel}_{periodo}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           key=f"csv_{csv_key}_{uid}")

    if not _sem_contagem.empty:
        nomes_sc = ", ".join(_sem_contagem["proteina"].tolist())
        st.warning(
            f"⚠️ Sem contagem Atlas para: **{nomes_sc}** — "
            f"consumo teorico calculado mas real nao disponivel nesta unidade/periodo."
        )


# ── Cozinha ───────────────────────────────────────────────────────────────────
_render_desvios(df_cozinha, "🍳 Cozinha — Teorico vs Real", "cozinha")

st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

# ── Bar ───────────────────────────────────────────────────────────────────────
_render_desvios(df_bar, "🍷 Bar — Teorico vs Real", "bar")

_tab_vendas.__exit__(None, None, None)
_tab_estoque.__enter__()

# ════════════════════════════════════════════════════════════════════════════
# BLOCO 5 — Estoque Atual (se disponível)
# ════════════════════════════════════════════════════════════════════════════

secao(f"📦 Estoque Atual  ·  Data: {datetime.strptime(data_estoque,'%Y-%m-%d').strftime('%d/%m/%Y') if data_estoque else '—'}")

if df_estoque.empty:
    st.info("Sem dados de estoque disponíveis.")
else:
    df_e = df_estoque[df_estoque["valor_estoque"] > 0].copy()
    total_estoque = df_e["valor_estoque"].sum()

    # ABC
    df_e = df_e.sort_values("valor_estoque", ascending=False).reset_index(drop=True)
    df_e["pct"]     = df_e["valor_estoque"] / total_estoque * 100
    df_e["pct_ac"]  = df_e["pct"].cumsum()
    df_e["classe"]  = df_e["pct_ac"].apply(lambda p: "A" if p <= 80 else ("B" if p <= 95 else "C"))

    col_e1, col_e2, col_e3 = st.columns(3)

    # Métricas rápidas
    for col, cls, cor, desc in [
        (col_e1, "A", COR_CRIT,  "80% do valor · vigilância DIÁRIA"),
        (col_e2, "B", COR_ATENC, "15% do valor · vigilância SEMANAL"),
        (col_e3, "C", COR_BOM,   "5% do valor · vigilância MENSAL"),
    ]:
        grp = df_e[df_e["classe"] == cls]
        with col:
            v = grp["valor_estoque"].sum()
            st.markdown(f"""
            <div class="kpi" style="border-left-color:{cor}">
              <div class="kpi-label">Classe {cls}</div>
              <div class="kpi-valor" style="color:{cor};font-size:1.6rem;">{len(grp)} produtos</div>
              <div class="kpi-meta">R$ {v:,.0f} · {v/total_estoque*100:.0f}% do estoque</div>
              <div class="kpi-delta" style="color:{AZUL_TEXTO};font-size:.75rem;">{desc}</div>
            </div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    # ── EI / Compras / EF / Consumo por item ─────────────────────────────────
    df_full = df_e.copy()
    if not df_estoque_ant.empty:
        # Casa o estoque inicial (EI) por sku_item_id — insumo_id não é estável
        # entre importações de contagem (overlap pode ser zero).
        df_full = df_full.merge(df_estoque_ant, on="sku_item_id", how="left")
        df_full["qtd_estoque_ant"] = df_full["qtd_estoque_ant"].fillna(0)
    else:
        df_full["qtd_estoque_ant"] = 0.0
    # Valor do EI no custo médio atual (consistente com o EF)
    df_full["valor_estoque_ant"] = df_full["qtd_estoque_ant"] * df_full["custo_medio"]

    # Compras por insumo entre a contagem anterior e a atual (valor E quantidade)
    _data_ei_est = None
    if data_estoque:
        _db_est = conn()
        _r_ei_est = _db_est.execute(
            "SELECT MAX(data) FROM contagens WHERE unidade_id=? AND data < ?",
            (uid, data_estoque)
        ).fetchone()
        _data_ei_est = _r_ei_est[0] if _r_ei_est else None
        if _data_ei_est:
            # Casa compras por sku_item_id (UUID do SKU Atlas), pois nas unidades
            # Atlas o insumo_id costuma vir NULL — o match por insumo_id perdia
            # as compras e zerava as colunas de Compras/Consumo.
            # Contagens são feitas de manhã (fotografia do início do dia):
            # data >= EI inclui o dia da contagem inicial, data < EF exclui
            # o dia da contagem final (compras desse dia ainda não foram
            # "vistas" pela contagem e pertencem à próxima janela).
            _comp_item_df = pd.read_sql(
                "SELECT sku_item_id, SUM(valor_total) AS comp_item, SUM(quantidade) AS comp_item_qtd "
                "FROM compras WHERE unidade_id=? AND data>=? AND data<? "
                f"AND valor_total>0 AND (status_pedido='conferido' OR status_pedido IS NULL) "
                f"{_SQL_EXCL_OP} AND sku_item_id IS NOT NULL GROUP BY sku_item_id",
                _db_est, params=[uid, _data_ei_est, data_estoque]
            )
            df_full = df_full.merge(_comp_item_df, on="sku_item_id", how="left")
        _db_est.close()
    if "comp_item" not in df_full.columns:
        df_full["comp_item"] = 0.0
    if "comp_item_qtd" not in df_full.columns:
        df_full["comp_item_qtd"] = 0.0
    df_full["comp_item"]     = df_full["comp_item"].fillna(0)
    df_full["comp_item_qtd"] = df_full["comp_item_qtd"].fillna(0)
    # Consumo em valor e em quantidade: EI + Compras − EF
    df_full["consumo_val"] = df_full["valor_estoque_ant"] + df_full["comp_item"] - df_full["valor_estoque"]
    df_full["consumo_qtd"] = df_full["qtd_estoque_ant"] + df_full["comp_item_qtd"] - df_full["quantidade"]

    def _fmt_cobertura(dias):
        if dias is None or (isinstance(dias, float) and dias != dias):
            return "—"
        d = int(dias)
        if d <= 7:   return f"⚠ {d}d"
        if d <= 21:  return f"✔ {d}d"
        return f"🔴 {d}d"

    df_full["Capital Parado"] = df_full["cobertura_dias"].apply(_fmt_cobertura)

    # ── Filtros e busca ───────────────────────────────────────────────────────
    _cats_disp = sorted(df_full["secao_norm"].dropna().unique().tolist())
    _fc1, _fc2, _fc3 = st.columns([2, 2, 1])
    with _fc1:
        _busca_est = st.text_input("🔍 Buscar produto", "", key=f"est_busca_{uid}")
    with _fc2:
        _cat_sel_est = st.multiselect("Categoria", _cats_disp, default=[], key=f"est_cat_{uid}",
                                      placeholder="Todas as categorias")
    with _fc3:
        _show_neg_est = st.checkbox("Só consumo negativo", False, key=f"est_neg_{uid}")

    df_disp_est = df_full.copy()
    if _busca_est:
        df_disp_est = df_disp_est[df_disp_est["nome"].str.contains(_busca_est, case=False, na=False)]
    if _cat_sel_est:
        df_disp_est = df_disp_est[df_disp_est["secao_norm"].isin(_cat_sel_est)]
    if _show_neg_est:
        df_disp_est = df_disp_est[df_disp_est["consumo_val"] < 0]

    n_a = len(df_e[df_e["classe"] == "A"])
    n_b = len(df_e[df_e["classe"] == "B"])
    n_c = len(df_e[df_e["classe"] == "C"])

    _ei_lbl = f"EI {datetime.strptime(_data_ei_est,'%Y-%m-%d').strftime('%d/%m')}" if _data_ei_est else "EI"
    _ef_lbl = f"EF {datetime.strptime(data_estoque,'%Y-%m-%d').strftime('%d/%m')}" if data_estoque else "EF"

    def _fmt_qtd(q):
        """Quantidade: inteiro quando redondo, senão até 2 casas (kg/lt)."""
        try:
            q = float(q)
        except (TypeError, ValueError):
            return "—"
        if abs(q - round(q)) < 0.01:
            return f"{q:,.0f}"
        return f"{q:,.2f}"

    df_show = df_disp_est[["nome", "secao_norm", "classe",
                             "qtd_estoque_ant", "comp_item_qtd", "quantidade",
                             "consumo_qtd", "consumo_val",
                             "custo_medio", "Capital Parado"]].copy()
    df_show.columns = ["Produto", "Categoria", "Classe",
                        _ei_lbl, "Compras (Qtd)", _ef_lbl, "Consumo (Qtd)", "Consumo (R$)",
                        "Custo Unit.", "Capital Parado"]
    df_show[_ei_lbl]          = df_show[_ei_lbl].map(_fmt_qtd)
    df_show["Compras (Qtd)"]  = df_show["Compras (Qtd)"].map(_fmt_qtd)
    df_show[_ef_lbl]          = df_show[_ef_lbl].map(_fmt_qtd)
    df_show["Custo Unit."]    = df_show["Custo Unit."].map("R$ {:.2f}".format)
    _consumo_qtd_raw = df_disp_est["consumo_qtd"].values
    _consumo_val_raw = df_disp_est["consumo_val"].values
    df_show["Consumo (Qtd)"] = [
        f"🔴 {_fmt_qtd(v)}" if v < 0 else _fmt_qtd(v) for v in _consumo_qtd_raw
    ]
    df_show["Consumo (R$)"] = [
        f"🔴 R$ {v:,.0f}" if v < 0 else f"R$ {v:,.0f}" for v in _consumo_val_raw
    ]

    with st.expander(
        f"📋 Posição completa de estoque — {len(df_show)} itens  "
        f"(A: {n_a}  ·  B: {n_b}  ·  C: {n_c})",
        expanded=True
    ):
        st.dataframe(df_show, use_container_width=True, hide_index=True)
        _xlsx_est = df_to_excel_bytes(df_show, sheet_name="Estoque")
        st.download_button(
            "⬇️ Baixar Excel",
            _xlsx_est,
            file_name=f"estoque_{nome_sel}_{data_estoque or 'sem_data'}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"csv_estoque_{uid}",
        )
        st.markdown(
            f'<div style="font-size:11px;color:{VI_SECAO};margin-top:4px;">'
            f'EI/EF/Compras em QUANTIDADE (contagem/compra) &nbsp;|&nbsp; '
            f'EI = contagem de {_data_ei_est or "—"} &nbsp;|&nbsp; '
            f'EF = contagem de {data_estoque or "—"} &nbsp;|&nbsp; '
            f'Consumo = EI + Compras − EF (em Qtd e R$) &nbsp;|&nbsp; '
            f'🔴 Consumo negativo = mais entrou do que saiu &nbsp;|&nbsp; '
            f'Capital Parado: ⚠ &lt;7d · ✔ 7–21d · 🔴 &gt;21d (excesso de capital imobilizado)'
            f'</div>', unsafe_allow_html=True
        )
_tab_estoque.__exit__(None, None, None)
_tab_compras.__enter__()

# ════════════════════════════════════════════════════════════════════════════
# BLOCO 6 — Lista de Compras (detalhada, colapsável)
# ════════════════════════════════════════════════════════════════════════════

exp_label = f"🗒️ Detalhes das Compras{' — ' + semana_sel if semana_filtro else ' do Mês'}"
with st.expander(exp_label, expanded=False):
    if df_compras_display.empty:
        st.info("Sem compras.")
    else:
        df_c = df_compras_display[["data","nome_insumo","secao","quantidade","valor_unitario","valor_total","fornecedor"]].copy()
        df_c.columns = ["Data","Produto","Seção","Qtd","V. Unit.","V. Total","Fornecedor"]
        df_c["Data"]    = pd.to_datetime(df_c["Data"]).dt.strftime("%d/%m")
        df_c["V. Unit."] = df_c["V. Unit."].map("R$ {:.2f}".format)
        df_c["V. Total"] = df_c["V. Total"].map("R$ {:,.2f}".format)
        df_c["Produto"]  = df_c["Produto"].str[:40]
        st.dataframe(df_c, use_container_width=True, hide_index=True)
        _xlsx_comp = df_to_excel_bytes(df_c, sheet_name="Compras")
        st.download_button(
            "⬇️ Baixar Excel",
            _xlsx_comp,
            file_name=f"compras_{nome_sel}_{periodo}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"csv_compras_{uid}",
        )
_tab_compras.__exit__(None, None, None)

# ── Rodapé ────────────────────────────────────────────────────────────────────

st.markdown(f"""
<div style="text-align:center;color:{AZUL_BORDA};font-size:.72rem;margin-top:32px;padding:10px;">
  Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')} · Banco: {DB_FILE} · {nome_sel}
</div>""", unsafe_allow_html=True)
