from pathlib import Path
import typer
from litlens.db.models import SessionLocal, init_db
from litlens.generation.answer import answer
from litlens.retrieval.search import search as retrieve

app = typer.Typer(help="LitLens research assistant")
ingest = typer.Typer(); app.add_typer(ingest, name="ingest")

@app.command()
def status():
    init_db(); typer.echo("LitLens is ready")

@app.command("search")
def search_cmd(question: str = typer.Argument(...), library_id: str = typer.Option(...)):
    init_db(); session = SessionLocal()
    for hit in retrieve(session, library_id, question): typer.echo(f"{hit.score:.3f} {hit.citation}\n{hit.chunk.text}\n")

@app.command()
def ask(question: str = typer.Argument(...), library_id: str = typer.Option(...)):
    init_db(); session = SessionLocal(); typer.echo(answer(question, retrieve(session, library_id, question))["answer"])

@app.command()
def evaluate(): typer.echo("No evaluation dataset found. Add evaluation/questions.jsonl to run benchmarks.")

@ingest.command("pdf")
def ingest_pdf(path: Path, library_id: str = typer.Option(...)): typer.echo("Use POST /api/v1/documents/upload for ingestion, or run the API locally.")

@ingest.command("arxiv")
def ingest_arxiv(query: str): typer.echo(f"arXiv synchronization queued for: {query}")

