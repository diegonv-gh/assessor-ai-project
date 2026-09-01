import logging
from datetime import date
from math import isfinite
from typing import Optional, List
from langchain.tools import tool
from pydantic import BaseModel, Field
from app.tools.db import get_conn

logger = logging.getLogger(__name__)


def _erro_banco(operacao: str, erro: Exception) -> dict:
    """Registra o detalhe técnico sem expô-lo ao modelo ou ao usuário."""
    logger.exception("Falha ao %s no banco de dados", operacao)
    return {
        "status": "error",
        "code": "database_error",
        "message": f"Não foi possível {operacao} no banco de dados agora. Tente novamente.",
    }


def _fechar_db(conn, cur) -> None:
    for recurso in (cur, conn):
        if recurso is not None:
            try:
                recurso.close()
            except Exception:
                pass


def _rollback(conn) -> None:
    if conn is not None:
        try:
            conn.rollback()
        except Exception:
            pass


def _validar_valor(valor, permitir_zero: bool = False) -> Optional[float]:
    try:
        valor_float = float(valor)
    except (TypeError, ValueError):
        return None
    if not isfinite(valor_float) or valor_float < 0 or (valor_float == 0 and not permitir_zero):
        return None
    return valor_float


def _normalizar_data_local(valor: Optional[str]) -> Optional[str]:
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    if "/" in texto:
        partes = texto.split("/")
        if len(partes) == 3:
            texto = f"{partes[2]}-{partes[1]}-{partes[0]}"
    try:
        return date.fromisoformat(texto).isoformat()
    except ValueError:
        return None

# Essa classe garante que o objeto de Python passe todos esses campos
class AddTransactionArgs(BaseModel):
    amount: float = Field(..., description="Valor da transação (use positivo).")
    source_text: str = Field(..., description="Texto original do usuário.")
    occurred_at: Optional[str] = Field(
        default=None,
        description="Timestamp ISO 8601; se ausente, usa NOW() no banco."
    )
    type_id: Optional[int] = Field(default=None, description="ID em transaction_types (1=INCOME, 2=EXPENSES, 3=TRANSFER).")
    type_name: Optional[str] = Field(default=None, description="Nome do tipo: INCOME | EXPENSES | TRANSFER.")
    category_id: Optional[int] = Field(default=None, description="FK de categories (opcional).")
    category_name: Optional[str] = Field(default=None, description="Reconheça a categoria com qual o dinheiro foi GASTO ou GANHO entre: comida, besteira, estudo, férias, transporte, moradia, saúde, lazer, contas, investimento, presente, outros")
    description: Optional[str] = Field(default=None, description="Descrição (opcional).")
    payment_method: Optional[str] = Field(default=None, description="Forma de pagamento (opcional).")

class QueryTransactionsArgs(BaseModel):
    query: Optional[str] = Field(default=None, description="Texto em source_text ou description para buscar (case-insensitive, parcial).")
    start_date: Optional[str] = Field(default=None, description="Data inicial local em YYYY-MM-DD.")
    end_date: Optional[str] = Field(default=None, description="Data final local em YYYY-MM-DD, inclusive.")
    type_name: Optional[str] = Field(default=None, description="Tipo: INCOME, EXPENSES ou TRANSFER.")
    category_name: Optional[str] = Field(default=None, description="Nome da categoria.")
    payment_method: Optional[str] = Field(default=None, description="Forma de pagamento.")
    limit: Optional[int] = Field(default=10, description="Número máximo de transações a retornar.")
    
    

# FAZER O TYPE_ALIASES
TYPE_ALIASES = {
    "INCOME": "INCOME", "ENTRADA": "INCOME", "RECEITA": "INCOME", "SALÁRIO": "INCOME",
    "EXPENSE": "EXPENSES", "EXPENSES": "EXPENSES", "DESPESA": "EXPENSES", "GASTO": "EXPENSES",
    "TRANSFER": "TRANSFER", "TRANSFERÊNCIA": "TRANSFER", "TRANSFERENCIA": "TRANSFER"
}

# Gera um trecho SQL que filtra datas no fuso horário de America/Sao_Paulo, usando um campo de data (padrão: occurred_at).
def _local_date_filter_sql(field: str = "occurred_at") -> str:
    """
    Retorna um trecho SQL para filtragem por dia local em America/Sao_Paulo.
    Ex.: (occurred_at AT TIME ZONE 'America/Sao_Paulo')::date = %s::date
    """
    return f"(({field} AT TIME ZONE 'America/Sao_Paulo')::date = %s::date)"

#Garante que o campo type da tabela transactions receba um id válido (1=INCOME, 2=EXPENSES, 3=TRANSFER
def _resolve_type_id(cur, type_id: Optional[int], type_name: Optional[str]) -> Optional[int]:
    if type_name:
        nome = type_name.strip().upper()
        t = TYPE_ALIASES.get(nome)
        if not t:
            return None
        cur.execute("SELECT id FROM transaction_types WHERE UPPER(type)=%s LIMIT 1;", (t,))
        row = cur.fetchone()
        return row[0] if row else None
    if type_id is not None:
        try:
            valor = int(type_id)
        except (TypeError, ValueError):
            return None
        return valor if valor > 0 else None
    return 2

CATEGORIES_ALIASES = {
    "COMIDA": [
        "COMIDA", "ALIMENTACAO", "ALMOÇO", "JANTA", "JANTAR",
        "CAFÉ", "CAFÉ DA MANHÃ", "LANCHE", "RESTAURANTE", "IFOOD"
    ],

    "BESTEIRA": [
        "BESTEIRA", "DOCE", "DOCES", "SNACK", "SALGADINHO",
        "CHOCOLATE", "BALINHA", "PORCARIA"
    ],

    "ESTUDO": [
        "ESTUDO", "CURSO", "FACULDADE", "ESCOLA", "LIVRO",
        "EDUCAÇÃO", "MENTORIA"
    ],

    "FÉRIAS": [
        "FÉRIAS", "VIAGEM", "HOTEL", "PASSAGEM",
        "TURISMO", "AIRBNB"
    ],

    "TRANSPORTE": [
        "TRANSPORTE", "UBER", "99", "TÁXI", "ÔNIBUS",
        "METRÔ", "COMBUSTÍVEL", "GASOLINA", "ESTACIONAMENTO"
    ],

    "MORADIA": [
        "MORADIA", "ALUGUEL", "CONDOMÍNIO", "CASA",
        "IPTU", "MANUTENÇÃO"
    ],

    "SAÚDE": [
        "SAÚDE", "MÉDICO", "DENTISTA", "FARMÁCIA",
        "REMÉDIO", "PLANO DE SAÚDE"
    ],

    "LAZER": [
        "LAZER", "CINEMA", "SHOW", "FESTA",
        "BAR", "VIAGEM CURTA", "HOBBY"
    ],

    "CONTAS": [
        "CONTAS", "LUZ", "ÁGUA", "INTERNET",
        "CELULAR", "BOLETO", "ASSINATURA"
    ],

    "INVESTIMENTO": [
        "INVESTIMENTO", "AÇÃO", "CRIPTO", "TESOURO",
        "APLICAÇÃO", "RENDA"
    ],

    "PRESENTE": [
        "PRESENTE", "DOAÇÃO", "ANIVERSÁRIO",
        "LEMBRANCINHA"
    ],

    "OUTROS": [
        "OUTROS", "DIVERSOS", "VARIADOS"
    ]
}

def _get_category_id(cur, category_id: Optional[int], category_name: Optional[str]) -> Optional[int]:
    if category_name:
        c = category_name.strip().upper()
        for cannonical,aliases in CATEGORIES_ALIASES.items():
            if c in aliases:
                c = cannonical
                break
        cur.execute("SELECT id FROM categories WHERE UPPER(name)=%s LIMIT 1;", (c,))
        row = cur.fetchone()
        return row[0] if row else None
    if category_id is not None:
        try:
            valor = int(category_id)
        except (TypeError, ValueError):
            return None
        return valor if valor > 0 else None


# Tool: add_transaction
@tool("add_transaction", args_schema=AddTransactionArgs)
def add_transaction(
    amount: float,
    source_text: str,
    occurred_at: Optional[str] = None,
    type_id: Optional[int] = None,
    type_name: Optional[str] = None,
    category_id: Optional[int] = None,
    category_name: Optional[str] = None,
    description: Optional[str] = None,
    payment_method: Optional[str] = None,
) -> dict:
    """Insere uma transação financeira no banco de dados Postgres."""
    valor = _validar_valor(amount)
    if valor is None:
        return {"status": "error", "code": "invalid_amount", "message": "O valor deve ser um número finito maior que zero."}
    if not isinstance(source_text, str) or not source_text.strip():
        return {"status": "error", "code": "invalid_source_text", "message": "Informe o texto de origem da transação."}

    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        resolved_type_id = _resolve_type_id(cur, type_id, type_name)
        if resolved_type_id is None:
            return {"status": "error", "message": "Tipo inválido (use type_id ou type_name: INCOME/EXPENSES/TRANSFER)."}
        
        if category_id is None:
            category_id = _get_category_id(cur, category_id, category_name)
            if category_name and category_id is None:
                return {"status": "error", "code": "invalid_category", "message": "Categoria não encontrada no banco de dados."}
        elif _get_category_id(cur, category_id, None) is None:
            return {"status": "error", "code": "invalid_category", "message": "O ID da categoria deve ser positivo."}

        if occurred_at:
            cur.execute(
                """
                INSERT INTO transactions
                    (amount, type, category_id, description, payment_method, occurred_at, source_text)
                VALUES
                    (%s, %s, %s, %s, %s, %s::timestamptz, %s)
                RETURNING id, occurred_at;
                """,
                (valor, resolved_type_id, category_id, description, payment_method, occurred_at, source_text.strip()),
            )
        else:
            cur.execute(
                """
                INSERT INTO transactions
                    (amount, type, category_id, description, payment_method, occurred_at, source_text)
                VALUES
                    (%s, %s, %s, %s, %s, NOW(), %s)
                RETURNING id, occurred_at;
                """,
                (valor, resolved_type_id, category_id, description, payment_method, source_text.strip()),
            )

        new_id, occurred = cur.fetchone()
        conn.commit()
        return {"status": "ok", "id": new_id, "occurred_at": str(occurred)}

    except Exception as e:
        _rollback(conn)
        return _erro_banco("adicionar a transação", e)
    finally:
        _fechar_db(conn, cur)

@tool("search_transactions", args_schema=QueryTransactionsArgs)
def search_transactions(
    query: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    type_name: Optional[str] = None,
    category_name: Optional[str] = None,
    payment_method: Optional[str] = None,
    limit: Optional[int] = 10
) -> dict:
    """Consulta as transações financeiras no banco de dados Postgres com base nos filtros fornecidos sendo eles:
    texto(source_text) e tipo e datas locais (America/Sao_Paulo)."""

    inicio = _normalizar_data_local(start_date)
    fim = _normalizar_data_local(end_date)
    if (start_date and inicio is None) or (end_date and fim is None):
        return {"status": "error", "code": "invalid_date", "message": "Use datas válidas no formato YYYY-MM-DD ou DD/MM/YYYY."}
    if inicio and fim and inicio > fim:
        return {"status": "error", "code": "invalid_date_range", "message": "A data inicial não pode ser posterior à data final."}
    try:
        limite = max(1, min(int(limit or 10), 100))
    except (TypeError, ValueError):
        return {"status": "error", "code": "invalid_limit", "message": "O limite deve ser um número inteiro entre 1 e 100."}

    conn = None
    cur = None

    try:
        conn = get_conn()
        cur = conn.cursor()
        sql = """
        SELECT t.id, t.amount, ty.type as transaction_type, c.name as category_name,
            t.description, t.payment_method, t.occurred_at, t.source_text
        FROM transactions t
        LEFT JOIN transaction_types ty ON t.type = ty.id
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE 1=1
        """
        params = []

        # Os filtros de data usam a data local de São Paulo, inclusive para
        # timestamps armazenados em UTC no PostgreSQL.
        if inicio:
            sql += " AND (t.occurred_at AT TIME ZONE 'America/Sao_Paulo')::date >= %s::date"
            params.append(inicio)

        if query:
            query_text = f"%{query}%"
            sql += " AND (t.source_text ILIKE %s OR t.description ILIKE %s)"
            params.extend([query_text, query_text])

        if fim:
            sql += " AND (t.occurred_at AT TIME ZONE 'America/Sao_Paulo')::date <= %s::date"
            params.append(fim)

        if type_name:
            t = TYPE_ALIASES.get(type_name.strip().upper())
            if not t:
                return {"status": "error", "code": "invalid_type", "message": "Tipo inválido: use INCOME, EXPENSES ou TRANSFER."}
            sql += " AND UPPER(ty.type) = %s"
            params.append(t)

        if category_name:
            c = category_name.strip().upper()
            for canonical, aliases in CATEGORIES_ALIASES.items():
                if c in aliases:
                    c = canonical
                    break
            sql += " AND UPPER(c.name) = %s"
            params.append(c)

        if payment_method:
            sql += " AND LOWER(t.payment_method) = %s"
            params.append(payment_method.strip().lower())

        sql += " ORDER BY t.occurred_at DESC LIMIT %s"
        params.append(limite)

        cur.execute(sql, params)
        rows = cur.fetchall()

        transactions = [
            {
                "id": row[0],
                "amount": float(row[1]),
                "type": row[2],
                "category": row[3],
                "description": row[4],
                "payment_method": row[5],
                "occurred_at": str(row[6]),
                "source_text": row[7],
            }
            for row in rows
        ]

        return {"status": "ok", "total": len(transactions), "transactions": transactions}
    
    except Exception as e:
        _rollback(conn)
        return _erro_banco("consultar as transações", e)
    finally:
        _fechar_db(conn, cur)

@tool("saldo_total")
def saldo_total() -> dict:
    """Calcula o saldo total (entradas - despesas) com base nas transações registradas. Ignore o tipo TRANSFER(3)"""
    conn = None
    cur = None

    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                SUM(CASE WHEN t.type = 1 THEN amount ELSE 0 END) AS total_income,
                SUM(CASE WHEN t.type = 2 THEN amount ELSE 0 END) AS total_expenses
            FROM transactions t;
        """)
        row = cur.fetchone()
        total_income = float(row[0]) if row[0] else 0.0
        total_expenses = float(row[1]) if row[1] else 0.0
        saldo = total_income - total_expenses
        return {"status": "ok", "saldo": saldo, "total_income": total_income, "total_expenses": total_expenses}
    
    except Exception as e:
        _rollback(conn)
        return _erro_banco("consultar o saldo total", e)
    finally:
        _fechar_db(conn, cur)

@tool("saldo_diario")
def saldo_diario(first_date: str, last_date: Optional[str] = None) -> dict: 
    """Use esta tool SEMPRE que o usuário perguntar sobre saldo, balanço, entradas ou saídas de um dia ou período específico. Exemplos: 'qual meu saldo de hoje?', 'quanto gastei essa semana?', 'meu balanço de 01/03 a 15/03'. Converta datas do formato DD/MM/YYYY para YYYY-MM-DD antes de chamar."""
    inicio = _normalizar_data_local(first_date)
    fim = _normalizar_data_local(last_date)
    if inicio is None or (last_date and fim is None):
        return {"status": "error", "code": "invalid_date", "message": "Use datas válidas no formato YYYY-MM-DD ou DD/MM/YYYY."}
    if fim and inicio > fim:
        return {"status": "error", "code": "invalid_date_range", "message": "A data inicial não pode ser posterior à data final."}

    conn = None
    cur = None

    try:
        conn = get_conn()
        cur = conn.cursor()

        if fim:
            cur.execute("""
                SELECT 
                    SUM(CASE WHEN t.type = 1 THEN amount ELSE 0 END) AS total_income,
                    SUM(CASE WHEN t.type = 2 THEN amount ELSE 0 END) AS total_expenses
                FROM transactions t
                WHERE (t.occurred_at AT TIME ZONE 'America/Sao_Paulo')::date >= %s::date
                AND (t.occurred_at AT TIME ZONE 'America/Sao_Paulo')::date <= %s::date
            """, (inicio, fim))
        else: cur.execute("""
                SELECT 
                    SUM(CASE WHEN t.type = 1 THEN amount ELSE 0 END) AS total_income,
                    SUM(CASE WHEN t.type = 2 THEN amount ELSE 0 END) AS total_expenses
                FROM transactions t
                WHERE (t.occurred_at AT TIME ZONE 'America/Sao_Paulo')::date = %s::date
            """, (inicio,)
            )
        row = cur.fetchone()
        total_income = float(row[0]) if row[0] else 0.0
        total_expenses = float(row[1]) if row[1] else 0.0
        saldo = total_income - total_expenses
        return {"status": "ok", "saldo": saldo, "total_income": total_income, "total_expenses": total_expenses}
    except Exception as e:
        _rollback(conn)
        return _erro_banco("consultar o saldo do período", e)
    finally:
        _fechar_db(conn, cur)

class UpdateTransactionArgs(BaseModel):
    id: Optional[int] = Field(
        default=None,
        description="ID da transação a atualizar. Se ausente, será feita uma busca por (match_text + date_local)."
    )
    match_text: Optional[str] = Field(
        default=None,
        description="Texto para localizar transação quando id não for informado (busca em source_text/description)."
    )
    date_local: Optional[str] = Field(
        default=None,
        description="Data local (YYYY-MM-DD) em America/Sao_Paulo; usado em conjunto com match_text quando id ausente."
    )
    amount: Optional[float] = Field(default=None, description="Novo valor.")
    type_id: Optional[int] = Field(default=None, description="Novo type_id (1/2/3).")
    type_name: Optional[str] = Field(default=None, description="Novo type_name: INCOME | EXPENSES | TRANSFER.")
    category_id: Optional[int] = Field(default=None, description="Nova categoria (id).")
    category_name: Optional[str] = Field(default=None, description="Nova categoria (nome).")
    description: Optional[str] = Field(default=None, description="Nova descrição.")
    payment_method: Optional[str] = Field(default=None, description="Novo meio de pagamento.")
    occurred_at: Optional[str] = Field(default=None, description="Novo timestamp ISO 8601.")

@tool("update_transaction", args_schema=UpdateTransactionArgs)
def update_transaction(
    id: Optional[int] = None,
    match_text: Optional[str] = None,
    date_local: Optional[str] = None,
    amount: Optional[float] = None,
    type_id: Optional[int] = None,
    type_name: Optional[str] = None,
    category_id: Optional[int] = None,
    category_name: Optional[str] = None,
    description: Optional[str] = None,
    payment_method: Optional[str] = None,
    occurred_at: Optional[str] = None,
) -> dict:
    """
    Atualiza uma transação existente.
    Estratégias:
      - Se 'id' for informado: atualiza diretamente por ID.
      - Caso contrário: localiza a transação mais recente que combine (match_text em source_text/description)
        E (date_local em America/Sao_Paulo), então atualiza.
    Retorna: status, rows_affected, id, e o registro atualizado.
    """
    if amount is not None and _validar_valor(amount, permitir_zero=True) is None:
        return {"status": "error", "code": "invalid_amount", "message": "O novo valor deve ser um número finito maior ou igual a zero."}
    if id is not None:
        try:
            id = int(id)
        except (TypeError, ValueError):
            return {"status": "error", "code": "invalid_id", "message": "O ID da transação deve ser um número inteiro."}
        if id <= 0:
            return {"status": "error", "code": "invalid_id", "message": "O ID da transação deve ser positivo."}

    if all(value is None for value in [
        amount,
        type_id,
        type_name,
        category_id,
        category_name,
        description,
        payment_method,
        occurred_at,
    ]):
        return {"status": "error", "message": "Nada para atualizar: forneça pelo menos um campo (amount, type, category, description, payment_method, occurred_at)."}

    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        # Resolve target_id
        target_id = id
        if target_id is None:
            if not match_text or not date_local:
                return {"status": "error", "message": "Sem 'id': informe match_text E date_local para localizar o registro."}
            date_local_normalizada = _normalizar_data_local(date_local)
            if date_local_normalizada is None:
                return {"status": "error", "code": "invalid_date", "message": "Use date_local no formato YYYY-MM-DD ou DD/MM/YYYY."}

            # Buscar o mais recente no dia local informado que combine o texto
            cur.execute(
                f"""
                SELECT t.id
                FROM transactions t
                WHERE (t.source_text ILIKE %s OR t.description ILIKE %s)
                  AND {_local_date_filter_sql("t.occurred_at")}
                ORDER BY t.occurred_at DESC
                LIMIT 2;
                """,
                (f"%{match_text}%", f"%{match_text}%", date_local_normalizada)
            )
            rows = cur.fetchall()
            if not rows:
                return {"status": "error", "message": "Nenhuma transação encontrada para os filtros fornecidos."}
            if len(rows) > 1:
                return {"status": "error", "code": "ambiguous_match", "message": "Mais de uma transação corresponde aos filtros. Informe o ID para evitar alterar o registro errado."}
            target_id = rows[0][0]

        # Resolver type_id / category_id a partir de nomes, se fornecidos
        resolved_type_id = _resolve_type_id(cur, type_id, type_name) if (type_id is not None or type_name) else None
        if (type_id is not None or type_name) and resolved_type_id is None:
            return {"status": "error", "code": "invalid_type", "message": "Tipo inválido: use INCOME, EXPENSES ou TRANSFER."}
        resolved_category_id = category_id
        if resolved_category_id is not None:
            try:
                resolved_category_id = int(resolved_category_id)
            except (TypeError, ValueError):
                return {"status": "error", "code": "invalid_category", "message": "O ID da categoria deve ser um número inteiro positivo."}
            if resolved_category_id <= 0:
                return {"status": "error", "code": "invalid_category", "message": "O ID da categoria deve ser positivo."}
        if category_name and category_id is None:
            resolved_category_id = _get_category_id(cur, None, category_name)
            if resolved_category_id is None:
                return {"status": "error", "code": "invalid_category", "message": "Categoria não encontrada no banco de dados."}

        # Montar SET dinâmico
        sets = []
        params: List[object] = []
        if amount is not None:
            sets.append("amount = %s")
            params.append(_validar_valor(amount, permitir_zero=True))
        if resolved_type_id is not None:
            sets.append("type = %s")
            params.append(resolved_type_id)
        if resolved_category_id is not None:
            sets.append("category_id = %s")
            params.append(resolved_category_id)
        if description is not None:
            sets.append("description = %s")
            params.append(description)
        if payment_method is not None:
            sets.append("payment_method = %s")
            params.append(payment_method)
        if occurred_at is not None:
            sets.append("occurred_at = %s::timestamptz")
            params.append(occurred_at)

        if not sets:
            return {"status": "error", "message": "Nenhum campo válido para atualizar."}

        params.append(target_id)

        cur.execute(
            f"UPDATE transactions SET {', '.join(sets)} WHERE id = %s;",
            params
        )
        rows_affected = cur.rowcount
        if rows_affected == 0:
            _rollback(conn)
            return {"status": "error", "code": "not_found", "message": "Nenhuma transação encontrada com esse ID."}
        conn.commit()

        # Retornar o registro atualizado
        cur.execute(
            """
            SELECT
              t.id, t.occurred_at, t.amount, tt.type AS type_name,
              c.name AS category_name, t.description, t.payment_method, t.source_text
            FROM transactions t
            JOIN transaction_types tt ON tt.id = t.type
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.id = %s;
            """,
            (target_id,)
        )
        r = cur.fetchone()
        updated = None
        if r:
            updated = {
                "id": r[0],
                "occurred_at": str(r[1]),
                "amount": float(r[2]),
                "type": r[3],
                "category": r[4],
                "description": r[5],
                "payment_method": r[6],
                "source_text": r[7],
            }

        return {
            "status": "ok",
            "rows_affected": rows_affected,
            "id": target_id,
            "updated": updated
        }

    except Exception as e:
        _rollback(conn)
        return _erro_banco("atualizar a transação", e)
    finally:
        _fechar_db(conn, cur)


# Exporta a lista de tools
TOOLS = [add_transaction, search_transactions, saldo_total, saldo_diario, update_transaction]
