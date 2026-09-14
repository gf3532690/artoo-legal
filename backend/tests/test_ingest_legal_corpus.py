"""批量入库脚本（``app/scripts/ingest_legal_corpus.py``）单测。

不连真实服务：HTTP 用 ``httpx.MockTransport`` 顶替，验证分批、断点续跑、失败入报告、
以及预检会拦下服务端一定会 skip 的文件。
"""

from __future__ import annotations

import os as _os

_os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-production")

import csv
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from app.scripts import ingest_legal_corpus as ingest


def _write_docx(path: Path, size: int = 0) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
    if size:
        with open(path, "ab") as handle:
            handle.write(b"0" * size)


def _write_list(path: Path, names: list[str]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["filename", "law_name"])
        writer.writeheader()
        for name in names:
            writer.writerow({"filename": name, "law_name": "某条例"})


class TestReadIngestList:
    def test_reads_csv_produced_by_audit(self, tmp_path: Path) -> None:
        path = tmp_path / "list.csv"
        _write_list(path, ["甲条例.docx", "乙条例.docx"])

        assert ingest.read_ingest_list(str(path)) == ["甲条例.docx", "乙条例.docx"]

    def test_reads_plain_text_list(self, tmp_path: Path) -> None:
        path = tmp_path / "list.txt"
        path.write_text("甲条例.docx\n\n乙条例.docx\n", encoding="utf-8")

        assert ingest.read_ingest_list(str(path)) == ["甲条例.docx", "乙条例.docx"]


class TestPreflight:
    def test_flags_missing_extension_size_and_bad_name(self, tmp_path: Path) -> None:
        _write_docx(tmp_path / "ok.docx")
        _write_docx(tmp_path / "big.docx", size=2 * 1024 * 1024)
        (tmp_path / "note.txt").write_text("x", encoding="utf-8")

        usable, problems = ingest.preflight(
            ["ok.docx", "big.docx", "note.txt", "bad<>name.docx", "gone.docx"],
            str(tmp_path), max_file_mb=1.0,
        )

        assert usable == ["ok.docx"]
        reasons = {name: reason for name, reason, _ in problems}
        assert reasons["big.docx"] == "too_large"
        assert reasons["note.txt"] == "extension"
        assert reasons["bad<>name.docx"] == "bad_name"
        assert reasons["gone.docx"] == "missing"


class TestBatches:
    def test_splits_evenly_and_keeps_order(self) -> None:
        assert ingest._batches(["a", "b", "c", "d", "e"], 2) == [["a", "b"], ["c", "d"], ["e"]]


class TestUploadBatch:
    def test_posts_multipart_with_paths_json(self, tmp_path: Path) -> None:
        _write_docx(tmp_path / "甲条例.docx")
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            body = request.read().decode("utf-8", "ignore")
            seen["has_name"] = "甲条例.docx" in body
            seen["path"] = "/api/knowledge-bases/kb-1/documents/upload-folder"
            seen["authorization"] = request.headers.get("authorization")
            return httpx.Response(201, json={
                "total_files": 1, "uploaded_count": 1, "skipped_count": 0,
                "created_folders": [],
                "results": [{"relative_path": "甲条例.docx", "filename": "甲条例.docx",
                             "doc_id": "doc-1", "status": "uploaded", "message": None}],
            })

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            rows = ingest.upload_batch(
                client, base_url="http://localhost:8000", kb_id="kb-1",
                corpus=str(tmp_path), names=["甲条例.docx"],
            )

        assert seen["path"] in seen["url"]
        assert seen["has_name"] is True
        assert rows == [{"filename": "甲条例.docx", "status": "uploaded",
                         "message": "", "doc_id": "doc-1"}]

    def test_missing_result_becomes_unknown(self, tmp_path: Path) -> None:
        _write_docx(tmp_path / "甲条例.docx")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(201, json={"results": []})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            rows = ingest.upload_batch(
                client, base_url="http://x", kb_id="kb", corpus=str(tmp_path),
                names=["甲条例.docx"],
            )

        assert rows[0]["status"] == "unknown"


class TestMain:
    def test_dry_run_makes_no_request(self, tmp_path: Path, capsys) -> None:
        _write_docx(tmp_path / "甲条例.docx")
        list_path = tmp_path / "list.csv"
        _write_list(list_path, ["甲条例.docx"])

        exit_code = ingest.main([
            "--list", str(list_path), "--corpus", str(tmp_path), "--dry-run",
        ])

        assert exit_code == 0
        assert "可用 1" in capsys.readouterr().out

    def test_resume_skips_submitted_and_reports(self, tmp_path: Path, monkeypatch) -> None:
        _write_docx(tmp_path / "甲条例.docx")
        _write_docx(tmp_path / "乙条例.docx")
        list_path = tmp_path / "list.csv"
        _write_list(list_path, ["甲条例.docx", "乙条例.docx"])
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps({"submitted": ["甲条例.docx"]}), encoding="utf-8")
        report_path = tmp_path / "report.csv"
        posted: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = request.read().decode("utf-8", "ignore")
            posted.append(body)
            return httpx.Response(201, json={
                "results": [{"filename": "乙条例.docx", "status": "uploaded", "message": None}],
            })

        # ``ingest.httpx`` 就是全局 httpx 模块，必须抓住原类再替换，否则 lambda 递归调用自己。
        real_client = httpx.Client
        monkeypatch.setattr(
            httpx, "Client",
            lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
        )
        exit_code = ingest.main([
            "--list", str(list_path), "--corpus", str(tmp_path), "--kb-id", "kb-1",
            "--state", str(state_path), "--report", str(report_path),
        ])

        assert exit_code == 0
        assert len(posted) == 1                      # 只上传了乙
        assert "乙条例.docx" in posted[0]
        assert "甲条例.docx" not in posted[0]
        with open(report_path, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert [r["filename"] for r in rows] == ["乙条例.docx"]

    def test_transport_failure_is_recorded(self, tmp_path: Path, monkeypatch) -> None:
        _write_docx(tmp_path / "甲条例.docx")
        list_path = tmp_path / "list.csv"
        _write_list(list_path, ["甲条例.docx"])
        report_path = tmp_path / "report.csv"

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        real_client = httpx.Client
        monkeypatch.setattr(
            httpx, "Client",
            lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
        )
        ingest.main([
            "--list", str(list_path), "--corpus", str(tmp_path), "--kb-id", "kb-1",
            "--report", str(report_path), "--retries", "0", "--timeout", "1",
        ])

        with open(report_path, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert rows[0]["status"] == "error"
        assert "ConnectError" in rows[0]["message"]
