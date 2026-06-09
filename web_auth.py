"""Gate de senha pra Streamlit Cloud — suporta senha master + senha por unidade.

Estrutura de secrets esperada:
    app_password = "senha-master"          # vê todas as unidades

    [unit_passwords]
    asa-norte    = "asanorte321"           # quando logar com essa senha,
    asa-sul      = "asasul321"             # o app trava a seleção nessa unidade
    aguas-claras = "aguasclaras321"
    koji         = "koji321"
    mane         = "mane321"
    spq-norte    = "spqnorte321"

Após login, st.session_state vai ter:
    auth_ok   = True
    unit_lock = None (master)  OU  slug da unidade (gerente)
"""
from __future__ import annotations

import os

import streamlit as st


def _master_password() -> str | None:
    try:
        if "app_password" in st.secrets:
            v = st.secrets["app_password"]
            if v:
                return str(v)
    except Exception:
        pass
    return os.environ.get("APP_PASSWORD") or None


def _unit_passwords() -> dict[str, str]:
    """Retorna {slug: senha} a partir de st.secrets['unit_passwords'] ou env vars."""
    out: dict[str, str] = {}
    try:
        if "unit_passwords" in st.secrets:
            for k, v in dict(st.secrets["unit_passwords"]).items():
                if v:
                    out[str(k)] = str(v)
    except Exception:
        pass
    # Fallback env vars: UNIT_PWD_asa_norte=... etc
    for env_key, env_val in os.environ.items():
        if env_key.startswith("UNIT_PWD_") and env_val:
            slug = env_key[len("UNIT_PWD_"):].lower().replace("_", "-")
            out.setdefault(slug, env_val)
    return out


def login_gate() -> bool:
    """Bloqueia o app até senha correta. Retorna True quando passa.

    Após login bem-sucedido seta `st.session_state['unit_lock']`:
      - None  → acesso master (todas as unidades)
      - slug  → acesso restrito àquela unidade
    """
    master = _master_password()
    unit_pwds = _unit_passwords()

    # Sem proteção configurada (dev local)
    if not master and not unit_pwds:
        st.session_state.setdefault("unit_lock", None)
        return True

    if st.session_state.get("auth_ok"):
        return True

    st.markdown(
        "<div style='max-width:420px;margin:60px auto;padding:24px;"
        "border:1px solid #d0d4dc;border-radius:8px;background:#fff;'>"
        "<h2 style='margin:0 0 8px;font-size:18px'>🔒 Acesso restrito</h2>"
        "<p style='color:#6b7280;font-size:13px;margin:0 0 16px'>"
        "Digite a senha que você recebeu. Gerentes acessam direto sua unidade.</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    def _entered():
        pwd = st.session_state.get("_pw_input", "")
        # Master
        if master and pwd == master:
            st.session_state["auth_ok"] = True
            st.session_state["unit_lock"] = None
            st.session_state.pop("_pw_input", None)
            st.session_state.pop("auth_err", None)
            return
        # Por unidade
        for slug, p in unit_pwds.items():
            if pwd == p:
                st.session_state["auth_ok"] = True
                st.session_state["unit_lock"] = slug
                st.session_state.pop("_pw_input", None)
                st.session_state.pop("auth_err", None)
                return
        st.session_state["auth_err"] = True

    st.text_input(
        "Senha",
        type="password",
        key="_pw_input",
        on_change=_entered,
        label_visibility="collapsed",
        placeholder="Senha",
    )
    if st.session_state.get("auth_err"):
        st.error("Senha incorreta.")
    return False
