"""
classificar.py — Classificação canônica de insumos pelo CÓDIGO Atlas.

A fonte de verdade é o full_code do SKU no Atlas (ex.: "10.112-672"): os 3
dígitos entre o ponto e o traço identificam a categoria do produto (112 =
Pescados) de forma estável — o código de um produto não muda, mesmo que o
Atlas renomeie ou reescreva o nome da categoria (maiúsculas, acentos,
abreviação) ao longo do tempo. Por isso o código é usado como identificador
PRIMÁRIO (CAT_POR_CODIGO); o nome de categoria vindo de compras.secao
(sku_categories.name) só é usado como fallback quando a linha não tem
full_code (VMarket/XLSX legado) ou o código não está mapeado.

Confirmado analisando os dados reais: um mesmo código sempre correspondeu à
mesma categoria — as poucas linhas com nome divergente pro mesmo código são
erros pontuais de digitação/seção no Atlas, não uma mudança real de código.

Categorias operacionais (excluídas do CMV/CMC):
  - "301" = Material de Limpeza
  - "400" = Alimentação Funcionários
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
    Categoria canônica de uma linha de compra — CÓDIGO primeiro, sempre.

    O código (3 dígitos do full_code) é o identificador estável: se a linha
    tem full_code e o código está em CAT_POR_CODIGO, essa é a categoria,
    ponto final — não importa o que está escrito em secao (que pode ter
    sido digitado/renomeado de formas diferentes ao longo do tempo). Só
    olha para secao quando o código está ausente ou não é reconhecido:
    primeiro tentando reconciliar contra a grafia atual do Atlas
    (normalizar_categoria_atlas), depois por palavra-chave para linhas sem
    nenhum vínculo com o cadastro Atlas (VMarket/XLSX legado).
    """
    tem_code = isinstance(full_code, str) and full_code.strip()
    if tem_code:
        cat = categoria_por_codigo(full_code)
        if cat is not None:
            return cat

    tem_secao = isinstance(secao, str) and secao.strip()
    if tem_secao:
        return normalizar_categoria_atlas(secao)
    return normalizar_secao(secao)
