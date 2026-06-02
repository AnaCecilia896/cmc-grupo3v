"""Gate de senha simples pra app no Streamlit Cloud.

Lê senha de:
  1. `st.secrets["app_password"]`
  2. env var `APP_PASSWORD`
  3. Se nenhum: sem proteção (modo dev local).
"""
from __future__ import annotations

import os

import streamlit as st


def _esperada() -> str | None:
    try:
        if "app_password" in st.secrets:
            v = st.secrets["app_password"]
            if v:
                return str(v)
    except Exception:
        pass
    return os.environ.get("APP_PASSWORD") or None


def login_gate() -> bool:
    """Bloqueia o app até senha correta. Retorna True quando passa.

    Uso típico no topo do app:
        if not login_gate(): st.stop()
    """
    esp = _esperada()
    if esp is None:
        return True  # sem proteção (dev local)

    if st.session_state.get("auth_ok"):
        return True

    st.markdown(
        "<div style='max-width:420px;margin:60px auto;padding:24px;"
        "border:1px solid #d0d4dc;border-radius:8px;background:#fff;'>"
        "<h2 style='margin:0 0 8px;font-size:18px'>🔒 Acesso restrito</h2>"
        "<p style='color:#6b7280;font-size:13px;margin:0 0 16px'>"
        "Digite a senha que você recebeu.</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    def _entered():
        if st.session_state.get("_pw_input") == esp:
            st.session_state["auth_ok"] = True
            st.session_state.pop("_pw_input", None)
        else:
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
