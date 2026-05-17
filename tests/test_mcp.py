"""Tests for MCP integration (C3): Server, Client, Tools, Transport, Registry."""

from __future__ import annotations

import asyncio
import json

import pytest

from researchclaw.mcp.client import MCPClient
from researchclaw.mcp.registry import MCPServerRegistry
from researchclaw.mcp.server import ResearchClawMCPServer
from researchclaw.mcp.tools import TOOL_DEFINITIONS, get_tool_schema, list_tool_names
from researchclaw.mcp.transport import SSETransport, StdioTransport

# ══════════════════════════════════════════════════════════════════
# MCP Tools tests
# ══════════════════════════════════════════════════════════════════


class TestMCPTools:
    def test_tool_definitions_not_empty(self) -> None:
        assert len(TOOL_DEFINITIONS) >= 6

    def test_all_tools_have_required_fields(self) -> None:
        for tool in TOOL_DEFINITIONS:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"

    def test_get_tool_schema_exists(self) -> None:
        schema = get_tool_schema("run_pipeline")
        assert schema is not None
        assert schema["name"] == "run_pipeline"

    def test_get_tool_schema_missing(self) -> None:
        assert get_tool_schema("nonexistent") is None

    def test_list_tool_names(self) -> None:
        names = list_tool_names()
        assert "run_pipeline" in names
        assert "get_pipeline_status" in names
        assert "search_literature" in names

    def test_run_pipeline_requires_topic(self) -> None:
        schema = get_tool_schema("run_pipeline")
        assert schema is not None
        assert "topic" in schema["inputSchema"]["required"]

    def test_get_paper_has_format_enum(self) -> None:
        schema = get_tool_schema("get_paper")
        assert schema is not None
        props = schema["inputSchema"]["properties"]
        assert "format" in props
        assert "enum" in props["format"]


# ══════════════════════════════════════════════════════════════════
# MCP Server tests
# ══════════════════════════════════════════════════════════════════


class TestMCPServer:
    def test_get_tools(self) -> None:
        server = ResearchClawMCPServer()
        tools = server.get_tools()
        assert len(tools) >= 6
        names = [t["name"] for t in tools]
        assert "run_pipeline" in names

    def test_handle_unknown_tool(self) -> None:
        server = ResearchClawMCPServer()
        result = asyncio.run(server.handle_tool_call("nonexistent", {}))
        assert result["success"] is False
        assert "Unknown tool" in result["error"]

    def test_handle_run_pipeline(self) -> None:
        server = ResearchClawMCPServer()
        result = asyncio.run(server.handle_tool_call("run_pipeline", {"topic": "GNN"}))
        assert result["success"] is True
        assert "GNN" in result["message"]
        assert not result["run_id"].startswith("mcp-stub-")

    def test_tool_call_logging_redacts_sensitive_arguments(self, caplog) -> None:
        server = ResearchClawMCPServer()
        with caplog.at_level("INFO", logger="researchclaw.mcp.server"):
            result = asyncio.run(
                server.handle_tool_call(
                    "run_pipeline",
                    {
                        "topic": "GNN",
                        "api_key": "sk-secret",
                        "nested": {"auth_token": "tok-secret"},
                    },
                )
            )

        assert result["success"] is True
        assert "sk-secret" not in caplog.text
        assert "tok-secret" not in caplog.text
        assert "[REDACTED]" in caplog.text

    def test_handle_run_pipeline_creates_trackable_run(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)

        server = ResearchClawMCPServer()
        result = asyncio.run(
            server.handle_tool_call("run_pipeline", {"topic": "Graph Neural Networks"})
        )

        run_dir = tmp_path / "artifacts" / result["run_id"]
        checkpoint = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
        progress = json.loads((run_dir / "progress.json").read_text(encoding="utf-8"))

        assert result["success"] is True
        assert result["status"] == "queued"
        assert result["output_dir"] == str(run_dir)
        assert checkpoint["topic"] == "Graph Neural Networks"
        assert checkpoint["status"] == "queued"
        assert progress["run_id"] == result["run_id"]

        status = asyncio.run(
            server.handle_tool_call("get_pipeline_status", {"run_id": result["run_id"]})
        )
        assert status["success"] is True
        assert status["checkpoint"]["status"] == "queued"

    def test_handle_get_status_missing_run(self) -> None:
        server = ResearchClawMCPServer()
        result = asyncio.run(server.handle_tool_call("get_pipeline_status", {"run_id": "nonexistent"}))
        assert result["success"] is False

    def test_handle_search_literature(self) -> None:
        server = ResearchClawMCPServer()
        result = asyncio.run(server.handle_tool_call("search_literature", {"query": "transformers"}))
        assert result["success"] is True

    def test_handle_search_literature_uses_literature_module(self, monkeypatch) -> None:
        from researchclaw.literature.models import Author, Paper

        def fake_search(query: str, limit: int = 10) -> list[Paper]:
            assert query == "transformers"
            assert limit == 2
            return [
                Paper(
                    paper_id="paper-1",
                    title="Attention Is All You Need",
                    authors=(Author("Vaswani"),),
                    year=2017,
                    url="https://example.test/attention",
                    source="test",
                )
            ]

        monkeypatch.setattr("researchclaw.literature.search.search_papers", fake_search)

        server = ResearchClawMCPServer()
        result = asyncio.run(
            server.handle_tool_call(
                "search_literature",
                {"query": "transformers", "limit": 2},
            )
        )

        assert result["success"] is True
        assert result["query"] == "transformers"
        assert result["count"] == 1
        assert result["results"][0]["title"] == "Attention Is All You Need"
        assert "stub" not in json.dumps(result).lower()

    def test_handle_review_paper(self) -> None:
        server = ResearchClawMCPServer()
        result = asyncio.run(server.handle_tool_call("review_paper", {"paper_path": "/tmp/paper.md"}))
        assert result["success"] is False
        assert "Paper not found" in result["error"]

    def test_handle_review_paper_reads_and_scores_markdown(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        paper_path = tmp_path / "artifacts" / "rc-test" / "paper.md"
        paper_path.parent.mkdir(parents=True)
        paper_path.write_text(
            "# Sample Paper\n\n"
            "## Abstract\n"
            "This paper proposes a compact method with measurable results.\n\n"
            "## Introduction\n"
            "Prior work motivates the problem and the benchmark.\n\n"
            "## Methods\n"
            "We train the model and compare against a baseline.\n\n"
            "## Results\n"
            "The proposed method improves accuracy by 2.0 points [1].\n\n"
            "## References\n"
            "[1] Example Reference.\n",
            encoding="utf-8",
        )

        server = ResearchClawMCPServer()
        result = asyncio.run(
            server.handle_tool_call(
                "review_paper",
                {"paper_path": str(paper_path)},
            )
        )

        assert result["success"] is True
        assert result["paper_path"] == str(paper_path.resolve())
        assert result["review"]["word_count"] > 20
        assert result["review"]["section_count"] >= 6
        assert result["review"]["citation_count"] == 2
        assert result["review"]["missing_sections"] == []
        assert "stub" not in json.dumps(result).lower()

    def test_handle_review_paper_rejects_paths_outside_artifacts(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        paper_path = tmp_path / "outside.md"
        paper_path.write_text("# Secret\n", encoding="utf-8")

        server = ResearchClawMCPServer()
        result = asyncio.run(
            server.handle_tool_call(
                "review_paper",
                {"paper_path": str(paper_path)},
            )
        )

        assert result["success"] is False
        assert "artifacts" in result["error"]

    def test_start_stop(self) -> None:
        server = ResearchClawMCPServer()
        assert not server.is_running

        async def _run() -> None:
            await server.start()
            assert server.is_running
            await server.stop()
            assert not server.is_running

        asyncio.run(_run())

    def test_handle_get_results_missing(self) -> None:
        server = ResearchClawMCPServer()
        result = asyncio.run(server.handle_tool_call("get_experiment_results", {"run_id": "missing"}))
        assert result["success"] is False

    def test_handle_get_paper_missing(self) -> None:
        server = ResearchClawMCPServer()
        result = asyncio.run(server.handle_tool_call("get_paper", {"run_id": "missing"}))
        assert result["success"] is False


# ══════════════════════════════════════════════════════════════════
# MCP Client tests
# ══════════════════════════════════════════════════════════════════


class TestMCPClient:
    def test_init(self) -> None:
        client = MCPClient("http://localhost:3000")
        assert client.uri == "http://localhost:3000"
        assert not client.is_connected

    def test_connect_disconnect(self) -> None:
        client = MCPClient("http://localhost:3000")

        async def _run() -> None:
            await client.connect()
            assert client.is_connected
            await client.disconnect()
            assert not client.is_connected

        asyncio.run(_run())

    def test_list_tools_not_connected(self) -> None:
        client = MCPClient("http://localhost:3000")
        with pytest.raises(ConnectionError):
            asyncio.run(client.list_tools())

    def test_call_tool_not_connected(self) -> None:
        client = MCPClient("http://localhost:3000")
        with pytest.raises(ConnectionError):
            asyncio.run(client.call_tool("test", {}))

    def test_list_resources_not_connected(self) -> None:
        client = MCPClient("http://localhost:3000")
        with pytest.raises(ConnectionError):
            asyncio.run(client.list_resources())

    def test_read_resource_not_connected(self) -> None:
        client = MCPClient("http://localhost:3000")
        with pytest.raises(ConnectionError):
            asyncio.run(client.read_resource("test://resource"))

    def test_list_tools_connected(self) -> None:
        client = MCPClient("http://localhost:3000")

        async def _run() -> list:
            await client.connect()
            return await client.list_tools()

        tools = asyncio.run(_run())
        assert isinstance(tools, list)
        assert any(tool["name"] == "run_pipeline" for tool in tools)

    def test_call_tool_connected_uses_local_researchclaw_server(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        client = MCPClient("local://researchclaw")

        async def _run() -> dict:
            await client.connect()
            return await client.call_tool("run_pipeline", {"topic": "MCP local client"})

        result = asyncio.run(_run())

        assert result["success"] is True
        assert result["status"] == "queued"
        assert (tmp_path / "artifacts" / result["run_id"] / "checkpoint.json").exists()

    def test_tools_cached(self) -> None:
        client = MCPClient("http://localhost:3000")

        async def _run() -> tuple:
            await client.connect()
            t1 = await client.list_tools()
            t2 = await client.list_tools()
            return t1, t2

        t1, t2 = asyncio.run(_run())
        assert t1 is t2


# ══════════════════════════════════════════════════════════════════
# MCP Server Registry tests
# ══════════════════════════════════════════════════════════════════


class TestMCPServerRegistry:
    def test_register_and_list(self) -> None:
        async def _run() -> list:
            reg = MCPServerRegistry()
            await reg.register("test", "http://localhost:3000")
            return reg.list_all()

        servers = asyncio.run(_run())
        assert len(servers) == 1
        assert servers[0]["name"] == "test"
        assert servers[0]["connected"] is True

    def test_unregister(self) -> None:
        async def _run() -> int:
            reg = MCPServerRegistry()
            await reg.register("test", "http://localhost:3000")
            await reg.unregister("test")
            return reg.count

        count = asyncio.run(_run())
        assert count == 0

    def test_get(self) -> None:
        async def _run() -> MCPClient | None:
            reg = MCPServerRegistry()
            await reg.register("test", "http://localhost:3000")
            return reg.get("test")

        client = asyncio.run(_run())
        assert client is not None
        assert client.is_connected

    def test_get_missing(self) -> None:
        reg = MCPServerRegistry()
        assert reg.get("nonexistent") is None

    def test_register_replaces_existing_server_and_disconnects_old_client(
        self,
        monkeypatch,
    ) -> None:
        disconnected: list[str] = []

        class FakeClient:
            def __init__(self, uri: str, transport: str = "stdio") -> None:
                self.uri = uri
                self.transport = transport
                self.is_connected = False

            async def connect(self) -> None:
                self.is_connected = True

            async def disconnect(self) -> None:
                disconnected.append(self.uri)
                self.is_connected = False

        monkeypatch.setattr("researchclaw.mcp.registry.MCPClient", FakeClient)

        async def _run() -> tuple[list[str], str]:
            reg = MCPServerRegistry()
            await reg.register("same", "local://old")
            await reg.register("same", "local://new")
            client = reg.get("same")
            assert client is not None
            return disconnected, client.uri

        disconnected_uris, active_uri = asyncio.run(_run())

        assert disconnected_uris == ["local://old"]
        assert active_uri == "local://new"

    def test_close_all(self) -> None:
        async def _run() -> int:
            reg = MCPServerRegistry()
            await reg.register("a", "http://a:3000")
            await reg.register("b", "http://b:3000")
            await reg.close_all()
            return reg.count

        count = asyncio.run(_run())
        assert count == 0


# ══════════════════════════════════════════════════════════════════
# Transport tests
# ══════════════════════════════════════════════════════════════════


class TestSSETransport:
    def test_start_stop(self) -> None:
        transport = SSETransport(port=9999)
        assert transport.host == "127.0.0.1"

        async def _run() -> None:
            await transport.start()
            assert transport._running is True
            await transport.close()
            assert transport._running is False

        asyncio.run(_run())

    def test_receive_waits_for_injected_message(self) -> None:
        transport = SSETransport()

        async def _run() -> dict:
            await transport.start()
            await transport.inject_message({"jsonrpc": "2.0", "id": 1, "method": "ping"})
            message = await transport.receive()
            await transport.close()
            return message

        assert asyncio.run(_run()) == {"jsonrpc": "2.0", "id": 1, "method": "ping"}

    def test_send_records_sse_frame(self) -> None:
        transport = SSETransport()

        async def _run() -> str:
            await transport.start()
            await transport.send({"jsonrpc": "2.0", "result": {"ok": True}})
            await transport.close()
            return transport.sent_events[-1]

        frame = asyncio.run(_run())
        assert frame.startswith("data: ")
        assert '"jsonrpc": "2.0"' in frame
        assert frame.endswith("\n\n")

    def test_sent_events_are_bounded(self) -> None:
        transport = SSETransport(max_sent_events=3)

        async def _run() -> list[str]:
            await transport.start()
            for idx in range(5):
                await transport.send({"jsonrpc": "2.0", "id": idx})
            await transport.close()
            return list(transport.sent_events)

        frames = asyncio.run(_run())
        assert len(frames) == 3
        assert '"id": 2' in frames[0]
        assert '"id": 4' in frames[-1]


class TestStdioTransport:
    def test_start_uses_running_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeWriteTransport:
            def is_closing(self) -> bool:
                return False

            def close(self) -> None:
                return None

        async def _run() -> None:
            running_loop = asyncio.get_running_loop()

            def fail_get_event_loop() -> asyncio.AbstractEventLoop:
                raise AssertionError("StdioTransport.start must use get_running_loop")

            async def fake_read_pipe(_factory: object, _pipe: object) -> None:
                return None

            async def fake_write_pipe(_factory: object, _pipe: object) -> tuple[object, object]:
                return FakeWriteTransport(), object()

            monkeypatch.setattr(asyncio, "get_event_loop", fail_get_event_loop)
            monkeypatch.setattr(running_loop, "connect_read_pipe", fake_read_pipe)
            monkeypatch.setattr(running_loop, "connect_write_pipe", fake_write_pipe)

            transport = StdioTransport()
            await transport.start()

            assert transport._reader is not None
            assert transport._writer is not None

        asyncio.run(_run())


class TestContext7MCPClient:
    def test_context7_client_source_has_no_print_calls(self) -> None:
        from pathlib import Path

        source = Path("researchclaw/mcp/context7_client.py").read_text(encoding="utf-8")

        assert "print(" not in source

    def test_context7_subprocess_env_filters_secrets(self, monkeypatch) -> None:
        from researchclaw.mcp.context7_client import Context7MCPClient

        captured: dict[str, object] = {}

        class FakeProcess:
            stdin = None
            stdout = None

            def poll(self) -> None:
                return None

        def fake_popen(*args, **kwargs):
            captured["env"] = kwargs.get("env")
            return FakeProcess()

        monkeypatch.setenv("OPENAI_API_KEY", "secret")
        monkeypatch.setenv("PATH", "keep-path")
        monkeypatch.setattr("researchclaw.mcp.context7_client._find_context7_binary", lambda: ["ctx"])
        monkeypatch.setattr("subprocess.Popen", fake_popen)

        client = Context7MCPClient()
        assert client._ensure_started() is False
        env = captured["env"]
        assert isinstance(env, dict)
        assert env.get("PATH") == "keep-path"
        assert "OPENAI_API_KEY" not in env

    def test_stop_logs_failed_kill(self, caplog) -> None:
        from researchclaw.mcp.context7_client import Context7MCPClient

        class BrokenProcess:
            def terminate(self) -> None:
                raise OSError("terminate failed")

            def wait(self, timeout: int) -> None:
                raise AssertionError("wait should not be called")

            def kill(self) -> None:
                raise OSError("kill failed")

        client = Context7MCPClient()
        client._proc = BrokenProcess()  # type: ignore[assignment]

        with caplog.at_level("DEBUG", logger="researchclaw.mcp.context7_client"):
            client.close()

        assert client._proc is None
        assert "Context7 MCP terminate failed" in caplog.text
        assert "Context7 MCP kill failed" in caplog.text

    def test_tool_timeout_stops_subprocess(self, monkeypatch) -> None:
        from researchclaw.mcp.context7_client import Context7MCPClient

        class HangingProcess:
            def __init__(self) -> None:
                self.terminated = False
                self.waited = False

            def poll(self) -> None:
                return None

            def terminate(self) -> None:
                self.terminated = True

            def wait(self, timeout: int) -> None:
                self.waited = True

        process = HangingProcess()
        client = Context7MCPClient(timeout_sec=1)
        client._proc = process  # type: ignore[assignment]
        monkeypatch.setattr(client, "_ensure_started", lambda: True)
        monkeypatch.setattr(client, "_send", lambda message: None)
        monkeypatch.setattr(client, "_recv", lambda timeout=30, expected_id=None: None)

        assert client._call_tool("query-docs", {"libraryId": "/x", "query": "y"}) is None
        assert process.terminated is True
        assert process.waited is True
        assert client._proc is None

    def test_recv_ignores_stale_json_rpc_response_ids(self) -> None:
        from researchclaw.mcp.context7_client import Context7MCPClient

        class FakeStdout:
            def __init__(self) -> None:
                self.lines = [
                    b'{"jsonrpc": "2.0", "id": 1, "result": "stale"}\n',
                    b'{"jsonrpc": "2.0", "id": 2, "result": "fresh"}\n',
                ]

            def readline(self) -> bytes:
                return self.lines.pop(0)

        class FakeProcess:
            stdout = FakeStdout()

        client = Context7MCPClient(timeout_sec=1)
        client._proc = FakeProcess()  # type: ignore[assignment]

        response = client._recv(timeout=1, expected_id=2)

        assert response == {"jsonrpc": "2.0", "id": 2, "result": "fresh"}
