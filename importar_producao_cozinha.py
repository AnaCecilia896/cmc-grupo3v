"""
importar_producao_cozinha.py — Produção da Cozinha (Italianos + Mané) → banco_central.db

Lê as respostas dos 3 formulários (Google Forms → Sheets) de produção/
porcionamento da cozinha e grava tudo normalizado numa única tabela
`producao_cozinha`, para a aba "🍳 Produção" do Dash consumir.

Roda LOCALMENTE (precisa de credentials.json + internet), igual ao
atualizar_dados.py — este ambiente (Streamlit Cloud / sessão remota) não
tem acesso à internet pra falar com o Google Sheets.

Uso:
    python importar_producao_cozinha.py             # importa e grava no banco
    python importar_producao_cozinha.py --dry-run   # só mostra o que seria
                                                      # gravado, não grava nada
    python importar_producao_cozinha.py --check-headers
        # só confere se o cabeçalho de cada aba ainda bate com o esperado
        # (útil pra rodar depois que alguém mexe na planilha)

ATENÇÃO — aba "CONTROLE DE PORCIONAMENTO" (Mané):
    Essa aba tem 9 colunas repetidas com o MESMO texto de pergunta
    ("Qual foi a perda em KG, após a limpeza da matéria prima?", 4x
    "APARAS", etc.) — o Google Forms permite isso, mas impede usar o nome
    da coluna pra identificar qual pergunta é qual: dá empate. Por isso,
    diferente das outras duas abas (que usam o NOME da coluna), esta aqui é
    lida por POSIÇÃO (índice 0-based na linha), reconstruindo por
    proximidade qual "perda"/"apara" pertence a qual bloco de produto.
    Essa reconstrução foi feita a partir do cabeçalho colado pelo usuário,
    sem acesso direto à planilha real — rode com --check-headers e depois
    --dry-run antes do primeiro import de verdade, e confira a amostra
    impressa contra a planilha.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import unicodedata
from datetime import datetime

from config import CREDENTIALS_FILE, DB_FILE, PRODUCAO_COZINHA_PLANILHAS

try:
    import gspread
    from google.oauth2.service_account import Credentials
except ImportError:
    print("Faltam dependências: pip install gspread google-auth")
    sys.exit(1)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


# ── Helpers genéricos ───────────────────────────────────────────────────────

def _client() -> "gspread.Client":
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    return gspread.authorize(creds)


def _norm_header(h: str) -> str:
    """Colapsa espaços duplicados/trailing — o cabeçalho real tem inconsistências
    de espaçamento (ex.: 'desse PROCESSADO  foram') que não devem quebrar o match."""
    return " ".join(str(h or "").split())


def _norm_texto(s: str) -> str:
    """Remove acento e caixa — pra casar 'Asa Norte' com 'ASA NORTE', 'asa norte' etc."""
    return unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode().upper().strip()


def _num(v) -> float | None:
    """Converte texto de resposta de formulário em float.
    Aceita tanto '1234,56' (vírgula decimal, formato pedido no form) quanto
    '1234.56' — e ignora milhar quando há vírgula decimal."""
    if v is None:
        return None
    s = str(v).strip().replace("R$", "").replace(" ", "")
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _data(v, carimbo: str | None = None) -> str | None:
    """Extrai data (YYYY-MM-DD) da resposta de data do form; se vazia, cai pro
    carimbo de data/hora (formato Google Forms: DD/MM/AAAA HH:MM:SS)."""
    for raw, fmts in ((v, ("%d/%m/%Y", "%d/%m/%y")),
                      (carimbo, ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"))):
        if not raw:
            continue
        s = str(raw).strip()
        for fmt in fmts:
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


class HeaderMap:
    """Lookup de coluna por NOME (com normalização de espaço). Usar quando a
    aba não tem perguntas duplicadas — é o modo robusto a reordenação."""

    def __init__(self, headers: list[str]):
        self.headers = headers
        self._idx = {}
        for i, h in enumerate(headers):
            self._idx.setdefault(_norm_header(h), i)

    def has(self, nome: str) -> bool:
        return _norm_header(nome) in self._idx

    def get(self, row: list[str], nome: str) -> str | None:
        i = self._idx.get(_norm_header(nome))
        if i is None or i >= len(row):
            return None
        v = row[i]
        return v if v not in (None, "") else None

    def missing(self, nomes: list[str]) -> list[str]:
        return [n for n in nomes if not self.has(n)]


def _get_uid_map(db: sqlite3.Connection) -> dict[str, int]:
    return dict(db.execute("SELECT slug, id FROM unidades").fetchall())


def _resolve_unidade(nome_resposta: str, slugs_permitidos: list[str], uid_map: dict[str, int],
                      nomes_slug: dict[str, str]) -> tuple[str | None, int | None]:
    """Casa a resposta livre de 'Qual unidade?' com um slug em slugs_permitidos."""
    alvo = _norm_texto(nome_resposta)
    for slug in slugs_permitidos:
        if _norm_texto(nomes_slug.get(slug, slug)) == alvo or _norm_texto(slug) in alvo:
            return slug, uid_map.get(slug)
    return None, None


# ── Schema ───────────────────────────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS producao_cozinha (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    unidade_id          INTEGER NOT NULL,
    casa                TEXT,        -- subunidade real (ex.: casas do Mané); NULL = mesma coisa que a unidade
    data                TEXT,        -- YYYY-MM-DD
    carimbo             TEXT,        -- carimbo de data/hora original da resposta (auditoria/dedupe)
    responsavel         TEXT,
    fonte               TEXT NOT NULL,   -- 'italianos' | 'mane_cozimento' | 'mane_porcionamento'
    materia_prima       TEXT,
    produto_final       TEXT,
    peso_bruto_kg       REAL,
    peso_liquido_kg     REAL,
    porcoes_produzidas  REAL,
    kg_produzido        REAL,
    perda_kg            REAL,
    apara_kg            REAL,
    observacao          TEXT,
    linha_origem        INTEGER,     -- nº da linha na planilha (debug)
    importado_em        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(fonte, carimbo, produto_final, linha_origem)
);
CREATE INDEX IF NOT EXISTS idx_producao_cozinha_unidade_data
    ON producao_cozinha(unidade_id, data);
"""


def _upsert(db: sqlite3.Connection, rows: list[dict]) -> int:
    cols = ["unidade_id", "casa", "data", "carimbo", "responsavel", "fonte",
            "materia_prima", "produto_final", "peso_bruto_kg", "peso_liquido_kg",
            "porcoes_produzidas", "kg_produzido", "perda_kg", "apara_kg",
            "observacao", "linha_origem"]
    sql = (
        f"INSERT INTO producao_cozinha ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' for _ in cols)}) "
        f"ON CONFLICT(fonte, carimbo, produto_final, linha_origem) DO UPDATE SET "
        + ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("unidade_id",))
    )
    db.executemany(sql, [[r.get(c) for c in cols] for r in rows])
    return len(rows)


# ── Fonte 1: Italianos (aba "BASE DE DADOS") ────────────────────────────────
# Grão: 1 linha do formulário → no máx. 1 linha em producao_cozinha (o form é
# condicional — só um bloco de produto fica preenchido por resposta).
#
# Peso bruto/líquido: existem 3 pares possíveis (genérico / Filé / Frango) —
# usa o primeiro que estiver preenchido.
# Produto final + quantidade: ~15 pares (unidades, kg) possíveis, um por
# produto específico — usa o primeiro que tiver unidades OU kg preenchido.
# "Perda" = TOTAL de APARAS (descarte). "Aproveitável" = TOTAL de LASCAS —
# guardado à parte, não soma como perda. (Ver observação enviada ao usuário:
# a planilha não tem uma coluna literal "perda", por isso esse mapeamento.)

_IT_COL = {
    "carimbo": "Carimbo de data/hora",
    "data": "Qual o dia que foi feita essa produção?",
    "unidade": "Qual unidade?",
    "responsavel": "Quem está realizando essa tarefa?",
    "categoria": "Qual Produção foi realizada?",
    "bruto_gen": "Qual o Peso Bruto da matéria prima em KG? valor com embalagem e líquidos (antes de porcionar)",
    "liquido_gen": "Qual o Peso Líquido da matéria prima após ser tratada em KG? valor sem embalagem e líquidos e com ela porcionada",
    "processado_gen": "Qual Processado foi produzido?",
    "und_gen": "Quantas unidades (UND) desse PROCESSADO foram produzidas? (Para Bacon, Queijos e outros no KG, lançar como 1)",
    "kg_gen": "Quantos quilos (KG) desse PROCESSADO já porcionado deu no total?",
    "apara_total": "Qual foi o TOTAL de APARAS (DESCARTES) gerada em quilos (KG) no final?",
    "tipo_file": "Qual o tipo de Filé?",
    "bruto_file": "Qual o Peso Bruto do Filé em KG? valor com embalagem e líquidos",
    "liquido_file": "Qual o Peso Líquido do Filé em KG? valor sem embalagem e líquidos",
    "liquido_file_ballotine": "Qual o Peso Líquido do Filé Ballotine em KG? valor sem embalagem e líquidos",
    "und_tartuffi": "Quantas unidades (UND) de TARTUFFI [PORCIONADO 180G] foram produzidas?",
    "kg_tartuffi": "Quantos quilos (KG) de TARTUFFI [PORCIONADO 180G] foram produzidos?",
    "und_parmegiana_carne": "Quantas unidades (UND) de PARMEGIANA DE CARNE [130G] foram produzidas?",
    "kg_parmegiana_carne": "Quantos quilos (KG) de PARMEGIANA DE CARNE [130G] foram produzidos?",
    "und_wellington": "Quantas unidades (UND) de WELLINGTON [INDIVIDUAL 150G] foram produzidas?",
    "kg_wellington": "Quantos quilos (KG) de WELLINGTON [INDIVIDUAL 150G] foram produzidos?",
    "und_file_ballotine": "Quantas unidades (UND) de FILE MIGNON LIMPO E PORCIONADO [BALLOTINE] foram produzidas?",
    "kg_file_ballotine": "Quantos quilos (KG) de FILE MIGNON LIMPO E PORCIONADO [BALLOTINE] foram produzidos?",
    "kg_file_tirinhas": "Quantos quilos (KG) de FILE MIGON TIRINHAS [PROCESSADO] foram produzidos?",
    "apara_file": "Quantos quilos (KG) de APARA foram produzidos?",
    "marca_file": "Qual a marca do Filé?",
    "file_limpo": "O Filé já está limpo? [BALLOTINE]",
    "bruto_frango": "Qual o Peso Bruto do Frango em KG? valor com embalagem e líquidos (antes de porcionar)",
    "liquido_frango": "Qual o Peso Líquido do Frango após ser tratada em KG? valor sem embalagem e líquidos",
    "und_peito_parmegiana": "Quantas unidades (UND) de FILE DE PEITO para PARMEGIANA [PORCIONADO 130G] foram produzidas?",
    "kg_peito_parmegiana": "Quantos quilos (KG) de FILE DE PEITO para PARMEGIANA [PORCIONADO 130G] foram produzidos?",
    "und_peito_180": "Quantas unidades (UND) de FILE DE PEITO [PORCIONADO 180G] foram produzidas?",
    "kg_peito_180": "Quantos quilos (KG) de FILE DE PEITO [PORCIONADO 180G] foram produzidos?",
    "kg_tirinhas_bambine": "Quantos quilos (KG) de TIRINHAS DE FRANGO para BAMBINE foram produzidos?",
    "und_saltimboca": "Quantas unidades (UND) de FILE DE PEITO para SALTIMBOCA [PORCIONADO 180G] foram produzidas?",
    "und_nuggets": "Quantas unidades (UND) de NUGGETS [PORCIONADO 110G] foram produzidas?",
    "kg_saltimboca": "Quantos quilos (KG) de SALTIMBOCA [PORCIONADO 180G] foram produzidos?",
    "kg_nuggets": "Quantos quilos (KG) de TIRINHAS DE FRANGO para NUGGETS [PORCIONADO 110G] foram produzidas?",
    "kg_cordon_bleu": "Quantos quilos (KG) de FRANGO CORDON BLEU [PORCIONADO 180G] foram produzidos?",
    "und_cordon_bleu": "Quantas unidades (UND) de FRANGO CORDON BLEU [PORCIONADO 180G] foram produzidos?",
    "lasca_total": "Qual foi o TOTAL de LASCAS (APROVEITAVEIS) gerada em quilos (KG) no final?",
}

# (produto_final, coluna_und, coluna_kg) — usado em ordem; primeiro par com
# und OU kg preenchido "ganha" a linha.
_IT_PRODUTOS = [
    ("Tartuffi 180g",                    "und_tartuffi", "kg_tartuffi"),
    ("Parmegiana de Carne 130g",         "und_parmegiana_carne", "kg_parmegiana_carne"),
    ("Wellington Individual 150g",       "und_wellington", "kg_wellington"),
    ("Filé Mignon Ballotine",            "und_file_ballotine", "kg_file_ballotine"),
    ("Filé Mignon Tirinhas (processado)", None, "kg_file_tirinhas"),
    ("Filé de Peito p/ Parmegiana 130g", "und_peito_parmegiana", "kg_peito_parmegiana"),
    ("Filé de Peito 180g",               "und_peito_180", "kg_peito_180"),
    ("Tirinhas de Frango p/ Bambine",    None, "kg_tirinhas_bambine"),
    ("Filé de Peito p/ Saltimboca 180g", "und_saltimboca", "kg_saltimboca"),
    ("Nuggets 110g",                     "und_nuggets", "kg_nuggets"),
    ("Frango Cordon Bleu 180g",          "und_cordon_bleu", "kg_cordon_bleu"),
    ("(processado genérico)",            "und_gen", "kg_gen"),  # usa nome de "processado_gen" se vazio, ver abaixo
]


def importar_italianos(db, dry_run=False):
    cfg = PRODUCAO_COZINHA_PLANILHAS["italianos"]
    if not cfg.get("sheet_id"):
        print("[italianos] sem sheet_id configurado — pulando")
        return 0
    ws = _client().open_by_key(cfg["sheet_id"]).worksheet(cfg["aba"])
    values = ws.get_all_values()
    if not values:
        print("[italianos] planilha vazia")
        return 0
    headers, linhas = values[0], values[1:]
    hm = HeaderMap(headers)

    faltando = hm.missing(list(_IT_COL.values()))
    if faltando:
        print(f"[italianos] AVISO — {len(faltando)} coluna(s) esperada(s) não encontrada(s) no cabeçalho atual:")
        for f in faltando:
            print(f"   - {f}")

    uid_map = _get_uid_map(db)
    nomes_slug = dict(db.execute("SELECT slug, nome FROM unidades").fetchall())
    slugs = cfg["slugs"]

    out = []
    for n, row in enumerate(linhas, start=2):  # linha 1 = cabeçalho
        g = lambda k: hm.get(row, _IT_COL[k])
        carimbo = g("carimbo")
        if not carimbo and not any(row):
            continue  # linha em branco no fim da planilha

        slug, uid = _resolve_unidade(g("unidade") or "", slugs, uid_map, nomes_slug)
        if uid is None:
            print(f"[italianos] linha {n}: unidade '{g('unidade')}' não reconhecida — pulando")
            continue

        # peso bruto/líquido: genérico → Filé → Frango, o que estiver preenchido
        bruto = _num(g("bruto_gen")) or _num(g("bruto_file")) or _num(g("bruto_frango"))
        liquido = (_num(g("liquido_gen")) or _num(g("liquido_file"))
                   or _num(g("liquido_file_ballotine")) or _num(g("liquido_frango")))

        produto_final = kg = und = None
        for nome, col_und, col_kg in _IT_PRODUTOS:
            v_und = _num(g(col_und)) if col_und else None
            v_kg = _num(g(col_kg)) if col_kg else None
            if v_und is not None or v_kg is not None:
                produto_final = g("processado_gen") if nome == "(processado genérico)" and g("processado_gen") else nome
                und, kg = v_und, v_kg
                break

        perda = _num(g("apara_total")) or _num(g("apara_file"))
        lasca = _num(g("lasca_total"))

        obs_partes = [p for p in [
            f"tipo de filé: {g('tipo_file')}" if g("tipo_file") else None,
            f"marca do filé: {g('marca_file')}" if g("marca_file") else None,
            f"filé limpo: {g('file_limpo')}" if g("file_limpo") else None,
            f"aproveitável (lascas): {lasca:g}kg" if lasca is not None else None,
        ] if p]

        out.append(dict(
            unidade_id=uid, casa=None,
            data=_data(g("data"), carimbo), carimbo=carimbo,
            responsavel=g("responsavel"), fonte="italianos",
            materia_prima=g("categoria"), produto_final=produto_final,
            peso_bruto_kg=bruto, peso_liquido_kg=liquido,
            porcoes_produzidas=und, kg_produzido=kg,
            perda_kg=perda, apara_kg=lasca,
            observacao="; ".join(obs_partes) or None,
            linha_origem=n,
        ))

    print(f"[italianos] {len(out)} linha(s) processada(s)")
    if dry_run:
        for r in out[:5]:
            print("   ", r)
        return 0
    return _upsert(db, out) if out else 0


# ── Fonte 2: Mané / CONTROLE DE COZIMENTO ───────────────────────────────────
# Grão: 1 linha do form → até 1 linha (bloco único ativo, mesma lógica condicional).

_MC_COL = {
    "carimbo": "Carimbo de data/hora",
    "responsavel": "Quem está PREENCHENDO este formulário?",
    "unidade": "Qual unidade?",
    "data": "Qual a data em que foi feito esse porcionamento?",
    "materia_prima": "Qual matéria prima foi utilizada?",
    "costela_crua": "Quantos KG de COSTELA [CRUA] foram utilizados?",
    "costela_assada": "Quantos KG de COSTELA [ASSADA] foram produzidos?",
    "peito_cru_brisket": "Quantos KG de PEITO BOVINO [CRU] foram utilizados?",
    "brisket": "Quantos KG de BRISKET foram produzidos?(pesar após retirado do forno)",
    "peito_cru_pastrami": "Quantos KG de PEITO BOVINO [CRU] foram colocados na cura?",
    "pastrami": "Quantos KG de PASTRAMI foram produzidos?(pesar após retirado do forno)",
    "maminha_crua": "Quantos KG de MAMINHA [CRUA] foram defumados?",
    "maminha_defumada": "Quantos KG de MAMINHA DEFUMADA foram produzidos?",
}

_MC_PARES = [
    ("Costela Assada",   "costela_crua", "costela_assada"),
    ("Brisket",          "peito_cru_brisket", "brisket"),
    ("Pastrami",         "peito_cru_pastrami", "pastrami"),
    ("Maminha Defumada", "maminha_crua", "maminha_defumada"),
]


def importar_mane_cozimento(db, dry_run=False):
    cfg = PRODUCAO_COZINHA_PLANILHAS["mane"]
    ws = _client().open_by_key(cfg["sheet_id"]).worksheet(cfg["abas"]["cozimento"])
    values = ws.get_all_values()
    if not values:
        print("[mane_cozimento] planilha vazia")
        return 0
    headers, linhas = values[0], values[1:]
    hm = HeaderMap(headers)

    faltando = hm.missing(list(_MC_COL.values()))
    if faltando:
        print(f"[mane_cozimento] AVISO — coluna(s) esperada(s) não encontrada(s):")
        for f in faltando:
            print(f"   - {f}")

    uid_map = _get_uid_map(db)
    uid_mane = uid_map.get("mane")

    out = []
    for n, row in enumerate(linhas, start=2):
        g = lambda k: hm.get(row, _MC_COL[k])
        carimbo = g("carimbo")
        if not carimbo and not any(row):
            continue

        for produto, col_bruto, col_prod in _MC_PARES:
            bruto = _num(g(col_bruto))
            produzido = _num(g(col_prod))
            if bruto is None and produzido is None:
                continue
            out.append(dict(
                unidade_id=uid_mane, casa=g("unidade"),
                data=_data(g("data"), carimbo), carimbo=carimbo,
                responsavel=g("responsavel"), fonte="mane_cozimento",
                materia_prima=g("materia_prima"), produto_final=produto,
                peso_bruto_kg=bruto, peso_liquido_kg=produzido,
                porcoes_produzidas=None, kg_produzido=produzido,
                perda_kg=None, apara_kg=None, observacao=None,
                linha_origem=n,
            ))

    print(f"[mane_cozimento] {len(out)} linha(s) processada(s)")
    if dry_run:
        for r in out[:5]:
            print("   ", r)
        return 0
    return _upsert(db, out) if out else 0


# ── Fonte 3: Mané / CONTROLE DE PORCIONAMENTO ───────────────────────────────
# RASCUNHO A VALIDAR — ver aviso no topo do arquivo. Lido por POSIÇÃO (índice
# 0-based) porque a aba tem 9 colunas com o texto "Qual foi a perda em KG..."
# repetido e 4 com "...APARAS EM KG..." repetido, o que impede lookup por nome.
#
# Índices abaixo reconstruídos a partir do cabeçalho colado pelo usuário em
# 2026-09 — ver docstring do topo do arquivo para o texto completo de cada
# coluna. Rode --check-headers para conferir se ainda bate com a planilha.

_MP_IDX = dict(
    carimbo=0, responsavel=1, data=2, casa=3, materia_prima=4, peso_inicial=5,
    chorizo250_und=6, chorizo250_kg=7,
    chorizo200_und=8, chorizo200_kg=9,
    coracao_und=10, coracao_kg=11,
    pastrami_und=12, pastrami_kg=13,
    perda_1=14, apara_1=15, perda_2=16, apara_2=17,          # bloco Chorizo/Coração/Pastrami
    maminha_und=18, maminha_kg=19, apara_maminha=20, perda_maminha=21,
    brisket_und=22, brisket_kg=23, brisket_desfiado_kg=24, perda_brisket=25,
    costela_und=26, costela_kg=27,
    costela_ripa_und=28, costela_ripa_kg=29,
    costela_empanada_und=30, lasca_costela_kg=31, perda_costela=32,
    babybeef200_und=33, babybeef200_kg=34,
    babybeefkids_und=35, babybeefkids_kg=36,
    perda_babybeef=37, apara_babybeef=38,
    vc_materia_prima=39, vc_peso_inicial=40,
    carnesol_parrilla_kg=41,
    carnesol_cubos_und=42, carnesol_cubos_kg=43,
    carnesol_nata_kg=44,
    perda_carnesol_1=45, peso_final_carnesol=46, perda_carnesol_2=47,
    babybeef150_und=48, babybeef150_kg=49,
    picanha200_und=50, picanha200_kg=51,
    perda_picanha200=52, apara_picanha200=53,
    responsavel_2=54,
    picanha400_und=55, picanha400_kg=56,
    apara_final=57,
    # índice 58 ("Peso Líquido do Frango...") ignorado — não se encaixa no
    # contexto desta aba (proteínas bovinas); provável resíduo de copiar o
    # form dos Italianos. Confirmar com quem preenche a planilha.
)

_MP_PARES = [
    # (produto_final, col_und, col_kg, col_perda, col_apara_ou_aproveitavel, obs)
    ("Chorizo 250g",            "chorizo250_und", "chorizo250_kg", "perda_1", "apara_1", None),
    ("Chorizo 200g",            "chorizo200_und", "chorizo200_kg", "perda_2", "apara_2", None),
    ("Coração Defumado 250g",   "coracao_und", "coracao_kg", "perda_1", "apara_1", None),
    ("Pastrami 150g",           "pastrami_und", "pastrami_kg", "perda_2", "apara_2", None),
    ("Maminha Defumada 150g",   "maminha_und", "maminha_kg", "perda_maminha", "apara_maminha", None),
    ("Brisket 150g",            "brisket_und", "brisket_kg", "perda_brisket", None, "brisket_desfiado_kg"),
    ("Costela 200g",            "costela_und", "costela_kg", "perda_costela", None, "lasca_costela_kg"),
    ("Costela Ripa 1kg",        "costela_ripa_und", "costela_ripa_kg", "perda_costela", None, "lasca_costela_kg"),
    ("Costela Empanada 200g",   "costela_empanada_und", None, "perda_costela", None, "lasca_costela_kg"),
    ("Baby Beef 200g",          "babybeef200_und", "babybeef200_kg", "perda_babybeef", "apara_babybeef", None),
    ("Baby Beef Kids 100g",     "babybeefkids_und", "babybeefkids_kg", "perda_babybeef", "apara_babybeef", None),
    ("Baby Beef 150g",          "babybeef150_und", "babybeef150_kg", "perda_picanha200", "apara_picanha200", None),
    ("Picanha 200g",            "picanha200_und", "picanha200_kg", "perda_picanha200", "apara_picanha200", None),
    ("Picanha 400g",            "picanha400_und", "picanha400_kg", None, "apara_final", None),
]
# Vei Chico (matéria-prima própria, colunas 39-47) tratado à parte por não
# seguir o padrão porções+kg simples.


def _mp_get(row: list[str], idx_key: str) -> str | None:
    i = _MP_IDX[idx_key]
    if i >= len(row):
        return None
    v = row[i]
    return v if v not in (None, "") else None


def importar_mane_porcionamento(db, dry_run=False):
    cfg = PRODUCAO_COZINHA_PLANILHAS["mane"]
    ws = _client().open_by_key(cfg["sheet_id"]).worksheet(cfg["abas"]["porcionamento"])
    values = ws.get_all_values()
    if not values:
        print("[mane_porcionamento] planilha vazia")
        return 0
    headers, linhas = values[0], values[1:]

    n_cols_esperado = max(_MP_IDX.values()) + 1
    if len(headers) < n_cols_esperado:
        print(f"[mane_porcionamento] AVISO — planilha tem {len(headers)} colunas, "
              f"esperava pelo menos {n_cols_esperado}. Mapeamento por posição pode estar errado — rode --check-headers.")

    uid_map = _get_uid_map(db)
    uid_mane = uid_map.get("mane")

    out = []
    for n, row in enumerate(linhas, start=2):
        carimbo = _mp_get(row, "carimbo")
        if not carimbo and not any(row):
            continue
        casa = _mp_get(row, "casa")
        responsavel = _mp_get(row, "responsavel") or _mp_get(row, "responsavel_2")
        data = _data(_mp_get(row, "data"), carimbo)

        emitiu_algo = False
        for produto, col_und, col_kg, col_perda, col_apara, col_extra in _MP_PARES:
            v_und = _num(_mp_get(row, col_und)) if col_und else None
            v_kg = _num(_mp_get(row, col_kg)) if col_kg else None
            if v_und is None and v_kg is None:
                continue
            emitiu_algo = True
            extra = _num(_mp_get(row, col_extra)) if col_extra else None
            obs = f"kg adicional (desfiado/lasca): {extra:g}kg" if extra is not None else None
            out.append(dict(
                unidade_id=uid_mane, casa=casa, data=data, carimbo=carimbo,
                responsavel=responsavel, fonte="mane_porcionamento",
                materia_prima=_mp_get(row, "materia_prima"), produto_final=produto,
                peso_bruto_kg=_num(_mp_get(row, "peso_inicial")), peso_liquido_kg=None,
                porcoes_produzidas=v_und, kg_produzido=v_kg,
                perda_kg=_num(_mp_get(row, col_perda)) if col_perda else None,
                apara_kg=_num(_mp_get(row, col_apara)) if col_apara else None,
                observacao=obs, linha_origem=n,
            ))

        # Bloco Vei Chico — matéria-prima e formato próprios (carne de sol)
        vc_mp = _mp_get(row, "vc_materia_prima")
        vc_kg_parrilla = _num(_mp_get(row, "carnesol_parrilla_kg"))
        vc_und_cubos = _num(_mp_get(row, "carnesol_cubos_und"))
        vc_kg_cubos = _num(_mp_get(row, "carnesol_cubos_kg"))
        vc_kg_nata = _num(_mp_get(row, "carnesol_nata_kg"))
        if vc_mp or vc_kg_parrilla is not None or vc_kg_cubos is not None or vc_kg_nata is not None:
            emitiu_algo = True
            bruto_vc = _num(_mp_get(row, "vc_peso_inicial"))
            liquido_vc = _num(_mp_get(row, "peso_final_carnesol"))
            perda_vc = _num(_mp_get(row, "perda_carnesol_1")) or _num(_mp_get(row, "perda_carnesol_2"))
            if vc_kg_parrilla is not None:
                out.append(dict(unidade_id=uid_mane, casa=casa or "Véi Chico Mané", data=data, carimbo=carimbo,
                                 responsavel=responsavel, fonte="mane_porcionamento",
                                 materia_prima=vc_mp, produto_final="Carne de Sol p/ Parrilla",
                                 peso_bruto_kg=bruto_vc, peso_liquido_kg=liquido_vc,
                                 porcoes_produzidas=None, kg_produzido=vc_kg_parrilla,
                                 perda_kg=perda_vc, apara_kg=None, observacao=None, linha_origem=n))
            if vc_und_cubos is not None or vc_kg_cubos is not None:
                out.append(dict(unidade_id=uid_mane, casa=casa or "Véi Chico Mané", data=data, carimbo=carimbo,
                                 responsavel=responsavel, fonte="mane_porcionamento",
                                 materia_prima=vc_mp, produto_final="Carne de Sol em Cubos",
                                 peso_bruto_kg=bruto_vc, peso_liquido_kg=liquido_vc,
                                 porcoes_produzidas=vc_und_cubos, kg_produzido=vc_kg_cubos,
                                 perda_kg=perda_vc, apara_kg=None, observacao=None, linha_origem=n))
            if vc_kg_nata is not None:
                out.append(dict(unidade_id=uid_mane, casa=casa or "Véi Chico Mané", data=data, carimbo=carimbo,
                                 responsavel=responsavel, fonte="mane_porcionamento",
                                 materia_prima=vc_mp, produto_final="Carne de Sol na Nata / Paçoca",
                                 peso_bruto_kg=bruto_vc, peso_liquido_kg=liquido_vc,
                                 porcoes_produzidas=None, kg_produzido=vc_kg_nata,
                                 perda_kg=perda_vc, apara_kg=None, observacao=None, linha_origem=n))

        if not emitiu_algo:
            continue  # linha sem nenhum bloco reconhecido preenchido

    print(f"[mane_porcionamento] {len(out)} linha(s) processada(s)  (RASCUNHO A VALIDAR — ver docstring do arquivo)")
    if dry_run:
        for r in out[:8]:
            print("   ", r)
        return 0
    return _upsert(db, out) if out else 0


def _check_headers():
    """Imprime, lado a lado, o índice/posição e o texto real de cada coluna
    das 3 abas — usar pra validar o mapeamento por posição do Porcionamento."""
    cfg_it = PRODUCAO_COZINHA_PLANILHAS["italianos"]
    cfg_mane = PRODUCAO_COZINHA_PLANILHAS["mane"]
    cli = _client()

    if cfg_it.get("sheet_id"):
        headers = cli.open_by_key(cfg_it["sheet_id"]).worksheet(cfg_it["aba"]).row_values(1)
        print(f"\n=== italianos / {cfg_it['aba']} — {len(headers)} colunas ===")
        for i, h in enumerate(headers):
            print(f"  [{i}] {h}")

    for aba_key, aba_nome in cfg_mane["abas"].items():
        headers = cli.open_by_key(cfg_mane["sheet_id"]).worksheet(aba_nome).row_values(1)
        print(f"\n=== mane / {aba_nome} — {len(headers)} colunas ===")
        for i, h in enumerate(headers):
            print(f"  [{i}] {h}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check-headers", action="store_true")
    args = ap.parse_args()

    if args.check_headers:
        _check_headers()
        return

    db = sqlite3.connect(DB_FILE)
    db.executescript(DDL)

    total = 0
    total += importar_italianos(db, dry_run=args.dry_run)
    if PRODUCAO_COZINHA_PLANILHAS["mane"].get("sheet_id"):
        total += importar_mane_cozimento(db, dry_run=args.dry_run)
        total += importar_mane_porcionamento(db, dry_run=args.dry_run)
    if not PRODUCAO_COZINHA_PLANILHAS["spq-norte"].get("sheet_id"):
        print("[spq-norte] sem sheet_id configurado ainda — pulando")

    if args.dry_run:
        db.rollback()
        print("\n[dry-run] nada foi gravado no banco.")
    else:
        db.commit()
        print(f"\n{total} linha(s) gravada(s)/atualizada(s) em producao_cozinha.")
    db.close()


if __name__ == "__main__":
    main()
