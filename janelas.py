"""
Janelas de datas e somas de faturamento/compras — fonte única do relatório.

Todas as abas devem ler faturamento e compras do mês por aqui, para que o
mesmo número apareça igual em Grupo, Resumo, Compras e Evolução.

Regra: o CMV só existe até a última contagem (EF), mas faturamento, compras e
CMC são "do mês até hoje". Em mês aberto (EF = contagem semanal) a janela viva
vai até o dia atual; em mês fechado (EF = inventário mensal) ela termina no EF.
Janela = [ini, fim) — início inclusivo, fim exclusivo (data da contagem é o
começo do dia, não entra).
"""
from datetime import date, timedelta

# Operacionais (Limpeza 301 / Alim. Funcionários 400) não entram em CMV/CMC.
SQL_EXCL_OP = (
    "AND NOT ("
    "  (sku_codigo IS NOT NULL AND sku_codigo != '' "
    "     AND (sku_codigo LIKE '__.400-%' OR sku_codigo LIKE '__.301-%')) "
    "  OR ((sku_codigo IS NULL OR sku_codigo = '') "
    "     AND (UPPER(COALESCE(secao,'')) LIKE '%LIMPEZA%' "
    "          OR UPPER(COALESCE(secao,'')) LIKE '%FUNCION%')) "
    ")"
)

SQL_CONFERIDO = "(status_pedido='conferido' OR status_pedido IS NULL)"


def amanha() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


def mes_fechado(db, uid, ef) -> bool:
    """True se o EF é um inventário mensal (mês encerrado)."""
    if not ef:
        return False
    return db.execute(
        "SELECT 1 FROM contagens WHERE unidade_id=? AND data=? "
        "AND tipo='inventario_mensal' LIMIT 1", (uid, ef)).fetchone() is not None


def fim_vivo(db, uid, ef) -> str:
    """Limite superior (exclusivo) da janela viva do mês."""
    if not ef or mes_fechado(db, uid, ef):
        return ef
    return max(ef, amanha())


def _fat_diario(db, uid, ini, fim):
    """Soma exata do faturamento diário da janela [ini, fim), ou None se a tabela
    não existe ou faltam dias (exige cobertura até ontem)."""
    try:
        lim = min(fim, date.today().isoformat())
        esperado = (date.fromisoformat(lim) - date.fromisoformat(ini)).days
        if esperado <= 0:
            return None
        n = db.execute("SELECT COUNT(*) FROM faturamento_diario WHERE unidade_id=? AND data>=? AND data<?",
                       (uid, ini, lim)).fetchone()[0]
        if n < esperado:
            return None
        r = db.execute("SELECT COALESCE(SUM(valor),0) FROM faturamento_diario WHERE unidade_id=? AND data>=? AND data<?",
                       (uid, ini, fim)).fetchone()
        return float(r[0] or 0)
    except Exception:
        return None


def fat_janela(db, uid, ini, fim) -> float:
    """Faturamento da janela [ini, fim). Preferência: soma diária exata
    (faturamento_diario). Reserva: linhas semanais 'Faturamento (API)' cuja
    data_inicio cai na janela (uma por semana); sem API, linhas de produto."""
    diario = _fat_diario(db, uid, ini, fim)
    if diario is not None:
        return diario
    api = db.execute(
        "SELECT COUNT(*) FROM vendas_produtos WHERE unidade_id=? AND data_inicio>=? "
        "AND data_inicio<? AND produto='Faturamento (API)'", (uid, ini, fim)).fetchone()[0] > 0
    if api:
        # uma linha por semana: a mesma semana gravada em dois períodos (ex.: 31/08,
        # que existe em agosto e em setembro) conta uma vez só
        r = db.execute(
            "SELECT COALESCE(SUM(v),0) FROM (SELECT MAX(valor_total) AS v FROM vendas_produtos "
            "WHERE unidade_id=? AND data_inicio>=? AND data_inicio<? AND tipo='VENDA' "
            "AND produto='Faturamento (API)' GROUP BY data_inicio, data_fim)", (uid, ini, fim)).fetchone()
        return float(r[0] or 0)
    filtro = "AND produto!='Faturamento (API)'"
    r = db.execute(
        "SELECT COALESCE(SUM(valor_total),0) FROM vendas_produtos WHERE unidade_id=? "
        f"AND data_inicio>=? AND data_inicio<? AND tipo='VENDA' {filtro}", (uid, ini, fim)).fetchone()
    return float(r[0] or 0)


def compras_janela(db, uid, ini, fim, status="conferido") -> float:
    """Compras da janela [ini, fim), sem operacionais.
    status: 'conferido' (efetivas) ou 'em_transito'."""
    filtro = SQL_CONFERIDO if status == "conferido" else "status_pedido='em_transito'"
    r = db.execute(
        "SELECT COALESCE(SUM(valor_total),0) FROM compras WHERE unidade_id=? "
        f"AND data>=? AND data<? AND valor_total>0 AND {filtro} {SQL_EXCL_OP}",
        (uid, ini, fim)).fetchone()
    return float(r[0] or 0)
