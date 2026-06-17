# -*- coding: utf-8 -*-
"""
sync_dados.py  —  roda NESTE PC (na rede da empresa).

Busca os dados do Firebird (somente leitura) e grava um SNAPSHOT no Postgres
(Neon) que o dashboard na nuvem (Streamlit Cloud) le. O Firebird nunca fica
exposto na internet.

Uso:
  # 1x para testar sem nuvem (gera arquivos .parquet locais):
  py sync_dados.py --parquet

  # producao (grava no Neon); a connection string vem da variavel PG_URL:
  set PG_URL=postgresql+psycopg2://user:senha@host/db?sslmode=require
  py sync_dados.py

Agende no Agendador de Tarefas do Windows para rodar de hora em hora.
"""
import os
import sys
import argparse
from datetime import datetime
from decimal import Decimal

import pandas as pd
from firebird.driver import connect, tpb, Isolation, TraAccessMode

from consultas import SQL_ITENS, SQL_MOVTO, SQL_FIN

# --- conexao Firebird (somente leitura) ---
READONLY_TPB = tpb(isolation=Isolation.SNAPSHOT, access_mode=TraAccessMode.READ)
DSN_CANDIDATOS = [
    os.getenv("FB_DSN", ""),
    r"192.168.191.200/3050:D:\AgroSoft\AgroGestao4\Dados\AG2006.FDB",
    r"192.168.191.200/3050:E:\AgroSoft\AgroGestao4\Dados\AG2006.FDB",
    r"192.168.191.200/3050:F:\AgroSoft\AgroGestao4\Dados\AG2006.FDB",
    r"192.168.191.200/3050:C:\AgroSoft\AgroGestao4\Dados\AG2006.FDB",
    r"192.168.191.200/3050:AG2006",
]
FB_USER = os.getenv("FB_USER", "SYSDBA")
FB_PASSWORD = os.getenv("FB_PASSWORD", "masterkey")
FB_CHARSET = os.getenv("FB_CHARSET", "WIN1252")


def _f(v):
    return float(v) if isinstance(v, Decimal) else v


def conectar_fb():
    erros = []
    for dsn in DSN_CANDIDATOS:
        if not dsn:
            continue
        try:
            con = connect(dsn, user=FB_USER, password=FB_PASSWORD, charset=FB_CHARSET)
            print(f"[Firebird] conectado: {dsn}")
            return con
        except Exception as e:  # noqa: BLE001
            erros.append(f"{dsn[:45]}... -> {str(e)[:60]}")
    raise ConnectionError("Nao conectou ao Firebird:\n  " + "\n  ".join(erros))


def buscar(cur, sql):
    cur.execute(sql)
    cols = [d[0].strip() for d in cur.description]
    return pd.DataFrame([[_f(v) for v in r] for r in cur.fetchall()], columns=cols)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", action="store_true",
                    help="grava .parquet locais em vez do Postgres (teste)")
    args = ap.parse_args()

    con = conectar_fb()
    try:
        cur = con.transaction_manager(READONLY_TPB).cursor()
        print("[Firebird] buscando itens...")
        itens = buscar(cur, SQL_ITENS)
        print(f"           {len(itens):,} linhas")
        print("[Firebird] buscando movto...")
        movto = buscar(cur, SQL_MOVTO)
        print(f"           {len(movto):,} linhas")
        print("[Firebird] buscando financeiro...")
        fin = buscar(cur, SQL_FIN)
        print(f"           {len(fin):,} linhas")
    finally:
        con.close()

    agora = datetime.now()
    meta = pd.DataFrame([{"atualizado_em": agora}])

    if args.parquet:
        itens.to_parquet("snap_itens.parquet", index=False)
        movto.to_parquet("snap_movto.parquet", index=False)
        fin.to_parquet("snap_fin.parquet", index=False)
        meta.to_parquet("snap_meta.parquet", index=False)
        print("\n[OK] snapshots .parquet gravados localmente (modo teste).")
        return

    pg_url = os.getenv("PG_URL", "")
    if not pg_url:
        sys.exit("ERRO: defina a variavel PG_URL (connection string do Neon) "
                 "ou use --parquet para testar.")
    from sqlalchemy import create_engine
    eng = create_engine(pg_url, pool_pre_ping=True)
    print("[Neon] gravando snapshot...")
    itens.to_sql("snap_itens", eng, if_exists="replace", index=False)
    movto.to_sql("snap_movto", eng, if_exists="replace", index=False)
    fin.to_sql("snap_fin", eng, if_exists="replace", index=False)
    meta.to_sql("snap_meta", eng, if_exists="replace", index=False)
    print(f"\n[OK] snapshot gravado no Neon em {agora:%d/%m/%Y %H:%M}.")


if __name__ == "__main__":
    main()
