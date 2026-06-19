# -*- coding: utf-8 -*-
"""
Dashboard de Contratos de Venda  -  Grupo Fernando Ribas Taques
Banco Firebird AG2006.FDB
================================================================
Mostra os contratos de venda detalhados, faturado x falta faturar, por
contrato / cliente / produtor / estado (UF) / safra / produto, com
faturamento semanal e mensal.

MOEDA: ha contratos em R$ e em US$ (DOLAR). A nota fiscal sai sempre em
R$, mas o valor de registro do contrato em dolar fica em US$. Por isso a
metrica confiavel e o VOLUME (kg / sacas de 60 kg), que independe de
moeda. O valor a faturar em R$ e ESTIMADO valorando o volume restante
pelo preco medio realizado (R$/kg), que ja embute o cambio.

Modelo:
  CONTRATOS -> CONTRATOS_ITENS -> CONTRATOS_ITENS_MOVTO (1 linha/NF)
  ligados por ID_CONTRATO_ITEM = CONTRATOS_ITENS.NR_SEQ_ITEM

Como rodar:
  pip install -r requirements.txt
  streamlit run dashboard_contratos.py
"""

import os
import glob
from decimal import Decimal

import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
from firebird.driver import connect, tpb, Isolation, TraAccessMode

try:
    from streamlit_autorefresh import st_autorefresh
    TEM_AUTORF = True
except Exception:  # noqa: BLE001
    TEM_AUTORF = False

# ----------------------------------------------------------------------
# MARCA (Grupo Fernando Ribas Taques)
# ----------------------------------------------------------------------
VERDE_ESCURO = "#14401E"
VERDE = "#2E7D32"
VERDE_MEDIO = "#43A047"
VERDE_CLARO = "#7CB342"
VERDE_PALIDO = "#C5E1A5"
TEAL = "#00695C"
CINZA = "#9E9E9E"

COR_FATURADO = VERDE_ESCURO
COR_A_FATURAR = VERDE_CLARO
COR_SITUACAO = {
    "Faturado": VERDE_ESCURO, "Parcial": VERDE_MEDIO, "A faturar": VERDE_CLARO,
    "Excedente": TEAL, "Cancelado": CINZA,
}

# unidade -> fator para KG
FATOR_KG = {1: 1.0, 4: 1000.0, 7: 40.0, 8: 50.0, 9: 60.0, 17: 25.0}
SACA_KG = 60.0          # 1 saca = 60 kg (soja/milho/trigo)
MOEDA_SIMB = {1: "R$", 2: "US$", 5: "SSJ", 6: "SM", 7: "US$"}

UF_NOMES = {
    "TO": "Tocantins", "PR": "Paraná", "MA": "Maranhão", "MT": "Mato Grosso",
    "MS": "Mato Grosso do Sul", "GO": "Goiás", "BA": "Bahia", "SP": "São Paulo",
    "RS": "Rio Grande do Sul", "SC": "Santa Catarina", "PA": "Pará",
    "PI": "Piauí", "MG": "Minas Gerais",
}

# ----------------------------------------------------------------------
# CONEXAO
# ----------------------------------------------------------------------
DSN_CANDIDATOS = [
    os.getenv("FB_DSN", ""),
    r"192.168.191.200/3050:D:\AgroSoft\AgroGestao4\Dados\AG2006.FDB",
    r"192.168.191.200/3050:E:\AgroSoft\AgroGestao4\Dados\AG2006.FDB",
    r"192.168.191.200/3050:F:\AgroSoft\AgroGestao4\Dados\AG2006.FDB",
    r"192.168.191.200/3050:C:\AgroSoft\AgroGestao4\Dados\AG2006.FDB",
    r"192.168.191.200/3050:AG2006",
    r"gw-castro/3050:AG2006",
]
USER = os.getenv("FB_USER", "SYSDBA")
PASSWORD = os.getenv("FB_PASSWORD", "masterkey")
CHARSET = os.getenv("FB_CHARSET", "WIN1252")

# Transacao SOMENTE LEITURA: o Firebird recusa qualquer escrita (INSERT/UPDATE/
# DELETE/DDL) nesta sessao. Garante que o dashboard nunca altera o banco.
READONLY_TPB = tpb(isolation=Isolation.SNAPSHOT, access_mode=TraAccessMode.READ)


def conectar():
    erros = []
    for dsn in DSN_CANDIDATOS:
        if not dsn:
            continue
        try:
            return connect(dsn, user=USER, password=PASSWORD, charset=CHARSET), dsn
        except Exception as e:  # noqa: BLE001
            erros.append(f"{dsn[:45]}...  ->  {str(e)[:60]}")
    raise ConnectionError("Nao conectou ao Firebird:\n  " + "\n  ".join(erros))


# ----------------------------------------------------------------------
# FONTE DE DADOS:  firebird (local, ao vivo)  ou  postgres (nuvem, snapshot)
# ----------------------------------------------------------------------
# Local (na rede da empresa): le direto do Firebird, em tempo real.
# Nuvem (Streamlit Cloud): le um snapshot gravado no Postgres/Neon pelo
# sync_dados.py — o Firebird nunca fica exposto na internet.
def _cfg(nome, padrao=""):
    """Le config de variavel de ambiente ou dos secrets do Streamlit."""
    v = os.getenv(nome, "")
    if v:
        return v
    try:
        return str(st.secrets[nome])
    except Exception:  # noqa: BLE001
        return padrao


FONTE_DADOS = _cfg("FONTE_DADOS", "firebird").lower()


def _pg_url():
    url = _cfg("PG_URL")
    if not url:
        raise ConnectionError("PG_URL nao configurada (secrets ou variavel de ambiente).")
    return url


@st.cache_resource(show_spinner=False)
def _engine_pg():
    from sqlalchemy import create_engine
    return create_engine(_pg_url(), pool_pre_ping=True)


def _fetch_fb(sqls):
    """Roda os SQLs no Firebird (read-only) e devolve [DataFrames], dsn."""
    con, dsn = conectar()
    out = []
    try:
        cur = con.transaction_manager(READONLY_TPB).cursor()
        for sql in sqls:
            cur.execute(sql)
            cols = [d[0].strip() for d in cur.description]
            out.append(pd.DataFrame(
                [[_f(v) for v in r] for r in cur.fetchall()], columns=cols))
    finally:
        con.close()
    return out, dsn


def _origem_pg():
    """Rotulo de origem (data do snapshot) para a fonte postgres."""
    try:
        m = pd.read_sql("SELECT atualizado_em FROM snap_meta", _engine_pg())
        ts = pd.to_datetime(m["atualizado_em"].iloc[0])
        return f"snapshot Neon de {ts.strftime('%d/%m/%Y %H:%M')}"
    except Exception:  # noqa: BLE001
        return "snapshot Neon"


# ----------------------------------------------------------------------
# CONSULTAS  (fonte unica em consultas.py — compartilhada com sync_dados.py)
# ----------------------------------------------------------------------
from consultas import SQL_ITENS, SQL_MOVTO, SQL_FIN  # noqa: E402


def _f(v):
    return float(v) if isinstance(v, Decimal) else v


def fator_kg(cd):
    try:
        return FATOR_KG.get(int(cd), 1.0)
    except (ValueError, TypeError):
        return 1.0


def extrai_uf(serie):
    return serie.str.extract(r"-\s*([A-Za-z]{2})\s*$", expand=False).str.upper()


@st.cache_data(ttl=300, show_spinner="Carregando dados...")
def carregar_dados(_tick=0):
    if FONTE_DADOS == "postgres":
        eng = _engine_pg()
        itens = pd.read_sql("SELECT * FROM snap_itens", eng)
        movto = pd.read_sql("SELECT * FROM snap_movto", eng)
        dsn = _origem_pg()
    else:
        (itens, movto), dsn = _fetch_fb([SQL_ITENS, SQL_MOVTO])

    for c in ("QT_CONTRATADA", "VL_UNITARIO", "VL_CONTRATADO"):
        itens[c] = pd.to_numeric(itens[c], errors="coerce").fillna(0.0)
    for c in ("QT_FAT", "VL_FAT"):
        movto[c] = pd.to_numeric(movto[c], errors="coerce").fillna(0.0)
    itens["DATA_CONTRATO"] = pd.to_datetime(itens["DATA_CONTRATO"], errors="coerce")
    itens["DATA_LIMITE"] = pd.to_datetime(itens["DATA_LIMITE"], errors="coerce")
    movto["DATA_FAT"] = pd.to_datetime(movto["DATA_FAT"], errors="coerce")
    for c in ("CLIENTE", "PRODUTOR", "SAFRA", "PRODUTO", "UNIDADE", "NUMERO",
              "OBSERVACAO"):
        itens[c] = itens[c].fillna("").astype(str).str.strip()

    # moeda
    itens["MOEDA"] = itens["CD_MOEDA"].map(lambda m: MOEDA_SIMB.get(int(m) if pd.notna(m)
                                                                    else 1, "?"))
    # produtor / UF
    itens["UF"] = extrai_uf(itens["PRODUTOR"]).fillna("S/UF")
    itens["PRODUTOR_NOME"] = (
        itens["PRODUTOR"].str.replace(r"\s*-\s*[A-Za-z]{2}\s*$", "", regex=True).str.strip())
    itens["ESTADO"] = itens["UF"].map(lambda u: UF_NOMES.get(u, u))

    # volume contratado em KG
    itens["QT_CONTR_KG"] = itens["QT_CONTRATADA"] * itens["CD_UNIDADE"].map(fator_kg)

    # FATURADO por item (apenas NF): valor R$ (exato) e volume em kg
    fat = movto[movto["ID_NOTA_ITEM"].notna()].copy()
    fat["QT_FAT_KG"] = fat["QT_FAT"] * fat["ID_UNIDADE"].map(fator_kg)
    fat_item = (fat.groupby("ID_ITEM_CONTRATO")
                .agg(QT_FAT_KG=("QT_FAT_KG", "sum"), VL_FATURADO=("VL_FAT", "sum"))
                .reset_index())
    itens = itens.merge(fat_item, on="ID_ITEM_CONTRATO", how="left")
    itens["QT_FAT_KG"] = itens["QT_FAT_KG"].fillna(0.0)
    itens["VL_FATURADO"] = itens["VL_FATURADO"].fillna(0.0)

    # preco realizado R$/kg: item -> produto -> global
    with np.errstate(divide="ignore", invalid="ignore"):
        preco_item = np.where(itens["QT_FAT_KG"] > 0,
                              itens["VL_FATURADO"] / itens["QT_FAT_KG"], np.nan)
    itens["PRECO_KG"] = preco_item
    grp = itens.groupby("PRODUTO")[["VL_FATURADO", "QT_FAT_KG"]].sum()
    gp = grp["VL_FATURADO"] / grp["QT_FAT_KG"].replace(0, np.nan)
    itens["PRECO_KG"] = itens["PRECO_KG"].fillna(itens["PRODUTO"].map(gp))
    g_preco = (itens["VL_FATURADO"].sum() / itens["QT_FAT_KG"].sum()
               if itens["QT_FAT_KG"].sum() > 0 else 0.0)
    itens["PRECO_KG"] = itens["PRECO_KG"].fillna(g_preco)

    # volumes / saldos
    itens["QT_SALDO_KG"] = (itens["QT_CONTR_KG"] - itens["QT_FAT_KG"]).clip(lower=0)
    itens["QT_EXC_KG"] = (itens["QT_FAT_KG"] - itens["QT_CONTR_KG"]).clip(lower=0)
    itens["SC_CONTR"] = itens["QT_CONTR_KG"] / SACA_KG
    itens["SC_FAT"] = itens["QT_FAT_KG"] / SACA_KG
    itens["SC_SALDO"] = itens["QT_SALDO_KG"] / SACA_KG
    # valor a faturar R$:
    #   contratos em R$  -> EXATO  = contratado - faturado (ambos em R$)
    #   contratos em US$ -> ESTIM. = volume restante x preco realizado (R$/kg)
    brl_mask = itens["MOEDA"] == "R$"
    exato = (itens["VL_CONTRATADO"] - itens["VL_FATURADO"]).clip(lower=0)
    estim = itens["QT_SALDO_KG"] * itens["PRECO_KG"]
    itens["VL_AF_EST"] = np.where(brl_mask, exato, estim).round(2)
    itens["PCT_FAT"] = (itens["QT_FAT_KG"] / itens["QT_CONTR_KG"].replace(0, pd.NA)
                        * 100).fillna(0.0).round(1)

    def _sit(r):
        if pd.notna(r["DATA_CANCELAMENTO"]):
            return "Cancelado"
        if r["QT_FAT_KG"] <= 0.5:
            return "A faturar"
        tol = max(1.0, r["QT_CONTR_KG"] * 0.01)
        if r["QT_FAT_KG"] > r["QT_CONTR_KG"] + tol:
            return "Excedente"
        if r["QT_SALDO_KG"] <= tol:
            return "Faturado"
        return "Parcial"

    itens["SITUACAO"] = itens.apply(_sit, axis=1)
    return itens, movto, dsn


@st.cache_data(ttl=300, show_spinner="Carregando recebíveis (financeiro)...")
def carregar_financeiro(_tick=0):
    """Parcelas de titulos a receber ligadas a contratos de venda (read-only)."""
    if FONTE_DADOS == "postgres":
        fin = pd.read_sql("SELECT * FROM snap_fin", _engine_pg())
    else:
        (fin,), _ = _fetch_fb([SQL_FIN])

    if fin.empty:
        return fin
    # dedup defensivo: mesma parcela pode vir pelos dois caminhos do UNION
    fin = fin.drop_duplicates(subset=["CONTRATO_ID", "NR_SEQ_GEN"])
    for c in ("VL_PARCELA", "VL_REC_PAG"):
        fin[c] = pd.to_numeric(fin[c], errors="coerce").fillna(0.0)
    fin["DATA_VENCIMENTO"] = pd.to_datetime(fin["DATA_VENCIMENTO"], errors="coerce")
    fin["DATA_QUITACAO"] = pd.to_datetime(fin["DATA_QUITACAO"], errors="coerce")
    fin["NR_DOCUMENTO"] = fin["NR_DOCUMENTO"].fillna("").astype(str).str.strip()
    fin["MOEDA"] = fin["CD_MOEDA"].map(
        lambda m: MOEDA_SIMB.get(int(m) if pd.notna(m) else 1, "?"))
    # saldo a receber (nao negativo) e situacao do titulo
    fin["VL_SALDO"] = (fin["VL_PARCELA"] - fin["VL_REC_PAG"]).clip(lower=0)

    hoje = pd.Timestamp.now().normalize()

    def _sit_fin(r):
        if r["VL_SALDO"] <= 0.01:
            return "Quitado"
        venc = r["DATA_VENCIMENTO"]
        atrasado = pd.notna(venc) and venc < hoje
        if r["VL_REC_PAG"] > 0.01:
            return "Parcial vencido" if atrasado else "Parcial"
        return "Vencido" if atrasado else "A vencer"

    fin["SITUACAO_FIN"] = fin.apply(_sit_fin, axis=1)

    def _aging(r):
        if r["VL_SALDO"] <= 0.01:
            return "Quitado"
        venc = r["DATA_VENCIMENTO"]
        if pd.isna(venc):
            return "Sem venc."
        dias = (hoje - venc).days
        if dias <= 0:
            return "A vencer"
        if dias <= 30:
            return "Vencido 1-30d"
        if dias <= 60:
            return "Vencido 31-60d"
        return "Vencido 60+d"

    fin["AGING"] = fin.apply(_aging, axis=1)
    return fin


# ----------------------------------------------------------------------
# FORMATACAO pt-BR
# ----------------------------------------------------------------------
def brl(v):
    try:
        return ("R$ {:,.2f}".format(float(v))).replace(",", "X").replace(
            ".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return "R$ 0,00"


def num(v, casas=0):
    try:
        s = ("{:,." + str(casas) + "f}").format(float(v))
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return "0"


def valor_moeda(v, simb):
    """Formata no padrao da moeda do contrato (R$ ou US$)."""
    base = num(v, 2)
    return f"{simb} {base}" if simb and simb != "R$" else f"R$ {base}"


def fmt_df(d, brl_cols=(), qt_cols=(), pct_cols=()):
    d = d.copy()
    for c in brl_cols:
        if c in d.columns:
            d[c] = d[c].map(brl)
    for c in qt_cols:
        if c in d.columns:
            d[c] = d[c].map(lambda x: num(x, 0))
    for c in pct_cols:
        if c in d.columns:
            d[c] = d[c].map(lambda x: ("{:.1f}".format(float(x)) + "%").replace(".", ","))
    return d


def achar_logo():
    for pat in ("logo.png", "logo.jpg", "logo.jpeg", "Logotipo*.png",
                "logo*.png", "*logo*.png", "*logo*.jpg"):
        hits = glob.glob(pat)
        if hits:
            return hits[0]
    for pdf in glob.glob("*ogotipo*.pdf") + glob.glob("*logo*.pdf"):
        try:
            import fitz
            doc = fitz.open(pdf)
            doc[0].get_pixmap(dpi=150).save("logo.png")
            return "logo.png"
        except Exception:  # noqa: BLE001
            pass
    return None


# ----------------------------------------------------------------------
# LOGIN (so na nuvem) — ativado quando existe a secao [auth] nos secrets.
# Localmente (sem secrets) o painel abre direto, sem fricção.
# ----------------------------------------------------------------------
def _tem_auth():
    try:
        return "auth" in st.secrets
    except Exception:  # noqa: BLE001
        return False


def login_gate():
    if not _tem_auth():
        return
    import streamlit_authenticator as stauth
    cfg = st.secrets["auth"]
    credentials = {"usernames": {
        u: {"name": d.get("name", u), "password": d["password"]}
        for u, d in cfg["credentials"]["usernames"].items()}}
    authenticator = stauth.Authenticate(
        credentials, cfg.get("cookie_name", "frt_dash"),
        cfg.get("cookie_key", "frt_key"),
        int(cfg.get("cookie_expiry_days", 7)))
    try:
        authenticator.login(location="main")          # streamlit-authenticator 0.4+
    except TypeError:
        authenticator.login("Login", "main")          # versoes 0.2 / 0.3
    status = st.session_state.get("authentication_status")
    if status is False:
        st.error("Usuário ou senha inválidos.")
        st.stop()
    if status is None:
        st.info("🔒 Faça login para acessar o painel.")
        st.stop()
    with st.sidebar:
        st.caption(f"Logado: {st.session_state.get('name', '')}")
        try:
            authenticator.logout(location="sidebar")
        except TypeError:
            authenticator.logout("Sair", "sidebar")


# ----------------------------------------------------------------------
# TOKEN (acesso via Portal) — quando exposto na internet pelo tunel.
# O Portal injeta ?token=... no iframe; sem o token certo, o painel nao abre.
# O segredo fica so na config deste PC (DASH_TOKEN) e nos Secrets do Portal —
# nunca no repositorio nem no banco do Portal.
# ----------------------------------------------------------------------
def token_gate():
    esperado = _cfg("DASH_TOKEN", "")
    if not esperado:
        return  # sem token configurado (ex.: uso local): nao exige
    try:
        recebido = st.query_params.get("token", "")
    except Exception:  # noqa: BLE001  (Streamlit antigo)
        recebido = st.experimental_get_query_params().get("token", [""])[0]
    if recebido != esperado:
        st.error("🔒 Acesso negado. Abra este painel pelo Portal.")
        st.stop()


# ----------------------------------------------------------------------
# APP
# ----------------------------------------------------------------------
st.set_page_config(page_title="Contratos de Venda - Grupo FRT",
                   page_icon="🌾", layout="wide",
                   initial_sidebar_state="expanded")
token_gate()
login_gate()
st.markdown(
    f"""<style>
      .stApp {{ background-color:#FFFFFF; }}
      [data-testid="stMetric"] {{ background:{VERDE_PALIDO}33;
          border:1px solid {VERDE_PALIDO}; border-radius:12px; padding:12px 16px;
          min-height:120px; display:flex; flex-direction:column; justify-content:center; }}
      [data-testid="stMetricValue"] {{ color:{VERDE_ESCURO}; font-size:1.35rem; }}
      h1,h2,h3 {{ color:{VERDE_ESCURO}; }}

      /* ---- Sidebar verde-escuro (estilo app) ---- */
      [data-testid="stSidebar"] {{
          background: linear-gradient(180deg, #14401E 0%, #0E2F16 100%);
      }}
      /* cabecalhos da sidebar (Atualizacao, Filtros, Exportar) em branco */
      [data-testid="stSidebar"] h1,
      [data-testid="stSidebar"] h2,
      [data-testid="stSidebar"] h3 {{ color:#FFFFFF !important; }}
      /* rotulos dos filtros e legendas em verde-claro */
      [data-testid="stSidebar"] label,
      [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
      [data-testid="stSidebar"] [data-testid="stCaptionContainer"],
      [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {{ color:#DCEBD6 !important; }}
      /* slider: valor atual e rotulos das pontas */
      [data-testid="stSidebar"] [data-testid="stThumbValue"] {{ color:#FFFFFF !important; }}
      [data-testid="stSidebar"] [data-testid="stTickBarMin"],
      [data-testid="stSidebar"] [data-testid="stTickBarMax"] {{ color:#9CC79C !important; }}
      /* divisorias claras */
      [data-testid="stSidebar"] hr {{ border-color: rgba(255,255,255,.12); }}
      /* logo no topo da sidebar dentro de cartao branco */
      [data-testid="stSidebar"] [data-testid="stImage"] {{
          background:#FFFFFF; border-radius:14px; padding:10px 12px;
          box-shadow:0 2px 8px rgba(0,0,0,.18);
      }}
    </style>""", unsafe_allow_html=True)

_logo = achar_logo()
if _logo:
    st.image(_logo, width=440)
else:
    st.markdown(
        f"<div style='background:{VERDE_ESCURO};color:#fff;border-radius:12px;"
        f"padding:12px 20px;display:inline-block;font-weight:700'>"
        f"🌾🐂 Grupo Fernando Ribas Taques</div>", unsafe_allow_html=True)
st.markdown(
    "<h1 style='margin:6px 0 0 0'>Contratos de Venda — Faturado x A Faturar</h1>"
    f"<p style='color:{VERDE};font-size:15px;margin-top:2px'>"
    "Volume em kg e sacas (60 kg) • valores em Real e Dólar</p>",
    unsafe_allow_html=True)

# ---- Logo no topo da sidebar (cartao branco, estilo app) ----
if _logo:
    st.sidebar.image(_logo, use_container_width=True)

# ---- Atualizacao automatica ----
st.sidebar.header("Atualização")
# Padrao DESLIGADO: o snapshot na nuvem so muda de hora em hora, e o auto-refresh
# "congela" a tela (filtros inacessiveis) a cada recarga. Ligue se quiser.
auto = st.sidebar.checkbox("Atualizar automaticamente", value=False)
intervalo = st.sidebar.select_slider("Intervalo", options=[1, 5, 10, 15, 30],
                                     value=5, format_func=lambda m: f"{m} min")
tick = 0
if auto:
    if TEM_AUTORF:
        tick = st_autorefresh(interval=intervalo * 60 * 1000, key="autorf")
    else:
        st.sidebar.warning("Rode: pip install streamlit-autorefresh")
if st.sidebar.button("🔄 Atualizar agora"):
    # recarrega os dados e RESSINCRONIZA os filtros (bump da versao -> widgets
    # voltam aos defaults derivados dos dados novos, ex.: safra mais recente).
    st.session_state["filtros_v"] = st.session_state.get("filtros_v", 0) + 1
    carregar_dados.clear()
    carregar_financeiro.clear()
    st.rerun()

try:
    itens, movto, dsn = carregar_dados(tick if auto else 0)
except Exception as e:  # noqa: BLE001
    st.error("Falha ao carregar os dados do banco.")
    st.code(str(e))
    st.stop()

st.caption(f"Conectado em `{dsn}`  •  {len(itens):,} itens  •  "
           f"auto-atualização: {'ligada ('+str(intervalo)+' min)' if auto else 'desligada'}")

# ---- Filtros ----
# Versao dos filtros: ao clicar "Atualizar agora" ela muda, trocando as chaves
# dos widgets e fazendo-os voltar aos defaults derivados dos dados recem-lidos.
fv = st.session_state.get("filtros_v", 0)
st.sidebar.header("Filtros")
tipos = sorted(t for t in itens["TIPO_ES"].dropna().unique())
rotulo_tipo = {"S": "Saída (Venda)", "E": "Entrada (Compra)"}
sel_tipo = st.sidebar.multiselect("Tipo de contrato", options=tipos,
                                  default=["S"] if "S" in tipos else tipos,
                                  format_func=lambda t: rotulo_tipo.get(t, t),
                                  key=f"f_tipo_{fv}")
incluir_cancelados = st.sidebar.checkbox("Incluir cancelados", value=False,
                                         key=f"f_canc_{fv}")
moedas = sorted(itens["MOEDA"].unique())
sel_moeda = st.sidebar.multiselect("Moeda", options=moedas, default=moedas,
                                   key=f"f_moeda_{fv}")
ufs = sorted(u for u in itens["UF"].unique() if u)
sel_uf = st.sidebar.multiselect(
    "Estado (UF)", options=ufs, default=ufs,
    format_func=lambda u: "Sem UF" if u == "S/UF" else f"{u} — {UF_NOMES.get(u, u)}",
    key=f"f_uf_{fv}")
produtores = sorted(p for p in itens["PRODUTOR"].unique() if p)
sel_prod = st.sidebar.multiselect("Produtor", options=produtores, default=[],
                                  key=f"f_prod_{fv}")
safras = sorted(s for s in itens["SAFRA"].unique() if s)
# Padrao: abre so com a safra mais recente marcada (nao todas), para o painel
# vir focado na safra atual ao atualizar. O usuario pode marcar as demais.
sel_safra = st.sidebar.multiselect("Safra", options=safras,
                                   default=[safras[-1]] if safras else [],
                                   key=f"f_safra_{fv}")
clientes = sorted(c for c in itens["CLIENTE"].unique() if c)
sel_cliente = st.sidebar.multiselect("Cliente", options=clientes, default=[],
                                     key=f"f_cliente_{fv}")
produtos_lista = sorted(p for p in itens["PRODUTO"].unique() if p)
sel_produto = st.sidebar.multiselect("Produto", options=produtos_lista, default=[],
                                     key=f"f_produto_{fv}")
sit_opcoes = ["A faturar", "Parcial", "Faturado", "Excedente", "Cancelado"]
sel_sit = st.sidebar.multiselect(
    "Situação", options=sit_opcoes,
    default=["A faturar", "Parcial", "Faturado", "Excedente"], key=f"f_sit_{fv}")
busca = st.sidebar.text_input("Buscar (nº / produto / obs)", key=f"f_busca_{fv}")

df = itens.copy()
if sel_tipo:
    df = df[df["TIPO_ES"].isin(sel_tipo)]
if not incluir_cancelados:
    df = df[df["DATA_CANCELAMENTO"].isna()]
if sel_moeda:
    df = df[df["MOEDA"].isin(sel_moeda)]
if sel_uf:
    df = df[df["UF"].isin(sel_uf)]
if sel_prod:
    df = df[df["PRODUTOR"].isin(sel_prod)]
if sel_safra:
    df = df[df["SAFRA"].isin(sel_safra)]
if sel_cliente:
    df = df[df["CLIENTE"].isin(sel_cliente)]
if sel_produto:
    df = df[df["PRODUTO"].isin(sel_produto)]
if sel_sit:
    df = df[df["SITUACAO"].isin(sel_sit)]
if busca:
    b = busca.strip().lower()
    df = df[df["NUMERO"].str.lower().str.contains(b, na=False)
            | df["PRODUTO"].str.lower().str.contains(b, na=False)
            | df["OBSERVACAO"].str.lower().str.contains(b, na=False)]

if df.empty:
    st.warning("Nenhum item de contrato para os filtros selecionados.")
    st.stop()

# filtra os movimentos de faturamento pelos ITENS exibidos (respeita todos os
# filtros, inclusive Produto/Situação) — assim o faturamento bate com o KPI.
mv_f = movto[movto["ID_NOTA_ITEM"].notna()
             & movto["ID_ITEM_CONTRATO"].isin(set(df["ID_ITEM_CONTRATO"].unique()))].copy()

# ---- KPIs ----
sc_contr = df["SC_CONTR"].sum()
sc_fat = df["SC_FAT"].sum()
sc_saldo = df["SC_SALDO"].sum()
vl_fat = df["VL_FATURADO"].sum()
vl_af = df["VL_AF_EST"].sum()
pct_vol = (sc_fat / sc_contr * 100) if sc_contr else 0

fat_semana = 0.0
rot_sem = "—"
if not mv_f.empty:
    mv_f["SEMANA"] = mv_f["DATA_FAT"].dt.to_period("W-SUN").apply(lambda p: p.start_time)
    ser = mv_f.groupby("SEMANA")["VL_FAT"].sum().sort_index()
    fat_semana = float(ser.iloc[-1])
    rot_sem = ser.index[-1].strftime("sem. %d/%m/%Y")

r1 = st.columns(3)
r1[0].metric("Volume contratado", f"{num(sc_contr)} sc",
             f"{num(sc_contr*SACA_KG/1000)} t", delta_color="off")
r1[1].metric("Volume faturado", f"{num(sc_fat)} sc", f"{pct_vol:.1f}% do contratado",
             delta_color="off")
r1[2].metric("Falta faturar (volume)", f"{num(sc_saldo)} sc",
             f"{num(sc_saldo*SACA_KG)} kg", delta_color="off")
r2 = st.columns(3)
r2[0].metric("Faturado (R$)", brl(vl_fat))
r2[1].metric("Falta faturar (R$ est.)", brl(vl_af), "volume × preço realizado",
             delta_color="off")
r2[2].metric("Faturado na semana", brl(fat_semana), rot_sem, delta_color="off")

n_usd = int((df["MOEDA"] != "R$").sum())
st.caption((
    f"**{df['CONTRATO_ID'].nunique()} contratos · {len(df):,} itens** "
    f"({n_usd} em dólar). O volume em sacas é exato — independe de moeda. "
    "**Faturado** é o valor real das notas fiscais. **Falta faturar (R$)** é exato "
    "nos contratos em Real e estimado nos contratos em dólar (volume restante × "
    "preço médio realizado, que já embute o câmbio). 1 saca = 60 kg."
).replace("$", "\\$"))
st.divider()


def agrega_contrato(d):
    g = (d.groupby(["CONTRATO_ID", "NUMERO", "DATA_CONTRATO", "CLIENTE",
                    "PRODUTOR_NOME", "UF", "SAFRA", "MOEDA"], dropna=False)
         .agg(SC_CONTR=("SC_CONTR", "sum"), SC_FAT=("SC_FAT", "sum"),
              SC_SALDO=("SC_SALDO", "sum"), VL_CONTRATADO=("VL_CONTRATADO", "sum"),
              VL_FATURADO=("VL_FATURADO", "sum"), VL_AF_EST=("VL_AF_EST", "sum"),
              QT_CONTR_KG=("QT_CONTR_KG", "sum"), QT_FAT_KG=("QT_FAT_KG", "sum"),
              ITENS=("ID_ITEM_CONTRATO", "nunique"),
              CANCELADO=("DATA_CANCELAMENTO", lambda s: s.notna().any()))
         .reset_index())
    g["PCT_FAT"] = (g["QT_FAT_KG"] / g["QT_CONTR_KG"].replace(0, pd.NA) * 100).fillna(0).round(1)

    def _sit(r):
        if r["CANCELADO"]:
            return "Cancelado"
        if r["QT_FAT_KG"] <= 0.5:
            return "A faturar"
        tol = max(1.0, r["QT_CONTR_KG"] * 0.01)
        if r["QT_FAT_KG"] > r["QT_CONTR_KG"] + tol:
            return "Excedente"
        if (r["QT_CONTR_KG"] - r["QT_FAT_KG"]) <= tol:
            return "Faturado"
        return "Parcial"

    g["SITUACAO"] = g.apply(_sit, axis=1)
    g["CONTRATADO_FMT"] = [valor_moeda(v, m) for v, m in zip(g["VL_CONTRATADO"], g["MOEDA"])]
    return g.sort_values("SC_SALDO", ascending=False)


contratos = agrega_contrato(df)
PX = dict(template="plotly_white")


def estilo(fig):
    """Padroniza as caixas de hover dos graficos: numeros em pt-BR (1.162.688),
    sem sufixo SI (nada de '1.16M'), e caixa branca elegante com a fonte da marca."""
    fig.update_layout(
        separators=",.",                       # decimal ',' e milhar '.'
        font=dict(family="Inter, 'Segoe UI', Arial, sans-serif", color=VERDE_ESCURO),
        hoverlabel=dict(
            bgcolor="white", bordercolor=VERDE_CLARO, align="left",
            font=dict(family="Inter, 'Segoe UI', Arial, sans-serif",
                      size=13, color=VERDE_ESCURO)),
        legend=dict(title_font=dict(color=VERDE_ESCURO)),
    )
    # barras/linhas: o numero segue o hoverformat do eixo de valor
    fig.update_xaxes(hoverformat=",.0f")
    fig.update_yaxes(hoverformat=",.0f")
    # pizzas: valor + percentual formatados
    fig.update_traces(
        selector=dict(type="pie"),
        hovertemplate="<b>%{label}</b><br>%{value:,.0f}  •  %{percent}<extra></extra>")
    return fig

(ab_geral, ab_contr, ab_prod, ab_cli, ab_item, ab_fat, ab_ent,
 ab_fin) = st.tabs(
    ["📊 Visão Geral", "📑 Contratos", "🧑‍🌾 Produtores / Estados",
     "🏢 Clientes", "🌾 Produtos", "🧾 Faturamento (semana)",
     "📅 Prazos de Entrega", "💰 Conciliação Financeira"])

# ======== VISAO GERAL ========
with ab_geral:
    modo = st.radio("Medida dos gráficos", ["Sacas (volume)", "R$ (valor)"],
                    horizontal=True)
    if modo.startswith("Sacas"):
        cfat, csaldo, ytit = "SC_FAT", "SC_SALDO", "Sacas"
    else:
        cfat, csaldo, ytit = "VL_FATURADO", "VL_AF_EST", "R$"

    c1, c2 = st.columns(2)
    ps = (df.groupby("SAFRA")[[cfat, csaldo]].sum().reset_index()
          .rename(columns={cfat: "Faturado", csaldo: "A Faturar"}))
    ps = ps[ps[["Faturado", "A Faturar"]].sum(axis=1) > 0]
    fig = px.bar(ps.melt("SAFRA", var_name="Tipo", value_name=ytit), x="SAFRA",
                 y=ytit, color="Tipo", barmode="stack",
                 title=f"Faturado x A Faturar por Safra ({ytit})",
                 color_discrete_map={"Faturado": COR_FATURADO, "A Faturar": COR_A_FATURAR},
                 **PX)
    c1.plotly_chart(estilo(fig), width="stretch")

    sc = contratos.groupby("SITUACAO")["SC_CONTR"].sum().reset_index()
    fig = px.pie(sc, names="SITUACAO", values="SC_CONTR", hole=0.45,
                 title="Volume contratado por situação", color="SITUACAO",
                 color_discrete_map=COR_SITUACAO, **PX)
    c2.plotly_chart(estilo(fig), width="stretch")

    pe = (df.groupby("ESTADO")[[cfat, csaldo]].sum().reset_index()
          .rename(columns={cfat: "Faturado", csaldo: "A Faturar"})
          .sort_values("Faturado"))
    fig = px.bar(pe.melt("ESTADO", var_name="Tipo", value_name=ytit), x=ytit,
                 y="ESTADO", color="Tipo", orientation="h", barmode="group",
                 title=f"Faturado x A Faturar por Estado ({ytit})",
                 color_discrete_map={"Faturado": COR_FATURADO, "A Faturar": COR_A_FATURAR},
                 **PX)
    st.plotly_chart(estilo(fig), width="stretch")

    if not mv_f.empty:
        m = mv_f.copy()
        m["MES"] = m["DATA_FAT"].dt.to_period("M").dt.to_timestamp()
        serie = m.groupby("MES")["VL_FAT"].sum().reset_index()
        fig = px.bar(serie, x="MES", y="VL_FAT",
                     title="Faturamento mensal R$ (NF emitidas)", **PX)
        fig.update_traces(marker_color=VERDE)
        st.plotly_chart(estilo(fig), width="stretch")

# ======== CONTRATOS ========
with ab_contr:
    st.subheader("Contratos")
    tv = contratos.copy()
    tv["DATA_CONTRATO"] = tv["DATA_CONTRATO"].dt.strftime("%d/%m/%Y")
    tv = tv.rename(columns={
        "NUMERO": "Número", "DATA_CONTRATO": "Data", "CLIENTE": "Cliente",
        "PRODUTOR_NOME": "Produtor", "SAFRA": "Safra", "MOEDA": "Moeda",
        "CONTRATADO_FMT": "Contratado", "SC_CONTR": "Sc Contr.", "SC_FAT": "Sc Fat.",
        "SC_SALDO": "Sc a Faturar", "VL_FATURADO": "Faturado R$",
        "VL_AF_EST": "A Faturar R$ (est.)", "PCT_FAT": "% Fat.", "SITUACAO": "Situação"})
    cols = ["Número", "Data", "Cliente", "Produtor", "UF", "Safra", "Moeda",
            "Contratado", "Sc Contr.", "Sc Fat.", "Sc a Faturar", "Faturado R$",
            "A Faturar R$ (est.)", "% Fat.", "Situação"]
    st.dataframe(fmt_df(tv[cols], brl_cols=["Faturado R$", "A Faturar R$ (est.)"],
                        qt_cols=["Sc Contr.", "Sc Fat.", "Sc a Faturar"],
                        pct_cols=["% Fat."]), width="stretch", hide_index=True)

    st.markdown("#### 🔎 Itens de todos os contratos (por produto)")
    st.caption("Um item por contrato, ordenado por produto. Respeita os filtros da "
               "barra lateral — use o filtro **Produto** para focar (ex.: MILHETO).")
    it_all = df.copy()
    it_all["PRECO_SC"] = it_all["PRECO_KG"] * SACA_KG
    it_all = it_all.sort_values(["PRODUTO", "SC_SALDO"], ascending=[True, False])
    itv = it_all.rename(columns={
        "PRODUTO": "Produto", "NUMERO": "Contrato", "CLIENTE": "Cliente",
        "PRODUTOR_NOME": "Produtor", "SAFRA": "Safra", "MOEDA": "Moeda",
        "QT_CONTR_KG": "Kg Contr.", "QT_FAT_KG": "Kg Fat.", "SC_CONTR": "Sc Contr.",
        "SC_FAT": "Sc Fat.", "SC_SALDO": "Sc a Faturar", "PRECO_SC": "R$/sc (realiz.)",
        "VL_FATURADO": "Faturado R$", "VL_AF_EST": "A Faturar R$ (est.)",
        "SITUACAO": "Situação"})
    colt = ["Produto", "Contrato", "Cliente", "Produtor", "UF", "Safra", "Moeda",
            "Kg Contr.", "Kg Fat.", "Sc Contr.", "Sc Fat.", "Sc a Faturar",
            "R$/sc (realiz.)", "Faturado R$", "A Faturar R$ (est.)", "Situação"]
    st.caption(f"{len(itv)} itens  •  {itv['Produto'].nunique()} produtos")
    st.dataframe(
        fmt_df(itv[colt], brl_cols=["R$/sc (realiz.)", "Faturado R$",
                                    "A Faturar R$ (est.)"],
               qt_cols=["Kg Contr.", "Kg Fat.", "Sc Contr.", "Sc Fat.", "Sc a Faturar"]),
        width="stretch", hide_index=True)

    st.markdown("#### 📄 Detalhe de um contrato (itens + notas fiscais)")
    rot_ctr = {r.CONTRATO_ID: f"{r.NUMERO} • {r.CLIENTE} • {num(r.SC_SALDO)} sc a faturar"
               for r in contratos.itertuples()}
    cid = st.selectbox("Selecione um contrato", options=list(rot_ctr.keys()),
                       format_func=lambda i: rot_ctr[i])
    itens_c = df[df["CONTRATO_ID"] == cid].copy()
    cab = itens_c.iloc[0]
    st.markdown((f"**Produtor:** {cab['PRODUTOR']} • **Cliente:** {cab['CLIENTE']} • "
                 f"**Safra:** {cab['SAFRA']} • **Moeda:** {cab['MOEDA']}"
                 ).replace("$", "\\$"))
    if cab["OBSERVACAO"]:
        st.info(("**Obs.:** " + cab["OBSERVACAO"]).replace("$", "\\$"))
    itens_c["PRECO_SC"] = itens_c["PRECO_KG"] * SACA_KG
    iv = itens_c.rename(columns={
        "PRODUTO": "Produto", "SC_CONTR": "Sc Contr.", "SC_FAT": "Sc Fat.",
        "SC_SALDO": "Sc a Faturar", "QT_CONTR_KG": "Kg Contr.", "QT_FAT_KG": "Kg Fat.",
        "PRECO_SC": "R$/sc (realiz.)", "VL_FATURADO": "Faturado R$",
        "VL_AF_EST": "A Faturar R$ (est.)", "SITUACAO": "Situação"})[
        ["Produto", "Kg Contr.", "Kg Fat.", "Sc Contr.", "Sc Fat.", "Sc a Faturar",
         "R$/sc (realiz.)", "Faturado R$", "A Faturar R$ (est.)", "Situação"]]
    st.dataframe(fmt_df(iv, brl_cols=["R$/sc (realiz.)", "Faturado R$",
                                      "A Faturar R$ (est.)"],
                        qt_cols=["Kg Contr.", "Kg Fat.", "Sc Contr.", "Sc Fat.",
                                 "Sc a Faturar"]), width="stretch", hide_index=True)

    nfs = movto[(movto["CONTRATO_ID"] == cid) & movto["ID_NOTA_ITEM"].notna()].copy()
    if not nfs.empty:
        nfs["DATA_FAT"] = nfs["DATA_FAT"].dt.strftime("%d/%m/%Y")
        nfs["SC"] = nfs["QT_FAT"] * nfs["ID_UNIDADE"].map(fator_kg) / SACA_KG
        st.markdown("##### 🧾 Notas fiscais faturadas neste contrato")
        st.dataframe(fmt_df(nfs.rename(columns={
            "DATA_FAT": "Data", "NR_DOCUMENTO": "Documento", "SC": "Sacas",
            "VL_FAT": "Valor R$"})[["Data", "Documento", "Sacas", "Valor R$"]],
            brl_cols=["Valor R$"], qt_cols=["Sacas"]), width="stretch", hide_index=True)

# ======== PRODUTORES / ESTADOS ========
with ab_prod:
    st.subheader("Faturamento por Produtor")
    pp = (df.groupby(["PRODUTOR", "UF"])
          .agg(SC_FAT=("SC_FAT", "sum"), SC_SALDO=("SC_SALDO", "sum"),
               VL_FATURADO=("VL_FATURADO", "sum"), VL_AF_EST=("VL_AF_EST", "sum"))
          .reset_index().sort_values("VL_FATURADO", ascending=False))
    plot = pp.sort_values("VL_FATURADO").rename(
        columns={"VL_FATURADO": "Faturado", "VL_AF_EST": "A Faturar"})
    fig = px.bar(plot.melt(["PRODUTOR", "UF"], value_vars=["Faturado", "A Faturar"],
                           var_name="Tipo", value_name="R$"),
                 x="R$", y="PRODUTOR", color="Tipo", orientation="h",
                 title="Faturado x A Faturar por Produtor (R$)",
                 color_discrete_map={"Faturado": COR_FATURADO, "A Faturar": COR_A_FATURAR},
                 **PX)
    fig.update_layout(height=450)
    st.plotly_chart(estilo(fig), width="stretch")
    st.dataframe(fmt_df(pp.rename(columns={
        "PRODUTOR": "Produtor", "SC_FAT": "Sc Faturadas", "SC_SALDO": "Sc a Faturar",
        "VL_FATURADO": "Faturado R$", "VL_AF_EST": "A Faturar R$ (est.)"}),
        brl_cols=["Faturado R$", "A Faturar R$ (est.)"],
        qt_cols=["Sc Faturadas", "Sc a Faturar"]), width="stretch", hide_index=True)

    st.subheader("Resumo por Estado (UF)")
    pest = (df.groupby(["UF", "ESTADO"])
            .agg(Contratos=("CONTRATO_ID", "nunique"), SC_FAT=("SC_FAT", "sum"),
                 SC_SALDO=("SC_SALDO", "sum"), VL_FATURADO=("VL_FATURADO", "sum"),
                 VL_AF_EST=("VL_AF_EST", "sum"))
            .reset_index().sort_values("VL_FATURADO", ascending=False))
    c1, c2 = st.columns([2, 3])
    fig = px.pie(pest, names="ESTADO", values="SC_FAT", hole=0.45,
                 title="Sacas faturadas por Estado",
                 color_discrete_sequence=[VERDE_ESCURO, VERDE_MEDIO, VERDE_CLARO,
                                          TEAL, VERDE_PALIDO], **PX)
    c1.plotly_chart(estilo(fig), width="stretch")
    c2.dataframe(fmt_df(pest.rename(columns={
        "ESTADO": "Estado", "SC_FAT": "Sc Faturadas", "SC_SALDO": "Sc a Faturar",
        "VL_FATURADO": "Faturado R$", "VL_AF_EST": "A Faturar R$ (est.)"})[
        ["UF", "Estado", "Contratos", "Sc Faturadas", "Sc a Faturar", "Faturado R$",
         "A Faturar R$ (est.)"]], brl_cols=["Faturado R$", "A Faturar R$ (est.)"],
        qt_cols=["Sc Faturadas", "Sc a Faturar"]), width="stretch", hide_index=True)

# ======== CLIENTES ========
with ab_cli:
    pc = (df.groupby("CLIENTE")
          .agg(SC_FAT=("SC_FAT", "sum"), SC_SALDO=("SC_SALDO", "sum"),
               VL_FATURADO=("VL_FATURADO", "sum"), VL_AF_EST=("VL_AF_EST", "sum"))
          .reset_index().sort_values("VL_AF_EST", ascending=False))
    top = pc.head(20).sort_values("VL_AF_EST").rename(
        columns={"VL_FATURADO": "Faturado", "VL_AF_EST": "A Faturar"})
    fig = px.bar(top.melt("CLIENTE", value_vars=["Faturado", "A Faturar"],
                          var_name="Tipo", value_name="R$"),
                 x="R$", y="CLIENTE", color="Tipo", orientation="h",
                 title="Top 20 clientes — Faturado x A Faturar (R$)",
                 color_discrete_map={"Faturado": COR_FATURADO, "A Faturar": COR_A_FATURAR},
                 **PX)
    fig.update_layout(height=600)
    st.plotly_chart(estilo(fig), width="stretch")
    st.dataframe(fmt_df(pc.rename(columns={
        "CLIENTE": "Cliente", "SC_FAT": "Sc Faturadas", "SC_SALDO": "Sc a Faturar",
        "VL_FATURADO": "Faturado R$", "VL_AF_EST": "A Faturar R$ (est.)"}),
        brl_cols=["Faturado R$", "A Faturar R$ (est.)"],
        qt_cols=["Sc Faturadas", "Sc a Faturar"]), width="stretch", hide_index=True)

# ======== PRODUTOS ========
with ab_item:
    pp2 = (df.groupby("PRODUTO")
           .agg(SC_CONTR=("SC_CONTR", "sum"), SC_FAT=("SC_FAT", "sum"),
                SC_SALDO=("SC_SALDO", "sum"), VL_FATURADO=("VL_FATURADO", "sum"),
                VL_AF_EST=("VL_AF_EST", "sum"))
           .reset_index().sort_values("SC_SALDO", ascending=False))
    top = pp2.head(20).sort_values("SC_SALDO").rename(
        columns={"SC_FAT": "Faturado", "SC_SALDO": "A Faturar"})
    fig = px.bar(top.melt("PRODUTO", value_vars=["Faturado", "A Faturar"],
                          var_name="Tipo", value_name="Sacas"),
                 x="Sacas", y="PRODUTO", color="Tipo", orientation="h",
                 title="Produtos — Faturado x A Faturar (sacas)",
                 color_discrete_map={"Faturado": COR_FATURADO, "A Faturar": COR_A_FATURAR},
                 **PX)
    fig.update_layout(height=600)
    st.plotly_chart(estilo(fig), width="stretch")
    st.dataframe(fmt_df(pp2.rename(columns={
        "PRODUTO": "Produto", "SC_CONTR": "Sc Contratadas", "SC_FAT": "Sc Faturadas",
        "SC_SALDO": "Sc a Faturar", "VL_FATURADO": "Faturado R$",
        "VL_AF_EST": "A Faturar R$ (est.)"}),
        brl_cols=["Faturado R$", "A Faturar R$ (est.)"],
        qt_cols=["Sc Contratadas", "Sc Faturadas", "Sc a Faturar"]),
        width="stretch", hide_index=True)

# ======== FATURAMENTO (SEMANA) ========
with ab_fat:
    st.subheader("Faturamento por Semana")
    if mv_f.empty:
        st.info("Sem faturamento para os filtros atuais.")
    else:
        mv_f["SC"] = mv_f["QT_FAT"] * mv_f["ID_UNIDADE"].map(fator_kg) / SACA_KG
        sem = (mv_f.groupby("SEMANA").agg(VL_FAT=("VL_FAT", "sum"), SC=("SC", "sum"))
               .reset_index().sort_values("SEMANA"))
        opc_sem = [2, 4, 6, 8, 12, 16, 26, 52, "Tudo"]
        janela = st.select_slider("Semanas a exibir (últimas N com faturamento)",
                                  options=opc_sem, value=8)
        n = len(sem) if janela == "Tudo" else int(janela)
        sp = sem.tail(n).copy()
        sp["Semana"] = sp["SEMANA"].dt.strftime("%d/%m")
        c1, c2 = st.columns(2)
        fig = px.bar(sp, x="Semana", y="VL_FAT", title="Faturado por semana (R$)", **PX)
        fig.update_traces(marker_color=VERDE)
        c1.plotly_chart(estilo(fig), width="stretch")
        fig = px.bar(sp, x="Semana", y="SC", title="Faturado por semana (sacas)", **PX)
        fig.update_traces(marker_color=VERDE_CLARO)
        c2.plotly_chart(estilo(fig), width="stretch")

        k = st.columns(4)
        k[0].metric("Semana recente (R$)", brl(sem["VL_FAT"].iloc[-1]),
                    sem["SEMANA"].iloc[-1].strftime("sem. %d/%m/%Y"), delta_color="off")
        k[1].metric("Semana recente (sacas)", f"{num(sem['SC'].iloc[-1])} sc",
                    delta_color="off")
        k[2].metric(f"Média/sem. ({n} sem.)", brl(sp["VL_FAT"].mean()),
                    delta_color="off")
        k[3].metric(f"Total período ({n} sem.)", brl(sp["VL_FAT"].sum()),
                    delta_color="off")

        st.markdown("##### Notas fiscais (mais recentes)")
        mvv = mv_f.sort_values("DATA_FAT", ascending=False).head(500).copy()
        mvv["DATA_FAT"] = mvv["DATA_FAT"].dt.strftime("%d/%m/%Y")
        st.dataframe(fmt_df(mvv.rename(columns={
            "DATA_FAT": "Data", "NR_DOCUMENTO": "Documento", "CLIENTE": "Cliente",
            "PRODUTOR": "Produtor", "SAFRA": "Safra", "SC": "Sacas",
            "VL_FAT": "Valor R$"})[["Data", "Documento", "Cliente", "Produtor",
                                    "Safra", "Sacas", "Valor R$"]],
            brl_cols=["Valor R$"], qt_cols=["Sacas"]), width="stretch", hide_index=True)

# ======== PRAZOS DE ENTREGA ========
with ab_ent:
    st.subheader("Prazos de Entrega (data limite dos contratos)")
    hoje = pd.Timestamp.now().normalize()
    ent = df.copy()
    ent["DIAS"] = (ent["DATA_LIMITE"] - hoje).dt.days
    pendente = ent["SC_SALDO"] > 0.5

    def _prazo(r):
        if r["SC_SALDO"] <= 0.5:
            return "Entregue"
        if pd.isna(r["DATA_LIMITE"]):
            return "Sem data"
        if r["DIAS"] < 0:
            return "Vencido"
        if r["DIAS"] <= 30:
            return "Vence ≤30d"
        return "No prazo"
    ent["PRAZO"] = ent.apply(_prazo, axis=1)

    so_pend = st.checkbox("Mostrar apenas itens com saldo a entregar", value=True)
    base = ent[pendente].copy() if so_pend else ent.copy()

    venc = base[base["PRAZO"] == "Vencido"]
    prox = base[base["PRAZO"] == "Vence ≤30d"]
    pend_tot = base[base["SC_SALDO"] > 0.5]
    k = st.columns(4)
    k[0].metric("Itens a entregar", num(len(pend_tot)))
    k[1].metric("Vencidos", f"{num(venc['SC_SALDO'].sum())} sc",
                f"{len(venc)} itens", delta_color="off")
    k[2].metric("Vence em ≤30 dias", f"{num(prox['SC_SALDO'].sum())} sc",
                f"{len(prox)} itens", delta_color="off")
    k[3].metric("Total a entregar", f"{num(pend_tot['SC_SALDO'].sum())} sc",
                delta_color="off")
    st.caption(f"Hoje: {hoje.strftime('%d/%m/%Y')}. 'Dias' negativo = vencido há N dias; "
               "positivo = faltam N dias para o limite.")

    cal = base[(base["SC_SALDO"] > 0.5) & base["DATA_LIMITE"].notna()].copy()
    if not cal.empty:
        cal["MES"] = cal["DATA_LIMITE"].dt.to_period("M").dt.to_timestamp()
        gm = cal.groupby("MES")["SC_SALDO"].sum().reset_index()
        fig = px.bar(gm, x="MES", y="SC_SALDO",
                     title="Sacas a entregar por mês limite", **PX)
        fig.update_traces(marker_color=VERDE)
        st.plotly_chart(estilo(fig), width="stretch")

    icone = {"Vencido": "🔴 Vencido", "Vence ≤30d": "🟡 Vence ≤30d",
             "No prazo": "🟢 No prazo", "Sem data": "⚪ Sem data",
             "Entregue": "✅ Entregue"}
    tab = base.sort_values("DATA_LIMITE", na_position="last").copy()
    tab["Prazo"] = tab["PRAZO"].map(icone)
    tab["Data Limite"] = tab["DATA_LIMITE"].dt.strftime("%d/%m/%Y").fillna("—")
    tab["Dias"] = [("—" if (pd.isna(d) or s <= 0.5) else f"{int(d):+d}")
                   for d, s in zip(tab["DIAS"], tab["SC_SALDO"])]
    tv = tab.rename(columns={
        "PRODUTO": "Produto", "NUMERO": "Contrato", "CLIENTE": "Cliente",
        "PRODUTOR_NOME": "Produtor", "SAFRA": "Safra", "MOEDA": "Moeda",
        "SC_CONTR": "Sc Contr.", "SC_FAT": "Sc Fat.", "SC_SALDO": "Sc a Entregar",
        "SITUACAO": "Situação"})
    cols = ["Data Limite", "Dias", "Prazo", "Produto", "Contrato", "Cliente",
            "Produtor", "Safra", "Sc Contr.", "Sc Fat.", "Sc a Entregar", "Situação"]
    st.dataframe(fmt_df(tv[cols], qt_cols=["Sc Contr.", "Sc Fat.", "Sc a Entregar"]),
                 width="stretch", hide_index=True)

# ======== CONCILIACAO FINANCEIRA ========
with ab_fin:
    st.subheader("Conciliação Financeira — Recebíveis dos Contratos")
    st.caption(
        "Concilia os **títulos a receber** gerados pelas notas dos contratos: "
        "**Titulado → Recebido → A Receber**. Ancorado nos títulos (contas a "
        "receber), não no faturado físico — pois há NFs de remessa sem título "
        "financeiro. Valores de caixa em R$ (a NF sai sempre em Real). "
        "Respeita os filtros da barra lateral.")
    try:
        fin_all = carregar_financeiro(tick if auto else 0)
    except Exception as e:  # noqa: BLE001
        st.error("Falha ao carregar os recebíveis.")
        st.code(str(e))
        fin_all = pd.DataFrame()

    if fin_all.empty:
        st.info("Sem recebíveis encontrados.")
    else:
        # respeita os filtros: so contratos visiveis na selecao atual
        ids_visiveis = set(df["CONTRATO_ID"].unique())
        fin = fin_all[fin_all["CONTRATO_ID"].isin(ids_visiveis)].copy()
        if fin.empty:
            st.info("Nenhum recebível para os filtros selecionados.")
        else:
            # rotulos de contrato (numero/cliente) a partir dos itens
            info_ctr = (df.drop_duplicates("CONTRATO_ID")
                        .set_index("CONTRATO_ID")[["NUMERO", "CLIENTE",
                                                   "PRODUTOR_NOME", "UF", "SAFRA"]])
            fin = fin.join(info_ctr, on="CONTRATO_ID")

            vl_tit = fin["VL_PARCELA"].sum()
            vl_rec = fin["VL_REC_PAG"].sum()
            vl_sal = fin["VL_SALDO"].sum()
            pct_rec = (vl_rec / vl_tit * 100) if vl_tit else 0.0
            vencido = fin[fin["AGING"].str.startswith("Vencido")]["VL_SALDO"].sum()
            n_ctr = fin["CONTRATO_ID"].nunique()

            # recebido na semana (por DATA_QUITACAO)
            quit_ = fin[fin["DATA_QUITACAO"].notna()].copy()
            rec_semana, rot_sem_f = 0.0, "—"
            if not quit_.empty:
                quit_["SEMANA"] = (quit_["DATA_QUITACAO"].dt.to_period("W-SUN")
                                   .apply(lambda p: p.start_time))
                sser = quit_.groupby("SEMANA")["VL_REC_PAG"].sum().sort_index()
                rec_semana = float(sser.iloc[-1])
                rot_sem_f = sser.index[-1].strftime("sem. %d/%m/%Y")

            r1 = st.columns(3)
            r1[0].metric("Titulado (R$)", brl(vl_tit),
                         f"{n_ctr} contratos", delta_color="off")
            r1[1].metric("Recebido (R$)", brl(vl_rec), f"{pct_rec:.1f}% do titulado",
                         delta_color="off")
            r1[2].metric("A receber (R$)", brl(vl_sal), delta_color="off")
            r2 = st.columns(3)
            r2[0].metric("Vencido em aberto (R$)", brl(vencido),
                         "inadimplência", delta_color="off")
            r2[1].metric("Recebido na semana (R$)", brl(rec_semana), rot_sem_f,
                         delta_color="off")
            r2[2].metric("Parcelas", num(len(fin)), delta_color="off")
            st.caption(("**Saldo a receber por idade (aging)** abaixo. 'Vencido em "
                        "aberto' = saldo de parcelas vencidas e não quitadas."
                        ).replace("$", "\\$"))
            st.divider()

            c1, c2 = st.columns(2)
            # aging
            ordem_ag = ["A vencer", "Vencido 1-30d", "Vencido 31-60d",
                        "Vencido 60+d", "Sem venc."]
            ag = (fin[fin["VL_SALDO"] > 0.01].groupby("AGING")["VL_SALDO"].sum()
                  .reindex(ordem_ag).dropna().reset_index())
            cor_ag = {"A vencer": VERDE_MEDIO, "Vencido 1-30d": VERDE_CLARO,
                      "Vencido 31-60d": "#F9A825", "Vencido 60+d": "#C62828",
                      "Sem venc.": CINZA}
            if not ag.empty:
                fig = px.bar(ag, x="AGING", y="VL_SALDO", color="AGING",
                             title="Saldo a receber por idade (R$)",
                             color_discrete_map=cor_ag, **PX)
                fig.update_layout(showlegend=False)
                c1.plotly_chart(estilo(fig), width="stretch")
            # situacao
            sf2 = fin.groupby("SITUACAO_FIN")["VL_PARCELA"].sum().reset_index()
            fig = px.pie(sf2, names="SITUACAO_FIN", values="VL_PARCELA", hole=0.45,
                         title="Titulado por situação", **PX)
            c2.plotly_chart(estilo(fig), width="stretch")

            # recebido por mes
            if not quit_.empty:
                quit_["MES"] = quit_["DATA_QUITACAO"].dt.to_period("M").dt.to_timestamp()
                gm = quit_.groupby("MES")["VL_REC_PAG"].sum().reset_index()
                fig = px.bar(gm, x="MES", y="VL_REC_PAG",
                             title="Recebido por mês (R$, por data de quitação)", **PX)
                fig.update_traces(marker_color=VERDE)
                st.plotly_chart(estilo(fig), width="stretch")

            # conciliacao por contrato
            st.markdown("#### 📋 Conciliação por contrato")
            gc = (fin.groupby("CONTRATO_ID")
                  .agg(NUMERO=("NUMERO", "first"), CLIENTE=("CLIENTE", "first"),
                       PRODUTOR=("PRODUTOR_NOME", "first"), UF=("UF", "first"),
                       SAFRA=("SAFRA", "first"),
                       VL_PARCELA=("VL_PARCELA", "sum"),
                       VL_REC_PAG=("VL_REC_PAG", "sum"),
                       VL_SALDO=("VL_SALDO", "sum"),
                       PARCELAS=("NR_SEQ_GEN", "nunique"),
                       VENCIDO=("AGING", lambda s: s.str.startswith("Vencido").sum()))
                  .reset_index())
            gc["PCT_REC"] = (gc["VL_REC_PAG"] / gc["VL_PARCELA"].replace(0, pd.NA)
                             * 100).fillna(0).round(1)
            gc["SIT"] = np.where(gc["VL_SALDO"] <= 0.01, "Quitado",
                        np.where(gc["VENCIDO"] > 0, "Tem vencido",
                        np.where(gc["VL_REC_PAG"] > 0.01, "Parcial", "A vencer")))
            gc = gc.sort_values("VL_SALDO", ascending=False)
            gtab = gc.rename(columns={
                "NUMERO": "Contrato", "CLIENTE": "Cliente", "PRODUTOR": "Produtor",
                "SAFRA": "Safra", "VL_PARCELA": "Titulado R$",
                "VL_REC_PAG": "Recebido R$", "VL_SALDO": "A Receber R$",
                "PARCELAS": "Parcelas", "PCT_REC": "% Receb.", "SIT": "Situação"})
            colf = ["Contrato", "Cliente", "Produtor", "UF", "Safra", "Titulado R$",
                    "Recebido R$", "A Receber R$", "Parcelas", "% Receb.", "Situação"]
            st.dataframe(
                fmt_df(gtab[colf], brl_cols=["Titulado R$", "Recebido R$",
                                             "A Receber R$"], pct_cols=["% Receb."]),
                width="stretch", hide_index=True)

            # drill-down de um contrato
            st.markdown("#### 🔎 Parcelas de um contrato")
            rot_f = {r.CONTRATO_ID: f"{r.NUMERO} • {r.CLIENTE} • {brl(r.VL_SALDO)} a receber"
                     for r in gc.itertuples()}
            cid_f = st.selectbox("Selecione um contrato ", options=list(rot_f.keys()),
                                 format_func=lambda i: rot_f[i], key="fin_ctr")
            pcs = fin[fin["CONTRATO_ID"] == cid_f].sort_values(
                ["DATA_VENCIMENTO", "NR_PARCELA"]).copy()
            pcs["DATA_VENCIMENTO"] = pcs["DATA_VENCIMENTO"].dt.strftime("%d/%m/%Y")
            pcs["DATA_QUITACAO"] = (pcs["DATA_QUITACAO"].dt.strftime("%d/%m/%Y")
                                    .fillna("—"))
            ptab = pcs.rename(columns={
                "NR_PARCELA": "Parc.", "NR_DOCUMENTO": "Documento",
                "DATA_VENCIMENTO": "Vencimento", "DATA_QUITACAO": "Quitação",
                "VL_PARCELA": "Titulado R$", "VL_REC_PAG": "Recebido R$",
                "VL_SALDO": "A Receber R$", "MOEDA": "Moeda",
                "SITUACAO_FIN": "Situação", "AGING": "Idade"})
            colp = ["Parc.", "Documento", "Vencimento", "Quitação", "Moeda",
                    "Titulado R$", "Recebido R$", "A Receber R$", "Situação", "Idade"]
            st.dataframe(
                fmt_df(ptab[colp], brl_cols=["Titulado R$", "Recebido R$",
                                             "A Receber R$"]),
                width="stretch", hide_index=True)

            st.download_button(
                "⬇️ Recebíveis (CSV)",
                fin.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
                "recebiveis_contratos.csv", "text/csv")

# ---- EXPORT ----
st.sidebar.divider()
st.sidebar.subheader("Exportar")
st.sidebar.download_button("⬇️ Itens (CSV)",
                           df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
                           "itens_contratos.csv", "text/csv")
st.sidebar.download_button("⬇️ Contratos (CSV)",
                           contratos.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
                           "contratos.csv", "text/csv")
