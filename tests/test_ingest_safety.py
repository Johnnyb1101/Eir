import chromadb
import pytest
from chromadb.api.models.Collection import Collection

from src import ingest


@pytest.fixture
def corpus_setup(tmp_path, monkeypatch):
    # Exercise actual database operations without downloading an embedding model.
    original_upsert = Collection.upsert

    def local_upsert(self, **kwargs):
        kwargs["embeddings"] = [[1.0, 0.0] for _ in kwargs["ids"]]
        return original_upsert(self, **kwargs)

    monkeypatch.setattr(Collection, "upsert", local_upsert)
    client = chromadb.PersistentClient(path=str(tmp_path / "db"))
    old = client.create_collection("corpus")
    old.upsert(ids=["old-id"], documents=["Old source text"],
               metadatas=[{"source": "Old document"}])
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "new.pdf").touch()
    monkeypatch.setattr(ingest, "read_lines", lambda path: [
        (1, "BACKGROUND ..... 2"), (2, "BACKGROUND"),
        (2, "New source text"),
    ])
    return client, corpus


def assert_old_corpus(client):
    assert {c.name for c in client.list_collections()} == {"corpus"}
    result = client.get_collection("corpus").get()
    assert result["ids"] == ["old-id"]
    assert result["documents"] == ["Old source text"]
    assert result["metadatas"] == [{"source": "Old document"}]


def test_unreadable_pdf_preserves_corpus(corpus_setup, monkeypatch):
    client, corpus = corpus_setup

    def unreadable(path):
        raise ValueError("Unreadable PDF")

    monkeypatch.setattr(ingest, "read_lines", unreadable)
    with pytest.raises(ValueError, match="Unreadable PDF"):
        ingest.ingest_corpus(client, corpus)
    assert_old_corpus(client)


@pytest.mark.parametrize("empty_directory", [True, False])
def test_empty_input_preserves_corpus(corpus_setup, monkeypatch, empty_directory):
    client, corpus = corpus_setup
    if empty_directory:
        (corpus / "new.pdf").unlink()
    else:
        monkeypatch.setattr(ingest, "chunk_document", lambda lines: [])
    with pytest.raises(ValueError, match="No usable chunks"):
        ingest.ingest_corpus(client, corpus)
    assert_old_corpus(client)


def test_failed_insertion_preserves_corpus(corpus_setup, monkeypatch):
    client, corpus = corpus_setup
    original_upsert = Collection.upsert

    def partial_failure(self, **kwargs):
        original_upsert(self, **kwargs)
        raise RuntimeError("Insertion failed after writing")

    monkeypatch.setattr(Collection, "upsert", partial_failure)
    with pytest.raises(RuntimeError, match="Insertion failed"):
        ingest.ingest_corpus(client, corpus)
    assert_old_corpus(client)


def test_failed_promotion_restores_old_name(corpus_setup, monkeypatch):
    client, corpus = corpus_setup
    original_modify = Collection.modify

    def fail_promotion(self, **kwargs):
        if self.name == ingest.STAGING_COLLECTION:
            raise RuntimeError("Promotion failed")
        return original_modify(self, **kwargs)

    monkeypatch.setattr(Collection, "modify", fail_promotion)
    with pytest.raises(RuntimeError, match="Promotion failed"):
        ingest.ingest_corpus(client, corpus)
    assert_old_corpus(client)


def test_next_run_recovers_interrupted_promotion(corpus_setup):
    client, corpus = corpus_setup
    client.create_collection(ingest.STAGING_COLLECTION)
    client.get_collection("corpus").modify(name=ingest.BACKUP_COLLECTION)
    (corpus / "new.pdf").unlink()

    with pytest.raises(ValueError, match="No usable chunks"):
        ingest.ingest_corpus(client, corpus)
    assert_old_corpus(client)


def test_successful_replacement_removes_old_chunks(corpus_setup):
    client, corpus = corpus_setup
    ingest.ingest_corpus(client, corpus)

    assert {c.name for c in client.list_collections()} == {"corpus"}
    result = client.get_collection("corpus").get()
    assert result["ids"] == ["new-s0"]
    assert result["documents"] == ["New source text\n"]
    assert result["metadatas"][0]["source"] == "new"
    assert result["metadatas"][0]["pages"] == "2-2"


def test_recovery_after_promotion_keeps_new_corpus(corpus_setup):
    client, corpus = corpus_setup
    client.get_collection("corpus").modify(name=ingest.BACKUP_COLLECTION)
    new = client.create_collection("corpus")
    new.upsert(ids=["new-id"], documents=["New source text"])

    ingest.recover_ingestion(client)

    assert {c.name for c in client.list_collections()} == {"corpus"}
    assert client.get_collection("corpus").get()["ids"] == ["new-id"]
