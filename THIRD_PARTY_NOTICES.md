# Third-Party Notices

## vendored/aspire

This repository vendors [`nemmons/aspire`](https://github.com/nemmons/aspire)
unmodified under `vendor/aspire/`.

- **Source:** https://github.com/nemmons/aspire
- **Author:** Nate Emmons (nemmons)
- **License:** MIT (see `vendor/aspire/LICENSE`)
- **What it is:** "A Simple Python Insurance Rating Engine" — a configurable
  insurance rating engine with rating manuals, rating steps, SQLAlchemy
  persistence, and batch rating.

UnderwriteIQ's own code in `src/underwriteiq/` was written for this project;
it does not import aspire at runtime today. Aspire is vendored as a reference
implementation of insurance rating-manual concepts (rating steps, factors,
manuals) that future UnderwriteIQ pricing work may build on or integrate
with. Its inclusion here is attribution of inspiration and groundwork, not a
claim of originality.

All submissions, applicants, and loss runs anywhere in this repository are
synthetic/fictional and do not represent any real client or policyholder.
