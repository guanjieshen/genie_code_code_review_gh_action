"""Ontology validation: decide which business questions a PR raises, answer what Unity Catalog can,
and ask Genie One only for the rest.

Flow (deterministic, before the AI review):
  1. plan()   - questions from the impact graph: identifier relationships, metric definitions,
                authoritative sources. Deduplicated by (intent, business concept), ranked by how
                much the answer could change the review, capped by a budget.
  2. resolve  - questions Unity Catalog already answers (metric view, certified/deprecated tag,
                key constraint) are marked "answered by Unity Catalog" and not sent to Genie One.
  3. ask()    - the rest go to the Genie One MCP server. Each answer becomes evidence
                `ev:genie_one:<id>` with its status, citations, and whether it is authoritative.

Genie One answers are sourced evidence, not verified facts. An answer counts as authoritative only
when it cites a governed source (a metric view, a certified dataset, or a Unity Catalog Page).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import requests
from concurrent.futures import ThreadPoolExecutor

from .util import log

ID_COLUMN = re.compile(r"^(?P<stem>[a-z][a-z0-9]*?)_?(id|key|number|no|code)$", re.I)
MEASURE = re.compile(r"(revenue|sales|margin|profit|ltv|churn|arr|mrr|gmv|bookings)", re.I)   # business metrics
INTENT_RANK = {"identifier_relationship": 0, "metric_definition": 1, "authoritative_source": 2}
ALIASES = {"cust": "customer", "acct": "account", "prod": "product", "emp": "employee", "ord": "order", "sku": "product"}


@dataclass
class Question:
    id: str
    intent: str                 # identifier_relationship | metric_definition | authoritative_source
    concept: str                # business concept, e.g. "customer", "net revenue"
    text: str
    objects: list[str]          # uc:<fqn>[.<col>] the question is about
    location: tuple[str, int] = ("", 0)
    status: str = "planned"     # planned | answered_by_uc | asked | skipped_budget
    uc_answer: str = ""         # evidence id that already answers it

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Answer:
    question_id: str
    status: str                 # answered | no_answer | error | unavailable
    answer: str = ""
    citations: list[dict[str, str]] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    authoritative: bool = False
    conflicting: bool = False
    deep_link: str = ""
    seconds: float = 0.0
    error: str = ""


# ------------------------------------------------------------------ planning


def entity_of(column: str) -> str | None:
    m = ID_COLUMN.match(column)
    if not m:
        return None
    stem = m.group("stem").lower()
    return ALIASES.get(stem, stem)


def _qid(intent: str, concept: str) -> str:
    return "q-" + hashlib.sha1(f"{intent}|{concept}".encode()).hexdigest()[:8]


def _short(fqn: str) -> str:
    return fqn.split(".")[-1]


def plan(impact, facts: dict[str, Any], budget: int = 4) -> list[Question]:
    """Questions whose answers could change the review, most valuable first."""
    qs: dict[str, Question] = {}

    # 1. identifier relationships: joins on identifier columns
    for key, refs in impact.refs.items():
        entry = impact.head.entries[key]["entry"]
        for j in refs.joins:
            (lt, lc), (rt, rc) = j["left"], j["right"]
            le, re_ = entity_of(lc), entity_of(rc)
            if not le or not re_ or le != re_:
                continue
            lmeta, rmeta = facts.get(f"ev:uc:{lt}:metadata") or {}, facts.get(f"ev:uc:{rt}:metadata") or {}
            if not (lmeta.get("exists") and rmeta.get("exists")):
                continue  # a missing table is already a finding; the relationship cannot be checked
            ltype = {c["name"].lower(): c["type"] for c in lmeta.get("columns", [])}.get(lc.lower())
            rtype = {c["name"].lower(): c["type"] for c in rmeta.get("columns", [])}.get(rc.lower())
            if lc.lower() == rc.lower() and ltype and ltype == rtype:
                continue  # same name, same type: nothing for the ontology to resolve
            concept = le
            qid = _qid("identifier_relationship", concept)
            q = qs.get(qid)
            objs = [f"uc:{lt}.{lc}", f"uc:{rt}.{rc}"]
            if q:
                q.objects = sorted(set(q.objects) | set(objs))
                continue
            schema = ".".join(lt.split(".")[:2])
            qs[qid] = Question(qid, "identifier_relationship", concept,
                               f"In `{schema}`, what is the canonical identifier for a {concept}, and how do "
                               f"`{_short(lt)}.{lc}` and `{_short(rt)}.{rc}` relate? Are they the same identifier system, "
                               "and is a mapping or specific join rule required? Cite the business definition you use.",
                               sorted(objs), (entry, j["line"]))

    # 2. metric definitions: measure-like outputs of tables the PR writes
    for e in impact.head.entries.values():
        for t, cols in (e.get("outputs") or {}).items():
            for c in cols:
                if not MEASURE.search(c):
                    continue
                concept = c.replace("_", " ").lower()
                qid = _qid("metric_definition", concept)
                if qid in qs:
                    qs[qid].objects = sorted(set(qs[qid].objects) | {f"uc:{t}.{c}"})
                    continue
                schema = ".".join(t.split(".")[:2])
                qs[qid] = Question(qid, "metric_definition", concept,
                                   f"In `{schema}`, what is the governed business definition of {concept} "
                                   "(filters, exclusions, and grain), and which asset is authoritative for it? Cite the definition.",
                                   [f"uc:{t}.{c}"], (e["entry"], e["writes"].get(t, 0)))

    # 3. authoritative source: business tables read without a certified/deprecated signal
    written = set(impact.graph.tables_with("writes"))
    for t in impact.graph.tables_with("reads"):
        if t in written or not MEASURE.search(_short(t)):
            continue
        concept = re.sub(r"^(gold|silver|bronze)_", "", _short(t)).replace("_", " ")
        qid = _qid("authoritative_source", concept)
        if qid not in qs:
            qs[qid] = Question(qid, "authoritative_source", concept,
                               f"Is `{t}` the authoritative source for {concept}? If not, which dataset is, and why? Cite the definition.",
                               [f"uc:{t}"])

    ordered = sorted(qs.values(), key=lambda q: (INTENT_RANK[q.intent], q.concept))
    for q in ordered:
        resolve_with_uc(q, facts)
    asked = 0
    for q in ordered:
        if q.status == "answered_by_uc":
            continue
        if asked >= budget:
            q.status = "skipped_budget"
            continue
        asked += 1
    return ordered


def resolve_with_uc(q: Question, facts: dict[str, Any]) -> None:
    """Mark questions Unity Catalog already answers, so the Genie One budget is spent on real gaps."""
    if q.intent == "metric_definition":
        measure = q.concept.replace(" ", "_")
        for k, v in facts.items():
            if k.endswith(":metric_view") and re.search(rf"\bname:\s*{re.escape(measure)}\b", v.get("definition") or ""):
                q.status, q.uc_answer = "answered_by_uc", k
                return
    if q.intent == "authoritative_source":
        fqn = q.objects[0][3:]
        tags = (facts.get(f"ev:uc:{fqn}:tags") or {}).get("table_tags") or {}
        comment = ((facts.get(f"ev:uc:{fqn}:metadata") or {}).get("comment") or "").lower()
        if any(k in ("certified", "deprecated") for k in tags) or comment.startswith(("authoritative", "deprecated")):
            q.status, q.uc_answer = "answered_by_uc", f"ev:uc:{fqn}:tags" if tags else f"ev:uc:{fqn}:metadata"
    # identifier relationships: Unity Catalog has no business-identity semantics beyond key constraints,
    # which are not collected yet, so these always go to Genie One.


# ------------------------------------------------------------------ asking Genie One


LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")


def classify_citation(label: str, url: str) -> dict[str, str]:
    """Citation kinds, from the inline links in a Genie One answer (the API has no structured sources)."""
    m = re.search(r"/explore/data/([^/?#]+)/([^/?#]+)/([^/?#]+)", url)
    if m:
        return {"kind": "uc_object", "ref": ".".join(m.groups()), "url": url, "label": label}
    if "#embed-query" in url:
        return {"kind": "query", "ref": url.rsplit("#", 1)[1], "url": url, "label": label}
    if re.search(r"/(pages?|glossary|knowledge)(/|\?|$)", url, re.I):
        return {"kind": "uc_page", "ref": url, "url": url, "label": label}
    return {"kind": "link", "ref": url, "url": url, "label": label}


DISCLAIMER = re.compile(
    r"(no (curated|governed|documented|authoritative) (business )?definition|"
    r"none (of them )?appl(y|ies)|(do|does)n['’]?t apply|didn['’]?t use (them|it)|not (relevant|applicable) here|"
    r"no (glossary|ontology) (page|snippet|entry)|isn['’]?t (a )?(documented|curated))", re.I)


def _tokens(s: str) -> set[str]:
    words = re.findall(r"[a-z]+", s.lower())
    return {ALIASES.get(w, w).rstrip("s") for w in words if len(w) > 2}


def assess(answer: str, citations: list[dict[str, str]], facts: dict[str, Any], concept: str = "",
           objects: list[str] | None = None) -> tuple[bool, bool]:
    """(authoritative, conflicting).

    Authoritative needs a cited governed source that is relevant to the question:
      - a Unity Catalog Page whose title matches the business concept, or
      - a metric view or certified dataset in the same catalog and schema as the objects asked about.
    A response alone, or an answer that says the available definitions do not apply, is never authoritative.
    """
    if DISCLAIMER.search(answer):
        governed = False
    else:
        schemas = {".".join(o[3:].split(".")[:2]) for o in (objects or [])}
        want = _tokens(concept)
        governed = False
        # The API does not return page content, so a page counts only if its title matches the concept AND the
        # same answer ties it to an object in the reviewed schema.
        anchored_here = any(c["kind"] == "uc_object" and (not schemas or ".".join(c["ref"].split(".")[:2]) in schemas)
                            for c in citations)
        for c in citations:
            if c["kind"] == "uc_page":
                title_match = bool(want and want & _tokens(c.get("label", "")))
                c["relevant"] = "yes" if title_match and anchored_here else "title only" if title_match else "no"
                governed |= c["relevant"] == "yes"
            elif c["kind"] == "uc_object":
                ref = c["ref"]
                tags = (facts.get(f"ev:uc:{ref}:tags") or {}).get("table_tags") or {}
                same_schema = not schemas or ".".join(ref.split(".")[:2]) in schemas
                if same_schema and (f"ev:uc:{ref}:metric_view" in facts or "certified" in tags):
                    c["relevant"] = "yes"
                    governed = True
    conflicting = bool(re.search(r"\b(conflict|inconsistent|two (different )?definitions|disagree)\w*", answer, re.I))
    return governed, conflicting


class GenieOne:
    """Minimal Genie One MCP client (genie_ask + genie_poll_response)."""

    def __init__(self, host: str, token: str, timeout: float = 120):
        self.url = f"{host.rstrip('/')}/ai-gateway/mcp-services/system.ai.genie_one_mcp"
        self.h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        self.timeout, self.sid, self.n = timeout, None, 0

    def _rpc(self, method: str, params: dict) -> dict:
        self.n += 1
        h = dict(self.h, **({"mcp-session-id": self.sid} if self.sid else {}))
        r = requests.post(self.url, headers=h, json={"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}, timeout=60)
        r.raise_for_status()
        self.sid = r.headers.get("mcp-session-id", self.sid)
        body = r.text
        if "data:" in body:
            body = [l[5:] for l in body.splitlines() if l.startswith("data:")][-1]
        out = json.loads(body)
        if "error" in out:
            raise RuntimeError(out["error"].get("message", "MCP error"))
        return out["result"]

    def ask(self, question: str) -> dict:
        if self.sid is None:
            self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "lakehouse-review", "version": "1"}})
        t0 = time.time()
        sc = self._rpc("tools/call", {"name": "genie_ask", "arguments": {"question": question}}).get("structuredContent") or {}
        while True:
            res = self._rpc("tools/call", {"name": "genie_poll_response", "arguments": {
                "conversation_id": sc.get("conversation_id"), "response_id": sc.get("response_id")}})
            ps = res.get("structuredContent") or {}
            if ps.get("status") not in ("pending", "running", "in_progress", None) or time.time() - t0 > self.timeout:
                ps["_seconds"] = round(time.time() - t0, 1)
                return ps
            time.sleep(3)


def _ask_one(q: Question, client: GenieOne | None, facts: dict[str, Any]) -> Answer:
    if client is None:
        return Answer(q.id, "unavailable", error="Genie One is not configured or not reachable")
    try:
        ps = client.ask(q.text)
    except Exception as e:  # noqa: BLE001
        log(f"Genie One question {q.id} failed: {str(e)[:120]}")
        return Answer(q.id, "error", error=str(e)[:200])
    text = ps.get("final_answer") or ""
    cites = [classify_citation(l, u) for l, u in LINK.findall(text)]
    auth, conflict = assess(text, cites, facts, q.concept, q.objects)
    status = "answered" if ps.get("status") == "completed" and text.strip() else "no_answer"
    return Answer(q.id, status, text[:3000], cites, [i.get("sql", "") for i in ps.get("query_items") or []],
                  auth, conflict, ps.get("deep_link", ""), ps.get("_seconds", 0.0))


def ask(questions: list[Question], client_factory, facts: dict[str, Any], parallel: int = 4) -> list[Answer]:
    """Ask planned questions in parallel. `client_factory()` returns a GenieOne client (one per thread) or None."""
    todo = [q for q in questions if q.status == "planned"]
    for q in todo:
        q.status = "asked"
    if not todo:
        return []
    if client_factory is None or not callable(client_factory):
        fixed = client_factory
        client_factory = lambda: fixed  # noqa: E731  (tests pass a client or None directly)
    with ThreadPoolExecutor(max_workers=min(parallel, len(todo))) as pool:
        return list(pool.map(lambda q: _ask_one(q, client_factory(), facts), todo))


def to_evidence(questions: list[Question], answers: list[Answer]) -> dict[str, Any]:
    """Evidence facts `ev:genie_one:<question id>` for the prompt, confidence, and the report."""
    by_q = {a.question_id: a for a in answers}
    out = {}
    for q in questions:
        a = by_q.get(q.id)
        if not a:
            continue
        out[f"ev:genie_one:{q.id}"] = {
            "intent": q.intent, "concept": q.concept, "question": q.text, "objects": q.objects,
            "status": a.status, "authoritative": a.authoritative, "conflicting": a.conflicting,
            "answer": a.answer, "citations": [{k: c.get(k, "") for k in ("kind", "ref", "label", "relevant")} for c in a.citations],
            "queries": a.queries[:5], "deep_link": a.deep_link, "error": a.error,
        }
    return out


def summary(questions: list[Question], answers: list[Answer], enabled: bool) -> dict[str, Any]:
    """Coverage line for the report. Never 'validated' just because Genie One responded."""
    if not enabled:
        return {"status": "Skipped", "detail": "ontology validation is turned off"}
    if not questions:
        return {"status": "Not needed", "detail": "no identifiers, metrics, or business sources affected"}
    uc = sum(q.status == "answered_by_uc" for q in questions)
    asked = list(answers)
    auth = sum(a.status == "answered" and a.authoritative for a in asked)
    hyp = sum(a.status == "answered" and not a.authoritative for a in asked)
    unresolved = sum(a.status in ("no_answer", "error") for a in asked)
    unavailable = sum(a.status == "unavailable" for a in asked)
    skipped = sum(q.status == "skipped_budget" for q in questions)
    rapid = sum(q.status == "skipped_rapid" for q in questions)
    if asked and unavailable == len(asked):
        status = "Unavailable"
    elif not asked and rapid:
        status = "Skipped"
    elif not asked:
        status = "Not needed"
    else:
        status = "Ran"
    parts = [f"{len(questions)} planned", f"{uc} answered by Unity Catalog", f"{len(asked)} asked"]
    if asked:
        parts.append(f"{auth} authoritative, {hyp} unsourced, {unresolved} unresolved")
    if skipped:
        parts.append(f"{skipped} over budget")
    if rapid:
        parts.append(f"{rapid} not asked (rapid mode)")
    return {"status": status, "detail": "; ".join(parts), "planned": len(questions), "answered_by_uc": uc,
            "asked": len(asked), "authoritative": auth, "unsourced": hyp, "unresolved": unresolved,
            "unavailable": unavailable, "over_budget": skipped, "skipped_rapid": rapid,
            "seconds": round(sum(a.seconds for a in asked), 1)}
