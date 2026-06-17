"""
Mapeador completo do banco Firebird AG2006.FDB
Gera:
  - mapa_banco.json  -> estrutura completa (para análise/IA)
  - mapa_banco.md    -> documentação legível por área

Requisitos: pip install firebird-driver
Ajuste DSN, USER, PASSWORD e CHARSET abaixo se necessário.
"""

from firebird.driver import connect, tpb, Isolation, TraAccessMode
import json
import sys
from collections import defaultdict

# Transacao SOMENTE LEITURA: o Firebird recusa qualquer escrita nesta sessao.
READONLY_TPB = tpb(isolation=Isolation.SNAPSHOT, access_mode=TraAccessMode.READ)

# ----------------------------------------------------------------------
# CONFIGURACAO  (ajuste se precisar)
# ----------------------------------------------------------------------
# Testa varios caminhos ate um conectar (igual ao extrair_schema que funcionou).
# Se voce ja sabe qual funcionou, deixe so ele na lista.
DSN_CANDIDATOS = [
    r'192.168.191.200/3050:D:\AgroSoft\AgroGestao4\Dados\AG2006.FDB',
    r'192.168.191.200/3050:E:\AgroSoft\AgroGestao4\Dados\AG2006.FDB',
    r'192.168.191.200/3050:F:\AgroSoft\AgroGestao4\Dados\AG2006.FDB',
    r'192.168.191.200/3050:C:\AgroSoft\AgroGestao4\Dados\AG2006.FDB',
    r'192.168.191.200/3050:AG2006',
    r'gw-castro/3050:AG2006',
]
USER     = 'SYSDBA'
PASSWORD = 'masterkey'
CHARSET  = 'WIN1252'  

# Quantas linhas de amostra trazer por tabela (0 = nenhuma, só estrutura)
AMOSTRA_LINHAS = 3

# Se True, inclui as tabelas de replicacao/staging (TBI_, TAU_, TAD_, DS_, etc).
# Por padrao False porque sao centenas de tabelas tecnicas que poluem o mapa.
INCLUIR_TABELAS_TECNICAS = False

PREFIXOS_TECNICOS = (
    'TBI_', 'TBIU_', 'TBIUD_', 'TBD_', 'TBDU_', 'TBU_',
    'TAI_', 'TAIU_', 'TAIUD_', 'TAU_', 'TAUD_', 'TAD_',
    'DS_', 'TMP_', 'REPLIC_', 'TBIGLEBAS', 'TBIUCX',
    'TIB_', 'TD_',
)

# ----------------------------------------------------------------------
# CODIGOS DE TIPO DE CAMPO DO FIREBIRD
# ----------------------------------------------------------------------
TIPOS_FB = {
    7: 'SMALLINT', 8: 'INTEGER', 10: 'FLOAT', 12: 'DATE', 13: 'TIME',
    14: 'CHAR', 16: 'BIGINT/NUMERIC', 23: 'BOOLEAN', 27: 'DOUBLE',
    35: 'TIMESTAMP', 37: 'VARCHAR', 261: 'BLOB',
}

def tipo_legivel(field_type, sub_type, length, scale):
    base = TIPOS_FB.get(field_type, f'TIPO_{field_type}')
    if field_type == 16:
        if sub_type == 1:
            return f'NUMERIC({length},{abs(scale)})' if scale else 'NUMERIC'
        if sub_type == 2:
            return f'DECIMAL({length},{abs(scale)})' if scale else 'DECIMAL'
        return 'BIGINT'
    if field_type in (37, 14):
        return f'{base}({length})'
    return base


def conectar():
    for dsn in DSN_CANDIDATOS:
        try:
            con = connect(dsn, user=USER, password=PASSWORD, charset=CHARSET)
            print(f'Conectado com sucesso: {dsn}')
            return con
        except Exception as e:
            print(f'  falhou: {dsn}  ({str(e)[:50]})')
    print('\nNenhum caminho conectou. Pegue o DSN exato do extrair_schema que funcionou')
    print('ou do campo "Database" da conexao no DBeaver, e coloque em DSN_CANDIDATOS.')
    sys.exit(1)


def eh_tecnica(nome):
    return nome.startswith(PREFIXOS_TECNICOS)


def main():
    con = conectar()
    cur = con.transaction_manager(READONLY_TPB).cursor()

    # ------------------------------------------------------------------
    # 1) Lista de tabelas e views (nao-sistema)
    # ------------------------------------------------------------------
    cur.execute("""
        SELECT TRIM(RDB$RELATION_NAME),
               CASE WHEN RDB$VIEW_BLR IS NULL THEN 'TABELA' ELSE 'VIEW' END
        FROM RDB$RELATIONS
        WHERE RDB$SYSTEM_FLAG = 0
        ORDER BY RDB$RELATION_NAME
    """)
    relacoes = [(n, t) for n, t in cur.fetchall()]
    if not INCLUIR_TABELAS_TECNICAS:
        relacoes = [(n, t) for n, t in relacoes if not eh_tecnica(n)]
    print(f'{len(relacoes)} objetos a mapear '
          f'({"com" if INCLUIR_TABELAS_TECNICAS else "sem"} tabelas tecnicas).')

    # ------------------------------------------------------------------
    # 2) Colunas de todas as relacoes (uma query so)
    # ------------------------------------------------------------------
    cur.execute("""
        SELECT TRIM(rf.RDB$RELATION_NAME), TRIM(rf.RDB$FIELD_NAME),
               f.RDB$FIELD_TYPE, f.RDB$FIELD_SUB_TYPE,
               f.RDB$FIELD_LENGTH, f.RDB$FIELD_SCALE,
               rf.RDB$NULL_FLAG, rf.RDB$FIELD_POSITION
        FROM RDB$RELATION_FIELDS rf
        JOIN RDB$FIELDS f ON f.RDB$FIELD_NAME = rf.RDB$FIELD_SOURCE
        ORDER BY rf.RDB$RELATION_NAME, rf.RDB$FIELD_POSITION
    """)
    colunas = defaultdict(list)
    for tab, campo, ftype, sub, length, scale, notnull, pos in cur.fetchall():
        colunas[tab].append({
            'coluna': campo,
            'tipo': tipo_legivel(ftype, sub, length, scale),
            'obrigatorio': bool(notnull),
            'pos': pos,
        })

    # ------------------------------------------------------------------
    # 3) Chaves primarias e estrangeiras
    # ------------------------------------------------------------------
    cur.execute("""
        SELECT TRIM(rc.RDB$RELATION_NAME), TRIM(rc.RDB$CONSTRAINT_TYPE),
               TRIM(sg.RDB$FIELD_NAME), TRIM(rc2.RDB$RELATION_NAME)
        FROM RDB$RELATION_CONSTRAINTS rc
        JOIN RDB$INDEX_SEGMENTS sg ON sg.RDB$INDEX_NAME = rc.RDB$INDEX_NAME
        LEFT JOIN RDB$REF_CONSTRAINTS refc
               ON refc.RDB$CONSTRAINT_NAME = rc.RDB$CONSTRAINT_NAME
        LEFT JOIN RDB$RELATION_CONSTRAINTS rc2
               ON rc2.RDB$CONSTRAINT_NAME = refc.RDB$CONST_NAME_UQ
        WHERE rc.RDB$CONSTRAINT_TYPE IN ('PRIMARY KEY', 'FOREIGN KEY')
        ORDER BY rc.RDB$RELATION_NAME
    """)
    pks = defaultdict(list)
    fks = defaultdict(list)
    for tab, ctype, campo, ref in cur.fetchall():
        if ctype == 'PRIMARY KEY':
            pks[tab].append(campo)
        else:
            fks[tab].append({'coluna': campo, 'referencia': ref})

    # ------------------------------------------------------------------
    # 4) Contagem de linhas + amostra (so para tabelas, opcional)
    # ------------------------------------------------------------------
    mapa = {}
    for i, (nome, tipo) in enumerate(relacoes, 1):
        info = {
            'tipo': tipo,
            'colunas': colunas.get(nome, []),
            'pk': pks.get(nome, []),
            'fk': fks.get(nome, []),
            'qt_linhas': None,
            'amostra': [],
        }
        # contagem (pode ser lenta em tabelas gigantes; protegido por try)
        try:
            cur.execute(f'SELECT COUNT(*) FROM "{nome}"')
            info['qt_linhas'] = cur.fetchone()[0]
        except Exception:
            pass
        # amostra
        if AMOSTRA_LINHAS > 0:
            try:
                cur.execute(f'SELECT FIRST {AMOSTRA_LINHAS} * FROM "{nome}"')
                cols = [d[0].strip() for d in cur.description]
                for row in cur.fetchall():
                    info['amostra'].append(
                        {c: (str(v)[:80] if v is not None else None)
                         for c, v in zip(cols, row)}
                    )
            except Exception:
                pass
        mapa[nome] = info
        if i % 25 == 0:
            print(f'  ... {i}/{len(relacoes)} mapeados')

    # ------------------------------------------------------------------
    # 5) Grava JSON
    # ------------------------------------------------------------------
    with open('mapa_banco.json', 'w', encoding='utf-8') as f:
        json.dump(mapa, f, ensure_ascii=False, indent=1)
    print('Gerado: mapa_banco.json')

    # ------------------------------------------------------------------
    # 6) Gera Markdown legivel
    # ------------------------------------------------------------------
    with open('mapa_banco.md', 'w', encoding='utf-8') as f:
        f.write('# Mapa do Banco AG2006.FDB\n\n')
        tabelas = sorted(k for k, v in mapa.items() if v['tipo'] == 'TABELA')
        views   = sorted(k for k, v in mapa.items() if v['tipo'] == 'VIEW')
        f.write(f'- **{len(tabelas)} tabelas** e **{len(views)} views** mapeadas\n')
        f.write(f'- Tabelas tecnicas de replicacao: '
                f'{"incluidas" if INCLUIR_TABELAS_TECNICAS else "ocultadas"}\n\n')

        def escreve(nome):
            v = mapa[nome]
            linhas = f' — {v["qt_linhas"]:,} linhas' if v['qt_linhas'] is not None else ''
            f.write(f'## {nome} ({v["tipo"]}){linhas}\n\n')
            if v['pk']:
                f.write(f'**PK:** {", ".join(v["pk"])}  \n')
            if v['fk']:
                refs = ", ".join(f'{x["coluna"]}->{x["referencia"]}' for x in v['fk'])
                f.write(f'**FK:** {refs}  \n')
            f.write('\n| Coluna | Tipo | Obrig. |\n|---|---|---|\n')
            for c in v['colunas']:
                ob = 'sim' if c['obrigatorio'] else ''
                f.write(f'| {c["coluna"]} | {c["tipo"]} | {ob} |\n')
            f.write('\n')

        f.write('\n---\n# TABELAS\n\n')
        for nome in tabelas:
            escreve(nome)
        f.write('\n---\n# VIEWS\n\n')
        for nome in views:
            escreve(nome)
    print('Gerado: mapa_banco.md')

    con.close()
    print('\nConcluido. Anexe o mapa_banco.json para analise do modelo.')


if __name__ == '__main__':
    main()