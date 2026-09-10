
### Text shape

| sample | text_chars B → A | line_count B → A |
| --- | --- | --- |
| `single_column.pdf` | 236 (0) | 4 (0) |
| `two_column.pdf` | 239 (0) | 8 (0) |
| `docs.md` | 219 (0) | 13 (0) |
| `bordered_table.pdf` | 174 (-17) | 19 (-12) |
| `borderless_table.pdf` | 176 (-17) | 19 (-12) |
| `cross_page_table.pdf` | 545 (0) | 47 (-36) |
| `scanned.pdf` | 50 (+21) | 1 (0) |

### Chunking (plain recursive split, comparable to committed baseline)

`n_chunks` = 1 and `avg_chunk_chars` = `text_chars` for every sample in **both** snapshots (all samples < 1024 chars).
