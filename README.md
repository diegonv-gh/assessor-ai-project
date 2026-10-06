# Assessor.AI

Assistente financeiro com FastAPI, LangGraph, LangChain, PostgreSQL, MongoDB e Qdrant.

O sistema conversa com o usuário, consulta e registra transações financeiras, responde dúvidas com base em um FAQ e recupera informações relevantes de sessões anteriores.

## Principais recursos

- Roteamento entre agentes especializados.
- Consulta, cadastro, atualização e cálculo de saldo financeiro.
- Consulta e cadastro de compromissos na agenda local, com sincronização opcional ao Google Calendar.
- Busca semântica no FAQ em PDF.
- Histórico de curto prazo por sessão.
- Resumos de sessões encerradas armazenados no MongoDB e indexados no Qdrant.
- Interface web incluída em `frontend/`.

## Serviços utilizados

- PostgreSQL: dados financeiros.
- MongoDB: mensagens e resumos das sessões.
- Qdrant: embeddings do FAQ e do histórico.
- Gemini e Groq: modelos de linguagem e embeddings.

Collections do Qdrant:

- `faq_chunks`
- `historico_resumos`

## Requisitos

- Python 3.10 ou superior.
- PostgreSQL, MongoDB e Qdrant configurados.
- Chaves de API do Gemini, Groq e Qdrant.

## Configuração

Crie um arquivo `.env` na raiz do projeto. O arquivo já é ignorado pelo Git e não deve conter valores publicados no repositório:

```env
GEMINI_API_KEY=<sua-chave>
GROQ_API_KEY=<sua-chave>
DATABASE_URL=<sua-connection-string-do-postgresql>
MONGODB_URI=<sua-url-do-mongodb>
MONGODB_DB=assessor
QDRANT_URL=<sua-url-do-qdrant>
QDRANT_API_KEY=<sua-chave>
```

Nunca substitua os placeholders por chaves reais no `README.md` ou em qualquer arquivo versionado.

## Instalação e execução

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install fastapi "uvicorn[standard]" pydantic python-dotenv psycopg2-binary pymongo qdrant-client langchain langgraph langchain-google-genai langchain-groq langchain-community langchain-text-splitters pypdf "mcp[cli]==2.2.0" "google-auth-oauthlib==1.5.0" tzdata
uvicorn app.main:app --reload
```

### Servidor MCP de finanças e agenda

O servidor MCP local expõe sete tools por `stdio`, usando a `DATABASE_URL` do `.env` da raiz do projeto. Ele não inicia o grafo nem depende de Gemini, Groq, MongoDB, Qdrant ou autenticação Google.

- Finanças: `query_transactions`, `total_balance`, `daily_balance`, `add_transaction` e `update_transaction`.
- Agenda PostgreSQL: `query_events` e `add_event`. `add_event` verifica sobreposição e grava somente no banco local; a sincronização Google ocorre pelo chat do Assessor.

Instale o SDK MCP no mesmo Python configurado no cliente:

```powershell
python -m pip install "mcp[cli]==2.2.0" "google-auth-oauthlib==1.5.0" tzdata
python -c "import sys; print(sys.executable)"
```

O arquivo `.cursor/mcp.json` já vem configurado para esta máquina. Em outro computador, ajuste `command` para o executável mostrado acima e `args` para o caminho absoluto de `app/mcp_server.py`. Paths com espaços são aceitos como uma única string JSON. Reinicie ou atualize a integração MCP do Cursor; `assessor-financeiro` deve mostrar as sete tools. O PostgreSQL configurado em `DATABASE_URL` precisa estar acessível quando uma tool for chamada.

Para configurar manualmente no Cline, abra **MCP Servers → Configure MCP Servers** e acrescente este objeto a `mcpServers` no arquivo de configuração do Cline. Atualize os dois caminhos para a sua máquina, como no `.cursor/mcp.json`, salve e habilite o servidor na lista:

```json
{
  "assessor-financeiro": {
    "command": "C:\\Program Files\\Python314\\python.exe",
    "args": [
      "C:\\Users\\diegovaladares-ieg\\OneDrive\\Tech\\2 ano\\IA2\\IA Assessor\\migracao_fastAPI\\app\\mcp_server.py"
    ]
  }
}
```

As consultas são marcadas como somente leitura; cadastro e atualização são operações de escrita. As anotações ajudam o cliente a decidir quando pedir confirmação, mas a política depende das configurações do cliente. Filtros de agenda e finanças usam dias locais em `America/Sao_Paulo`, no formato `YYYY-MM-DD`; inícios e términos de eventos usam ISO 8601 com fuso. Intervalos de consulta incluem o último dia. Os saldos excluem transferências.

### Google Calendar no chat do Assessor

A integração Google é opcional: sem credenciais ou login, a agenda PostgreSQL continua disponível. O processo de autenticação usa credenciais OAuth do tipo **Desktop app**. O arquivo `gcp-oauth.keys.json` está na raiz desta instalação e é ignorado pelo Git. Em outro clone, coloque na raiz o JSON Desktop fornecido para o projeto Google autorizado (ou ajuste `GOOGLE_OAUTH_CREDENTIALS`); nunca publique esse arquivo ou `google-calendar.token.json`.

O serviço MCP do Google Calendar está em [Developer Preview](https://developers.google.com/workspace/calendar/api/v3/reference/mcp/tools_list/create_event); o formato implementado usa `summary`, início e fim ISO 8601, com local, descrição e fuso opcionais.

O `.env` pode definir estas opções; todas são opcionais:

| Variável | Uso |
|---|---|
| `GOOGLE_OAUTH_CREDENTIALS` | Caminho do JSON OAuth Desktop. Caminhos relativos são resolvidos a partir da raiz do projeto; padrão `gcp-oauth.keys.json`. |
| `GOOGLE_CALENDAR_MCP_URL` | URL do serviço MCP Google Calendar; por padrão `https://calendarmcp.googleapis.com/mcp/v1`. |
| `GOOGLE_CALENDAR_ACCESS_TOKEN` | Token opcional para automação local; tem precedência sobre o token salvo em arquivo. |

Faça o consentimento inicial explicitamente no terminal da raiz do projeto. O comando abre o navegador para a conta Google desta instalação e salva o token localmente:

```powershell
python -c "from app.tools.calendario_google import autenticar; print(autenticar())"
```

O token local é renovado quando expira e há refresh token. Para conferir a lista pública de tools do serviço MCP sem criar eventos:

```powershell
python -c "from app.tools.calendario_google import listar_tools_google; print(listar_tools_google())"
```

Na conversa, o Assessor consulta primeiro a agenda, pede os dados ausentes (inclusive duração/fim) e não grava em caso de conflito. Em seguida, grava no PostgreSQL e só após sucesso tenta criar no calendário `primary` da conta autorizada. O chat informa separadamente os resultados locais e Google; se Google falhar, o evento local permanece salvo. Uma falha incerta não dispara nova criação automática. O fallback REST usa o mesmo token somente quando há resposta explícita de que o serviço MCP está desabilitado.

#### Diagnóstico

- **Servidor não aparece ou `ModuleNotFoundError`:** confira `command`, `args` e se MCP, LangChain e dependências do projeto estão instalados no mesmo Python; depois atualize ou reinicie a integração no Cursor.
- **`DATABASE_URL ausente`:** confirme que o `.env` existe na raiz deste projeto e define `DATABASE_URL`. O processo MCP não imprime credenciais.
- **Erro de conexão ao chamar uma tool:** verifique se o PostgreSQL está rodando e se a URL e a rede estão corretas.
- **Agenda Google não autorizada:** instale `google-auth-oauthlib` no Python usado pelo Assessor e execute `autenticar()` no terminal. Se a tela de consentimento bloquear o acesso, confira se a conta está cadastrada como usuário de teste no projeto OAuth do Google.
- **Google `403` ou API desabilitada:** confira se Google Calendar API/MCP está habilitado no projeto OAuth, se a conta concedeu o escopo de eventos e se o token salvo tem acesso ao calendário principal.
- **Resultado local salvo, Google falhou:** o evento continua no PostgreSQL. Confira a autorização e o calendário antes de qualquer nova tentativa para evitar duplicatas.
- **Inspecionar falhas no Cursor:** consulte **View → Output → MCP Logs**. O protocolo MCP usa `stdout`; mensagens de diagnóstico são encaminhadas a `stderr`.

Com o servidor em execução:

- Aplicação: http://localhost:8000
- Documentação da API: http://localhost:8000/docs
- Verificação de configuração: `GET /health`

## Indexação do FAQ

Depois de configurar o Qdrant, indexe o PDF oficial:

```powershell
python -m app.ingest_faq
```

O comando gera embeddings e atualiza a collection `faq_chunks`. Execute-o novamente quando o PDF for alterado.

## Uso da API

### Conversa

`POST /chat`

```json
{
  "user_id": "usuario-exemplo",
  "session_id": "sessao-exemplo",
  "pergunta": "Qual é o meu saldo total?"
}
```

### Encerrar uma sessão

`POST /sessions/{session_id}/encerrar`

```json
{
  "user_id": "usuario-exemplo"
}
```

O encerramento gera o resumo que será usado pela memória semântica em conversas futuras. O mesmo `user_id` deve ser mantido entre sessões do mesmo usuário; o `session_id` deve mudar a cada nova conversa.

## Teste rápido da memória

1. Envie uma mensagem sobre um plano ou preferência usando um `session_id`.
2. Encerre essa sessão pela rota acima.
3. Use outro `session_id`, mantendo o mesmo `user_id`.
4. Pergunte sobre o assunto anterior.

O histórico só fica disponível para busca semântica depois que a sessão é encerrada e resumida.

## Segurança

- Não versione `.env`, chaves, senhas ou connection strings reais.
- Revise o `git diff` antes de publicar alterações.
- A configuração de CORS atual é voltada para desenvolvimento; restrinja as origens antes de usar em produção.

## Status

Projeto acadêmico em desenvolvimento.
