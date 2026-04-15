# Incident and Recovery

Owner: Operations + Engineering  
Last reviewed: 2026-04-15

## Incident lifecycle

1. Detect
2. Triage
3. Mitigate
4. Recover
5. Communicate
6. Post-incident review

## Immediate triage checks

- Service health and container status
- Auth/session endpoint availability
- OCR provider/API availability
- Firestore and Redis connectivity

## Recovery checklist

- Verify home page login flow
- Verify preview extraction flow (`/extract/preview`)
- Verify review page save/download
- Verify billing summary endpoint (`/billing/data`)
- Verify admin panel accessibility for support actions

## Communication templates

- Degraded: \"We are investigating reduced performance; workaround is ...\"
- Outage: \"Service is temporarily unavailable; next update at ...\"
- Resolved: \"Service restored; monitoring ongoing.\"

## Post-incident notes

- Root cause
- Impact window
- User impact summary
- Preventive actions and owners
