# Licensing

HANNA is released in layers, because code, documents and country data are not
the same kind of thing.

| Layer | What it covers | Licence |
|---|---|---|
| **Code** | `core/`, `server/`, `tools/`, `migrations/`, `benchmarks/`, `deploy/` | Apache-2.0 — see `LICENSE` |
| **Documents** | `docs/`, design records, the concept index (`docs/concepts/`), the country brief | CC BY-SA 4.0 |
| **Country packs** | `countries/<name>/` — form, template, location, ground truth | Supplied by the country under its own permission; see the `NOTICE.md` in each pack |
| **Third-party models and data** | MinerU, PaddleOCR-VL, Qwen, WHO and COD-AB material | Their own licences; not covered here |

## What this means in practice

- You may run, modify and redistribute the code, including inside a ministry or
  a commercial service, provided you keep the notices.
- If you improve the concept index or the docs, share the improvement back.
- Country cards, templates and ground truth belong to the country that supplied
  them. This project does not relicense them. Do not copy a country pack into
  your own product without that country's permission.
- The HANNA and Last Mile Road names and logos are not covered by the code
  licence. Ask before using them to imply endorsement.

## Contributing

Contributions are accepted under the licence of the layer they touch, with a
Developer Certificate of Origin sign-off. See `CONTRIBUTING.md`.
