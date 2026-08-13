"""
classificar.py — Classificação canônica de insumos pela categoria do Atlas.

A fonte de verdade é o nome da categoria cadastrada no Atlas para o produto
(sku_categories.name, ligado a cada sku_item). Isso já vem junto com a compra
via sincronizar_atlas.py e é gravado em compras.secao — quando a linha tem
full_code (veio do Atlas), secao É o nome real da categoria, não um texto
livre a ser interpretado.

O mapeamento por CÓDIGO (full_code, ex.: "10.112-672" → "112") só é usado
como fallback quando uma linha do Atlas não trouxe sku_categories (produto
sem categoria cadastrada). Classificação por palavra-chave no nome de seção
é o último fallback, para linhas antigas sem código (ex.: VMarket/XLSX legado).

Categorias operacionais (excluídas do CMV/CMC):
  - "Material de Limpeza"
  - "Alimentação Funcionários" / "Alim. Funcionarios"
"""
import unicodedata


def _n(s):
    return unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode().upper().strip()


# Lista canônica de categorias cadastradas no Atlas (sku_categories.name),
# consultada em 2026-08-13. Usada para reconciliar grafias antigas de
# compras.secao (capturadas em datas diferentes, o Atlas pode ter renomeado
# categorias desde então) com o nome ATUAL.
CATEGORIAS_ATLAS = [
    "Alcóolicos - Cervejas",
    "Alcóolicos - Destilados",
    "Alcóolicos - Vinhos e Espumantes",
    "Alimentação Funcionários",
    "Alimentos",
    "Bebidas",
    "Bebidas Não-Alcóolicas",
    "Carne Vermelha",
    "Carnes Brancas",
    "Confeitaria",
    "Conservas, Cond., Molhos e Óleos",
    "Consumíveis",
    "Consumo Interno",
    "Descartáveis",
    "Embalagens",
    "Hortifruti",
    "Material de Limpeza",
    "Ovos e Latícinios",
    "Pescados e Frutos do Mar",
    "Processados",
    "Queijos",
    "Secos e outros temperos",
]
_CATEGORIAS_POR_CHAVE = {_n(c): c for c in CATEGORIAS_ATLAS}

# Grafias antigas que não reconciliam só por acento/maiúscula (o nome em si
# mudou no Atlas) — mapeia pro nome canônico atual.
_ALIASES_HISTORICOS = {
    "CARNES VERMELHAS": "Carne Vermelha",
    "CONSERVAS, CONDIMENTOS, MOLHOS E OLEOS": "Conservas, Cond., Molhos e Óleos",
    "BEBIDAS NAO ALCOOLICAS": "Bebidas Não-Alcóolicas",   # sem o hífen do nome atual
    "USO INTERNO": "Consumo Interno",
}


def normalizar_categoria_atlas(nome: str) -> str:
    """
    Reconcilia uma categoria vinda do Atlas (compras.secao ou
    sku_codigos.categoria_nome) com a grafia canônica ATUAL — cobre casos em
    que o dado foi capturado há meses e o Atlas renomeou a categoria depois
    (maiúsculas/acentos diferentes, ou o nome mudou). Categoria não
    reconhecida (nova, ainda não nesta lista) volta como veio — melhor
    mostrar do que descartar o dado.
    """
    # Guarda contra NaN do pandas: float('nan') é "truthy" (not nan é False),
    # então "if not nome" sozinho deixa passar e .strip() quebra em float.
    if not isinstance(nome, str) or not nome.strip():
        return nome if isinstance(nome, str) else None
    nome = nome.strip()
    chave = _n(nome)
    if chave in _CATEGORIAS_POR_CHAVE:
        return _CATEGORIAS_POR_CHAVE[chave]
    alias = _ALIASES_HISTORICOS.get(chave)
    if alias:
        return alias
    return nome


# Categoria (3 dígitos do full_code) → nome canônico. Fallback só usado
# quando um sku do Atlas não trouxe sku_categories (produto sem categoria
# cadastrada) — na prática cada vez mais raro.
CAT_POR_CODIGO = {
    "110": "Carne Vermelha",
    "111": "Carnes Brancas",
    "112": "Pescados e Frutos do Mar",
    "113": "Hortifruti",
    "120": "Ovos e Latícinios",
    "121": "Queijos",
    "130": "Confeitaria",
    "140": "Secos e outros temperos",
    "141": "Conservas, Cond., Molhos e Óleos",
    "200": "Bebidas Não-Alcóolicas",
    "210": "Alcóolicos - Cervejas",
    "220": "Alcóolicos - Vinhos e Espumantes",
    "230": "Alcóolicos - Destilados",
    "300": "Embalagens",
    "301": "Material de Limpeza",   # OPERACIONAL
    "302": "Descartáveis",
    "303": "Consumo Interno",
    "400": "Alimentação Funcionários",    # OPERACIONAL
    "500": "Processados",
}

# Categorias que NÃO entram no CMV/CMC (custos operacionais).
# Inclui o nome real do Atlas e as grafias antigas — a exclusão precisa
# funcionar independente de qual caminho classificou a linha.
CATS_OPERACIONAIS = {
    "Material de Limpeza",
    "Alim. Funcionarios", "Alimentação Funcionários", "Alimentacao Funcionarios",
}

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


# ── Fallback por nome (linhas sem código — VMarket/XLSX legado) ────────────────

_SECAO_MAP = [
    (["CARNE VERMELHA", "CARNES VERMELHA"],          "Carne Vermelha"),
    (["CARNE BRANCA", "CARNES BRANCA"],              "Carnes Brancas"),
    (["PESCADO", "FRUTO DO MAR"],                    "Pescados e Frutos do Mar"),
    (["ALCOOLICO", "ALCOOLICOS", "VINHO", "ESPUMANTE"], "Alcóolicos - Vinhos e Espumantes"),
    (["CERVEJA"],                                    "Alcóolicos - Cervejas"),
    (["DESTILADO"],                                  "Alcóolicos - Destilados"),
    (["BEBIDA NAO", "BEBIDAS NAO", "NAO-ALCOO", "NAO ALCOO"], "Bebidas Não-Alcóolicas"),
    (["HORTIFRUTI"],                                 "Hortifruti"),
    (["QUEIJO"],                                     "Queijos"),
    (["LATICINIOS", "LATICINIO", "OVOS"],            "Ovos e Latícinios"),
    (["CONFEITARIA"],                                "Confeitaria"),
    (["CONSERVA", "CONDIMENTO", "MOLHO", "OLEO"],    "Conservas, Cond., Molhos e Óleos"),
    (["SECO", "TEMPERO", "ESPECIARIA"],              "Secos e outros temperos"),
    (["PROCESSADO"],                                 "Processados"),
    (["DESCARTA"],                                   "Descartáveis"),
    (["EMBALA"],                                     "Embalagens"),
    (["MATERIAL DE LIMPEZA", "LIMPEZA"],             "Material de Limpeza"),
    (["ALIMENTACAO FUNCIONARIO", "FUNCION"],         "Alimentação Funcionários"),
    (["USO INTERNO", "USO MENSAL", "CONSUMO INTERNO"], "Consumo Interno"),
]


def normalizar_secao(secao) -> str:
    n = _n(secao)
    for kws, cat in _SECAO_MAP:
        if any(kw in n for kw in kws):
            return cat
    return "Outros"


def classificar(full_code, secao) -> str:
    """
    Categoria canônica de uma linha de compra.

    Quando full_code está presente, a linha veio do Atlas e secao já é o
    nome REAL da categoria (sku_categories.name, capturado no mesmo join que
    trouxe o full_code) — normaliza contra a grafia atual (o dado pode ter
    sido capturado antes de uma renomeação no Atlas) e usa direto, sem
    reinterpretar palavra por palavra. Só cai pro código numérico se o
    produto não tiver categoria cadastrada no Atlas, e pro fallback por
    palavra-chave quando a linha nem tem full_code (VMarket/XLSX legado).
    """
    tem_code = isinstance(full_code, str) and full_code.strip()
    tem_secao = isinstance(secao, str) and secao.strip()
    if tem_code and tem_secao:
        return normalizar_categoria_atlas(secao)
    if tem_code:
        cat = categoria_por_codigo(full_code)
        if cat is not None:
            return cat
    return normalizar_secao(secao)
