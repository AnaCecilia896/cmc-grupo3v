"""
classificar.py — Classificação canônica de insumos por CÓDIGO Atlas.

A fonte de verdade é o full_code do SKU no Atlas (ex.: "10.112-672"):
  - 2 primeiros dígitos = macro categoria (10 Alimentos / 20 Bebidas / 30 Consumíveis)
  - 3 dígitos após o ponto = categoria (ex.: 112 = Pescados)
  - dígitos após o traço = sequencial do insumo

A categoria (3 dígitos) determina TUDO. Classificação por nome de seção fica
apenas como fallback para linhas antigas sem código (ex.: VMarket/XLSX legado).

Categorias operacionais (excluídas do CMV/CMC):
  - 400 = Alimentação Funcionários
  - 301 = Material de Limpeza
"""
import unicodedata


# Categoria (3 dígitos do full_code) → nome canônico usado em cmv_resumo/dashboard.
# Mantém o MESMO conjunto de categorias que a classificação por nome já produzia,
# para não quebrar linhas existentes de cmv_resumo.
CAT_POR_CODIGO = {
    "110": "Carnes Vermelhas",
    "111": "Carnes Brancas",
    "112": "Pescados e Frutos do Mar",
    "113": "Hortifruti",
    "120": "Laticinios e Ovos",
    "121": "Laticinios e Ovos",   # Queijos são agrupados em Laticínios e Ovos
    "130": "Confeitaria",
    "140": "Secos e Condimentos",
    "141": "Secos e Condimentos",  # Conservas/Condimentos/Molhos/Óleos
    "200": "Bebidas Nao Alcoolicas",
    "210": "Bebidas Alcoolicas",
    "220": "Bebidas Alcoolicas",
    "230": "Bebidas Alcoolicas",
    "300": "Embalagens",
    "301": "Material de Limpeza",   # OPERACIONAL
    "302": "Descartaveis",
    "303": "Uso Interno",
    "400": "Alim. Funcionarios",    # OPERACIONAL
    "500": "Processados",
}

# Categorias que NÃO entram no CMV/CMC (custos operacionais).
CATS_OPERACIONAIS = {"Material de Limpeza", "Alim. Funcionarios"}

# Códigos de categoria operacionais (para filtros SQL diretos por full_code).
CODIGOS_OPERACIONAIS = {"301", "400"}


def cod_categoria(full_code) -> str | None:
    """Extrai os 3 dígitos de categoria de um full_code '10.112-672' → '112'."""
    if not full_code:
        return None
    s = str(full_code)
    ponto = s.find(".")
    traco = s.find("-", ponto + 1)
    if ponto < 0 or traco < 0:
        return None
    meio = s[ponto + 1:traco].strip()
    return meio if meio.isdigit() else None


def categoria_por_codigo(full_code) -> str | None:
    """full_code → nome canônico da categoria, ou None se não reconhecido."""
    cod = cod_categoria(full_code)
    if cod is None:
        return None
    return CAT_POR_CODIGO.get(cod)


# ── Fallback por nome (linhas sem código) ─────────────────────────────────────

def _n(s):
    return unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode().upper().strip()

_SECAO_MAP = [
    (["CARNE VERMELHA", "CARNES VERMELHA"],          "Carnes Vermelhas"),
    (["CARNE BRANCA", "CARNES BRANCA"],              "Carnes Brancas"),
    (["PESCADO", "FRUTO DO MAR"],                    "Pescados e Frutos do Mar"),
    (["ALCOOLICO", "ALCOOLICOS", "VINHO", "ESPUMANTE", "CERVEJA", "DESTILADO"], "Bebidas Alcoolicas"),
    (["BEBIDA NAO", "BEBIDAS NAO", "NAO-ALCOO", "NAO ALCOO"], "Bebidas Nao Alcoolicas"),
    (["HORTIFRUTI"],                                 "Hortifruti"),
    (["LATICINIOS", "LATICINIO", "OVOS", "QUEIJO"],  "Laticinios e Ovos"),
    (["CONGELADO"],                                  "Congelados"),
    (["CONFEITARIA"],                                "Confeitaria"),
    (["SECO", "TEMPERO", "CONDIMENTO", "CONSERVA", "OLEO", "ESPECIARIA"], "Secos e Condimentos"),
    (["PROCESSADO"],                                 "Processados"),
    (["PAO", "PAES"],                                "Paes"),
    (["DESCARTA"],                                   "Descartaveis"),
    (["EMBALA"],                                     "Embalagens"),
    (["MATERIAL DE LIMPEZA", "LIMPEZA"],             "Material de Limpeza"),
    (["ALIMENTACAO FUNCIONARIO", "FUNCION"],         "Alim. Funcionarios"),
    (["USO INTERNO", "USO MENSAL", "CONSUMO INTERNO"], "Uso Interno"),
]


def normalizar_secao(secao) -> str:
    n = _n(secao)
    for kws, cat in _SECAO_MAP:
        if any(kw in n for kw in kws):
            return cat
    return "Outros"


def classificar(full_code, secao) -> str:
    """Categoria canônica: por código quando disponível, senão por nome de seção."""
    cat = categoria_por_codigo(full_code)
    if cat is not None:
        return cat
    return normalizar_secao(secao)
