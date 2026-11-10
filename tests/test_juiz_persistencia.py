import json
import unittest
from unittest.mock import patch
from uuid import uuid4
from types import SimpleNamespace

from app import graph, juiz
from app.tools import financeiro, propostas


class Cursor:
    def __init__(self):
        self.params = None
    def execute(self, sql, params=None):
        self.params = params
    def fetchone(self):
        return (17, "2026-11-09")
    def close(self):
        pass


class Connection:
    def __init__(self):
        self.cur = Cursor()
        self.committed = False
    def cursor(self):
        return self.cur
    def commit(self):
        self.committed = True
    def rollback(self):
        pass
    def close(self):
        pass


class DeferredWritesTests(unittest.TestCase):
    def test_real_and_dollar_transactions_use_shared_writer(self):
        for source, amount, expected in [("Paguei R$ 20", 20, 20), ("Paguei US$ 20", 20, 108)]:
            with self.subTest(source=source):
                conn = Connection()
                with patch.object(financeiro, "get_conn", return_value=conn):
                    result = financeiro.add_transaction.invoke({"amount": amount, "source_text": source})
                self.assertEqual((result["status"], result["id"], result["amount"]), ("ok", 17, expected))
                self.assertEqual(conn.cur.params[0], expected)
                self.assertEqual(conn.committed, True)

    def test_proposals_do_not_access_database(self):
        with patch.object(financeiro, "get_conn") as db:
            result = propostas.preparar_add_transaction.invoke({"amount": 20, "source_text": "Paguei R$ 20"})
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["arguments"]["amount"], 20)
        db.assert_not_called()

    def test_rejected_or_missing_verdict_never_writes(self):
        proposta = {"operation": "add_transaction", "arguments": {"amount": 20, "source_text": "R$ 20"}}
        for approved in (False, None):
            with self.subTest(approved=approved), patch.object(graph, "add_transaction") as tool:
                result = graph.no_efetivar_escritas({"pending_writes": [proposta], "juiz_aprovou": approved, "messages": [{"role": "assistant", "content": "Reprovado."}]})
                self.assertEqual(result["pending_writes"], [])
                tool.invoke.assert_not_called()

    def test_local_failure_never_calls_google_and_partial_google_is_reported(self):
        proposta = {"operation": "add_event", "arguments": {}}
        base = {"pending_writes": [proposta], "juiz_aprovou": True}
        with patch.object(graph, "add_event") as local_tool, patch.object(graph, "add_google_event") as google:
            local_tool.invoke.return_value = {"status": "error", "message": "Horário indisponível."}
            result = graph.no_efetivar_escritas(base)
            google.invoke.assert_not_called()
            self.assertIn("Horário indisponível", result["messages"][0]["content"])
        local = {"status": "ok", "id": 1, "title": "Consulta", "start_time": "2026-11-09T10:00:00-03:00", "end_time": "2026-11-09T11:00:00-03:00"}
        with patch.object(graph, "add_event") as local_tool, patch.object(graph, "add_google_event") as google:
            local_tool.invoke.return_value = local
            google.invoke.return_value = {"status": "error"}
            result = graph.no_efetivar_escritas(base)
            self.assertIn("agenda local", result["messages"][0]["content"])
            self.assertIn("não confirmei", result["messages"][0]["content"])
            google.invoke.assert_called_once()

    def test_multiple_results_distinguish_committed_and_failed(self):
        propostas_turno = [{"operation": "add_transaction", "arguments": {}}, {"operation": "add_transaction", "arguments": {}}]
        with patch.object(graph, "add_transaction") as tool:
            tool.invoke.side_effect = [{"status": "ok", "id": 1, "amount": 10}, {"status": "error", "message": "Categoria inválida."}]
            result = graph.no_efetivar_escritas({"pending_writes": propostas_turno, "juiz_aprovou": True})
        self.assertIn("Registrei R$ 10,00", result["messages"][0]["content"])
        self.assertIn("Categoria inválida", result["messages"][0]["content"])

    def test_judge_receives_proposals_and_rejects_on_missing_key(self):
        state = {"messages": [{"role": "user", "content": "Paguei R$ 20"}, {"role": "assistant", "content": "Ainda não registrei."}], "pending_writes": [{"operation": "add_transaction", "arguments": {"amount": 20}}]}
        with patch.object(juiz, "TYPESAFE_API_KEY", None):
            self.assertFalse(juiz.no_juiz(state)["juiz_aprovou"])
        with patch.object(juiz, "_julgar_jev", return_value={"juiz_aprovou": True}) as judge:
            juiz.no_juiz(state)
        self.assertEqual(judge.call_args.args[3], state["pending_writes"])

    def test_judge_sends_proposal_to_jev_and_uses_verdict(self):
        proposal = [{"operation": "add_transaction", "arguments": {"amount": 20, "source_text": "Paguei R$ 20"}}]
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return json.dumps({"answers": {"veredito": {"choice": "apoiada"}, "apoiada": {"noul": 0.9}}}).encode()
        with patch.object(juiz, "TYPESAFE_API_KEY", "test"), patch.object(juiz.urllib.request, "urlopen", return_value=Response()) as request:
            result = juiz._julgar_jev("Paguei R$ 20", "Ainda não registrei.", "", proposal)
        self.assertTrue(result["juiz_aprovou"])
        body = json.loads(request.call_args.args[0].data)
        self.assertEqual(body["state"]["propostas"], proposal)
        self.assertIn("proposta", body["questions"]["veredito"]["instructions"])
        self.assertIn("null", body["questions"]["veredito"]["instructions"])
        self.assertIn("null", body["questions"]["veredito"]["criteria"]["apoiada"])
        self.assertGreaterEqual(juiz.LIMIAR, 0.70)

    def test_graph_approval_then_new_turn_rejection_does_not_repeat_write(self):
        user = "Paguei R$ 20"
        proposal = propostas.preparar_add_transaction.invoke({"amount": 20, "source_text": user})
        output = {"messages": [{"role": "user", "content": user}, {"role": "tool", "name": "add_transaction", "content": json.dumps(proposal)}]}
        config = {"configurable": {"thread_id": str(uuid4())}}
        initial = {"messages": [{"role": "user", "content": user}], "pending_writes": [], "juiz_aprovou": False, "agentes_chamados": [], "mapa_pii": {}, "evidencia_turno": ""}
        verdicts = [{"juiz_aprovou": True, "agentes_chamados": ["juiz:aprovado"]}, {"juiz_aprovou": False, "agentes_chamados": ["juiz:rejeitado"], "messages": [{"role": "assistant", "content": "Reprovado."}]}]
        with patch.object(graph, "guardrail_entrada", return_value={"bloqueado": False}), patch.object(graph, "guardrail_saida", side_effect=lambda text, _map: {"bloqueado": False, "conteudo": text}), patch.object(graph, "router_model") as router, patch.object(graph, "financeiro_app") as financeiro_app, patch.object(juiz, "_julgar_jev", side_effect=verdicts), patch.object(graph, "add_transaction") as write:
            router.invoke.return_value = SimpleNamespace(content="ROUTE=financeiro")
            financeiro_app.invoke.return_value = output
            write.invoke.return_value = {"status": "ok", "id": 1, "amount": 20}
            first = graph.fluxo_agentes.invoke(initial, config=config)
            second = graph.fluxo_agentes.invoke(initial, config=config)
        self.assertIn("Registrei", first["messages"][-1].content)
        self.assertEqual(second["messages"][-1].content, "Reprovado. Nenhuma proposta foi gravada.")
        write.invoke.assert_called_once()


if __name__ == "__main__":
    unittest.main()
