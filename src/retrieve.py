import chromadb

client = chromadb.PersistentClient(path="db")
collection = client.get_or_create_collection("corpus")

MAX_DISTANCE = 1.2

def retrieve(topic, n=10):
    hits = collection.query(query_texts=[topic], n_results=n)
    chunks = []
    for cid, text, meta, dist in zip(hits["ids"][0], hits["documents"][0],
                                     hits["metadatas"][0], hits["distances"][0]):
        if dist > MAX_DISTANCE:
            continue
        chunks.append({"id": cid, "text": text, "section": meta["section"],
                       "pages": meta["pages"], "source": meta["source"], "distance": dist})
    return chunks

def document_sections(chunks):
    if not chunks:
        return []
    sources = {c["source"] for c in chunks}
    hits = collection.get(where={"source": {"$in": sorted(sources)}}, include=["metadatas"])
    sections = []
    for cid, meta in zip(hits["ids"], hits["metadatas"]):
        sections.append({"id": cid, "section": meta["section"],
                         "pages": meta["pages"], "source": meta["source"]})
    return sections

def entry_query(entry):
    query = f"{entry.title}. {entry.objective}"
    return query