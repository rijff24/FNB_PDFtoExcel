# Billing Transparency

Owner: Product + Finance  
Last reviewed: 2026-04-15

## Billing model summary

- Pricing is pooled per billing scope (user or organization).
- OCR and non-OCR documents are tracked separately.
- Shared monthly infrastructure cost is distributed across the pool document volume.
- OCR documents include Google Document AI variable cost; non-OCR do not.

## Source of pricing truth

- App billing settings are stored in the admin-managed pricing configuration.
- The current Google reference for Bank Statement Parser pricing is Google Cloud Document AI pricing: https://cloud.google.com/document-ai/pricing
- The engineering source check on 2026-04-16 confirmed the public pricing page lists Bank Statement Parser at `$0.75 per classified document`.
- Customer-facing values should come from `/billing/data`, not hardcoded copy in templates.

## Live estimate vs finalized amount

- During the month: values are live estimates and can reprice as usage changes.
- Month end: totals are finalized and locked for billing.

## What users can see

- Current volume
- OCR and non-OCR unit rates
- Shared infrastructure contribution per document
- Estimated monthly total
- Finalization status

## Limits and controls

- User-configurable monthly limit warning/block settings
- Service-side quota controls for file size, page count, and request rates

## Disputes and questions

Include in a billing query:

- account email
- month affected
- expected vs shown total
- screenshots from billing page
