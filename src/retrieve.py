import chromadb

client = chromadb.PersistentClient(path="db")
collection = client.get_or_create_collection("corpus")

def retrieve(topic, n=3):
    hits = collection.query(query_texts=[topic], n_results=n)
    chunks = []
    for cid, text, meta in zip(hits["ids"][0], hits["documents"][0],
                               hits["metadatas"][0]):
        chunks.append({"id": cid, "text": text, "section": meta["section"], "pages": meta["pages"]})
    return chunks