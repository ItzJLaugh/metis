# Postmortem: Route Optimizer Outage — September 17, 2026

Severity: Sev-1 | Author: Sam Patel | Status: Action items in progress

## Summary
From 2:14 PM to 3:02 PM CT (48 minutes), the route-optimizer service returned errors for all customers. Drivers could still see assigned loads, but new routes could not be calculated.

## Root cause
A configuration change raised the route cache size from 2 GB to 8 GB, but the pod memory limit stayed at 6 GB. Pods were repeatedly killed for running out of memory (OOMKilled).

## Customer impact
212 customers affected; 3,480 route requests failed. Northwind Logistics and Atlas Cold Chain opened Sev-1 tickets. September uptime was 99.89%, below the 99.9% SLA, so service credits of 10% apply for affected Enterprise customers.

## Action items
1. Add a check in CI that compares cache size to pod memory limits — owner: Nina Okafor — due Oct 15 (done).
2. Alert when pod restarts exceed 3 in 5 minutes — owner: Sam Patel — due Oct 1 (done).
3. Run a load test before any cache configuration change — owner: Grace Liu — due Nov 30.
