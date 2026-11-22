"""
vendor_followup.py
==================
Combines

    BigQuery stock risk   (already in report["inventory"], risk calculated
                           by the application: Critical / High / Normal)
  + vendor e-mail evidence (mailbox_service)
  + AI reasoning           (Gemini, business-language explanation)

into a prioritised "supply risk" per material.

The FINAL supply_risk is decided by the deterministic matrix below
(auditable, same philosophy as the stock-risk rules). The AI is used to
  (a) understand what the vendor wrote (confirmed / delayed / ...), and
  (b) explain the situation and the next step in business language.
If the AI call fails, a keyword fallback and templated text are used.

Nothing here runs unless the report is requested with the vendor check.
"""

import json
import logging
import os
import re

from google.adk.agents import Agent

from agent_package.agent import GEMINI_MODEL

import mailbox_service as mail

log = logging.getLogger("vendor_followup")

# Hours without a vendor reply before the request counts as overdue.
REPLY_SLA_HOURS = int(os.getenv("VENDOR_REPLY_SLA_HOURS", "48"))

# Which stock risks are checked against the mailbox, and how many.
CHECKED_RISKS = ("Critical", "High")
MAX_MATERIALS = int(os.getenv("VENDOR_CHECK_MAX_MATERIALS", "60"))

OUTCOMES = {"confirmed", "partial", "delayed", "rejected", "unclear"}
SEVERITY = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}

# stock risk  x  communication state  ->  supply risk
MATRIX = {
    "Critical": {
        "no_request": "Critical",   # stock-out and nobody has been asked
        "overdue":    "Critical",   # stock-out and vendor silent
        "awaiting":   "High",
        "confirmed":  "Medium",     # covered on paper, monitor receipt
        "partial":    "Critical",
        "delayed":    "Critical",
        "rejected":   "Critical",
        "unclear":    "High",
        "unknown":    "Critical",   # mailbox could not be checked
    },
    "High": {
        "no_request": "High",
        "overdue":    "High",
        "awaiting":   "Medium",
        "confirmed":  "Low",
        "partial":    "High",
        "delayed":    "High",
        "rejected":   "High",
        "unclear":    "Medium",
        "unknown":    "High",
    },
}

STATE_TEXT = {
    "no_request": "No replenishment request to a vendor was found",
    "awaiting":   "Request sent, vendor reply still within the expected time",
    "overdue":    "Request sent, vendor has not replied within the expected time",
    "confirmed":  "Vendor confirmed supply",
    "partial":    "Vendor can only deliver part of the quantity",
    "delayed":    "Vendor reported a delay",
    "rejected":   "Vendor cannot supply",
    "unclear":    "Vendor replied but the answer is not conclusive",
    "unknown":    "Mailbox could not be checked",
}

ACTION_TEXT = {
    "no_request": "Send a replenishment request to the vendor now and "
                  "consider an expedited or alternative source.",
    "awaiting":   "Follow up with the vendor if no answer arrives by the "
                  "end of the reply window.",
    "overdue":    "Escalate by phone to the vendor / purchasing and check "
                  "for an alternative source or inter-plant transfer.",
    "confirmed":  "Verify the delivery date and ASN; monitor goods receipt.",
    "partial":    "Secure the missing quantity from another source and "
                  "prioritise the partial delivery.",
    "delayed":    "Assess the line impact of the new date; expedite or "
                  "find an alternative source.",
    "rejected":   "Find an alternative vendor or inter-plant transfer "
                  "immediately.",
    "unclear":    "Contact the vendor to get a clear delivery commitment.",
    "unknown":    "Review the stock situation manually.",
}


# =========================================================
# AI REASONING AGENT (no tools - it only reasons over the evidence)
# =========================================================

REASONING_INSTRUCTION = """
You support SAP MRP controllers. You receive JSON evidence about
materials with a stock risk (from BigQuery) and the status of the
replenishment request sent to the vendor (from the controller's mailbox).

For EVERY item return:
- reply_outcome: only when reply_status is "Replied", classify the
  vendor's reply_excerpt as exactly one of:
  "confirmed" (vendor commits to deliver), "partial" (only part of the
  quantity), "delayed" (later than needed), "rejected" (cannot supply),
  "unclear" (no firm commitment). Use null when there is no reply.
- reasoning: 1-2 sentences in plain business language (no SAP jargon)
  explaining why this material is or is not a supply risk, using the
  stock figures, the waiting time and the reply content.
- recommended_action: one concrete next step for the controller.

Also return executive_summary: 2-4 short bullet sentences for management.

Rules:
- Use only the facts in the evidence. Never invent quantities, dates,
  vendors or reply content.
- Do not change stock_risk. Do not decide the final supply risk; the
  application does that.

Return exactly one JSON object, no Markdown fences:
{
  "executive_summary": ["..."],
  "items": [
    {"material_number": "...", "plant": "...", "reply_outcome": null,
     "reasoning": "...", "recommended_action": "..."}
  ]
}
"""

reasoning_agent = Agent(
    name="vendor_followup_reasoning_agent",
    model=GEMINI_MODEL,
    description="Explains vendor-response risk for MRP materials.",
    instruction=REASONING_INSTRUCTION,
)


# =========================================================
# DETERMINISTIC LOGIC
# =========================================================

def keyword_outcome(text):
    """Fallback classifier when the AI is unavailable."""
    t = (text or "").lower()

    if re.search(r"(cannot|can't|unable|not possible|out of stock|"
                 r"reject|nicht lieferbar)", t):
        return "rejected"
    if re.search(r"(partial|split|only \d+|teillieferung)", t):
        return "partial"
    if re.search(r"(delay|postpone|backorder|later|next week|"
                 r"verz[oö]ger)", t):
        return "delayed"
    if re.search(r"(confirm|will (deliver|ship)|shipped|dispatch|"
                 r"asn|best[aä]tigt)", t):
        return "confirmed"

    return "unclear"


def comms_state(row, outcome):
    status = row["reply_status"]

    if status == mail.UNAVAILABLE:
        return "unknown"
    if status == mail.NO_REQUEST:
        return "no_request"
    if status == mail.REPLIED:
        return outcome if outcome in OUTCOMES else "unclear"

    # Awaiting reply / auto-reply only
    waited = row.get("hours_waiting") or 0
    return "overdue" if waited >= REPLY_SLA_HOURS else "awaiting"


def combine(stock_risk, state):
    return MATRIX.get(stock_risk, MATRIX["High"]).get(state, stock_risk)


# =========================================================
# ORCHESTRATION
# =========================================================

def _select_materials(inventory):
    """Worst stock risk per (plant, material), Critical first, capped."""
    worst = {}

    for row in inventory:
        if row.get("risk") not in CHECKED_RISKS:
            continue

        key = (str(row.get("plant")), str(row.get("material_number")))
        current = worst.get(key)

        if current is None or (
            SEVERITY[row["risk"]] < SEVERITY[current["risk"]]
        ):
            worst[key] = {
                "plant": key[0],
                "material_number": key[1],
                "risk": row["risk"],
                "unrestricted_stock": row.get("unrestricted_stock"),
                "safety_stock": row.get("safety_stock"),
                "in_transit_stock": row.get("in_transit_stock"),
                "base_uom": row.get("base_uom"),
            }

    ordered = sorted(
        worst.values(),
        key=lambda r: (SEVERITY[r["risk"]], r["plant"],
                       r["material_number"]),
    )

    return ordered[:MAX_MATERIALS], max(0, len(ordered) - MAX_MATERIALS)


def _parse_json(text):
    text = re.sub(r"^```(?:json)?\s*", "", (text or "").strip(),
                  flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")

    if start == -1 or end == -1:
        raise ValueError("AI reasoning did not return JSON")

    return json.loads(text[start:end + 1])


def _ai_reasoning(llm, evidence):
    payload = json.dumps(
        {"reply_window_hours": REPLY_SLA_HOURS, "items": evidence},
        ensure_ascii=False, default=str,
    )
    return _parse_json(llm(payload))


def enrich_report(report, llm=None, interactive=False):
    """
    Adds to the report dict (existing keys are only appended to):
        vendor_followups, vendor_summary, + bullets in
        executive_summary / recommendations / data_limitations
    llm: callable(prompt_text) -> response_text, or None.
    """

    selected, skipped = _select_materials(report.get("inventory", []))
    limitations = report.setdefault("data_limitations", [])

    if not selected:
        report["vendor_followups"] = []
        report["vendor_summary"] = {}
        limitations.append(
            "Vendor e-mail check: no Critical or High risk materials "
            "to verify."
        )
        return report

    if skipped:
        limitations.append(
            f"Vendor e-mail check covered the {MAX_MATERIALS} most "
            f"critical materials; {skipped} further materials were "
            "not checked."
        )

    # ---- 1. mailbox evidence ----------------------------------
    by_plant = {}
    for item in selected:
        by_plant.setdefault(item["plant"], []).append(
            item["material_number"]
        )

    evidence_rows, warnings = mail.check_vendor_replies(
        by_plant, interactive=interactive
    )
    limitations.extend(warnings)

    evidence = {
        (str(r["plant"]), str(r["material_number"])): r
        for r in evidence_rows
    }

    # ---- 2. AI: classify replies + explain --------------------
    ai_items, ai_summary = {}, []

    if llm is not None:
        try:
            compact = []
            for item in selected:
                row = evidence[(item["plant"], item["material_number"])]
                compact.append({
                    "material_number": item["material_number"],
                    "plant": item["plant"],
                    "stock_risk": item["risk"],
                    "unrestricted_stock": item["unrestricted_stock"],
                    "safety_stock": item["safety_stock"],
                    "in_transit_stock": item["in_transit_stock"],
                    "base_uom": item["base_uom"],
                    "reply_status": row["reply_status"],
                    "vendor": row["vendor"],
                    "request_sent_at": row["request_sent_at"],
                    "hours_waiting": row["hours_waiting"],
                    "reply_excerpt": row["reply_excerpt"],
                })

            result = _ai_reasoning(llm, compact)
            ai_summary = result.get("executive_summary") or []

            for entry in result.get("items", []):
                key = (str(entry.get("plant")),
                       str(entry.get("material_number")))
                ai_items[key] = entry

        except Exception as exc:
            log.exception("AI reasoning failed - using fallback")
            limitations.append(
                f"AI explanation unavailable ({exc}); standard wording "
                "and keyword classification were used."
            )

    # ---- 3. final supply risk (deterministic) -----------------
    followups = []

    for item in selected:
        key = (item["plant"], item["material_number"])
        row = evidence[key]
        ai = ai_items.get(key, {})

        outcome = None

        if row["reply_status"] == mail.REPLIED:
            outcome = ai.get("reply_outcome")
            if outcome not in OUTCOMES:
                outcome = keyword_outcome(row["reply_excerpt"])

        state = comms_state(row, outcome)
        supply_risk = combine(item["risk"], state)

        waited = row.get("hours_waiting")
        reasoning = ai.get("reasoning") or (
            f"{STATE_TEXT[state]}"
            + (f" (waiting {waited:.0f} h)" if waited else "")
            + f". Stock risk is {item['risk']}."
        )
        action = ai.get("recommended_action") or ACTION_TEXT[state]

        followups.append({
            "material_number": item["material_number"],
            "plant": item["plant"],
            "stock_risk": item["risk"],
            "unrestricted_stock": item["unrestricted_stock"],
            "safety_stock": item["safety_stock"],
            "vendor": row["vendor"] or "-",
            "request_sent": row["request_sent_at"] or "-",
            "reply_status": row["reply_status"],
            "hours_waiting": waited if waited is not None else "-",
            "reply_outcome": outcome or "-",
            "supply_risk": supply_risk,
            "assessment": f"{reasoning} Action: {action}",
            "reply_excerpt": row["reply_excerpt"],
        })

    followups.sort(key=lambda r: (
        SEVERITY[r["supply_risk"]],
        -(r["hours_waiting"] if isinstance(r["hours_waiting"],
                                           (int, float)) else 0),
    ))

    # ---- 4. summary, bullets ----------------------------------
    counts = {level: 0 for level in SEVERITY}
    for r in followups:
        counts[r["supply_risk"]] += 1

    no_request = sum(r["reply_status"] == mail.NO_REQUEST
                     for r in followups)
    unanswered = sum(r["reply_status"] in (mail.AWAITING, mail.AUTO_ONLY)
                     for r in followups)

    report["vendor_followups"] = followups
    report["vendor_summary"] = {
        "checked": len(followups),
        "critical": counts["Critical"],
        "high": counts["High"],
        "medium": counts["Medium"],
        "low": counts["Low"],
        "no_request": no_request,
        "awaiting_reply": unanswered,
    }

    report.setdefault("executive_summary", []).extend(
        ai_summary or [
            f"Vendor check: {counts['Critical']} of {len(followups)} "
            "checked materials are a Critical supply risk after "
            "considering vendor responses.",
            f"{no_request} have no replenishment request and "
            f"{unanswered} are still waiting for a vendor reply.",
        ]
    )

    top = [r for r in followups if r["supply_risk"] == "Critical"][:10]
    recs = report.setdefault("recommendations", [])

    for r in top:
        recs.append(
            f"[Vendor] Plant {r['plant']} / material "
            f"{r['material_number']}: {r['assessment']}"
        )

    return report
