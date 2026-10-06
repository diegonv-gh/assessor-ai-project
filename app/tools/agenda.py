"""Tools de consulta e gravação da agenda no PostgreSQL do Assessor."""

import logging
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from langchain.tools import tool
from pydantic import BaseModel, Field

from app.tools.db import get_conn

logger = logging.getLogger(__name__)
FUSO_LOCAL = ZoneInfo("America/Sao_Paulo")


def _data_local(valor: Optional[str], campo: str) -> tuple[Optional[date], Optional[dict]]:
    if valor is None:
        return None, None
    try:
        dia = date.fromisoformat(valor)
    except (TypeError, ValueError):
        return None, {
            "status": "error",
            "code": "invalid_date",
            "message": f"{campo} deve estar no formato YYYY-MM-DD.",
        }
    if dia.isoformat() != valor:
        return None, {
            "status": "error",
            "code": "invalid_date",
            "message": f"{campo} deve estar no formato YYYY-MM-DD.",
        }
    return dia, None


def _timestamp(valor: str, campo: str) -> tuple[Optional[datetime], Optional[dict]]:
    if not isinstance(valor, str) or not valor.strip():
        return None, {
            "status": "error",
            "code": "invalid_datetime",
            "message": f"Informe {campo} como timestamp ISO 8601 com fuso horário.",
        }
    try:
        instante = datetime.fromisoformat(valor.strip().replace("Z", "+00:00"))
    except ValueError:
        instante = None
    if instante is None or instante.tzinfo is None or instante.utcoffset() is None:
        return None, {
            "status": "error",
            "code": "invalid_datetime",
            "message": f"{campo} deve ser um timestamp ISO 8601 com fuso, por exemplo 2026-10-05T14:00:00-03:00.",
        }
    return instante, None


def _fechar(conn, cur) -> None:
    for recurso in (cur, conn):
        if recurso is not None:
            try:
                recurso.close()
            except Exception:
                pass


def _erro_banco(operacao: str, erro: Exception) -> dict:
    logger.error("Falha ao %s na agenda (%s)", operacao, type(erro).__name__)
    return {
        "status": "error",
        "code": "database_error",
        "message": f"Não foi possível {operacao} agora. Tente novamente.",
    }


class AddEventArgs(BaseModel):
    title: str = Field(..., min_length=1, description="Título do compromisso.")
    source_text: str = Field(..., min_length=1, description="Texto original do pedido do usuário.")
    start_time: str = Field(..., description="Início em ISO 8601 com fuso horário.")
    end_time: str = Field(..., description="Fim em ISO 8601 com fuso horário; obrigatório após esclarecer a duração.")
    location: Optional[str] = Field(default=None, description="Local opcional.")
    notes: Optional[str] = Field(default=None, description="Observações opcionais.")


@tool("add_event", args_schema=AddEventArgs)
def add_event(
    title: str,
    source_text: str,
    start_time: str,
    end_time: str,
    location: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """Grava um compromisso na agenda local se o horário estiver livre."""
    titulo = title.strip() if isinstance(title, str) else ""
    origem = source_text.strip() if isinstance(source_text, str) else ""
    if not titulo:
        return {"status": "error", "code": "invalid_title", "message": "Informe o título do compromisso."}
    if not origem:
        return {"status": "error", "code": "invalid_source_text", "message": "Informe o texto original do pedido."}

    inicio, erro = _timestamp(start_time, "start_time")
    if erro:
        return erro
    fim, erro = _timestamp(end_time, "end_time")
    if erro:
        return erro
    if fim <= inicio:
        return {"status": "error", "code": "invalid_time_range", "message": "O horário final deve ser posterior ao horário inicial."}

    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        # Serializa novas gravações para que dois pedidos simultâneos não
        # passem pela verificação de conflito antes de qualquer INSERT.
        cur.execute("LOCK TABLE events IN SHARE ROW EXCLUSIVE MODE;")
        cur.execute(
            """
            SELECT id, title, start_time, end_time, location
            FROM events
            WHERE start_time < %s
              AND (end_time > %s OR end_time IS NULL)
            ORDER BY start_time ASC;
            """,
            (fim, inicio),
        )
        candidatos = cur.fetchall()
        conflitos = []
        duracao_desconhecida = []
        for row in candidatos:
            evento_inicio = row[2]
            evento_fim = row[3]
            if evento_fim is None:
                # Sem horário final não há como provar que um compromisso
                # anterior terminou antes do novo. Isso inclui eventos que
                # começaram no dia anterior (ou antes).
                duracao_desconhecida.append(row)
            elif evento_inicio < fim and evento_fim > inicio:
                conflitos.append(row)

        if duracao_desconhecida:
            conn.rollback()
            return {
                "status": "error",
                "code": "unknown_existing_duration",
                "message": "Há compromisso no período sem horário final registrado; não consigo confirmar se há conflito.",
                "events": [
                    {"id": row[0], "title": row[1], "start_time": row[2].isoformat()}
                    for row in duracao_desconhecida
                ],
            }
        if conflitos:
            conn.rollback()
            return {
                "status": "error",
                "code": "event_conflict",
                "message": "O horário solicitado se sobrepõe a um compromisso existente.",
                "events": [
                    {
                        "id": row[0],
                        "title": row[1],
                        "start_time": row[2].isoformat(),
                        "end_time": row[3].isoformat(),
                        "location": row[4],
                    }
                    for row in conflitos
                ],
            }

        cur.execute(
            """
            INSERT INTO events (title, start_time, end_time, location, notes, source_text)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, start_time, end_time;
            """,
            (titulo, inicio, fim, location, notes, origem),
        )
        event_id, salvo_inicio, salvo_fim = cur.fetchone()
        conn.commit()
        return {
            "status": "ok",
            "id": event_id,
            "title": titulo,
            "start_time": salvo_inicio.isoformat(),
            "end_time": salvo_fim.isoformat(),
            "location": location,
            "notes": notes,
        }
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return _erro_banco("gravar o compromisso", exc)
    finally:
        _fechar(conn, cur)


class QueryEventsArgs(BaseModel):
    text: Optional[str] = Field(default=None, description="Texto parcial em título, local, notas ou pedido original.")
    date_local: Optional[str] = Field(default=None, description="Um dia local em America/Sao_Paulo, formato YYYY-MM-DD.")
    date_from_local: Optional[str] = Field(default=None, description="Data inicial local inclusiva, formato YYYY-MM-DD.")
    date_to_local: Optional[str] = Field(default=None, description="Data final local inclusiva, formato YYYY-MM-DD.")
    limit: int = Field(default=20, ge=1, le=200, description="Máximo de resultados (1 a 200).")


@tool("query_events", args_schema=QueryEventsArgs)
def query_events(
    text: Optional[str] = None,
    date_local: Optional[str] = None,
    date_from_local: Optional[str] = None,
    date_to_local: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """Consulta compromissos por texto e/ou dia ou intervalo local."""
    if date_local and (date_from_local or date_to_local):
        return {
            "status": "error",
            "code": "conflicting_date_filters",
            "message": "Use date_local ou date_from_local/date_to_local, não ambos.",
        }

    dia, erro = _data_local(date_local, "date_local")
    if erro:
        return erro
    inicio_dia, erro = _data_local(date_from_local, "date_from_local")
    if erro:
        return erro
    fim_dia, erro = _data_local(date_to_local, "date_to_local")
    if erro:
        return erro
    if inicio_dia and fim_dia and fim_dia < inicio_dia:
        return {"status": "error", "code": "invalid_date_range", "message": "A data final não pode ser anterior à inicial."}
    try:
        limite = int(limit)
    except (TypeError, ValueError):
        limite = 0
    if not 1 <= limite <= 200:
        return {"status": "error", "code": "invalid_limit", "message": "O limite deve estar entre 1 e 200."}

    filtros = []
    params: dict[str, object] = {"limit": limite}
    if text and text.strip():
        filtros.append(
            "(e.title ILIKE %(texto)s OR e.location ILIKE %(texto)s "
            "OR e.notes ILIKE %(texto)s OR e.source_text ILIKE %(texto)s)"
        )
        params["texto"] = f"%{text.strip()}%"

    lower_date = dia or inicio_dia
    upper_date = dia or fim_dia
    if lower_date:
        params["data_inicio"] = datetime.combine(lower_date, time.min, FUSO_LOCAL)
        params["dia_inicio"] = lower_date
        filtros.append(
            "(e.end_time > %(data_inicio)s OR e.start_time >= %(data_inicio)s "
            "OR (e.end_time IS NULL AND "
            "(e.start_time AT TIME ZONE 'America/Sao_Paulo')::date >= %(dia_inicio)s::date))"
        )
    if upper_date:
        filtros.append("e.start_time < %(data_fim)s")
        params["data_fim"] = datetime.combine(upper_date + timedelta(days=1), time.min, FUSO_LOCAL)
    where_sql = " AND ".join(filtros) if filtros else "TRUE"

    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT e.id, e.title, e.start_time, e.end_time, e.location,
                   e.notes, e.source_text, e.recorded_at
            FROM events e
            WHERE {where_sql}
            ORDER BY e.start_time ASC
            LIMIT %(limit)s;
            """,
            params,
        )
        rows = cur.fetchall()
        events = [
            {
                "id": row[0],
                "title": row[1],
                "start_time": row[2].isoformat(),
                "end_time": row[3].isoformat() if row[3] else None,
                "location": row[4],
                "notes": row[5],
                "source_text": row[6],
                "recorded_at": row[7].isoformat() if row[7] else None,
            }
            for row in rows
        ]
        return {"status": "ok", "count": len(events), "results": events}
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return _erro_banco("consultar a agenda", exc)
    finally:
        _fechar(conn, cur)


TOOLS_AGENDA = [add_event, query_events]
