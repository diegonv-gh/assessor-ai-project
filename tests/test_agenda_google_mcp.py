import asyncio
import json
import os
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.tools import agenda, calendario_google


class FakeCursor:
    def __init__(self, rows=None, inserted=None, failure=None):
        self.rows = rows or []
        self.inserted = inserted
        self.failure = failure
        self.executed = []
        self.closed = False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if self.failure:
            raise self.failure

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.inserted

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class AgendaDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.start = "2026-10-05T00:30:00-03:00"
        self.end = "2026-10-05T01:30:00-03:00"

    def _event_row(self, event_start, event_end, event_id=7):
        tz = ZoneInfo("America/Sao_Paulo")
        return (
            event_id,
            "Consulta",
            datetime.fromisoformat(event_start).astimezone(tz),
            datetime.fromisoformat(event_end).astimezone(tz) if event_end else None,
            "Clínica",
        )

    def test_add_event_rejects_invalid_or_naive_times_without_database_access(self):
        with patch.object(agenda, "get_conn") as get_conn:
            result = agenda.add_event.invoke({
                "title": "Consulta", "source_text": "marcar consulta",
                "start_time": "2026-10-05T10:00:00", "end_time": self.end,
            })
        self.assertEqual(result["code"], "invalid_datetime")
        get_conn.assert_not_called()

    def test_add_event_detects_overlap_started_on_previous_local_day(self):
        cursor = FakeCursor(rows=[self._event_row("2026-10-04T23:30:00-03:00", "2026-10-05T01:00:00-03:00")])
        conn = FakeConnection(cursor)
        with patch.object(agenda, "get_conn", return_value=conn):
            result = agenda.add_event.invoke({
                "title": "Reunião", "source_text": "marcar reunião",
                "start_time": self.start, "end_time": self.end,
            })
        self.assertEqual(result["code"], "event_conflict")
        self.assertEqual(result["events"][0]["id"], 7)
        self.assertTrue(conn.rolled_back)
        self.assertTrue(conn.closed)

    def test_add_event_warns_for_unknown_duration_even_if_started_previous_day(self):
        cursor = FakeCursor(rows=[self._event_row("2026-10-04T23:30:00-03:00", None)])
        conn = FakeConnection(cursor)
        with patch.object(agenda, "get_conn", return_value=conn):
            result = agenda.add_event.invoke({
                "title": "Reunião", "source_text": "marcar reunião",
                "start_time": self.start, "end_time": self.end,
            })
        self.assertEqual(result["code"], "unknown_existing_duration")
        self.assertEqual(result["events"][0]["id"], 7)
        self.assertTrue(conn.rolled_back)

    def test_add_event_commits_and_returns_real_id_when_slot_is_free(self):
        cursor = FakeCursor(inserted=(25, datetime.fromisoformat(self.start), datetime.fromisoformat(self.end)))
        conn = FakeConnection(cursor)
        with patch.object(agenda, "get_conn", return_value=conn):
            result = agenda.add_event.invoke({
                "title": "Reunião", "source_text": "marcar reunião",
                "start_time": self.start, "end_time": self.end,
            })
        self.assertEqual((result["status"], result["id"]), ("ok", 25))
        self.assertTrue(conn.committed)
        self.assertTrue(cursor.closed)
        self.assertTrue(conn.closed)
        self.assertEqual(len(cursor.executed), 3)  # lock, overlap check, insert

    def test_query_events_uses_local_inclusive_interval_and_returns_rows(self):
        inicio = datetime.fromisoformat("2026-10-05T10:00:00-03:00")
        fim = datetime.fromisoformat("2026-10-05T11:00:00-03:00")
        registrado = datetime.fromisoformat("2026-10-01T12:00:00-03:00")
        cursor = FakeCursor(rows=[(25, "Reunião", inicio, fim, "Sala 3", "Notas", "pedido", registrado)])
        conn = FakeConnection(cursor)
        with patch.object(agenda, "get_conn", return_value=conn):
            result = agenda.query_events.invoke({"date_from_local": "2026-10-05", "date_to_local": "2026-10-07"})
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["id"], 25)
        params = cursor.executed[0][1]
        self.assertEqual(params["data_fim"].isoformat(), "2026-10-08T00:00:00-03:00")
        self.assertIn("e.end_time > %(data_inicio)s", cursor.executed[0][0])
        self.assertIn("ORDER BY e.start_time ASC", cursor.executed[0][0])

    def test_query_events_rejects_invalid_or_mixed_dates_without_database_access(self):
        for filtros, codigo in (
            ({"date_local": "2026-02-30"}, "invalid_date"),
            ({"date_local": "2026-10-05", "date_from_local": "2026-10-05"}, "conflicting_date_filters"),
            ({"date_from_local": "2026-10-08", "date_to_local": "2026-10-05"}, "invalid_date_range"),
        ):
            with self.subTest(filtros=filtros), patch.object(agenda, "get_conn") as get_conn:
                result = agenda.query_events.invoke(filtros)
                self.assertEqual(result["code"], codigo)
                get_conn.assert_not_called()

    def test_database_failure_rolls_back_and_closes_without_leaking_details(self):
        cursor = FakeCursor(failure=RuntimeError("secret-host details"))
        conn = FakeConnection(cursor)
        with patch.object(agenda, "get_conn", return_value=conn):
            result = agenda.query_events.invoke({})
        self.assertEqual(result["code"], "database_error")
        self.assertNotIn("secret-host", result["message"])
        self.assertTrue(conn.rolled_back)
        self.assertTrue(conn.closed)


class GoogleCalendarClientTests(unittest.TestCase):
    def setUp(self):
        self.args = {
            "title": "Reunião", "start_time": "2026-10-05T15:00:00-03:00",
            "end_time": "2026-10-05T16:00:00-03:00", "location": "Sala 3",
        }

    def test_google_tool_requires_aware_iso_timestamps_and_valid_range(self):
        result = calendario_google.add_google_event.invoke({**self.args, "start_time": "2026-10-05T15:00:00"})
        self.assertEqual(result["code"], "invalid_datetime")
        result = calendario_google.add_google_event.invoke({**self.args, "end_time": "2026-10-05T14:00:00-03:00"})
        self.assertEqual(result["code"], "invalid_time_range")

    def test_structured_and_text_mcp_responses_are_normalized_and_id_required(self):
        structured = SimpleNamespace(
            structured_content={"event": {"id": "google-1", "summary": "Reunião"}}, content=[], is_error=False,
        )
        self.assertEqual(calendario_google._resultado_evento(calendario_google._conteudo_resultado(structured))["id"], "google-1")
        text = SimpleNamespace(
            structured_content=None,
            content=[SimpleNamespace(text='{"eventId":"google-2","summary":"Reunião"}')],
            is_error=False,
        )
        self.assertEqual(calendario_google._resultado_evento(calendario_google._conteudo_resultado(text))["id"], "google-2")
        no_id = calendario_google._resultado_evento({"result": "Evento criado"})
        self.assertEqual(no_id["code"], "google_event_unconfirmed")

    def test_mcp_creation_success_requires_google_id(self):
        resposta = {"event": {"id": "google-25", "summary": "Reunião", "startTime": self.args["start_time"]}}
        with patch.object(calendario_google, "_access_token", return_value="token"), \
             patch.object(calendario_google, "_chamar_mcp", new=AsyncMock(return_value=resposta)) as chamar:
            result = calendario_google._executar_criacao_google({
                "summary": "Reunião", "startTime": self.args["start_time"],
                "endTime": self.args["end_time"], "timeZone": "America/Sao_Paulo",
            })
        self.assertEqual((result["status"], result["id"]), ("ok", "google-25"))
        chamar.assert_awaited_once()

    def test_no_token_returns_auth_required_without_calling_mcp(self):
        with patch.object(calendario_google, "_access_token", return_value=None), \
             patch.object(calendario_google, "_chamar_mcp", new=AsyncMock()) as chamar:
            result = calendario_google._executar_criacao_google({"summary": "R"})
        self.assertEqual(result["code"], "google_auth_required")
        chamar.assert_not_awaited()

    def test_token_refresh_failure_is_distinct_and_does_not_call_calendar(self):
        with patch.object(calendario_google, "_access_token", side_effect=RuntimeError("expired")), \
             patch.object(calendario_google, "_chamar_mcp", new=AsyncMock()) as chamar:
            result = calendario_google._executar_criacao_google({"summary": "R"})
        self.assertEqual(result["code"], "google_token_error")
        chamar.assert_not_awaited()

    def test_expired_token_is_refreshed_and_saved_when_refresh_token_exists(self):
        class Credencial:
            expired = True
            refresh_token = "refresh"
            valid = True
            token = "old"

            def refresh(self, _request):
                self.expired = False
                self.token = "new"

        credencial = Credencial()
        token_path = SimpleNamespace(is_file=lambda: True)
        with patch.object(calendario_google, "ARQUIVO_TOKEN", token_path), \
             patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=credencial), \
             patch("google.auth.transport.requests.Request", return_value=object()), \
             patch.object(calendario_google, "_salvar_token") as salvar:
            result = calendario_google._credenciais_usuario()
        self.assertIs(result, credencial)
        self.assertEqual(credencial.token, "new")
        salvar.assert_called_once_with(credencial)

    def test_public_catalog_call_returns_catalog_without_creating_events(self):
        resultado = [{"name": "create_event", "description": "Cria evento", "inputSchema": {"required": ["summary"]}}]
        with patch.object(calendario_google, "_listar_tools_mcp", new=AsyncMock(return_value=resultado)):
            result = calendario_google.listar_tools_google()
        self.assertEqual(result["tools"][0]["name"], "create_event")
        self.assertEqual(result["tools"][0]["inputSchema"]["required"], ["summary"])

    def test_explicit_service_disabled_uses_rest_with_same_token(self):
        with patch.object(calendario_google, "_access_token", return_value="token"), \
             patch.object(calendario_google, "_chamar_mcp", new=AsyncMock(return_value={"status": "mcp_disabled"})), \
             patch.object(calendario_google, "_criar_via_rest", return_value={"status": "ok", "id": "rest-1"}) as rest:
            result = calendario_google._executar_criacao_google({"summary": "R", "startTime": self.args["start_time"], "endTime": self.args["end_time"], "timeZone": "America/Sao_Paulo"})
        self.assertEqual(result["id"], "rest-1")
        rest.assert_called_once()
        self.assertEqual(rest.call_args.args[1], "token")

    @unittest.skipUnless(sys.version_info >= (3, 11), "ExceptionGroup disponível no Python 3.11+")
    def test_explicit_disabled_service_inside_sdk_exception_group_uses_rest(self):
        class HttpFailure(Exception):
            response = SimpleNamespace(
                status_code=403,
                text='{"error":{"status":"SERVICE_DISABLED","message":"Calendar MCP API is disabled"}}',
            )

        async def falhar(_nome, _argumentos, _token):
            raise ExceptionGroup("MCP response", [HttpFailure()])

        with patch.object(calendario_google, "_access_token", return_value="token"), \
             patch.object(calendario_google, "_chamar_mcp", side_effect=falhar), \
             patch.object(calendario_google, "_criar_via_rest", return_value={"status": "ok", "id": "rest-2"}) as rest:
            result = calendario_google._executar_criacao_google({
                "summary": "R", "startTime": self.args["start_time"],
                "endTime": self.args["end_time"], "timeZone": "America/Sao_Paulo",
            })
        self.assertEqual(result["id"], "rest-2")
        rest.assert_called_once()

    def test_timeout_does_not_fallback_or_retry_creation(self):
        with patch.object(calendario_google, "_access_token", return_value="token"), \
             patch.object(calendario_google, "_chamar_mcp", new=AsyncMock(side_effect=TimeoutError())), \
             patch.object(calendario_google, "_criar_via_rest") as rest:
            result = calendario_google._executar_criacao_google({"summary": "R", "startTime": self.args["start_time"], "endTime": self.args["end_time"], "timeZone": "America/Sao_Paulo"})
        self.assertEqual(result["code"], "google_mcp_uncertain")
        rest.assert_not_called()

    def test_environment_token_takes_precedence_and_missing_oauth_is_reported(self):
        with patch.object(calendario_google, "GOOGLE_CALENDAR_ACCESS_TOKEN", " env-token "), \
             patch.object(calendario_google, "_credenciais_usuario") as saved:
            self.assertEqual(calendario_google._access_token(), "env-token")
            saved.assert_not_called()
        with patch.object(calendario_google, "_arquivo_oauth", return_value=None):
            result = calendario_google.autenticar()
        self.assertEqual(result["code"], "oauth_credentials_missing")

    def test_disabled_service_classifier_requires_explicit_service_disabled_signal(self):
        self.assertTrue(calendario_google._servico_mcp_desabilitado({"message": "Calendar MCP API is disabled"}))
        self.assertFalse(calendario_google._servico_mcp_desabilitado({"message": "Permission denied"}, 403))


class GraphAndMcpContractTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        # O grafo mantém clientes Mongo persistentes ao ser importado; libere-os
        # após os testes isolados para não deixar sockets abertos no processo.
        import sys

        for nome in ("app.memory", "app.perfil"):
            modulo = sys.modules.get(nome)
            cliente = getattr(modulo, "_mongo", None) if modulo else None
            if cliente is not None:
                cliente.close()

    def test_google_write_requires_successful_local_event_in_current_turn(self):
        from app import graph

        request = SimpleNamespace(
            tool_call={"name": "add_google_event", "id": "google-call", "args": {}},
            state={"messages": [{"role": "human", "content": "agenda"}]},
        )
        result = graph._ordenar_gravacoes_agenda.wrap_tool_call(request, lambda _request: "called")
        self.assertIsInstance(result, graph.ToolMessage)
        self.assertEqual(json.loads(result.content)["code"], "google_requires_local_success")

    def test_google_write_follows_local_success_and_matches_saved_fields(self):
        from app import graph

        local = {
            "status": "ok", "id": 25, "title": "Reunião",
            "start_time": "2026-10-05T15:00:00-03:00", "end_time": "2026-10-05T16:00:00-03:00",
            "location": "Sala 3", "notes": "Pauta",
        }
        request = SimpleNamespace(
            tool_call={
                "name": "add_google_event", "id": "google-call",
                "args": {
                    "title": "Reunião", "start_time": local["start_time"], "end_time": local["end_time"],
                    "location": "Sala 3", "description": "Pauta",
                },
            },
            state={"messages": [
                {"role": "human", "content": "agenda"},
                {"role": "tool", "name": "add_event", "content": json.dumps(local)},
            ]},
        )
        self.assertEqual(graph._ordenar_gravacoes_agenda.wrap_tool_call(request, lambda _request: "called"), "called")

    def test_google_write_is_not_repeated_after_an_uncertain_attempt(self):
        from app import graph

        local = {
            "status": "ok", "id": 25, "title": "Reunião",
            "start_time": "2026-10-05T15:00:00-03:00", "end_time": "2026-10-05T16:00:00-03:00",
        }
        args = {"title": "Reunião", "start_time": local["start_time"], "end_time": local["end_time"]}
        request = SimpleNamespace(
            tool_call={"name": "add_google_event", "id": "google-retry", "args": args},
            state={"messages": [
                {"role": "human", "content": "agenda"},
                {"role": "tool", "name": "add_event", "content": json.dumps(local)},
                {"role": "tool", "name": "add_google_event", "content": json.dumps({"status": "error", "code": "google_mcp_uncertain"})},
            ]},
        )
        chamado = False

        def handler(_request):
            nonlocal chamado
            chamado = True
            return "called"

        result = graph._ordenar_gravacoes_agenda.wrap_tool_call(request, handler)
        self.assertEqual(json.loads(result.content)["code"], "google_creation_already_attempted")
        self.assertFalse(chamado)

    def test_node_reports_partial_success_when_google_fails_after_local_commit(self):
        from app import graph

        messages = [
            {"role": "human", "content": "crie compromisso"},
            {"role": "tool", "name": "add_event", "content": json.dumps({"status": "ok", "id": 25, "title": "Reunião"})},
            {"role": "tool", "name": "add_google_event", "content": json.dumps({"status": "error", "code": "google_auth_required"})},
        ]
        with patch.object(graph.agenda_app, "invoke", return_value={"messages": messages}):
            resultado = graph.no_agenda({"messages": messages[:1]}, {})
        dados = json.loads(resultado["messages"][0]["content"])
        self.assertEqual(dados["escrita"], {"operacao": "adicionar", "id": 25, "google": "falhou"})
        self.assertIn("agenda local", dados["resposta"])
        self.assertIn("Google Calendar", dados["resposta"])

    def test_node_does_not_confirm_model_claim_without_real_local_id(self):
        from app import graph

        saida_falsa = {
            "messages": [
                {"role": "human", "content": "crie compromisso"},
                {"role": "assistant", "content": json.dumps({"dominio": "agenda", "intencao": "criar", "resposta": "Criei", "recomendacao": "", "escrita": {"id": 999}})},
            ]
        }
        with patch.object(graph.agenda_app, "invoke", return_value=saida_falsa):
            resultado = graph.no_agenda({"messages": saida_falsa["messages"][:1]}, {})
        dados = json.loads(resultado["messages"][0]["content"])
        self.assertNotIn("escrita", dados)
        self.assertIn("não consegui confirmar", dados["resposta"].lower())

    def test_mcp_server_exposes_seven_tools_and_agenda_annotations(self):
        from app.mcp_server import mcp

        tools = asyncio.run(mcp.list_tools())
        self.assertEqual(len(tools), 7)
        by_name = {item.name: item for item in tools}
        self.assertTrue(by_name["query_events"].annotations.read_only_hint)
        self.assertFalse(by_name["add_event"].annotations.read_only_hint)
        self.assertFalse(by_name["add_event"].annotations.destructive_hint)
        schema = by_name["add_event"].input_schema
        self.assertEqual(set(schema["required"]), {"title", "source_text", "start_time", "end_time"})
        serialized = json.dumps(schema)
        self.assertNotIn("type_id", serialized)
        self.assertNotIn("category_id", serialized)

    def test_mcp_wrapper_forwards_arguments_and_preserves_return(self):
        import app.mcp_server as server

        valor = {"status": "error", "code": "event_conflict", "events": [{"id": 8}]}
        args = {
            "title": "Reunião", "source_text": "Pedido", "start_time": "2026-10-05T15:00:00-03:00",
            "end_time": "2026-10-05T16:00:00-03:00", "location": "Sala 3", "notes": "Pauta",
        }
        with patch.object(server, "_add_event") as add_event:
            add_event.invoke.return_value = valor
            result = server.add_event(**args)
        self.assertIs(result, valor)
        add_event.invoke.assert_called_once_with(args)

    def test_agenda_prompt_includes_a_fresh_sao_paulo_time_reference(self):
        from app.prompts import AGENDA_PROMPT_COMPLETO, agenda_prompt_atual

        prompt = agenda_prompt_atual()
        self.assertNotEqual(prompt, AGENDA_PROMPT_COMPLETO)
        self.assertRegex(prompt, r"Data e hora atual em America/Sao_Paulo: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")

    @unittest.skipUnless(os.environ.get("RUN_MCP_STDIO_TEST") == "1", "ativado na validação final do servidor stdio")
    def test_stdio_discovery_from_outside_project(self):
        # O cliente MCP real inicia o servidor com um cwd fora do projeto,
        # como fazem os hosts MCP que executam por caminho absoluto.
        project = Path(__file__).resolve().parents[1]
        servidor = project / "app" / "mcp_server.py"
        script = r'''
import asyncio, json
from mcp import Client
from mcp.client.stdio import StdioServerParameters
async def main():
    async with Client(StdioServerParameters(command=sys.executable, args=[str(servidor)])) as cliente:
        tools = await cliente.list_tools()
        print(json.dumps([tool.name for tool in tools.tools]))
import sys
servidor = sys.argv[1]
asyncio.run(main())
'''
        processo = subprocess.run(
            [sys.executable, "-c", script, str(servidor)], cwd=Path(sys.executable).parent,
            capture_output=True, text=True, timeout=40,
        )
        self.assertEqual(processo.returncode, 0, processo.stderr)
        self.assertEqual(len(json.loads(processo.stdout.strip().splitlines()[-1])), 7)

        ambiente_sem_banco = os.environ.copy()
        ambiente_sem_banco["DATABASE_URL"] = ""
        falha_config = subprocess.run(
            [sys.executable, str(servidor)], cwd=Path(sys.executable).parent,
            env=ambiente_sem_banco, capture_output=True, text=True, timeout=20,
        )
        self.assertNotEqual(falha_config.returncode, 0)
        self.assertEqual(falha_config.stdout, "")
        self.assertIn("DATABASE_URL ausente", falha_config.stderr)


if __name__ == "__main__":
    unittest.main()
