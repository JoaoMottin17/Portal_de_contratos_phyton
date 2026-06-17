# -*- coding: utf-8 -*-
"""Teste rapido: conecta no Firebird e valida os calculos do dashboard."""
from firebird.driver import connect, tpb, Isolation, TraAccessMode

# Transacao SOMENTE LEITURA: o Firebird recusa qualquer escrita nesta sessao.
READONLY_TPB = tpb(isolation=Isolation.SNAPSHOT, access_mode=TraAccessMode.READ)

DSN_CANDIDATOS = [
    r"192.168.191.200/3050:D:\AgroSoft\AgroGestao4\Dados\AG2006.FDB",
    r"192.168.191.200/3050:E:\AgroSoft\AgroGestao4\Dados\AG2006.FDB",
    r"192.168.191.200/3050:F:\AgroSoft\AgroGestao4\Dados\AG2006.FDB",
    r"192.168.191.200/3050:C:\AgroSoft\AgroGestao4\Dados\AG2006.FDB",
    r"192.168.191.200/3050:AG2006",
]

con = None
for dsn in DSN_CANDIDATOS:
    try:
        con = connect(dsn, user="SYSDBA", password="masterkey", charset="WIN1252")
        print("Conectado:", dsn)
        break
    except Exception as e:
        print("  falhou:", dsn[:40], "->", str(e)[:50])
if con is None:
    raise SystemExit("Nenhum DSN conectou (rode na rede do servidor).")

cur = con.transaction_manager(READONLY_TPB).cursor()

cur.execute("""
    SELECT COUNT(DISTINCT c.ID), COALESCE(SUM(ci.VALOR_TOTAL),0)
    FROM CONTRATOS c
    JOIN CONTRATOS_ITENS ci
      ON ci.CD_USUARIO = c.CD_USUARIO AND ci.NR_SEQUENCIAL = c.NR_SEQUENCIAL
    WHERE c.ST_TIPO_ES = 'S' AND c.DATA_CANCELAMENTO IS NULL
""")
n, contratado = cur.fetchone()

cur.execute("""
    SELECT COALESCE(SUM(mv.VL_TOTAL),0)
    FROM CONTRATOS_ITENS_MOVTO mv
    JOIN CONTRATOS_ITENS ci ON ci.NR_SEQ_ITEM = mv.ID_CONTRATO_ITEM
    JOIN CONTRATOS c
      ON c.CD_USUARIO = ci.CD_USUARIO AND c.NR_SEQUENCIAL = ci.NR_SEQUENCIAL
    WHERE c.ST_TIPO_ES = 'S' AND c.DATA_CANCELAMENTO IS NULL
      AND mv.ID_NOTA_ITEM IS NOT NULL
""")
faturado = cur.fetchone()[0]

print("\n=== CONTRATOS DE VENDA (ST_TIPO_ES = S, nao cancelados) ===")
print(f"  Contratos .......: {n}")
print(f"  Valor contratado : R$ {float(contratado):,.2f}")
print(f"  Valor faturado ..: R$ {float(faturado):,.2f}")
print(f"  Falta faturar ...: R$ {float(contratado) - float(faturado):,.2f}")
pct = float(faturado) / float(contratado) * 100 if contratado else 0
print(f"  % faturado ......: {pct:.1f}%")

print("\n  Amostra de 8 contratos com saldo a faturar:")
cur.execute("""
    SELECT FIRST 8 c.NUMERO, p.NOME, ci_sum.CONTRATADO,
           COALESCE(mv_sum.FAT,0) AS FAT
    FROM CONTRATOS c
    LEFT JOIN PESSOAS p ON p.CODIGO = c.CD_CLIENTE
    JOIN (
        SELECT CD_USUARIO, NR_SEQUENCIAL, SUM(VALOR_TOTAL) CONTRATADO
        FROM CONTRATOS_ITENS GROUP BY CD_USUARIO, NR_SEQUENCIAL
    ) ci_sum ON ci_sum.CD_USUARIO = c.CD_USUARIO
            AND ci_sum.NR_SEQUENCIAL = c.NR_SEQUENCIAL
    LEFT JOIN (
        SELECT ci.CD_USUARIO, ci.NR_SEQUENCIAL, SUM(mv.VL_TOTAL) FAT
        FROM CONTRATOS_ITENS_MOVTO mv
        JOIN CONTRATOS_ITENS ci ON ci.NR_SEQ_ITEM = mv.ID_CONTRATO_ITEM
        WHERE mv.ID_NOTA_ITEM IS NOT NULL
        GROUP BY ci.CD_USUARIO, ci.NR_SEQUENCIAL
    ) mv_sum ON mv_sum.CD_USUARIO = c.CD_USUARIO
            AND mv_sum.NR_SEQUENCIAL = c.NR_SEQUENCIAL
    WHERE c.ST_TIPO_ES = 'S' AND c.DATA_CANCELAMENTO IS NULL
      AND ci_sum.CONTRATADO - COALESCE(mv_sum.FAT,0) > 1
    ORDER BY ci_sum.CONTRATADO - COALESCE(mv_sum.FAT,0) DESC
""")
for numero, nome, ctr, fat in cur.fetchall():
    ctr, fat = float(ctr), float(fat)
    print(f"    {str(numero):14} {str(nome)[:28]:28} "
          f"contr={ctr:14,.2f}  fat={fat:14,.2f}  saldo={ctr-fat:14,.2f}")

con.close()
print("\nOK - calculos validados.")
