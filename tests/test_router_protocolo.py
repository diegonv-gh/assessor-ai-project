import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import graph


class RouterProtocolTests(unittest.TestCase):
    def _route(self, model_output, question):
        router = SimpleNamespace(
            invoke=lambda *args, **kwargs: model_output,
        )
        with patch.object(graph, "router_model", router):
            return graph.no_roteador(
                {"messages": [{"role": "human", "content": question}]},
                {},
            )

    def test_empty_router_output_routes_prior_conversation_question_to_finance(self):
        output = SimpleNamespace(content="", tool_calls=[])
        result = self._route(output, "Na conversa anterior, qual meta financeira combinamos?")
        self.assertEqual(result["rota"], "financeiro")
        self.assertNotIn("messages", result)
        self.assertEqual(graph.decidir_especialista(result), "financeiro")

    def test_malformed_protocol_routes_profile_request_to_finance(self):
        output = SimpleNamespace(content="Encaminhamento: ROUTE=financeiro", tool_calls=[])
        result = self._route(output, "Qual objetivo financeiro cadastrei no perfil?")
        self.assertEqual(result["rota"], "financeiro")
        self.assertNotIn("messages", result)

    def test_empty_unrecognized_output_returns_nonempty_safe_response(self):
        output = SimpleNamespace(content="", tool_calls=[])
        result = self._route(output, "... ")
        self.assertEqual(result["rota"], "fim")
        self.assertTrue(result["messages"][0]["content"].strip())

    def test_valid_protocol_keeps_normal_route(self):
        output = SimpleNamespace(content="ROUTE=agenda", tool_calls=[])
        result = self._route(output, "Marque uma reunião amanhã")
        self.assertEqual(result["rota"], "agenda")


if __name__ == "__main__":
    unittest.main()
