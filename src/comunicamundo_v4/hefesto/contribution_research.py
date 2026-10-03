"""Adaptive search and cheap relevance triage for an authorized contribution."""
from __future__ import annotations

import asyncio
from .contracts import SourceCandidate, SourceCandidateJudgment, SourceSearchPlan, SourceSelectionProposal
from .source_research import SourceResearch, SourceResearchResult, _pack_payload, _unpack_payload


class ContributionSearchPaused(RuntimeError):
    """A technical inability to advance; never a withdrawal of the promise."""


def search_history(progress):
    return [{
        "query": progress.get("queries", {}).get(key, {"legacy_batch": key}),
        "results": [{
            "candidate": progress["candidates"][cid],
            "rejection": progress.get("rejections", {}).get(cid),
            "accepted": progress["outcomes"].get(cid),
        } for cid in ids],
    } for key, ids in progress["batches"].items()]


class ContributionResearch(SourceResearch):
    async def run(
        self, brief, project, *, accept_acquired, record_usage=None,
        load_progress=None, save_progress=None, rejection_reason=None,
        seed_candidates=(),
    ):
        if len(project.subjects) != 1:
            raise ValueError("Contribution research requires the one selected opportunity")
        progress = await load_progress() if load_progress else None
        progress = progress or {}
        for key in ("batches", "candidates", "judgments", "payloads", "outcomes",
                    "coverage", "attempts", "queries", "rejections", "screens"):
            progress.setdefault(key, {})
        for cid, accepted in progress["outcomes"].items():
            if not accepted and rejection_reason and cid not in progress["rejections"]:
                progress["rejections"][cid] = rejection_reason(cid)
        usages = []

        async def checkpoint():
            if save_progress:
                await save_progress(progress)

        async def record(result):
            physical = result.usages or (result.usage,)
            usages.extend(physical)
            if record_usage:
                for call in physical:
                    await record_usage(call)

        # Leads verified before the suggestion are reusable, not invented URLs.
        # A separate batch avoids treating a lead as an already executed query.
        if seed_candidates and "leads" not in progress["batches"]:
            progress["batches"]["leads"] = [c.candidate_id for c in seed_candidates]
            for candidate in seed_candidates:
                progress["candidates"][candidate.candidate_id] = candidate.model_dump(mode="json")
            await checkpoint()

        batch_index = 0
        pending = list(progress["batches"])
        while not progress["coverage"]:
            if not pending:
                key = f"adaptive:{batch_index}"
                while key in progress["batches"]:
                    batch_index += 1
                    key = f"adaptive:{batch_index}"
                if key not in progress["queries"]:
                    result = await self._editor.next_search_plan(
                        brief, project, history=search_history(progress),
                    )
                    await record(result)
                    used = {(q["provider"], q["query"].strip().casefold())
                            for q in progress["queries"].values()}
                    fresh = [q for q in result.value.queries
                             if q.subject_index == 0
                             and (q.provider, q.query.strip().casefold()) not in used]
                    if not fresh:
                        await checkpoint()
                        raise ContributionSearchPaused(
                            "O planejador repetiu as consultas sem avançar. "
                            "Contribuição pendente e progresso preservado para retomada."
                        )
                    progress["queries"][key] = fresh[0].model_dump(mode="json")
                    progress.setdefault("plan", result.value.model_dump(mode="json"))
                    await checkpoint()
                query = progress["queries"][key]
                progress["current_query"] = query
                await checkpoint()
                found = await asyncio.to_thread(
                    self._discovery.discover_query, query["query"], query["provider"],
                )
                progress["batches"][key] = [c.candidate_id for c in found]
                for candidate in found:
                    progress["candidates"][candidate.candidate_id] = candidate.model_dump(mode="json")
                progress["attempts"]["0"] = progress["attempts"].get("0", 0) + 1
                await checkpoint()
                pending.append(key)

            key = pending.pop(0)
            for cid in progress["batches"][key]:
                if cid in progress["outcomes"]:
                    continue
                candidate = SourceCandidate.model_validate(progress["candidates"][cid])
                if cid not in progress["judgments"]:
                    result = await self._editor.judge_candidate(
                        brief, project, candidate, eligible_subject_indexes=[0],
                    )
                    await record(result)
                    progress["judgments"][cid] = result.value.model_dump(mode="json")
                    await checkpoint()
                judgment = SourceCandidateJudgment.model_validate(progress["judgments"][cid])
                if judgment.decision == "skip" or 0 not in judgment.covered_subject_indexes:
                    progress["outcomes"][cid] = False
                    progress["rejections"][cid] = "Title/abstract did not show a hint of the promised perspective."
                    await checkpoint()
                    continue
                if cid not in progress["payloads"]:
                    payload = await asyncio.to_thread(self._acquirer.acquire, candidate)
                    progress["payloads"][cid] = _pack_payload(payload)
                    await checkpoint()
                payload = _unpack_payload(progress["payloads"][cid])
                if payload.record.status != "acquired":
                    progress["outcomes"][cid] = False
                    progress["rejections"][cid] = payload.record.failure_reason or "Full text inaccessible; seek an equivalent or another source."
                    await checkpoint()
                    continue

                # Read just until there is a concrete hint, BEFORE expensive ingestion.
                # The window bounds one call, never the source: negative excerpts advance.
                screen = progress["screens"].setdefault(cid, {"offset": 0, "promising": False, "reasons": []})
                source_text = payload.record.normalized_text or ""
                while not screen["promising"] and screen["offset"] < len(source_text):
                    end = min(len(source_text), screen["offset"] + 12000)
                    result = await self._editor.inspect_excerpt(
                        brief, project, candidate, source_text[screen["offset"]:end],
                    )
                    await record(result)
                    screen["offset"] = end
                    screen["promising"] = result.value.promising
                    screen["reasons"].append(result.value.reason)
                    await checkpoint()
                if source_text and not screen["promising"]:
                    progress["outcomes"][cid] = False
                    progress["rejections"][cid] = "\n".join(screen["reasons"])
                    await checkpoint()
                    continue
                # Binary-only acquisition has no readable preview. Metadata was screened;
                # use the existing reader rather than rejecting an unread source.
                accepted = await accept_acquired(payload)
                progress["outcomes"][cid] = accepted
                if accepted:
                    progress["coverage"][cid] = [0]
                else:
                    progress["rejections"][cid] = (
                        rejection_reason(cid) if rejection_reason else
                        "Full reading did not fulfill the promised contribution."
                    )
                await checkpoint()
                if accepted:
                    break

        ids = list(progress["coverage"])
        plan = progress.get("plan") or {
            "queries": [{"subject_index": 0, "query": project.subjects[0].title, "provider": "you"}],
            "editorial_coverage": [project.subjects[0].title],
        }
        return SourceResearchResult(
            search_plan=SourceSearchPlan.model_validate(plan),
            candidates=tuple(SourceCandidate.model_validate(c) for c in progress["candidates"].values()),
            selection=SourceSelectionProposal(selected_candidate_ids=ids,
                subject_coverage=progress["coverage"], coverage_complete=True),
            acquisitions=tuple(_unpack_payload(progress["payloads"][cid]) for cid in ids),
            usages=tuple(usages), attempts_by_subject={0: progress["attempts"].get("0", 0)},
            failed_acquisition_count=sum(v["record"]["status"] != "acquired" for v in progress["payloads"].values()),
        )
