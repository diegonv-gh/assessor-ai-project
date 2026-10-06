# Assessor.AI

Assistente financeiro com FastAPI, LangGraph, LangChain, PostgreSQL, MongoDB e Qdrant.

O sistema conversa com o usuário, consulta e registra transações financeiras, responde dúvidas com base em um FAQ e recupera informações relevantes de sessões anteriores.

## Principais recursos

- Roteamento entre agentes especializados.
- Consulta, cadastro, atualização e cálculo de saldo financeiro.
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
pip install fastapi "uvicorn[standard]" pydantic python-dotenv psycopg2-binary pymongo qdrant-client langchain langgraph langchain-google-genai langchain-groq langchain-community langchain-text-splitters pypdf "mcp[cli]==2.2.0"
uvicorn app.main:app --reload
```

### Servidor MCP financeiro

O servidor MCP expõe as mesmas cinco tools financeiras usadas pelo Assessor: `query_transactions`, `total_balance`, `daily_balance`, `add_transaction` e `update_transaction`. Ele roda como um processo local por `stdio` e usa a `DATABASE_URL` do `.env` da raiz do projeto. Não inicia o grafo nem precisa das configurações de Gemini, Groq, MongoDB ou Qdrant.

Instale o SDK MCP no mesmo Python configurado no cliente:

```powershell
python -m pip install "mcp[cli]==2.2.0"
python -c "import sys; print(sys.executable)"
```

O arquivo `.cursor/mcp.json` já vem configurado para esta máquina. Em outro computador, ajuste `command` para o caminho do executável exibido acima e o caminho em `args` para `app/mcp_server.py` neste projeto. Paths com espaços são aceitos como uma única string JSON. Reinicie ou atualize as integrações MCP do Cursor; o servidor `assessor-financeiro` deve mostrar cinco tools. O banco PostgreSQL configurado em `DATABASE_URL` precisa estar acessível quando uma tool for chamada.

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

As tools de consulta são marcadas como somente leitura. Cadastro e atualização são operações de escrita; as anotações MCP ajudam o cliente a decidir quando pedir confirmação, mas a confirmação depende das configurações do próprio cliente. Filtros de data usam dias locais em `America/Sao_Paulo`, no formato `YYYY-MM-DD`; timestamps de transações usam ISO 8601. Os cálculos de saldo excluem transferências.

#### Diagnóstico

- **Servidor não aparece ou `ModuleNotFoundError`:** confira `command`, `args` e que o MCP, LangChain e dependências do projeto estão instalados no mesmo Python; depois use **Refresh** ou reinicie a janela do Cursor.
- **`DATABASE_URL ausente`:** confirme que o `.env` existe na raiz deste projeto e define `DATABASE_URL`. O processo MCP não imprime credenciais.
- **Erro de conexão ao chamar uma tool:** verifique se o PostgreSQL está rodando e se a URL e a rede estão corretas.
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
