# Como publicar um painel Streamlit dentro do Portal

Prompt reutilizável: cole o bloco abaixo para o Claude Code (ou outro assistente)
quando quiser publicar **outro painel Streamlit** dentro do portal, do mesmo jeito
que foi feito com o painel de contratos.

---

```text
CONTEXTO E OBJETIVO
Tenho um dashboard em Streamlit (arquivo .py) que lê dados de um banco INTERNO
da empresa (Firebird, na rede local — não exposto à internet). Quero publicá-lo
DENTRO do meu portal de painéis, sem copiar os dados para nenhum outro banco.

O portal já existe: é um app Streamlit (repo GitHub `JoaoMottin17/portal-powerbi`,
publicado em https://paineis-grupofrt.streamlit.app, dados de login/relatórios em
Supabase). Ele lista "relatórios" e os mostra embutidos via <iframe> em tela cheia.
Ele já aceita links Power BI, *.streamlit.app e *.ts.net, e injeta automaticamente
`?embed=true&token=...` (o token vem dos Secrets do portal, nunca do banco).

ARQUITETURA QUE QUERO (mesma do painel de contratos)
[Este PC, na rede da empresa] roda o dashboard lendo o banco interno AO VIVO
   -> exposto por um túnel HTTPS público (Tailscale Funnel), URL fixa *.ts.net
   -> cadastrado como "relatório" no portal, que injeta o token e embute no iframe.
Os dados NUNCA saem do banco interno (o túnel só transmite a tela do app).
A URL pública é protegida por um TOKEN secreto (o app só abre com ?token=correto).

PASSOS QUE QUERO QUE VOCÊ EXECUTE
1) No dashboard .py:
   - set_page_config com initial_sidebar_state="expanded".
   - Adicione um "token_gate()": lê DASH_TOKEN de st.secrets/variável de ambiente;
     se estiver definido, exige que a query string traga ?token=<igual>; senão
     mostra "acesso negado" e st.stop(). Chame antes de renderizar.
   - Confirme que ele lê o banco interno por padrão (sem subir nada pra nuvem).
2) Crie um .streamlit/secrets.toml LOCAL (gitignored) com:
     DASH_TOKEN = "<gere um token aleatório longo, url-safe>"
3) Túnel:
   - Instale o Tailscale (winget), rode `tailscale up` (login no navegador) e
     `tailscale funnel --bg <porta>` para expor a porta do Streamlit. Anote a URL
     pública *.ts.net (habilite o Funnel na conta se for a 1ª vez).
4) Rodar sempre: crie um .bat que sobe o funnel + o streamlit (use o caminho
   EXPLÍCITO do Python global que tem as dependências, NÃO `py` — pode pegar um
   .venv errado). Faça um .vbs na pasta Startup do Windows para iniciar oculto no
   logon. (PC precisa ficar ligado e logado.)
5) No portal (repo `JoaoMottin17/portal-powerbi`), garanta que:
   - o validador de link aceite "ts.net";
   - ao renderizar o iframe, para links streamlit/ts.net, acrescente
     `?embed=true` e `&token=` lido de st.secrets["DASH_TOKEN"] (token só nos
     Secrets do portal, jamais salvo no Supabase);
   - dê commit + push (o Streamlit Cloud reimplanta sozinho).
6) Nos Secrets do portal no Streamlit Cloud, adicione:
     DASH_TOKEN = "<o mesmo token do passo 2>"
7) No portal logado como admin: ➕ Novo Relatório -> Título, Categoria e
   Link = a URL *.ts.net LIMPA (o portal adiciona embed+token sozinho). Salvar.

VALIDAÇÃO
- Abra a URL *.ts.net sem token: deve dar "acesso negado".
- Abra o relatório pelo portal: deve aparecer embutido, em tela cheia, funcionando.
- Confirme que o token não está no GitHub nem no Supabase (só no PC e nos Secrets
  do portal) e que nenhum dado do banco interno foi copiado para fora.

REGRAS
- Valide a sintaxe (py_compile) antes de cada push.
- Não suba segredos para o GitHub (.gitignore deve cobrir secrets.toml).
- Me avise os passos que dependem de login no navegador (Tailscale, GitHub, Neon)
  para eu autorizar.
```

---

## Referência rápida do que já está montado (painel de contratos)

| Item | Valor |
|---|---|
| Painel | `dashboard_contratos.py` (repo `JoaoMottin17/Portal_de_contratos_phyton`) |
| Banco | Firebird `192.168.191.200/3050:D:\AgroSoft\AgroGestao4\Dados\AG2006.FDB` |
| URL pública (túnel) | `https://desktop-94tp5fa.tail0cfe7c.ts.net` |
| Portal | repo `JoaoMottin17/portal-powerbi` → https://paineis-grupofrt.streamlit.app |
| Token | em `.streamlit/secrets.toml` (PC) e nos Secrets do portal — fora do Git/Supabase |
| Liga sozinho | `iniciar_painel.bat` via `Startup\iniciar_painel.vbs` (Python 3.13 global) |

> Observação: o PC precisa ficar **ligado e logado** para o painel funcionar (ele lê
> o banco interno ao vivo). Se reiniciar, sobe sozinho no logon.
