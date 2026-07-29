from pypdf import PdfReader
import chromadb
from pathlib import Path

def doc_id(path):
    return Path(path).stem.lower().replace("_", "-")

def doc_title(path):
    return Path(path).stem.replace("_", " ")

SKIP_HEADINGS = ("TABLE OF CONTENTS", "JOINT TRAUMA SYSTEM")

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

def toc_key(text):
    return "".join(text.split("..")[0].split()).upper()

def read_toc(lines):
    entries = []
    for page_number, line in lines:
        if "..." in line:
            entries.append(toc_key(line))
    return entries

def find_headings(lines, toc):
    headings = {}
    for i, (page_number, line) in enumerate(lines):
        if not is_heading(line) or "..." in line:
            continue
        if toc_key(line) in toc:
            headings[i] = clean_heading(line)
        elif i + 1 < len(lines):
            joined = line + " " + lines[i + 1][1]
            if toc_key(joined) in toc:
                headings[i] = clean_heading(joined)
    return headings

def unmatched_toc(toc, headings):
    found = []
    for label in headings.values():
        found.append(toc_key(label))
    missing = []
    for entry in toc:
        if entry not in found:
            missing.append(entry)
    return missing

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
    toc = read_toc(lines)
    headings = find_headings(lines, toc)
    chunks = []
    for i, (page_number, line) in enumerate(lines):
        if furniture_key(line) in furniture:
            continue
        if i in headings:
            chunks.append({"section": headings[i],
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


def chunk_meta(pdf, chunk):
    return {"source": doc_title(pdf), "path": str(pdf),
            "section": chunk["section"], "pages": f"{chunk['start_page']}-{chunk['end_page']}"}

if __name__ == "__main__":
    client.delete_collection("corpus")
    collection = client.get_or_create_collection("corpus")

    ids, texts, metas = [], [], []
    for pdf in sorted(Path("corpus").glob("*.pdf")):
        lines = read_lines(pdf)
        headings = find_headings(lines, read_toc(lines))
        for entry in unmatched_toc(read_toc(lines), headings):
            print(f"  ! {pdf.name}: TOC lists {entry} but no heading matched")
        for index, chunk in enumerate(chunk_document(lines)):
            if keep_chunk(chunk):
                ids.append(f"{doc_id(pdf)}-s{index}")
                texts.append(chunk["text"])
                metas.append(chunk_meta(pdf, chunk))

    collection.upsert(ids=ids, documents=texts, metadatas=metas)
    print(f"Ingested {len(ids)} chunks")