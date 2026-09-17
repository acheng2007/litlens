# Project context

Read [docs/roadmap.md](docs/roadmap.md) when planning improvements or continuing roadmap work.
It records improvement ideas discussed with the user, their proposed order, and the current evaluation baseline.
The user asked to preserve these ideas; the roadmap is not an instruction to implement every item automatically.
When implementing an item, inspect the current code first and update its status with verification evidence.

LitLens already supports real uploaded PDFs and arXiv papers.
The default demo corpus and original recorded benchmark are synthetic research-method notes.
The separate `real-papers` dataset has six pinned PDFs and 36 source-checked questions; see `docs/real-paper-evaluation.md` and `docs/evaluation-real-papers.json` for reproduction and measured limitations.
Its annotations were source-checked by an agent and have not received independent human review.
Keep that distinction explicit in documentation and progress reports.
