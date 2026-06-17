# Publicar o Dashboard online (Streamlit Cloud + Neon)

Arquitetura: **este PC** (na rede da empresa) busca os dados do Firebird e grava
um *snapshot* num **Postgres gratuito (Neon)**. O **Streamlit Cloud** lê esse
snapshot e mostra o painel com **login**. O Firebird nunca fica exposto na internet.

```
[Este PC] sync_dados.py --(de hora em hora)--> [Neon/Postgres] <--le-- [Streamlit Cloud + login]
```

O código já está pronto e testado. Falta só configurar as contas. Siga na ordem.

---

## 1. Criar o banco grátis no Neon
1. Acesse https://neon.tech e crie conta (pode usar o Google).
2. **Create project** → regiao **AWS São Paulo (sa-east-1)** → Create.
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

## 4. Gerar as senhas dos usuários
Para cada pessoa que vai acessar:
```powershell
py gerar_hash_senha.py "senhaDoSocio" "senhaDoGestor"
```
Copie os **hashes** gerados (começam com `$2b$12$...`). Você vai colá-los no passo 6.

## 5. Subir os arquivos no GitHub
- Suba a pasta para um repositório (pode ser o que você já usa).
- O `.gitignore` já impede que segredos e arquivos grandes subam.
- ⚠️ **Confira que `mapa_banco.json` e `.streamlit/secrets.toml` NÃO foram enviados.**
- Os dados financeiros ficam no Neon (privado), então o repositório pode ser
  público ou privado sem vazar nada.

## 6. Publicar no Streamlit Cloud
1. Acesse https://share.streamlit.io → **New app** → escolha o repositório.
2. **Main file path:** `dashboard_contratos.py` → Deploy.
3. Em **Settings → Secrets**, cole (use `secrets.toml.exemplo` como base):
   ```toml
   FONTE_DADOS = "postgres"
   PG_URL = "postgresql+psycopg2://usuario:senha@ep-xxx.sa-east-1.aws.neon.tech/neondb?sslmode=require"

   [auth]
   cookie_name = "frt_dash"
   cookie_key = "uma-chave-aleatoria-bem-longa"
   cookie_expiry_days = 7

   [auth.credentials.usernames.socio]
   name = "Nome do Sócio"
   password = "$2b$12$...hash do passo 4..."

   [auth.credentials.usernames.gestor]
   name = "Nome do Gestor"
   password = "$2b$12$...hash do passo 4..."
   ```
4. Salve. O app reinicia, pede **login** e mostra os dados do snapshot.
5. Compartilhe a URL (`https://...streamlit.app`) com os sócios — funciona no celular.

---

## Resumo do que cada coisa faz
| Arquivo | Função |
|---|---|
| `dashboard_contratos.py` | O painel. Local lê Firebird; na nuvem lê o snapshot + pede login. |
| `consultas.py` | As 3 consultas SQL (fonte única, usada pelo painel e pelo sync). |
| `sync_dados.py` | Roda neste PC: Firebird → snapshot no Neon. |
| `gerar_hash_senha.py` | Gera o hash das senhas para o secrets. |
| `.streamlit/secrets.toml.exemplo` | Modelo dos secrets do Streamlit Cloud. |

## Dúvidas comuns
- **Trocar para dados ao vivo?** Possível, mas exige expor o Firebird por um túnel
  seguro — mais arriscado. O snapshot horário é mais simples e seguro.
- **Mudar a frequência:** altere o intervalo no Agendador de Tarefas.
- **Adicionar/remover usuário:** edite a seção `[auth]` nos Secrets do Streamlit.
