# The HANNA manual

This is the operating manual for the HANNA system (Hansen Notification Agent):
how a photo of a patient card becomes a verified, located, notified case.

## Who each part is for

Every page has two layers:

- **In plain language** — a short summary at the top, for country programme
  staff, reviewers and funders. No jargon that the summary itself does not
  explain.
- **The technical layer** — below, for whoever runs, debugs or extends the
  component: exact files, tables, thresholds and decision rules.

## The pages

| Page | Component |
|---|---|
| `01-what-hanna-does.md` | The whole pipeline, end to end |
| `02-extraction.md` | Reading the card with a vision model |
| `03-location.md` | The gazetteer, the geocoding cascade, the map picker and the alias table |
| `04-review.md` | The review screen, corrections and what they teach |
| `05-country-onboarding.md` | What a new country does to join |

## The ritual

The manual is updated **in the same session as the work**, not afterwards:

1. Changed or built a component → update its page the same day. New behaviour,
   new thresholds, new failure modes go in; removed behaviour comes out.
2. Anything a new country would ask ("why does it do that?", "what do I
   press?") is a sign the plain-language layer is missing a sentence. Add it.
3. Anything you had to look up in code or an old journal to explain is a gap —
   close it while it is fresh.

The journal (`memory/journal/`) records *what happened when*. The manual
records *how it works now*. Never make a reader reconstruct the present from a
history.

## Building the site (optional)

The pages are plain markdown and read fine in GitHub. For a searchable,
navigable site: `mkdocs` with the Material theme (see `mkdocs.yml` beside this
folder). Not required for reading or editing.
