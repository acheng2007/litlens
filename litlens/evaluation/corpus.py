import json
from importlib.resources import files

import pymupdf


def load_corpus():
    return json.loads(files("litlens.evaluation").joinpath("corpus.json").read_text())


def pdf_bytes(document):
    with pymupdf.open() as pdf:
        # One fact per page gives stable, independently checkable gold page references.
        for i, fact in enumerate(document["facts"], 1):
            page = pdf.new_page()
            page.insert_text((72, 72), f"{i} {document['section']}", fontsize=14)
            remaining = page.insert_textbox((72, 110, 520, 700), fact["text"], fontsize=12)
            if remaining < 0:
                raise ValueError("Fixture text overflows page")
        return pdf.tobytes()
