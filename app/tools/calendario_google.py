"""Cliente Python do MCP oficial do Google Calendar para a conta local."""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx
import httpx2
from langchain.tools import tool
from pydantic import BaseModel, Field

from app.config import (
    BASE_DIR,
    GOOGLE_CALENDAR_ACCESS_TOKEN,
    GOOGLE_CALENDAR_MCP_URL,
    GOOGLE_OAUTH_CREDENTIALS,
)

logger = logging.getLogger(__name__)
ARQUIVO_TOKEN = BASE_DIR / "google-calendar.token.json"
ESCOPOS = ["https://www.googleapis.com/auth/calendar.events"]
CABECALHOS_MCP = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


def _arquivo_oauth() -> Optional[Path]:
    if not GOOGLE_OAUTH_CREDENTIALS:
        return None
    caminho = Path(GOOGLE_OAUTH_CREDENTIALS)
    if not caminho.is_absolute():
        caminho = BASE_DIR / caminho
    return caminho if caminho.is_file() else None


def _salvar_token(credenciais) -> None:
    ARQUIVO_TOKEN.write_text(credenciais.to_json(), encoding="utf-8")


def _credenciais_usuario():
    if not ARQUIVO_TOKEN.is_file():
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    credenciais = Credentials.from_authorized_user_file(str(ARQUIVO_TOKEN), ESCOPOS)
    if credenciais.expired and credenciais.refresh_token:
        credenciais.refresh(Request())
        _salvar_token(credenciais)
    return credenciais if credenciais.valid else None


def _access_token() -> Optional[str]:
    if GOOGLE_CALENDAR_ACCESS_TOKEN and GOOGLE_CALENDAR_ACCESS_TOKEN.strip():
        return GOOGLE_CALENDAR_ACCESS_TOKEN.strip()
    credenciais = _credenciais_usuario()
    return credenciais.token if credenciais else None


def autenticar() -> dict:
    """Autoriza uma vez a conta Google desta instalação e salva seu token local."""
    arquivo = _arquivo_oauth()
    if arquivo is None:
        return {
            "status": "error",
            "code": "oauth_credentials_missing",
            "message": "Não encontrei gcp-oauth.keys.json na raiz do projeto.",
        }
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow

        fluxo = InstalledAppFlow.from_client_secrets_file(str(arquivo), ESCOPOS)
        credenciais = fluxo.run_local_server(port=0, prompt="consent")
        _salvar_token(credenciais)
        return {"status": "ok", "message": "Conta Google autorizada para esta instalação."}
    except Exception:
        logger.exception("Falha durante a autorização OAuth do Google Calendar")
        return {
            "status": "error",
            "code": "oauth_authorization_failed",
            "message": "Não foi possível concluir a autorização Google. Confira o terminal e tente novamente.",
        }


async def _listar_tools_mcp() -> list[dict]:
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    async with httpx2.AsyncClient(headers=CABECALHOS_MCP) as cliente_http:
        transporte = streamable_http_client(GOOGLE_CALENDAR_MCP_URL, http_client=cliente_http)
        async with Client(transporte) as cliente:
            resultado = await cliente.list_tools()
    return [
        {"name": item.name, "description": item.description, "inputSchema": item.input_schema}
        for item in resultado.tools
    ]


def listar_tools_google() -> dict:
    """Lista o catálogo público do MCP do Google Calendar sem criar eventos."""
    try:
        return {"status": "ok", "tools": asyncio.run(_listar_tools_mcp())}
    except Exception:
        logger.exception("Falha ao consultar o catálogo MCP do Google Calendar")
        return {"status": "error", "code": "mcp_catalog_unavailable", "message": "Não foi possível consultar o MCP do Google Calendar."}


def _conteudo_resultado(resultado: Any) -> dict:
    estruturado = getattr(resultado, "structured_content", None)
    if isinstance(estruturado, dict):
        return estruturado
    textos = [
        bloco.text
        for bloco in (getattr(resultado, "content", None) or [])
        if isinstance(getattr(bloco, "text", None), str)
    ]
    if not textos:
        return {"status": "error", "message": "O MCP do Google não retornou conteúdo."}
    bruto = "\n".join(textos)
    try:
        dados = json.loads(bruto)
    except json.JSONDecodeError:
        return {"status": "ok", "result": bruto}
    return dados if isinstance(dados, dict) else {"status": "ok", "result": dados}


def _mensagem(dados: dict) -> str:
    partes = [dados.get("message"), dados.get("error"), dados.get("result")]
    evento = dados.get("event")
    if isinstance(evento, dict):
        partes.append(evento.get("message"))
    return " ".join(str(parte) for parte in partes if parte is not None)


def _servico_mcp_desabilitado(dados: dict, status_code: Optional[int] = None) -> bool:
    texto = _mensagem(dados).casefold()
    indicio_desabilitado = any(
        frase in texto
        for frase in (
            "calendar mcp api has not been used",
            "calendar mcp api is disabled",
            "calendarmcp.googleapis.com has not been used",
            "service_disabled",
        )
    )
    if not indicio_desabilitado:
        return False
    return (
        status_code == 403
        or "has not been used" in texto
        or "is disabled" in texto
        or "service_disabled" in texto
    )


def _detalhes_erro_http(erro: Exception) -> tuple[Optional[int], str]:
    pendentes = [erro]
    vistos = set()
    while pendentes:
        atual = pendentes.pop()
        if id(atual) in vistos:
            continue
        vistos.add(id(atual))
        resposta = getattr(atual, "response", None)
        status = getattr(resposta, "status_code", None)
        texto = getattr(resposta, "text", "")
        if status is not None or texto:
            return status, texto if isinstance(texto, str) else ""
        pendentes.extend(getattr(atual, "exceptions", ()) or ())
        for vinculo in (getattr(atual, "__cause__", None), getattr(atual, "__context__", None)):
            if vinculo is not None:
                pendentes.append(vinculo)
    return None, ""


def _resultado_evento(dados: dict) -> dict:
    if dados.get("status") == "error":
        return dados
    evento = dados.get("event")
    if isinstance(evento, dict):
        dados = evento
    elif isinstance(dados.get("result"), dict):
        aninhado = dados["result"]
        if isinstance(aninhado.get("event"), dict):
            dados = aninhado["event"]
        else:
            dados = aninhado
    evento_id = dados.get("id") or dados.get("eventId")
    if not evento_id:
        return {
            "status": "error",
            "code": "google_event_unconfirmed",
            "message": "O Google não confirmou a criação do evento com um identificador.",
        }
    inicio = dados.get("inicio") or dados.get("startTime")
    if not inicio and isinstance(dados.get("start"), dict):
        inicio = dados["start"].get("dateTime") or dados["start"].get("date")
    return {
        "status": "ok",
        "id": evento_id,
        "title": dados.get("titulo") or dados.get("summary"),
        "start_time": inicio,
        "link": dados.get("link") or dados.get("htmlLink") or dados.get("html_link"),
    }


async def _chamar_mcp(nome_tool: str, argumentos: dict, token: str) -> dict:
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    async with httpx2.AsyncClient(headers={**CABECALHOS_MCP, "Authorization": f"Bearer {token}"}) as cliente_http:
        transporte = streamable_http_client(GOOGLE_CALENDAR_MCP_URL, http_client=cliente_http)
        async with Client(transporte) as cliente:
            resultado = await cliente.call_tool(nome_tool, argumentos)
    dados = _conteudo_resultado(resultado)
    if getattr(resultado, "is_error", False):
        dados["status"] = "error"
        dados.setdefault("message", "O MCP do Google informou uma falha na criação.")
    if dados.get("status") == "error" and _servico_mcp_desabilitado(dados):
        return {"status": "mcp_disabled", "message": _mensagem(dados)}
    return dados


def _criar_via_rest(payload: dict, token: str) -> dict:
    corpo = {
        "summary": payload["summary"],
        "start": {"dateTime": payload["startTime"], "timeZone": payload["timeZone"]},
        "end": {"dateTime": payload["endTime"], "timeZone": payload["timeZone"]},
    }
    if payload.get("location"):
        corpo["location"] = payload["location"]
    if payload.get("description"):
        corpo["description"] = payload["description"]
    try:
        resposta = httpx.post(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            headers={"Authorization": f"Bearer {token}", "content-type": "application/json"},
            json=corpo,
            timeout=30,
        )
    except httpx.HTTPError:
        logger.exception("Falha de rede ao criar evento pela Calendar API")
        return {"status": "error", "code": "google_rest_unavailable", "message": "A criação no Google ficou sem confirmação; não foi repetida."}
    try:
        dados = resposta.json()
    except ValueError:
        dados = {}
    if resposta.status_code >= 400:
        erro = dados.get("error", {}) if isinstance(dados, dict) else {}
        return {
            "status": "error",
            "code": "google_rest_error",
            "http_status": resposta.status_code,
            "message": erro.get("message", "A Calendar API recusou a criação do evento."),
        }
    return _resultado_evento(dados)


def _executar_criacao_google(payload: dict) -> dict:
    try:
        token = _access_token()
    except Exception:
        logger.exception("Não foi possível acessar a autorização local do Google Calendar")
        return {
            "status": "error",
            "code": "google_token_error",
            "message": "Não consegui carregar ou renovar o token Google; nenhuma criação foi tentada.",
        }
    if not token:
        return {
            "status": "error",
            "code": "google_auth_required",
            "message": "O Google Calendar ainda não foi autorizado. Execute autenticar() no terminal.",
        }
    try:
        resultado = asyncio.run(_chamar_mcp("create_event", payload, token))
    except Exception as exc:
        status_code, corpo = _detalhes_erro_http(exc)
        dados_erro = {"message": corpo or type(exc).__name__}
        if _servico_mcp_desabilitado(dados_erro, status_code):
            return _resultado_evento(_criar_via_rest(payload, token))
        logger.exception("Falha MCP ao criar evento; nenhuma repetição automática foi feita")
        return {
            "status": "error",
            "code": "google_mcp_uncertain",
            "message": "O resultado do Google ficou sem confirmação; a criação não foi repetida.",
        }
    if resultado.get("status") == "mcp_disabled":
        return _resultado_evento(_criar_via_rest(payload, token))
    if resultado.get("status") == "error":
        return resultado
    return _resultado_evento(resultado)


class AddGoogleEventArgs(BaseModel):
    title: str = Field(..., min_length=1, description="Título do evento no calendário principal da conta autorizada.")
    start_time: str = Field(..., description="Início ISO 8601 com fuso horário.")
    end_time: str = Field(..., description="Fim ISO 8601 com fuso horário; obrigatório.")
    location: Optional[str] = Field(default=None, description="Local opcional.")
    description: Optional[str] = Field(default=None, description="Descrição opcional.")


@tool("add_google_event", args_schema=AddGoogleEventArgs)
def add_google_event(
    title: str,
    start_time: str,
    end_time: str,
    location: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """Cria no Google Calendar o evento já registrado na agenda local."""
    if not title.strip():
        return {"status": "error", "code": "invalid_title", "message": "Informe o título do evento."}
    instantes = []
    for nome, valor in (("start_time", start_time), ("end_time", end_time)):
        try:
            instante = datetime.fromisoformat(valor.strip().replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            instante = None
        if instante is None or instante.tzinfo is None or instante.utcoffset() is None:
            return {
                "status": "error",
                "code": "invalid_datetime",
                "message": f"{nome} deve ser timestamp ISO 8601 com fuso horário.",
            }
        instantes.append(instante)
    if instantes[1] <= instantes[0]:
        return {
            "status": "error",
            "code": "invalid_time_range",
            "message": "O horário final deve ser posterior ao horário inicial.",
        }
    payload = {
        "summary": title.strip(),
        "startTime": start_time,
        "endTime": end_time,
        "timeZone": "America/Sao_Paulo",
    }
    if location:
        payload["location"] = location
    if description:
        payload["description"] = description
    return _executar_criacao_google(payload)


TOOLS_GOOGLE = [add_google_event]
