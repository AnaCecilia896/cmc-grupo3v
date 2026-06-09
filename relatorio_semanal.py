"""
relatorio_semanal.py — Relatório Semanal Cantucci Asa Norte  (reformulado)
Uso: streamlit run relatorio_semanal.py --server.port 8502
"""

import sqlite3
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
def fetch_fat_api(periodo: str, slug: str = "asa-norte") -> dict | None:
    """
    Busca faturamento_servico diário via API cantuccidados para o período YYYY-MM e slug.
    Quando o slug mapeia para múltiplas lojas, soma os valores diários.
    Retorna {"total": float, "por_dia": {"YYYY-MM-DD": float}} ou None se falhar.
    Cache de 30 min.
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
    primeiro = date(ano, mes, 1)
    ultimo   = min(date(ano, mes, calendar.monthrange(ano, mes)[1]), date.today())

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

# Filtro SQL para excluir compras operacionais (não entram no CMC/CMV)
_SQL_EXCL_OP = (
    "AND NOT (UPPER(COALESCE(secao,'')) LIKE '%LIMPEZA%' "
    "OR UPPER(COALESCE(secao,'')) LIKE '%FUNCIONARIO%')"
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

def graf_layout(fig, height=300):
    fig.update_layout(
        paper_bgcolor=AZUL_CARD, plot_bgcolor=AZUL_CARD,
        font=dict(color=BRANCO, size=12),
        margin=dict(t=30, b=30, l=10, r=10),
        height=height,
    )
    fig.update_xaxes(gridcolor=AZUL_BORDA, zeroline=False)
    fig.update_yaxes(gridcolor=AZUL_BORDA, zeroline=False)
    return fig

def normalizar_secao(s):
    import unicodedata, re
    def _n(x): return unicodedata.normalize("NFD", str(x or "")).encode("ascii","ignore").decode().upper().strip()
    n = _n(s)
    MAP = [
        (["CARNE VERMELHA"],"Carnes Vermelhas"),
        (["CARNE BRANCA"], "Carnes Brancas"),
        (["PESCADO","FRUTO DO MAR"],"Pescados"),
        (["ALCOOLICO","ALCOOLICOS","VINHO","ESPUMANTE","CERVEJA","DESTILADO"],"Beb. Alcoólicas"),
        (["BEBIDA NAO","BEBIDA N"],"Beb. N/Alcoólicas"),
        (["HORTIFRUTI"],"Hortifruti"),
        (["LATICINIOS","LATICINIO","OVOS","QUEIJO"],"Laticínios/Ovos"),
        (["CONGELADO"],"Congelados"),
        (["CONFEITARIA"],"Confeitaria"),
        (["SECO","CONDIMENTO","CONSERVA","OLEO","ESPECIARIA","TEMPERO"],"Secos/Condimentos"),
        (["PROCESSADO"],"Processados"),
        (["DESCARTAVEL"],"Descartáveis"),
        (["EMBALAGEM"],"Embalagens"),
        (["MATERIAL DE LIMPEZA","LIMPEZA"],"Mat. Limpeza"),
        (["ALIMENTACAO FUNCIONARIO","USO INTERNO","USO MENSAL"],"Uso Interno"),
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
def load_cmv_mes(uid, periodo):
    db = conn()
    df = pd.read_sql(
        "SELECT categoria, estoque_inicial, compras, estoque_final, cmv_valor, faturamento, cmv_percentual "
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
    # Compras efetivas (conferidas) — excluindo categorias operacionais
    compras_conf = pd.read_sql(
        f"SELECT data, SUM(valor_total) as comp "
        f"FROM compras WHERE unidade_id=? AND strftime('%Y-%m', data)=? "
        f"AND valor_total>0 AND (status_pedido='conferido' OR status_pedido IS NULL) {_SQL_EXCL_OP} "
        f"GROUP BY data",
        db, params=[uid, periodo]
    )
    # Compras em trânsito — excluindo categorias operacionais
    compras_tran = pd.read_sql(
        f"SELECT data, SUM(valor_total) as em_transito "
        f"FROM compras WHERE unidade_id=? AND strftime('%Y-%m', data)=? "
        f"AND valor_total>0 AND status_pedido='em_transito' {_SQL_EXCL_OP} "
        f"GROUP BY data",
        db, params=[uid, periodo]
    )
    db.close()

    if semanas.empty:
        return pd.DataFrame(columns=["data_inicio", "data_fim", "comp", "em_transito"])

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
def load_compras_mes(uid, periodo):
    """Retorna apenas compras CONFERIDAS (efetivas) do mês, excluindo categorias operacionais."""
    db = conn()
    df = pd.read_sql(
        "SELECT secao, nome_insumo, quantidade, valor_unitario, valor_total, fornecedor, data "
        f"FROM compras WHERE unidade_id=? AND strftime('%Y-%m', data)=? AND valor_total>0 "
        f"AND (status_pedido='conferido' OR status_pedido IS NULL) {_SQL_EXCL_OP} "
        "ORDER BY data, valor_total DESC",
        db, params=[uid, periodo]
    )
    db.close()
    return df

@st.cache_data(ttl=120)
def load_em_transito_mes(uid, periodo) -> float:
    """Retorna o valor total em trânsito (realizado/confirmado, não conferido) do mês."""
    db = conn()
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
    """Top produtos para uma semana específica (por data_inicio/data_fim exatos)."""
    db = conn()
    df = pd.read_sql(
        "SELECT produto, SUM(valor_total) as fat, SUM(quantidade) as qtd "
        "FROM vendas_produtos WHERE unidade_id=? AND tipo='VENDA' "
        "AND data_inicio=? AND data_fim=? AND produto!='Faturamento (API)' "
        "GROUP BY produto ORDER BY fat DESC LIMIT ?",
        db, params=[uid, data_inicio, data_fim, n]
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
          AND NOT (UPPER(COALESCE(secao,'')) LIKE '%LIMPEZA%'
               OR  UPPER(COALESCE(secao,'')) LIKE '%FUNCIONARIO%')
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


@st.cache_data(ttl=120)
def load_quadro_compras(periodo: str,
                        semana_inicio: str = None,
                        semana_fim: str = None) -> pd.DataFrame:
    """
    Retorna DataFrame com acompanhamento de compras por unidade.
    Inclui dados mensais e (opcionalmente) semanais.
    Colunas: nome, slug, fat_mes, comp_mes, em_trans_mes, cmc_mes,
             comp_sem, em_trans_sem, fat_sem, cmc_sem,
             meta, meta_val, desvio,
             projecao, meta_val_proj, desvio_proj  ← novos
    """
    db  = conn()
    proj_dict = load_projecao_mensal(periodo)   # {slug: {...}}
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

        # Prefere linhas da API quando disponíveis (evita dupla contagem com dados do Sheets)
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
        comp_sem = em_trans_sem = fat_sem = 0.0
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

        # ── Projeção e meta baseada na projeção ─────────────────────
        proj_info    = proj_dict.get(slug_u, {})
        projecao     = proj_info.get("projecao", 0.0)
        meta_vendas  = proj_info.get("meta_vendas", 0.0)

        # Meta de compras = 28% da projeção do mês todo
        # Fallback: se não há projeção, usa faturamento real
        base_meta    = projecao if projecao > 0 else fat_mes
        meta_val_proj = base_meta * meta_pct / 100
        desvio_proj  = (comp_mes - meta_val_proj) if base_meta > 0 else None

        # CMC% calculado sobre faturamento real (como antes)
        cmc_mes  = (comp_mes  / fat_mes  * 100) if fat_mes  > 0 else None
        cmc_sem  = (comp_sem  / fat_sem  * 100) if fat_sem  > 0 else None

        # meta_val legacy (sobre fat_mes) — mantido para compatibilidade
        meta_val = fat_mes * meta_pct / 100
        desvio   = (comp_mes - meta_val) if fat_mes > 0 else None

        rows.append({
            "nome": nome_u, "slug": slug_u,
            "fat_mes": fat_mes, "comp_mes": comp_mes, "em_trans_mes": em_trans_mes,
            "cmc_mes": cmc_mes, "meta": meta_pct,
            # Meta baseada na projeção (nova) — usada nos alertas de status
            "meta_val": meta_val_proj, "desvio": desvio_proj,
            "projecao": projecao, "meta_vendas": meta_vendas,
            # Meta legacy sobre faturamento real
            "meta_val_fat": meta_val, "desvio_fat": desvio,
            "comp_sem": comp_sem, "em_trans_sem": em_trans_sem,
            "fat_sem": fat_sem, "cmc_sem": cmc_sem,
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
        SELECT ct.insumo_id, i.nome, ct.quantidade,
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

    df = df.merge(consumo_df, on="insumo_id", how="left")
    # Cobertura em dias: estoque_atual / consumo_diário
    df["cobertura_dias"] = df.apply(
        lambda r: round(r["quantidade"] / r["consumo_dia"])
        if (r.get("consumo_dia") or 0) > 0 else None,
        axis=1,
    )

    # Normalizar seção via lookup de compras
    db2 = conn()
    sec_df = pd.read_sql(
        "SELECT insumo_id, MAX(secao) as secao FROM compras "
        "WHERE unidade_id=? AND secao IS NOT NULL GROUP BY insumo_id",
        db2, params=[uid]
    )
    db2.close()
    df = df.merge(sec_df, on="insumo_id", how="left")
    df["secao_norm"] = df["secao"].fillna("Outros").apply(normalizar_secao)
    return df, data_ef


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
    Retorna lista de semanas definidas por pares de contagens.
    Cada semana = (data_inicio, data_fim, ei_data, ef_data)
      - data_inicio : primeiro dia do período (= ei_data)
      - data_fim    : último dia antes da próxima contagem (= ef_data - 1 dia)
      - ei_data     : data da contagem de abertura (EI)
      - ef_data     : data da contagem de fechamento (EF), None se semana ainda aberta

    A primeira semana do mês pode usar a última contagem do mês anterior como EI.
    """
    from datetime import timedelta as _td
    db = conn()
    ano, mes = int(periodo[:4]), int(periodo[5:7])

    # Última contagem do mês anterior (possível EI da S1)
    mes_ant = f"{ano}-{mes-1:02d}" if mes > 1 else f"{ano-1}-12"
    ei_ant = db.execute(
        "SELECT MAX(data) FROM contagens WHERE unidade_id=? AND strftime('%Y-%m',data)=?",
        (uid, mes_ant)
    ).fetchone()[0]

    # Todas as contagens do período atual
    datas_mes = [r[0] for r in db.execute(
        "SELECT DISTINCT data FROM contagens WHERE unidade_id=? AND strftime('%Y-%m',data)=? "
        "ORDER BY data",
        (uid, periodo)
    ).fetchall()]
    db.close()

    # Monta lista de pontos de contagem: [ei_anterior?] + datas do mês
    todos = []
    if ei_ant:
        todos.append(ei_ant)
    todos.extend(datas_mes)

    if len(todos) < 2:
        return []   # sem pares suficientes para definir semanas

    semanas = []
    for i in range(len(todos) - 1):
        ei = todos[i]
        ef = todos[i + 1]
        data_ini = ei
        data_fim = (date.fromisoformat(ef) - timedelta(days=1)).isoformat()
        semanas.append((data_ini, data_fim, ei, ef))

    # Semana aberta: depois da última contagem (EF=None)
    ultima = todos[-1]
    hoje = date.today().isoformat()
    if ultima <= hoje:
        semanas.append((ultima, hoje, ultima, None))

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
df_top       = load_top_produtos(uid, periodo)
df_compras   = load_compras_mes(uid, periodo)
em_transito_mes = load_em_transito_mes(uid, periodo)

# Estoque: usa a contagem de fechamento da semana selecionada (ef_data),
# ou a contagem mais recente quando nenhuma semana é selecionada
_ef_data_estoque = semana_filtro[3] if (semana_filtro and semana_filtro[3]) else None
df_estoque, data_estoque = load_estoque_atual(uid, _ef_data_estoque)

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

# API de faturamento em tempo real — para todas as unidades com loja mapeada
if LOJA_POR_SLUG.get(SLUG_SEL):
    with st.spinner("Buscando faturamento atualizado…"):
        api_data = fetch_fat_api(periodo, SLUG_SEL)
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

    tot_comp_s = df_quad["comp_sem"].sum()
    tot_fat_s  = df_quad["fat_sem"].sum()
    tot_cmc_s  = (tot_comp_s / tot_fat_s * 100) if tot_fat_s > 0 else 0.0

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

    def _badge_dev(desvio, base):
        """desvio = comp_mes − meta_proj; base = projeção (ou fat se sem proj)"""
        if desvio is None or base <= 0:
            return '<span style="color:#aaa;font-style:italic">—</span>'
        pct = abs(desvio) / base * 100
        if desvio <= 0:  fc = "#1b5e20"; sinal = "▼"
        elif pct <= 3:   fc = "#7d4e00"; sinal = "▲"
        else:            fc = "#7f0000"; sinal = "▲"
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
            sem_cells = f"""
          <td style="text-align:right;">R$ {r['comp_sem']:,.0f}</td>
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
          <td style="text-align:right;">{_badge_dev(desvio_r, base_r)}</td>
          <td style="text-align:center;font-size:16px;">{_status_icon_proj(r['comp_mes'], meta_v, base_r)}</td>
        </tr>"""

    # Linha de total
    tot_sem_cells = ""
    if semana_filtro:
        tot_cmc_s_badge = _badge_cmc(tot_cmc_s if tot_fat_s > 0 else None, 28.0)
        tot_sem_cells = f"""
          <td style="text-align:right;font-weight:700;">R$ {tot_comp_s:,.0f}</td>
          <td style="text-align:center;">{tot_cmc_s_badge}</td>"""

    tot_cmc_badge  = _badge_cmc(tot_cmc if tot_fat > 0 else None, 28.0)
    tot_dev_badge  = _badge_dev(tot_dev if base_grupo > 0 else None, base_grupo)
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

_unid_label = nome_sel
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

    # Meta de compra (CMC 28%) por semana
    df_fat_sem["meta_comp"] = df_fat_sem["fat"] * META_CMC / 100
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
        # Linha: meta de compra (CMC 28%)
        fig.add_trace(go.Scatter(
            name=f"Meta compra ({META_CMC:.0f}%)",
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
                "sem":          r["semana"],
                "periodo":      f"{datetime.strptime(r['data_inicio'],'%Y-%m-%d').strftime('%d/%m')} – {datetime.strptime(r['data_fim'],'%Y-%m-%d').strftime('%d/%m')}",
                "fat":          r["fat"],
                "comp":         r["comp"],
                "em_transito":  float(r.get("em_transito", 0)),
                "meta_comp":    r["meta_comp"],
                "cmc_pct":      r["cmc_pct"],
                "cor_cmc":      cor_cmc,
                "desvio":       desvio,
                "sinal":        sinal,
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

# ════════════════════════════════════════════════════════════════════════════
# BLOCO 3 — CMV por Categoria
# ════════════════════════════════════════════════════════════════════════════

secao("🔬 CMV por Categoria")

if cats.empty:
    if not has_cmv:
        st.info(f"CMV não calculado para {nome_sel} em {periodo} — são necessárias ao menos 2 contagens de estoque.")
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
        df_cats_show["EI"] = df_cats_show["estoque_inicial"].map("R$ {:,.0f}".format)
        df_cats_show["Compras"] = df_cats_show["compras"].map("R$ {:,.0f}".format)
        df_cats_show["EF"] = df_cats_show["estoque_final"].map("R$ {:,.0f}".format)
        df_cats_show["CMV"] = df_cats_show["cmv_valor"].map("R$ {:,.0f}".format)
        df_cats_show["CMV%"] = df_cats_show["cmv_percentual"].map("{:.1f}%".format)
        st.dataframe(
            df_cats_show[["categoria", "Compras", "CMV", "CMV%"]].rename(columns={"categoria": "Categoria"}),
            use_container_width=True, hide_index=True, height=340
        )

    # Resumo CMV total
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
        df_compras_display["secao_norm"] = df_compras_display["secao"].fillna("Outros").apply(normalizar_secao)
        comp_cat = (df_compras_display.groupby("secao_norm")["valor_total"]
                    .sum().reset_index()
                    .sort_values("valor_total", ascending=False))
        comp_cat["pct"] = comp_cat["valor_total"] / comp_cat["valor_total"].sum() * 100

        fig4 = px.pie(
            comp_cat.head(10),
            values="valor_total",
            names="secao_norm",
            hole=0.45,
            color_discrete_sequence=[
                COR_BOM, COR_ATENC, COR_CRIT, COR_TRI,
                "#6b8f5e", "#b5720a", "#7a0622", "#c8ba7a",
                "#4a7042", "#8a6500",
            ],
        )
        fig4.update_traces(textinfo="percent+label", textfont_size=10,
                           textfont_color=VI_BRANCO)
        fig4 = graf_layout(fig4, height=360)
        fig4.update_layout(
            title=dict(text=f"Distribuição de compras — R$ {comp_cat['valor_total'].sum():,.0f} total",
                       font=dict(size=12)),
            showlegend=False,
        )
        st.plotly_chart(fig4, use_container_width=True)

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

    # Tabela classe A
    df_a = df_e[df_e["classe"] == "A"].head(20).copy()
    df_a_show = df_a[["nome", "secao_norm", "quantidade", "custo_medio",
                       "valor_estoque", "pct", "cobertura_dias"]].copy()
    df_a_show.columns = ["Produto", "Categoria", "Qtd", "Custo Unit.",
                          "Valor (R$)", "% Estoque", "Capital Parado"]
    df_a_show["Custo Unit."]  = df_a_show["Custo Unit."].map("R$ {:.2f}".format)
    df_a_show["Valor (R$)"]   = df_a_show["Valor (R$)"].map("R$ {:,.0f}".format)
    df_a_show["% Estoque"]    = df_a_show["% Estoque"].map("{:.1f}%".format)
    df_a_show["Qtd"]          = df_a_show["Qtd"].map("{:.1f}".format)

    def _fmt_cobertura(dias):
        if dias is None or (isinstance(dias, float) and dias != dias):
            return "—"
        d = int(dias)
        if d <= 7:   return f"⚠ {d}d"    # estoque baixo
        if d <= 21:  return f"✔ {d}d"    # normal
        return f"🔴 {d}d"                # capital parado excessivo

    df_a_show["Capital Parado"] = df_a_show["Capital Parado"].apply(_fmt_cobertura)

    with st.expander(f"📋 Produtos Classe A — top {len(df_a)} itens de maior valor em estoque", expanded=True):
        st.dataframe(df_a_show, use_container_width=True, hide_index=True)
        st.markdown(
            f'<div style="font-size:11px;color:{VI_SECAO};margin-top:4px;">'
            f'Capital Parado = dias de estoque cobrindo o consumo médio dos últimos 30 dias &nbsp;|&nbsp; '
            f'⚠ &lt; 7 dias &nbsp; ✔ 7–21 dias &nbsp; 🔴 &gt; 21 dias (excesso de capital imobilizado)'
            f'</div>', unsafe_allow_html=True
        )

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

# ── Rodapé ────────────────────────────────────────────────────────────────────

st.markdown(f"""
<div style="text-align:center;color:{AZUL_BORDA};font-size:.72rem;margin-top:32px;padding:10px;">
  Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')} · Banco: {DB_FILE} · {nome_sel}
</div>""", unsafe_allow_html=True)
