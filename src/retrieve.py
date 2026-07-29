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