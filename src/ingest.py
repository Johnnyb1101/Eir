from pypdf import PdfReader
import chromadb

client = chromadb.PersistentClient(path="db")
collection = client.get_or_create_collection("corpus")

DOC = "corpus/Blunt_Abdominal_Trauma_Splenectomy_Vaccination_13_May_2020_ID09.pdf"

SECTIONS = [
    ("Background", 2, 3),
    ("Overwhelming Post-Splenectomy Infection", 3, 3),
    ("Vaccine Candidates", 3, 3),
    ("Vaccine Dosing", 4, 4),
    ("Vaccine Administration Time", 4, 5),
    ("Vaccine Documentation", 5, 5),
    ("PI Monitoring", 6, 7),
    ("Appendix B", 9, 9),
]

reader = PdfReader(DOC)
ids, texts, metas = [], [], []
for i, (name, start, end) in enumerate(SECTIONS):
    text = ""
    for p in range(start - 1, end):
        text += reader.pages[p].extract_text()
    ids.append(f"bat-vacc-2020-s{i}")
    texts.append(text)
    metas.append({"source": DOC, "section": name, "pages": f"{start}-{end}"})

collection.add(ids=ids, documents=texts, metadatas=metas)
print(f"Ingested {len(ids)} chunks")