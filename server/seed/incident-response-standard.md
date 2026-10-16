---
id: incident-response-standard
path: engineering/standards/incident-response
title: Incident Response Standard
authority_level: standard
owner: platform-eng
tags: incidents, oncall, sev, engineering
status: published
---
# Incident Response Standard

## Severity levels

- SEV1: customer-facing outage. Page on-call immediately; incident commander required.
- SEV2: degraded service or imminent risk. Respond within 30 minutes.
- SEV3: minor impact. Handle during business hours.

## Process

1. Declare the incident in the #incidents channel.
2. Assign an incident commander for SEV1/SEV2.
3. Post status updates every 30 minutes until mitigated.
4. Write a blameless postmortem within 5 business days.
