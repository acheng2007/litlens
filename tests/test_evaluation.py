import hashlib
from types import SimpleNamespace

import pytest
import respx
from pydantic import ValidationError
from typer.testing import CliRunner

from litlens.cli import app
from litlens.evaluation.corpus import pdf_bytes
from litlens.evaluation.datasets import (
    RealCorpus,
    group_recall,
    load_real_corpus,
    resolve_real_questions,
)
from litlens.evaluation.seed import fetch_papers


def label_rows():
    corpus = load_real_corpus()
    documents = [SimpleNamespace(source_key=d.sha256, status="ready") for d in corpus.documents]
    by_id = dict(zip((d.id for d in corpus.documents), documents))
    rows = [
        (SimpleNamespace(id=e.id, page=e.page, text=e.anchor), by_id[e.document])
        for e in corpus.evidence
    ]
    return corpus, rows, documents


def test_real_labels_resolve_by_revision_page_and_text_not_title():
    corpus, rows, docs = label_rows()
    questions = resolve_real_questions(corpus, rows, docs)
    assert len(questions) == 36
    assert sum(q["expected_supported"] for q in questions) == 27
    contrast = next(q for q in questions if q["id"] == "contrast-context")
    assert len(contrast["gold_groups"]) == 2
    assert group_recall(contrast["gold_groups"], ["mamba-5"]) == 0.5
    rows[0][0].page = 999
    with pytest.raises(ValueError, match="Missing gold evidence attention-1"):
        resolve_real_questions(corpus, rows, docs)


def test_real_corpus_rejects_missing_tampered_and_contaminated_libraries():
    corpus, rows, docs = label_rows()
    for invalid in [docs[:-1], docs + [SimpleNamespace(source_key="unrelated", status="ready")]]:
        with pytest.raises(ValueError, match="exactly the pinned"):
            resolve_real_questions(corpus, rows, invalid)
    docs[0].status = "queued"
    with pytest.raises(ValueError, match="Wait for every"):
        resolve_real_questions(corpus, rows, docs)
    docs[0].source_key = "wrong-revision"
    with pytest.raises(ValueError, match="exactly the pinned"):
        resolve_real_questions(corpus, rows, docs)


def test_alternative_chunks_do_not_dilute_evidence_recall():
    assert group_recall([{"one", "overlap"}, {"two"}], ["overlap", "two"]) == 1
    assert group_recall([{"one", "overlap"}, {"two"}], ["overlap"]) == 0.5
    assert group_recall([], []) is None


def test_dataset_validates_labels_and_pinned_urls():
    corpus = load_real_corpus().model_dump()
    corpus["questions"][0]["evidence"] = ["invented"]
    with pytest.raises(ValidationError, match="Unknown evidence"):
        RealCorpus.model_validate(corpus)
    corpus = load_real_corpus().model_dump()
    corpus["documents"][0]["pdf_url"] = "https://example.com/unpinned.pdf"
    with pytest.raises(ValidationError, match="pinned arXiv"):
        RealCorpus.model_validate(corpus)


@respx.mock
def test_seed_download_cache_and_checksum_failure(tmp_path, monkeypatch):
    import litlens.evaluation.seed as seed

    corpus = load_real_corpus()
    data = pdf_bytes({"section": "Test", "facts": [{"text": "A small test PDF."}]})
    paper = corpus.documents[0].model_copy(
        update={
            "sha256": hashlib.sha256(data).hexdigest(),
            "pages": 1,
        }
    )
    monkeypatch.setattr(seed, "load_real_corpus", lambda: SimpleNamespace(documents=[paper]))
    monkeypatch.setattr(seed.time, "sleep", lambda _: None)
    route = respx.get(paper.pdf_url).respond(200, content=data)
    _, paths = fetch_papers(tmp_path)
    fetch_papers(tmp_path)
    assert route.call_count == 1
    paths[0][1].write_bytes(b"not the pinned PDF")
    with pytest.raises(ValueError, match="checksum mismatch"):
        fetch_papers(tmp_path)
    assert route.call_count == 1


@respx.mock
def test_failed_download_does_not_pollute_cache(tmp_path, monkeypatch):
    import litlens.evaluation.seed as seed

    paper = load_real_corpus().documents[0]
    monkeypatch.setattr(seed, "load_real_corpus", lambda: SimpleNamespace(documents=[paper]))
    monkeypatch.setattr(seed.time, "sleep", lambda _: None)
    respx.get(paper.pdf_url).respond(200, content=b"wrong bytes")
    with pytest.raises(ValueError, match="checksum mismatch"):
        fetch_papers(tmp_path)
    assert list(tmp_path.iterdir()) == []


@respx.mock
def test_cli_selects_dataset_and_preserves_existing_report(tmp_path, monkeypatch):
    monkeypatch.setenv("LITLENS_API_URL", "http://api.test")
    route = respx.post("http://api.test/api/v1/evaluations").respond(201, json={"id": "report"})
    output = tmp_path / "report.json"
    args = ["evaluate", "--library-id", "lib", "--dataset", "real-papers", "--output", str(output)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert b'"dataset":"real-papers"' in route.calls[0].request.content
    assert CliRunner().invoke(app, args).exit_code != 0
    assert route.call_count == 1


def test_real_evaluation_api_is_scoped_and_rejects_incomplete_corpus(api):
    client, _ = api
    library = client.post("/api/v1/libraries", json={"name": "real test"}).json()["id"]
    payload = {"library_id": library, "dataset": "real-papers"}
    assert (
        client.post(
            "/api/v1/evaluations", json=payload, headers={"Authorization": "Bearer bob-token"}
        ).status_code
        == 404
    )
    result = client.post("/api/v1/evaluations", json=payload)
    assert result.status_code == 409
    assert "exactly the pinned" in result.json()["detail"]
    assert client.get("/api/v1/evaluations", params={"library_id": library}).json() == []
    assert (
        client.post("/api/v1/evaluations", json={**payload, "dataset": "typo"}).status_code == 422
    )
