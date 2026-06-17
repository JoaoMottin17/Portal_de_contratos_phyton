# Publicar o Dashboard de Contratos e embutir no Portal Power BI

Arquitetura: **este PC** (na rede da empresa) busca os dados do Firebird e grava
um *snapshot* num **Postgres gratuito (Neon)**. O dashboard roda no **Streamlit
Cloud** lendo esse snapshot, e fica **embutido dentro do Portal** (paineis-grupofrt)
como mais um relatório. O Firebird nunca fica exposto na internet, e o controle de
acesso é feito pelo **login do portal** (o dashboard não tem login próprio).

```
[Este PC] sync_dados.py --(de hora em hora)--> [Neon/Postgres]
                                                      |
                                                      v
                                      [Dashboard no Streamlit Cloud]
                                                      |
                                  (URL cadastrada como relatório, iframe)
                                                      v
                          [Portal Power BI] <-- login do portal controla o acesso
```

O código já está pronto e testado. Falta configurar as contas. Siga na ordem.

---

## 1. Criar o banco grátis no Neon
1. Acesse https://neon.tech e crie conta (pode usar o Google).
2. **Create project** → região **AWS São Paulo (sa-east-1)** → Create.
3. Em **Connection string**, copie a URL. Ela vem assim:
   `postgresql://usuario:senha@ep-xxx.sa-east-1.aws.neon.tech/neondb?sslmode=require`
4. **Troque** `postgresql://` por `postgresql+psycopg2://` (o resto fica igual).
   Essa é a sua **PG_URL**. Guarde-a.

## 2. Gerar o primeiro snapshot (neste PC)
No PowerShell, dentro da pasta do projeto:
```powershell
$env:PG_URL = "postgresql+psycopg2://usuario:senha@ep-xxx.sa-east-1.aws.neon.tech/neondb?sslmode=require"
py sync_dados.py
```
Deve aparecer `[OK] snapshot gravado no Neon`. (Para testar sem nuvem: `py sync_dados.py --parquet`.)

## 3. Agendar a atualização de hora em hora
1. Crie um arquivo **`atualizar.bat`** na pasta (troque a PG_URL):
   ```bat
   @echo off
   set PG_URL=postgresql+psycopg2://usuario:senha@ep-xxx.sa-east-1.aws.neon.tech/neondb?sslmode=require
   py "C:\Users\User\Desktop\Visual code BD\sync_dados.py"
   ```
2. Abra o **Agendador de Tarefas do Windows** → **Criar Tarefa Básica**.
3. Nome: `Sync Dashboard FRT`. Disparo: **Diariamente**, repetir **a cada 1 hora**.
4. Ação: **Iniciar programa** → aponte para o `atualizar.bat`.
5. Marque "Executar estando o usuário conectado ou não". Concluir.
> Obs.: se o PC desligar/dormir, o painel continua no ar com o último snapshot
> (só fica mais "velho"). Para dados sempre frescos, deixe o PC ligado.

## 4. Subir os arquivos no GitHub
- Suba a pasta para um repositório (pode ser público ou privado — os dados
  financeiros ficam no Neon, não no repo).
- O `.gitignore` já impede que segredos e arquivos grandes subam.
- ⚠️ **Confira que `mapa_banco.json` e `.streamlit/secrets.toml` NÃO foram enviados.**

## 5. Publicar no Streamlit Cloud
1. Acesse https://share.streamlit.io → **New app** → escolha o repositório.
2. **Main file path:** `dashboard_contratos.py` → Deploy.
3. Em **Settings → Secrets**, cole **apenas** isto (sem login próprio):
   ```toml
   FONTE_DADOS = "postgres"
   PG_URL = "postgresql+psycopg2://usuario:senha@ep-xxx.sa-east-1.aws.neon.tech/neondb?sslmode=require"
   ```
4. Salve. O app reinicia e mostra os dados do snapshot **sem pedir login**
   (o acesso será controlado pelo portal).
5. Copie a URL do app (`https://....streamlit.app`). Você vai usá-la no passo 6.

> Por que sem `[auth]`? O painel só fica acessível através do Portal, que já exige
> login. Assim o usuário não precisa logar duas vezes. Quem tiver a URL direta
> consegue abrir — mantenha-a discreta, ou volte a ativar `[auth]` se preferir
> exigir senha também no acesso direto.

## 6. Embutir no Portal Power BI
O portal (`paineis-grupofrt`) já mostra relatórios via iframe. Ele foi ajustado
para aceitar links de apps Streamlit (não só Power BI).
1. Faça o **deploy da versão atualizada do portal** no GitHub/Streamlit (commit
   "Aceitar links de apps Streamlit no portal").
2. Abra o portal e entre como **administrador**.
3. Vá em **➕ Novo Relatório**:
   - **Título:** `Contratos de Venda`
   - **Link:** a URL do passo 5 (`https://....streamlit.app`)
   - **Categoria:** a que os sócios/gestores enxergam (ex.: Financeiro/Vendas)
4. Salve. No **📊 Dashboard**, clique em **"Abrir no portal"** — o dashboard
   aparece embutido em tela cheia, dentro do portal e do login dele.

---

## Resumo do que cada coisa faz
| Arquivo | Função |
|---|---|
| `dashboard_contratos.py` | O painel. Local lê Firebird; na nuvem lê o snapshot. Sem login próprio. |
| `consultas.py` | As 3 consultas SQL (fonte única, usada pelo painel e pelo sync). |
| `sync_dados.py` | Roda neste PC: Firebird → snapshot no Neon. |
| `gerar_hash_senha.py` | (Opcional) Só é necessário se você reativar o login próprio do dashboard. |
| `.streamlit/secrets.toml.exemplo` | Modelo dos secrets do Streamlit Cloud. |

## Dúvidas comuns
- **Voltar a exigir senha no dashboard direto?** Adicione a seção `[auth]` nos
  Secrets (use `gerar_hash_senha.py` para os hashes). O painel volta a pedir login.
- **Mudar a frequência do sync:** altere o intervalo no Agendador de Tarefas.
- **Trocar para dados ao vivo?** Possível, mas exige expor o Firebird por um túnel
  seguro — mais arriscado. O snapshot horário é mais simples e seguro.
