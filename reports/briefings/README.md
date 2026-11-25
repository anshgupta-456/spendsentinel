# Briefing experiment

Each month's briefing is written by a local model from SQL facts, then every figure in it is checked against those facts. If any figure fails, the model is told which ones and gets one rewrite. A second check confirms that the department the briefing names as the largest change is the one SQL found. This table keeps the latest run per month and model.

**First drafts: 202 of 211 figures verified (96%). After correction: 209 of 210 (100%).** 5 of 20 briefings needed a rewrite. Largest-change claim correct in 19 of 20 briefings that made one.

| Month | Model | First draft verified | Final verified | Attempts | Largest change |
| --- | --- | ---: | ---: | ---: | --- |
| [2025-10](llama3.1-8b/2025-10.md) | llama3.1:8b | 7 / 7 | 7 / 7 | 1 | correct |
| [2025-11](llama3.1-8b/2025-11.md) | llama3.1:8b | 17 / 17 | 17 / 17 | 1 | correct |
| [2025-12](llama3.1-8b/2025-12.md) | llama3.1:8b | 10 / 10 | 10 / 10 | 1 | correct |
| [2026-01](llama3.1-8b/2026-01.md) | llama3.1:8b | 15 / 15 | 15 / 15 | 1 | correct |
| [2026-02](llama3.1-8b/2026-02.md) | llama3.1:8b | 18 / 18 | 18 / 18 | 1 | correct |
| [2026-03](llama3.1-8b/2026-03.md) | llama3.1:8b | 9 / 9 | 9 / 9 | 1 | correct |
| [2026-04](llama3.1-8b/2026-04.md) | llama3.1:8b | 18 / 18 | 18 / 18 | 1 | correct |
| [2026-05](llama3.1-8b/2026-05.md) | llama3.1:8b | 10 / 10 | 10 / 10 | 1 | correct |
| [2026-06](llama3.1-8b/2026-06.md) | llama3.1:8b | 18 / 18 | 18 / 18 | 1 | WRONG |
| [2026-07](llama3.1-8b/2026-07.md) | llama3.1:8b | 7 / 10 | 9 / 9 | 2 | correct |
| [2025-10](llama3.2-3b/2025-10.md) | llama3.2:3b | 6 / 6 | 6 / 6 | 1 | correct |
| [2025-11](llama3.2-3b/2025-11.md) | llama3.2:3b | 8 / 8 | 8 / 8 | 1 | correct |
| [2025-12](llama3.2-3b/2025-12.md) | llama3.2:3b | 6 / 7 | 7 / 7 | 2 | correct |
| [2026-01](llama3.2-3b/2026-01.md) | llama3.2:3b | 8 / 8 | 8 / 8 | 1 | correct |
| [2026-02](llama3.2-3b/2026-02.md) | llama3.2:3b | 7 / 7 | 7 / 7 | 1 | correct |
| [2026-03](llama3.2-3b/2026-03.md) | llama3.2:3b | 6 / 9 | 8 / 9 | 2 | correct |
| [2026-04](llama3.2-3b/2026-04.md) | llama3.2:3b | 7 / 8 | 8 / 8 | 2 | correct |
| [2026-05](llama3.2-3b/2026-05.md) | llama3.2:3b | 10 / 10 | 10 / 10 | 1 | correct |
| [2026-06](llama3.2-3b/2026-06.md) | llama3.2:3b | 8 / 8 | 8 / 8 | 1 | correct |
| [2026-07](llama3.2-3b/2026-07.md) | llama3.2:3b | 7 / 8 | 8 / 8 | 2 | correct |

## What a human read found

In the first version the model was asked to work out which department changed most. Every figure still checked out, but reading the February 2026 briefings showed the 3B model giving the Department for Transport's +4.2% as the change in total spend, and naming it as the largest change when it was the smallest (DWP +38.1%, DBT -27.8%). Correct numbers in a wrong comparison pass a figure check. Rankings and the total change are now computed in SQL and passed in as facts, and the largest-change claim is checked automatically.

Limits: the figure check confirms each number written in digits appears in the facts. It does not check numbers written as words, or that every figure is attached to the right department or supplier, so a person still reads the text.
