#!/usr/bin/env python3
"""Validate evidence structure and declared provenance, not scientific truth."""
import argparse
import json
import math
import re
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

ACCESS = {"fulltext", "excerpt", "abstract", "metadata", "unavailable"}
ROLES = {"fact", "mechanism", "method", "evaluation", "boundary", "counterevidence", "analogy"}
RELATIONS = {"supports", "contradicts", "bounds", "depends_on", "imports", "supersedes"}
SYNTHESES = {"convergent", "conditional", "contested", "refuted", "uninformative", "insufficient"}
COLLECTIONS = ("papers", "claims", "propositions", "edges", "tensions", "reading_queue", "searches")
KEYS = {
    "root": {"schema_version", "profile", "context", *COLLECTIONS},
    "context": {"question", "decision", "target_setting", "mode", "as_of", "bounds", "stop_reason", "stop_basis", "deep_read_budget"},
    "paper": {"id", "title", "url", "version", "access", "evidence_family", "doi", "arxiv_id", "authors", "datasets", "code_urls", "study_ids", "publication_status", "date", "retrieved_at", "aliases", "note", "main_claim", "main_claim_locator", "main_claim_ids", "main_claim_omission"},
    "claim": {"id", "paper_id", "statement", "kind", "scope", "evidence", "assessment", "uses", "evidence_family", "status", "superseded_by", "inherited_threats", "estimate"},
    "evidence": {"locator", "observation", "authors_interpretation", "analyst_inference"},
    "assessment": {"strength", "reason", "limitation"},
    "use": {"role", "decision", "transfer_assumption"},
    "estimate": {"effect", "ci_low", "ci_high", "null_value", "unit"},
    "edge": {"id", "source", "target", "relation", "status", "evidence_claims", "rationale", "independence", "source_locator", "independence_basis"},
    "tension": {"id", "claim_ids", "classification", "comparison", "explanation", "resolution_search", "frontier_candidate", "alternatives", "test"},
    "test": {"variable", "controls", "predictions", "gap_killer"},
    "proposition": {"id", "statement", "scope", "supporting_claim_ids", "counter_claim_ids", "uncertain_claim_ids", "context_claim_ids", "candidate_claim_ids", "synthesis", "independent_evidence_count", "independent_replication_count", "independence_assessment", "independence_basis", "coverage", "justification", "refutation_basis"},
    "queue": {"paper_id", "action", "focus", "reason"},
    "search": {"channel", "query", "source", "date", "outcome", "kept_ids", "round", "changed"},
}


def canonical_url(value):
    """Normalize identity clues only; never fetch a URL or infer study independence."""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return value
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    if host in {"doi.org", "dx.doi.org"}:
        return "doi:" + path.lstrip("/").lower()
    if host in {"arxiv.org", "export.arxiv.org"}:
        path = re.sub(r"^/(?:abs|pdf)/", "/", path).removesuffix(".pdf")
    return urlunparse(("https", host, path, "", urlencode(sorted(query)), ""))


def check(data):
    errors, cautions = [], []

    def require(ok, message):
        if not ok:
            errors.append(message)

    def enum(value, allowed):
        return isinstance(value, str) and value in allowed

    def obj(value, kind, where):
        if not isinstance(value, dict):
            errors.append(f"{where}: expected object")
            return False
        for key in value:
            if key not in KEYS[kind]:
                cautions.append(f"{where}.{key}: unknown field; check spelling or document the extension")
        return True

    def fields(value, names, where):
        if not isinstance(value, dict):
            errors.append(f"{where}: expected object")
            return False
        for name in names:
            require(isinstance(value.get(name), str) and bool(value[name].strip()),
                    f"{where}.{name}: expected nonempty string")
        return True

    def choice(value, key, allowed, where):
        require(enum(value.get(key), allowed), f"{where}.{key}: expected one of {', '.join(sorted(allowed))}")

    def strings(value, where):
        require(isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value),
                f"{where}: expected list of nonempty strings")
        return [x for x in value if isinstance(x, str) and x.strip()] if isinstance(value, list) else []

    def iso(value, where):
        try:
            valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
        except (ValueError, TypeError):
            valid = False
        require(valid, f"{where}: expected ISO date")
        return value if valid else None

    if not obj(data, "root", "root"):
        return errors, cautions
    version = data.get("schema_version")
    require(type(version) is int and version in (1, 2), "schema_version: expected 1 or 2")
    profile = data.get("profile", "full")
    require(enum(profile, {"scout", "full"}), "profile: expected scout or full")
    compact = version == 2 and profile == "scout"
    context = data.get("context")
    as_of, budget = None, None
    if obj(context, "context", "context"):
        fields(context, ["question", "decision", "bounds", "stop_reason"] + ([] if compact else ["target_setting"]), "context")
        choice(context, "mode", {"scout", "map", "update"}, "context")
        as_of = iso(context.get("as_of"), "context.as_of")
        if "stop_basis" in context:
            choice(context, "stop_basis", {"budget", "corpus_exhausted", "decision_saturation", "other"}, "context")
        if "deep_read_budget" in context:
            budget = context["deep_read_budget"]
            require(type(budget) is int and budget >= 0, "context.deep_read_budget: expected nonnegative integer")

    collections, indexes = {}, {}
    for name in COLLECTIONS:
        optional = compact and name in {"propositions", "edges", "tensions", "searches"}
        value = data.get(name, [] if optional else None)
        require(isinstance(value, list), f"{name}: expected list")
        collections[name] = value if isinstance(value, list) else []
    for name, kind in [("papers", "paper"), ("claims", "claim"), ("propositions", "proposition"), ("edges", "edge"), ("tensions", "tension")]:
        index = {}
        for i, value in enumerate(collections[name]):
            where = f"{name}[{i}]"
            if not obj(value, kind, where):
                continue
            fields(value, ["id"], where)
            ident = value.get("id")
            if isinstance(ident, str) and ident.strip():
                require(ident not in index, f"{name}: duplicate ID {ident}")
                index[ident] = value
        indexes[name] = index
    papers, claims = indexes["papers"], indexes["claims"]

    def refs(value, index, where, minimum=0):
        ids = strings(value, where)
        require(len(ids) >= minimum, f"{where}: requires at least {minimum} entries")
        for ident in ids:
            require(ident in index, f"{where}: unknown reference {ident!r}")
        require(len(set(ids)) == len(ids), f"{where}: duplicate references")
        return [x for x in ids if x in index]

    def paper_for(cid):
        pid = claims.get(cid, {}).get("paper_id")
        return papers.get(pid, {}) if isinstance(pid, str) else {}

    def inspected(cid):
        return enum(paper_for(cid).get("access"), {"fulltext", "excerpt"})

    def supported(cid):
        assessment = claims.get(cid, {}).get("assessment")
        return inspected(cid) and isinstance(assessment, dict) and assessment.get("strength") == "supported"

    def family(cid):
        value = claims.get(cid, {}).get("evidence_family", paper_for(cid).get("evidence_family", "unknown"))
        return value if isinstance(value, str) else "unknown"

    def roles(cid):
        uses = claims[cid].get("uses")
        return {u["role"] for u in uses if isinstance(u, dict) and isinstance(u.get("role"), str)} if isinstance(uses, list) else set()

    seen_identity, seen_titles = {}, {}
    for pid, value in papers.items():
        fields(value, ["title", "url"] + ([] if compact else ["version", "evidence_family"]), pid)
        choice(value, "access", ACCESS, pid)
        url = value.get("url")
        if isinstance(url, str):
            try:
                parsed = urlparse(url)
                valid = parsed.scheme in {"http", "https"} and bool(parsed.netloc) or parsed.scheme == "local" and bool(parsed.path)
                require(valid, f"{pid}.url: expected source URL/local URI")
                key = (canonical_url(url), str(value.get("version", "unknown")))
                require(key not in seen_identity, f"{pid}: duplicate source identity with {seen_identity.get(key)}; merge paper records, keep separate claims/versions")
                seen_identity[key] = pid
            except ValueError:
                errors.append(f"{pid}.url: malformed URL")
        doi = value.get("doi")
        if isinstance(doi, str) and doi.strip():
            key = ("doi:" + re.sub(r"^(?:https?://(?:dx\.)?doi.org/|doi:)", "", doi, flags=re.I).lower(), str(value.get("version", "unknown")))
            prior = seen_identity.get(key)
            require(prior is None or prior == pid, f"{pid}: duplicate DOI/version with {prior}")
            seen_identity[key] = pid
        title = value.get("title")
        if isinstance(title, str):
            normalized = re.sub(r"\W+", "", title).lower()
            if normalized in seen_titles:
                cautions.append(f"{pid}: same normalized title as {seen_titles[normalized]}; check versions and shared origins")
            seen_titles[normalized] = pid
        for key in ["authors", "datasets", "code_urls", "study_ids", "aliases"]:
            if key in value:
                strings(value[key], f"{pid}.{key}")
        if value.get("evidence_family") == "unknown" and not compact:
            cautions.append(f"{pid}: evidence independence not established")
        if "main_claim_ids" in value:
            main_ids = refs(value["main_claim_ids"], claims, f"{pid}.main_claim_ids")
            require(all(claims[cid].get("paper_id") == pid for cid in main_ids), f"{pid}: main_claim_ids belong to another paper")
        if enum(value.get("access"), {"fulltext", "excerpt"}) and not value.get("main_claim_ids") and not value.get("main_claim_omission"):
            cautions.append(f"{pid}: main-claim coverage unrecorded; identify central claims/locations or explain inspection limits")

    for cid, value in claims.items():
        fields(value, ["paper_id", "statement", "scope"], cid)
        pid = value.get("paper_id")
        require(isinstance(pid, str) and pid in papers, f"{cid}: unknown paper_id")
        choice(value, "kind", {"empirical", "theoretical", "interpretive"}, cid)
        if "evidence_family" in value:
            fields(value, ["evidence_family"], cid)
        if "status" in value:
            choice(value, "status", {"active", "superseded", "withdrawn"}, cid)
        evidence, assessment = value.get("evidence"), value.get("assessment")
        if obj(evidence, "evidence", f"{cid}.evidence"):
            fields(evidence, ["locator", "observation"] + ([] if compact else ["authors_interpretation", "analyst_inference"]), f"{cid}.evidence")
        if obj(assessment, "assessment", f"{cid}.assessment"):
            fields(assessment, ["reason", "limitation"], f"{cid}.assessment")
            choice(assessment, "strength", {"supported", "provisional", "unsupported"}, f"{cid}.assessment")
            if assessment.get("strength") == "supported":
                require(inspected(cid), f"{cid}: supported claim requires inspected primary text/excerpt")
        uses = value.get("uses")
        require(isinstance(uses, list) and bool(uses), f"{cid}.uses: requires a decision link")
        for use in uses if isinstance(uses, list) else []:
            if obj(use, "use", f"{cid}.uses"):
                fields(use, ["decision", "transfer_assumption"], f"{cid}.uses")
                choice(use, "role", ROLES, f"{cid}.uses")
        if "superseded_by" in value:
            refs([value["superseded_by"]], claims, f"{cid}.superseded_by", 1)
            require(value["superseded_by"] != cid, f"{cid}: cannot supersede itself")
        if "inherited_threats" in value:
            inherited = refs(value["inherited_threats"], claims, f"{cid}.inherited_threats")
            require(cid not in inherited, f"{cid}: inherited threat cannot reference itself")
        if "estimate" in value and obj(value["estimate"], "estimate", f"{cid}.estimate"):
            est = value["estimate"]
            numeric = {}
            for key in ["effect", "ci_low", "ci_high", "null_value"]:
                number = est.get(key)
                valid = type(number) is int or type(number) is float and math.isfinite(number)
                require(valid, f"{cid}.estimate.{key}: expected finite number")
                if valid:
                    numeric[key] = number
            fields(est, ["unit"], f"{cid}.estimate")
            if "ci_low" in numeric and "ci_high" in numeric:
                require(numeric["ci_low"] <= numeric["ci_high"], f"{cid}: reversed confidence interval")

    def independence(ids, where, asserted, basis):
        if not asserted:
            return
        families = {family(cid) for cid in ids}
        require("unknown" not in families, f"{where}: independence is unestablished")
        if not basis:
            cautions.append(f"{where}: independence is asserted from labels without independence_basis; verify original studies")
        for i, left in enumerate(ids):
            for right in ids[i + 1:]:
                for key in ["authors", "datasets", "code_urls", "study_ids"]:
                    a, b = paper_for(left).get(key), paper_for(right).get(key)
                    a = {x.casefold() for x in a if isinstance(x, str)} if isinstance(a, list) else set()
                    b = {x.casefold() for x in b if isinstance(x, str)} if isinstance(b, list) else set()
                    if a & b:
                        cautions.append(f"{where}: {left}/{right} share {key}; independence needs a result-level justification")

    conflict_pairs, inactive = [], set()
    for cid, value in claims.items():
        if enum(value.get("status"), {"superseded", "withdrawn"}) or "superseded_by" in value:
            inactive.add(cid)
    for edge in indexes["edges"].values():
        if edge.get("relation") == "supersedes" and edge.get("status") == "source_supported" and isinstance(edge.get("target"), str):
            inactive.add(edge["target"])

    for tid, value in indexes["tensions"].items():
        ids = refs(value.get("claim_ids"), claims, f"{tid}.claim_ids", 2)
        fields(value, ["comparison", "explanation", "resolution_search"], tid)
        choice(value, "classification", {"comparable_conflict", "scope_mismatch", "inconclusive", "not_opposed"}, tid)
        if "frontier_candidate" in value:
            require(type(value["frontier_candidate"]) is bool, f"{tid}.frontier_candidate: expected boolean")
        require(not (value.get("frontier_candidate") is True and value.get("classification") == "not_opposed"), f"{tid}: not_opposed cannot be a contradiction frontier")
        if value.get("classification") == "comparable_conflict":
            require(all(supported(cid) and cid not in inactive for cid in ids), f"{tid}: comparable conflict requires supported, active primary evidence")
            conflict_pairs.append(set(ids))
            for cid in ids:
                est = claims[cid].get("estimate")
                if isinstance(est, dict) and all(type(est.get(k)) in (int, float) for k in ("ci_low", "ci_high", "null_value")) and est["ci_low"] <= est["null_value"] <= est["ci_high"]:
                    cautions.append(f"{tid}: {cid} interval includes the null; opposing point estimates alone do not establish conflict")
        if value.get("classification") == "comparable_conflict" or value.get("frontier_candidate") is True:
            alternatives = strings(value.get("alternatives"), f"{tid}.alternatives")
            require(len(alternatives) >= 2, f"{tid}: requires competing explanations")
            if obj(value.get("test"), "test", f"{tid}.test"):
                fields(value["test"], ["variable", "controls", "predictions", "gap_killer"], f"{tid}.test")
        elif "test" in value and obj(value["test"], "test", f"{tid}.test"):
            fields(value["test"], ["variable", "controls", "predictions", "gap_killer"], f"{tid}.test")

    for eid, value in indexes["edges"].items():
        ids = refs([value.get("source"), value.get("target")], claims, eid, 2)
        evidence = refs(value.get("evidence_claims"), claims, f"{eid}.evidence_claims", 1)
        fields(value, ["rationale"], eid)
        choice(value, "relation", RELATIONS, eid)
        choice(value, "status", {"source_supported", "hypothesis"}, eid)
        choice(value, "independence", {"independent", "shared", "unknown"}, eid)
        if value.get("status") == "source_supported":
            require(all(supported(cid) for cid in ids + evidence), f"{eid}: source-supported edge requires supported inspected endpoints and evidence")
            if not value.get("source_locator"):
                cautions.append(f"{eid}: relationship has no source_locator; endpoint citations alone do not verify the relationship")
            if value.get("relation") == "contradicts":
                require(any(set(ids) <= pair for pair in conflict_pairs), f"{eid}: contradiction lacks comparable-conflict card")
        if value.get("independence") == "independent" and len(ids) == 2:
            require(family(ids[0]) != family(ids[1]), f"{eid}: shared evidence family cannot be independent")
            independence(ids, eid, True, value.get("independence_basis"))

    for qid, value in indexes["propositions"].items():
        fields(value, ["statement", "scope", "independence_assessment", "coverage", "justification"], qid)
        choice(value, "synthesis", SYNTHESES, qid)
        groups = {}
        for key in ["supporting_claim_ids", "counter_claim_ids", "uncertain_claim_ids", "context_claim_ids", "candidate_claim_ids"]:
            groups[key] = refs(value.get(key, []), claims, f"{qid}.{key}")
        used = set()
        for key, ids in groups.items():
            require(not used.intersection(ids), f"{qid}: claim membership overlaps across support/counter/uncertain/context/candidate lists")
            used.update(ids)
            if key in {"supporting_claim_ids", "counter_claim_ids", "uncertain_claim_ids"}:
                for cid in ids:
                    require(supported(cid) and cid not in inactive, f"{qid}: direct or interval evidence must be supported and active; {cid} is unavailable, provisional or superseded")
                    require(roles(cid) != {"analogy"}, f"{qid}: analogy-only {cid} belongs in context_claim_ids, not direct evidence")
                    if claims[cid].get("inherited_threats"):
                        cautions.append(f"{qid}: {cid} carries inherited validity threats; assess before relying on its result")
        support, counter = groups["supporting_claim_ids"], groups["counter_claim_ids"]
        count = value.get("independent_evidence_count")
        require("independent_evidence_count" in value and (count is None or type(count) is int and count >= 0), f"{qid}: independent_evidence_count must be nonnegative integer or null")
        families = {family(cid) for cid in support if family(cid) != "unknown"}
        if type(count) is int:
            require(count <= len(families), f"{qid}: independent count exceeds known supporting evidence families")
        if value.get("synthesis") == "convergent":
            require(type(count) is int and count >= 2 and len(families) >= 2, f"{qid}: convergent requires at least two declared independent supporting contributions; use conditional when independence is unknown")
        if value.get("synthesis") == "convergent":
            require(not counter, f"{qid}: convergent cannot retain unresolved direct counterevidence")
        if value.get("synthesis") == "contested":
            require(bool(counter), f"{qid}: contested requires direct counterevidence")
        if value.get("synthesis") == "refuted":
            require(bool(counter), f"{qid}: refuted requires direct counterevidence")
            fields(value, ["refutation_basis"], qid)
        if value.get("synthesis") == "uninformative":
            require(bool(groups["uncertain_claim_ids"]), f"{qid}: uninformative requires inspected interval or nondiscriminating evidence")
        if "independent_replication_count" in value:
            rep = value["independent_replication_count"]
            require(rep is None or type(rep) is int and rep >= 0, f"{qid}.independent_replication_count: expected nonnegative integer or null")
            if type(rep) is int and type(count) is int:
                require(rep <= max(count - 1, 0), f"{qid}: replication count excludes the originating contribution")
        independence(support, qid, type(count) is int and count > 1, value.get("independence_basis"))
    if claims and not indexes["propositions"] and not compact:
        cautions.append("No proposition synthesis; support convergence has not been assessed")

    seen, deep_reads = set(), 0
    for i, value in enumerate(collections["reading_queue"]):
        where = f"reading_queue[{i}]"
        if not obj(value, "queue", where):
            continue
        fields(value, ["paper_id", "focus", "reason"], where)
        pid = value.get("paper_id")
        if isinstance(pid, str):
            require(pid in papers, f"{where}: unknown paper_id")
            require(pid not in seen, f"{where}: duplicate paper in queue")
            seen.add(pid)
        choice(value, "action", {"deep_read", "targeted_read", "verify_access", "defer", "exclude"}, where)
        deep_reads += value.get("action") == "deep_read"
    if type(budget) is int:
        require(deep_reads <= budget, "reading_queue: deep_read count exceeds context.deep_read_budget")

    kept, rounds = set(), {}
    all_ids = set().union(*(set(index) for index in indexes.values()))
    all_ids.update("queue:" + pid for pid in papers)
    for i, value in enumerate(collections["searches"]):
        where = f"searches[{i}]"
        if not obj(value, "search", where):
            continue
        fields(value, ["channel", "query", "source", "outcome"], where)
        when = iso(value.get("date"), f"{where}.date")
        if when and as_of:
            require(when <= as_of, f"{where}: search date is after context.as_of")
        kept.update(refs(value.get("kept_ids"), papers, f"{where}.kept_ids"))
        if "round" in value or "changed" in value:
            number = value.get("round")
            require(type(number) is int and number >= 1, f"{where}.round: expected positive integer")
            changed = strings(value.get("changed"), f"{where}.changed")
            require(all(x in all_ids for x in changed), f"{where}.changed: unknown claim/paper/proposition/edge/tension/queue ID")
            if type(number) is int and number >= 1:
                rounds.setdefault(number, set()).update(changed)
    if collections["searches"] and set(papers) - kept:
        cautions.append("Papers absent from search/corpus log: " + ", ".join(sorted(set(papers) - kept)))
    if not collections["searches"] and not compact:
        cautions.append("No search/corpus-review log; coverage cannot be assessed")
    if isinstance(context, dict) and context.get("stop_basis") == "decision_saturation":
        ordered = sorted(rounds)
        require(len(ordered) >= 2 and not rounds[ordered[-1]] and not rounds[ordered[-2]] and ordered[-1] == ordered[-2] + 1,
                "context.stop_basis: decision_saturation requires two consecutive logged rounds with no changes")
    if not papers:
        cautions.append("No papers found; this is an empty map")
    return errors, list(dict.fromkeys(cautions))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.evidence.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2
    errors, cautions = check(data)
    for message in errors:
        print(f"ERROR: {message}")
    for message in cautions:
        print(f"CAUTION: {message}")
    print(f"{len(errors)} error(s), {len(cautions)} caution(s). Declared provenance checked; source meaning and independence not certified.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
