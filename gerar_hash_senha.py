# -*- coding: utf-8 -*-
"""Gera o hash de senha(s) para colar no secrets.toml (secao [auth]).

Uso:
  py gerar_hash_senha.py "senhaDoSocio" "senhaDoGestor"

Copie cada hash gerado para o campo password do usuario correspondente.
NUNCA guarde a senha em texto puro no secrets — só o hash.
"""
import sys
import streamlit_authenticator as stauth

senhas = sys.argv[1:]
if not senhas:
    print('Informe ao menos uma senha. Ex.: py gerar_hash_senha.py "minhaSenha"')
    raise SystemExit(1)

for s in senhas:
    try:
        h = stauth.Hasher.hash(s)                 # streamlit-authenticator 0.4+
    except Exception:                             # noqa: BLE001
        h = stauth.Hasher([s]).generate()[0]      # versoes 0.2 / 0.3
    print(f'{s}  ->  {h}')
