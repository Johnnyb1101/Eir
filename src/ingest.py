from pypdf import PdfReader
import chromadb

def is_heading(line):
    return line.strip().isupper()

def read_lines(path):
    reader = PdfReader(path)
    lines = []
    for page_number, page in enumerate(reader.pages, start=1):
        for line in page.extract_text().split("\n"):
            lines.append((page_number, line))
    return lines

def clean_heading(line):
    return " ".join(line.split())

def furniture_key(line):
    return clean_heading(line).rstrip("0123456789 ")

def find_furniture(lines):
    page_count = lines[-1][0]
    counts = {}
    for page_number, line in lines:
        key = furniture_key(line)
        if key:
            counts[key] = counts.get(key, 0) + 1

    furniture = []
    for key, count in counts.items():
        if count > page_count / 2:
            furniture.append(key)
    return furniture

def chunk_document(lines):
    furniture = find_furniture(lines)
    chunks = []
    for page_number, line in lines:
        if furniture_key(line) in furniture:
            continue
        if is_heading(line):
            chunks.append({"section": clean_heading(line),
                           "start_page": page_number,
                           "end_page": page_number,
                           "text": ""})
        elif chunks:
            chunks[-1]["text"] += line + "\n"
            chunks[-1]["end_page"] = page_number
    return chunks

def keep_chunk(chunk):
    if not chunk["text"].strip():
        return False
    for skip in SKIP_HEADINGS:
        if skip in chunk["section"]:
            return False
    return True

client = chromadb.PersistentClient(path="db")
collection = client.get_or_create_collection("corpus")

DOC = "corpus/Blunt_Abdominal_Trauma_Splenectomy_Vaccination_13_May_2020_ID09.pdf"
SKIP_HEADINGS = ("TABLE OF CONTENTS", "JOINT TRAUMA SYSTEM")
TITLE = "Blunt Abdominal Trauma (JTS CPG 13 May 2020)"

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

if __name__ == "__main__":
    reader = PdfReader(DOC)
    ids, texts, metas = [], [], []
    for i, (name, start, end) in enumerate(SECTIONS):
        text = ""
        for p in range(start - 1, end):
            text += reader.pages[p].extract_text()
        ids.append(f"bat-vacc-2020-s{i}")
        texts.append(text)
        metas.append({"source": TITLE, "path": DOC, "section": name, "pages": f"{start}-{end}"})

    collection.upsert(ids=ids, documents=texts, metadatas=metas)
    print(f"Ingested {len(ids)} chunks")